#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ESTRÉS del sistema Caídas — fills REALISTAS (gap-through) + sub-período bajista + out-of-sample.

Diferencia clave vs backtest_dip_sized: el SL ya NO se asume llenado exacto a -1%. Se usa el
OHLC real de cada día forward:
  - si el día ABRE por debajo del stop  -> sales al OPEN (gap-through: pérdida peor que -SL)
  - si ABRE por encima del target       -> sales al OPEN (gap a favor: ganancia > TP)
  - intradía (sin gap): si toca stop y target el mismo día -> conservador = stop primero
Entrada = precio del soporte (limit). Sin lookahead (soportes con barras[:j], estrellas predictivas
ya las da analyze_ticker reformulado).

Tres análisis:
  A. OPTIMISTA (stop exacto) vs REALISTA (gap-through) — cuánto se degrada.
  B. Sub-período BAJISTA (régimen SPY no-sano) vs alcista.
  C. Out-of-sample: primera mitad (in-sample) vs segunda mitad (OOS) — ¿sobreajuste?
"""
import os, sys, statistics as st
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
from utils.dip_levels import fetch_daily_bars, analyze_ticker, spy_regime

UNIVERSE = "ACHR,AKAM,AMC,AMD,ANET,APLD,APP,APPS,ARM,ASML,AVAV,AVGO,BB,BBAI,BE,COHR,CRM,CRWD,DELL,DY,GLW,GME,HOOD,HYLN,INOD,INTC,IONQ,IREN,JMIA,JOBY,KTOS,LCID,LITE,LUNR,MARA,MELI,MRVL,MSTR,MU,NVDA,PLTR,QBTS,QUBT,RDDT,RGTI,RIOT,RIVN,RKLB,SE,SEDG,SHOP,SMCI,SNDK,SNOW,SOFI,SOUN,TLN,TSLA,TSM,TTWO,VRRM,VRT,WDC,WOLF,ZM,ZS".split(",")

WARMUP, SAMPLE, TOUCH_HZN, TOUCH_TOL, FWD = 220, 5, 15, 0.004, 10
TP, SL = 3.0, 1.0
FEE = 0.10                      # round-trip Binance perp (lo viable)
WEIGHT = {5: 1.0, 4: 0.6, 3: 0.3, 2: 0.0, 1: 0.0}


# NOTA: entramos al soporte el dia del toque (t0) via limit. El open/high de t0 ocurren ANTES
# de nuestra entrada (el precio CAE al soporte intradia) -> se evaluan salidas desde t0+1 para
# evitar lookahead. Conservador: un rebote mismo-dia no se captura.

def race_opt(highs, lows, closes, t0, entry):
    """OPTIMISTA: stop/target exactos. (outcome, pnl%). Evalua desde t0+1."""
    stop, tgt = entry * (1 - SL / 100), entry * (1 + TP / 100)
    for t in range(t0 + 1, min(t0 + 1 + FWD, len(highs))):
        up, down = highs[t] >= tgt, lows[t] <= stop
        if down and not up: return "loss", -SL
        if up and not down: return "win", TP
        if up and down:     return "loss", -SL
    return "none", round((closes[min(t0 + FWD, len(closes) - 1)] / entry - 1) * 100, 2)


def race_real(opens, highs, lows, closes, t0, entry):
    """REALISTA: gap-through con OHLC. (outcome, pnl%). pnl en % sobre entry. Evalua desde t0+1."""
    stop, tgt = entry * (1 - SL / 100), entry * (1 + TP / 100)
    for t in range(t0 + 1, min(t0 + 1 + FWD, len(highs))):
        o = opens[t]
        if o <= stop:                       # gap DOWN a través del stop -> sale al open
            return "loss_gap", round((o / entry - 1) * 100, 2)
        if o >= tgt:                        # gap UP a través del target -> sale al open (a favor)
            return "win_gap", round((o / entry - 1) * 100, 2)
        hit_stop, hit_tgt = lows[t] <= stop, highs[t] >= tgt
        if hit_stop and hit_tgt: return "loss", -SL     # ambiguo intradía -> conservador
        if hit_stop:             return "loss", -SL
        if hit_tgt:              return "win", TP
    return "none", round((closes[min(t0 + FWD, len(closes) - 1)] / entry - 1) * 100, 2)


def gen_events():
    spy_bars = fetch_daily_bars("SPY", limit=600)
    spy_map = {b["t"][:10]: float(b["c"]) for b in spy_bars}
    events = []
    for ti, tk in enumerate(UNIVERSE, 1):
        bars = fetch_daily_bars(tk, limit=600)
        if len(bars) < WARMUP + TOUCH_HZN + FWD + 5:
            continue
        o = [float(b["o"]) for b in bars]; h = [float(b["h"]) for b in bars]
        l = [float(b["l"]) for b in bars]; c = [float(b["c"]) for b in bars]
        dts = [b["t"][:10] for b in bars]
        last = len(bars) - TOUCH_HZN - FWD - 1
        for j in range(WARMUP, last, SAMPLE):
            res = analyze_ticker(tk, bars[:j + 1], spy_map=spy_map)
            if not res:
                continue
            asof = dts[j]
            spy_h = res["risk"].get("spy_healthy")
            sups = (res.get("short_supports") or []) + (res.get("struct_supports") or [])
            seen = set()
            for sup in sups:
                p = sup["price"]; key = round(p, 1)
                if key in seen: continue
                seen.add(key)
                touch_t = None
                for t in range(j + 1, min(j + 1 + TOUCH_HZN, len(bars) - FWD)):
                    if l[t] <= p * (1 + TOUCH_TOL):
                        touch_t = t; break
                if touch_t is None: continue
                oo, opnl = race_opt(h, l, c, touch_t, p)
                ro, rpnl = race_real(o, h, l, c, touch_t, p)
                events.append({"tk": tk, "asof": asof, "spy_h": spy_h,
                               "stars": sup["strength"], "conf": sup["conf_score"],
                               "opt_pnl": opnl, "real_out": ro, "real_pnl": rpnl})
        print(f"  [{ti}/{len(UNIVERSE)}] {tk}: {len(events)} ev.")
    return events


def stats(evs, fee=FEE, key="real_pnl"):
    """expectancy ponderada por confianza, neta de fee; winrate; avg pérdida realista."""
    dep = [(e, WEIGHT[e["stars"]]) for e in evs if WEIGHT[e["stars"]] > 0]
    if not dep: return None
    sumw = sum(w for _, w in dep)
    exp = sum(w * (e[key] - fee) for e, w in dep) / sumw
    wins = [e for e, _ in dep if e["real_out"] in ("win", "win_gap")]
    losses = [e for e, _ in dep if e["real_out"] in ("loss", "loss_gap")]
    winr = round(100 * len(wins) / (len(wins) + len(losses))) if (wins or losses) else None
    avg_loss = round(st.mean([e["real_pnl"] for e in losses]), 2) if losses else None
    gaps = [e for e in losses if e["real_out"] == "loss_gap"]
    gap_share = round(100 * len(gaps) / len(losses)) if losses else None
    return {"n": len(dep), "exp": round(exp, 3), "winr": winr,
            "avg_loss": avg_loss, "gap_share": gap_share}


def show(label, s):
    if not s: print(f"  {label:30s} sin trades"); return
    print(f"  {label:30s} n={s['n']:>5d} win={str(s['winr'])+'%':>5} exp={s['exp']:+.3f}%/tr "
          f"perd.media={str(s['avg_loss'])+'%':>7} gaps={str(s['gap_share'])+'%':>4}")


def main():
    evs = gen_events()
    print(f"\n{len(evs)} eventos generados.\n")

    # A. Optimista vs realista (global, sizing por confianza)
    print("=== A. OPTIMISTA (stop exacto) vs REALISTA (gap-through) — fee 0.10% ===")
    so = stats(evs, key="opt_pnl"); sr = stats(evs, key="real_pnl")
    show("OPTIMISTA", so); show("REALISTA (gap)", sr)
    deg = round(so["exp"] - sr["exp"], 3) if so and sr else None
    print(f"  -> degradacion por gap-through: {deg} %/trade\n")

    # estrellas bajo modelo realista
    print("=== estrellas (modelo REALISTA, fee 0.10%) ===")
    byst = defaultdict(list)
    for e in evs: byst[e["stars"]].append(e)
    for s in range(1, 6):
        if byst.get(s):
            ss = stats(byst[s], fee=FEE, key="real_pnl")
            # winrate crudo por estrella (sin filtro de peso)
            dec = [e for e in byst[s] if e["real_out"] in ("win","win_gap","loss","loss_gap")]
            w = round(100*sum(e["real_out"] in ("win","win_gap") for e in dec)/len(dec)) if dec else None
            print(f"  {s}* n={len(byst[s]):>5d} winrate={str(w)+'%':>5} "
                  f"exp(realista)={round(st.mean([e['real_pnl']-FEE for e in byst[s]]),3):+.3f}%/tr")

    # B. Sub-periodo bajista (regimen no-sano) vs alcista
    print("\n=== B. SUB-PERIODO por REGIMEN (modelo realista, fee 0.10%) ===")
    show("ALCISTA (SPY sano)",   stats([e for e in evs if e["spy_h"] is True]))
    show("BAJISTA (SPY no-sano)", stats([e for e in evs if e["spy_h"] is False]))
    show("solo 5* en BAJISTA",   stats([e for e in evs if e["spy_h"] is False and e["stars"] == 5]))

    # C. Out-of-sample: split temporal por fecha
    dates = sorted(set(e["asof"] for e in evs))
    mid = dates[len(dates) // 2]
    print(f"\n=== C. OUT-OF-SAMPLE (corte {mid}; modelo realista, fee 0.10%) ===")
    ins = [e for e in evs if e["asof"] < mid]; oos = [e for e in evs if e["asof"] >= mid]
    show("IN-SAMPLE (1a mitad)",  stats(ins)); show("OUT-OF-SAMPLE (2a mitad)", stats(oos))
    print("  separacion de estrellas OOS (winrate por estrella):")
    bo = defaultdict(list)
    for e in oos: bo[e["stars"]].append(e)
    for s in range(1, 6):
        if bo.get(s):
            dec = [e for e in bo[s] if e["real_out"] in ("win","win_gap","loss","loss_gap")]
            w = round(100*sum(e["real_out"] in ("win","win_gap") for e in dec)/len(dec)) if dec else None
            print(f"    {s}* n={len(bo[s]):>5d} winrate={w}%")


if __name__ == "__main__":
    main()
