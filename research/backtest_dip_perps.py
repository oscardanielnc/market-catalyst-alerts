#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SISTEMA Caídas en PERPS 24/7 (Binance Futures de acciones tradfi) — ¿revive el TP3/SL1?

Hipótesis (del estrés en acciones): el gap-through que mató el stop de -1% es un artefacto del
GAP NOCTURNO de las acciones. En perps 24/7 (sin cierre) el stop debería respetarse mucho mejor.

Método (apples-to-apples con backtest_dip_stress, pero on-venue):
  - Velas 1h de Binance USDⓈ-M (binanceusdm, TICKER/USDT:USDT), toda la historia (~7 meses).
  - Resample 1h -> 1d (UTC) para calcular SOPORTES con el mismo motor dip_levels (estrellas reformul.).
  - TOUCH + carrera TP/SL simulada sobre las velas de 1H (fill fino, 24/7) -> sin gap nocturno.
  - Compara pérdida media real y % de gaps vs el resultado en ACCIONES (-3.0% / 55%).

Sin lookahead: soportes con daily[:j]; entrada/salidas en 1h desde el día siguiente al as-of.
Costo Binance perp ~0.10% round-trip. Local, NO se commitea ni despliega.
"""
import os, sys, time, statistics as st
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dip_levels import analyze_ticker
import ccxt

UNIVERSE = ["TSLA","NVDA","MU","AMD","AVGO","MRVL","ARM","INTC","PLTR","HOOD","MSTR","TSM",
            "COHR","WDC","BE","RKLB","LITE","SNDK","SMCI","CRWD","SHOP","MELI","SOFI","MARA"]

TP, SL = 3.0, 1.0
FEE = 0.10
WARMUP_D = 50          # días daily de calentamiento (historia corta: sin SMA200, ok)
SAMPLE_D = 1           # muestrear cada N días as-of
TOUCH_HZN_D = 10       # días para que el precio LLEGUE al soporte
FWD_D = 7              # días forward para la carrera TP/SL
TOUCH_TOL = 0.004
WEIGHT = {5: 1.0, 4: 0.6, 3: 0.3, 2: 0.0, 1: 0.0}


def fetch_1h(ex, ticker):
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 230 * 24 * 3600 * 1000
    out = []
    while True:
        try:
            o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception as e:
            return None
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.05)
    seen = {}
    for b in out:
        seen[b[0]] = b
    return [seen[k] for k in sorted(seen)]   # [ts_ms,o,h,l,c,v]


def resample_daily(bars1h):
    """1h -> velas diarias UTC. Devuelve (daily_dicts, day_start_idx) donde day_start_idx[date]
    = índice en bars1h de la primera vela de ese día (para arrancar el forward sin lookahead)."""
    days = {}
    order = []
    start_idx = {}
    for i, b in enumerate(bars1h):
        d = datetime.fromtimestamp(b[0] / 1000, timezone.utc).strftime("%Y-%m-%d")
        if d not in days:
            days[d] = {"o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]}
            order.append(d); start_idx[d] = i
        else:
            x = days[d]
            x["h"] = max(x["h"], b[2]); x["l"] = min(x["l"], b[3]); x["c"] = b[4]; x["v"] += b[5]
    daily = [{"t": d + "T00:00:00Z", **days[d]} for d in order]
    return daily, order, start_idx


def race_1h(bars1h, i_start, i_max, entry):
    """Carrera TP/SL sobre 1h desde i_start hasta i_max. (outcome, pnl%).
    gap = open de la vela 1h por debajo del stop / encima del target (en 24/7 ~ mínimo)."""
    stop, tgt = entry * (1 - SL / 100), entry * (1 + TP / 100)
    for i in range(i_start, min(i_max, len(bars1h))):
        o, h, l = bars1h[i][1], bars1h[i][2], bars1h[i][3]
        if o <= stop: return "loss_gap", round((o / entry - 1) * 100, 2)
        if o >= tgt:  return "win_gap",  round((o / entry - 1) * 100, 2)
        hit_s, hit_t = l <= stop, h >= tgt
        if hit_s and hit_t: return "loss", -SL
        if hit_s: return "loss", -SL
        if hit_t: return "win", TP
    last = min(i_max, len(bars1h)) - 1
    return "none", round((bars1h[last][4] / entry - 1) * 100, 2)


def run():
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    ex.load_markets()
    events = []
    used = []
    for tk in UNIVERSE:
        bars1h = fetch_1h(ex, tk)
        if not bars1h or len(bars1h) < (WARMUP_D + TOUCH_HZN_D + FWD_D + 5) * 24:
            print(f"  [skip] {tk}: {0 if not bars1h else len(bars1h)} velas 1h")
            continue
        daily, order, start_idx = resample_daily(bars1h)
        if len(daily) < WARMUP_D + TOUCH_HZN_D + FWD_D + 5:
            print(f"  [skip] {tk}: {len(daily)} dias"); continue
        used.append(tk)
        lastd = len(daily) - TOUCH_HZN_D - FWD_D - 1
        for j in range(WARMUP_D, lastd, SAMPLE_D):
            res = analyze_ticker(tk, daily[:j + 1])
            if not res:
                continue
            sups = (res.get("short_supports") or []) + (res.get("struct_supports") or [])
            # arranque del forward en 1h = primera vela del día siguiente (sin lookahead)
            if j + 1 >= len(order):
                continue
            i_fwd0 = start_idx[order[j + 1]]
            i_touch_max = i_fwd0 + TOUCH_HZN_D * 24
            seen = set()
            for sup in sups:
                p = sup["price"]; key = round(p, 2)
                if key in seen: continue
                seen.add(key)
                # buscar toque en 1h
                i_touch = None
                for i in range(i_fwd0, min(i_touch_max, len(bars1h))):
                    if bars1h[i][3] <= p * (1 + TOUCH_TOL):
                        i_touch = i; break
                if i_touch is None:
                    continue
                out, pnl = race_1h(bars1h, i_touch + 1, i_touch + 1 + FWD_D * 24, p)
                events.append({"tk": tk, "stars": sup["strength"], "conf": sup["conf_score"],
                               "out": out, "pnl": pnl})
        print(f"  {tk}: {len(daily)} dias, {len(events)} ev. acum.")

    print(f"\nPERPS: {len(used)} tickers con perp+historia: {', '.join(used)}")
    print(f"{len(events)} eventos (TP{TP}/SL{SL}, fill 1h 24/7, fee {FEE}%)\n")

    def report(label, evs):
        dep = [(e, WEIGHT[e["stars"]]) for e in evs if WEIGHT[e["stars"]] > 0]
        if not dep:
            print(f"  {label:26s} sin trades"); return
        sumw = sum(w for _, w in dep)
        exp = sum(w * (e["pnl"] - FEE) for e, w in dep) / sumw
        wins = [e for e, _ in dep if e["out"] in ("win", "win_gap")]
        loss = [e for e, _ in dep if e["out"] in ("loss", "loss_gap")]
        wr = round(100 * len(wins) / (len(wins) + len(loss))) if (wins or loss) else None
        avg_loss = round(st.mean([e["pnl"] for e in loss]), 2) if loss else None
        gaps = [e for e in loss if e["out"] == "loss_gap"]
        gshare = round(100 * len(gaps) / len(loss)) if loss else None
        print(f"  {label:26s} n={len(dep):>5d} win={str(wr)+'%':>5} exp={exp:+.3f}%/tr "
              f"perd.media={str(avg_loss)+'%':>7} gaps={str(gshare)+'%':>4}")

    print("=== SISTEMA en PERPS 24/7 (sizing por confianza) ===")
    report("TODOS", events)
    report("solo 5*", [e for e in events if e["stars"] == 5])
    print("\n  vs ACCIONES (estrés diario): perd.media -3.0%, gaps 55%, exp +0.15%/tr\n")

    print("=== winrate por estrella (perps, crudo) ===")
    bs = defaultdict(list)
    for e in events: bs[e["stars"]].append(e)
    for s in range(1, 6):
        if bs.get(s):
            dec = [e for e in bs[s] if e["out"] in ("win","win_gap","loss","loss_gap")]
            w = round(100*sum(e["out"] in ("win","win_gap") for e in dec)/len(dec)) if dec else None
            al = [e["pnl"] for e in dec if e["out"] in ("loss","loss_gap")]
            print(f"  {s}* n={len(bs[s]):>5d} winrate={str(w)+'%':>5} "
                  f"perd.media={round(st.mean(al),2) if al else 'n/a'}%")


if __name__ == "__main__":
    run()
