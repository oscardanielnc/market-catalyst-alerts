#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SISTEMA Caídas en CRYPTO PERPS 24/7 — el test DEFINITIVO del método (años de historia).

Por qué crypto: los perps de acciones de Binance son muy nuevos (3-5 meses) → sin SMA200, muestra
chica. Los perps de crypto tienen AÑOS de historia 24/7 → SMA200 real, régimen vía BTC, muestra
grande, y es 24/7 puro (donde el estrés demostró que el stop SE RESPETA).

Método (mismo motor dip_levels, sin lookahead):
  - 1h de Binance USDⓈ-M (binanceusdm, SYM/USDT:USDT), ~550 días → resample a daily para SOPORTES.
  - BTC como "mercado": btc_map -> beta/idiosyncratic + régimen (spy_regime sobre BTC).
  - TOUCH + carrera TP/SL en velas 1h (fill fino 24/7).
  - REJILLA de TP/SL para hallar el ratio óptimo del venue (no solo 3/1).

Costo Binance perp ~0.10% round-trip. Local, NO commit/deploy.
"""
import os, sys, time, csv, statistics as st
from collections import defaultdict
from datetime import datetime, timezone

TOUCH_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dip_crypto_touches.csv")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dip_levels import analyze_ticker, spy_regime
import ccxt

UNIVERSE = ["BTC","ETH","SOL","BNB","XRP","ADA","AVAX","DOGE","LINK","DOT","LTC","ATOM",
            "NEAR","APT","ARB","OP","INJ","SUI","TIA","SEI","FIL","AAVE","UNI","ICP"]

DAYS_HIST = 550
FEE = 0.10
WARMUP_D, SAMPLE_D, TOUCH_HZN_D, FWD_D, TOUCH_TOL = 210, 2, 12, 8, 0.004
WEIGHT = {5: 1.0, 4: 0.6, 3: 0.3, 2: 0.0, 1: 0.0}
GRID = [(3, 1), (3, 1.5), (3, 2), (3, 3), (4, 2), (5, 2), (2, 1), (2, 1.5)]  # (TP%, SL%)


def fetch_1h(ex, sym_tk):
    sym = f"{sym_tk}/USDT:USDT"
    since = ex.milliseconds() - DAYS_HIST * 24 * 3600 * 1000
    out = []
    while True:
        try:
            o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception:
            return None
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.04)
    seen = {b[0]: b for b in out}
    return [seen[k] for k in sorted(seen)]


def resample_daily(bars1h):
    days, order, start_idx = {}, [], {}
    for i, b in enumerate(bars1h):
        d = datetime.fromtimestamp(b[0] / 1000, timezone.utc).strftime("%Y-%m-%d")
        if d not in days:
            days[d] = {"o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]}
            order.append(d); start_idx[d] = i
        else:
            x = days[d]; x["h"] = max(x["h"], b[2]); x["l"] = min(x["l"], b[3]); x["c"] = b[4]; x["v"] += b[5]
    daily = [{"t": d + "T00:00:00Z", **days[d]} for d in order]
    return daily, order, start_idx


def race_1h(bars1h, i_start, i_max, entry, tp, sl):
    out, pnl, _ = race_1h_idx(bars1h, i_start, i_max, entry, tp, sl)
    return out, pnl


def race_1h_idx(bars1h, i_start, i_max, entry, tp, sl):
    """Como race_1h pero devuelve también el índice 1h de SALIDA (para duración/calendario)."""
    stop, tgt = entry * (1 - sl / 100), entry * (1 + tp / 100)
    for i in range(i_start, min(i_max, len(bars1h))):
        o, h, l = bars1h[i][1], bars1h[i][2], bars1h[i][3]
        if o <= stop: return "loss_gap", round((o / entry - 1) * 100, 2), i
        if o >= tgt:  return "win_gap",  round((o / entry - 1) * 100, 2), i
        if l <= stop and h >= tgt: return "loss", -sl, i
        if l <= stop: return "loss", -sl, i
        if h >= tgt:  return "win", tp, i
    last = min(i_max, len(bars1h)) - 1
    return "none", round((bars1h[last][4] / entry - 1) * 100, 2), last


def run():
    ex = ccxt.binanceusdm({"enableRateLimit": True})
    ex.load_markets()

    # BTC primero: benchmark de mercado (beta/idio + régimen)
    btc1h = fetch_1h(ex, "BTC")
    btc_daily, _, _ = resample_daily(btc1h)
    btc_map = {b["t"][:10]: float(b["c"]) for b in btc_daily}

    # cache de toques: guardamos (bars1h, i_touch, entry, stars, conf, spy_h) y corremos la rejilla
    touches = []
    used = []
    for tk in UNIVERSE:
        bars1h = btc1h if tk == "BTC" else fetch_1h(ex, tk)
        if not bars1h or len(bars1h) < (WARMUP_D + TOUCH_HZN_D + FWD_D + 5) * 24:
            print(f"  [skip] {tk}"); continue
        daily, order, start_idx = resample_daily(bars1h)
        if len(daily) < WARMUP_D + TOUCH_HZN_D + FWD_D + 5:
            print(f"  [skip] {tk}: {len(daily)} d"); continue
        used.append(tk)
        lastd = len(daily) - TOUCH_HZN_D - FWD_D - 1
        n0 = len(touches)
        for j in range(WARMUP_D, lastd, SAMPLE_D):
            res = analyze_ticker(tk, daily[:j + 1], spy_map=btc_map)
            if not res or j + 1 >= len(order):
                continue
            spy_h = res["risk"].get("spy_healthy")
            sups = (res.get("short_supports") or []) + (res.get("struct_supports") or [])
            i_fwd0 = start_idx[order[j + 1]]
            seen = set()
            for sup in sups:
                p = sup["price"]; key = round(p, 4)
                if key in seen: continue
                seen.add(key)
                i_touch = None
                for i in range(i_fwd0, min(i_fwd0 + TOUCH_HZN_D * 24, len(bars1h))):
                    if bars1h[i][3] <= p * (1 + TOUCH_TOL):
                        i_touch = i; break
                if i_touch is None:
                    continue
                tdate = datetime.fromtimestamp(bars1h[i_touch][0] / 1000, timezone.utc).strftime("%Y-%m-%d")
                touches.append({"bars": bars1h, "i": i_touch, "p": p, "tk": tk, "date": tdate,
                                "stars": sup["strength"], "spy_h": spy_h})
        print(f"  {tk}: {len(daily)} d, +{len(touches)-n0} toques")

    print(f"\nCRYPTO PERPS: {len(used)} símbolos, {len(touches)} toques de soporte")
    print(f"Universo: {', '.join(used)}\n")

    def eval_grid(tp, sl, subset=None):
        evs = []
        src = subset if subset is not None else touches
        for t in src:
            out, pnl = race_1h(t["bars"], t["i"] + 1, t["i"] + 1 + FWD_D * 24, t["p"], tp, sl)
            evs.append({**t, "out": out, "pnl": pnl})
        dep = [(e, WEIGHT[e["stars"]]) for e in evs if WEIGHT[e["stars"]] > 0]
        if not dep: return None
        sumw = sum(w for _, w in dep)
        exp = sum(w * (e["pnl"] - FEE) for e, w in dep) / sumw
        wins = [e for e, _ in dep if e["out"] in ("win", "win_gap")]
        loss = [e for e, _ in dep if e["out"] in ("loss", "loss_gap")]
        wr = round(100 * len(wins) / (len(wins) + len(loss))) if (wins or loss) else None
        al = round(st.mean([e["pnl"] for e in loss]), 2) if loss else None
        gshare = round(100 * sum(e["out"] == "loss_gap" for e in loss) / len(loss)) if loss else None
        return {"n": len(dep), "exp": round(exp, 3), "wr": wr, "al": al, "gaps": gshare, "evs": evs}

    print("=== REJILLA TP/SL (fill 1h 24/7, sizing por confianza, fee 0.10%) ===")
    print(f"  {'TP/SL':9s} {'n':>5s} {'winrate':>8s} {'exp/trade':>10s} {'perd.media':>11s} {'gaps':>5s} {'breakeven_wr':>12s}")
    best = None
    for tp, sl in GRID:
        r = eval_grid(tp, sl)
        if not r: continue
        be = round(100 * (sl + FEE) / (tp + sl), 1)   # winrate de breakeven aprox
        flag = ""
        if best is None or r["exp"] > best[1]:
            best = ((tp, sl), r["exp"]);
        print(f"  {str(tp)+'/'+str(sl):9s} {r['n']:>5d} {str(r['wr'])+'%':>8s} {str(r['exp'])+'%':>10s} "
              f"{str(r['al'])+'%':>11s} {str(r['gaps'])+'%':>5s} {str(be)+'%':>12s}")
    print(f"\n  -> mejor expectancy: TP/SL {best[0][0]}/{best[0][1]} = {best[1]:+.3f}%/trade\n")

    # Volcar toques con resultado TP3/SL1 + entrada/salida (walk-forward + calendario)
    with open(TOUCH_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tk", "date", "stars", "spy_h", "out", "pnl", "entry_ts", "exit_ts", "dur_h"])
        for t in touches:
            i_in = t["i"] + 1
            out, pnl, i_out = race_1h_idx(t["bars"], i_in, i_in + FWD_D * 24, t["p"], 3, 1)
            entry_ts = t["bars"][i_in][0] if i_in < len(t["bars"]) else t["bars"][t["i"]][0]
            exit_ts = t["bars"][i_out][0]
            dur_h = round((exit_ts - entry_ts) / 3600000)
            w.writerow([t["tk"], t["date"], t["stars"], t["spy_h"], out, pnl, entry_ts, exit_ts, dur_h])
    print(f"  toques (TP3/SL1 + entrada/salida) -> {os.path.basename(TOUCH_CSV)}\n")

    # Detalle del mejor + 3/1 de referencia
    for tp, sl in sorted({best[0], (3, 1)}):
        r = eval_grid(tp, sl)
        print(f"=== DETALLE TP{tp}/SL{sl} ===")
        bs = defaultdict(list)
        for e in r["evs"]: bs[e["stars"]].append(e)
        for s in range(1, 6):
            if bs.get(s):
                dec = [e for e in bs[s] if e["out"] in ("win","win_gap","loss","loss_gap")]
                w = round(100*sum(e["out"] in ("win","win_gap") for e in dec)/len(dec)) if dec else None
                print(f"    {s}* n={len(bs[s]):>5d} winrate={w}%")
        # régimen BTC
        for label, val in [("BULL (BTC sano)", True), ("BEAR (BTC no-sano)", False)]:
            sub = [t for t in touches if t["spy_h"] is val]
            rr = eval_grid(tp, sl, subset=sub)
            if rr:
                print(f"    {label:20s} n={rr['n']:>5d} win={rr['wr']}% exp={rr['exp']:+.3f}%/tr")
        print()


if __name__ == "__main__":
    run()
