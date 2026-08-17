#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DESCUBRIMIENTO + SISTEMA Caídas — ¿qué features predicen el rebote, y es operable TP/SL?

Paso 1 (este script, modo --discover): por cada toque de soporte (sin lookahead) vuelca a
CSV todas las features candidatas + el outcome bajo TP/SL (carrera path-dependent) para ver
QUÉ predice el rebote (+TP antes de −SL). Sobre eso se reformulan las estrellas.

Outcome por evento (entra en el soporte, entry = precio del soporte):
  - win  = high llega a +TP% antes de que low llegue a −SL%, dentro de FWD días
  - loss = −SL% primero (o ambos el mismo día → conservador = loss)
  - none = ninguno en FWD → se cierra al close del día touch+FWD (ret_exit)
PnL por evento = +TP / −SL / ret_exit  (para la simulación con sizing en el paso 3)

Sin fees/slippage (test de la SEÑAL). TP/SL parametrizables abajo.
Uso:  python research/backtest_dip_system.py
"""
import os, sys, csv, statistics as st
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
from utils.dip_levels import fetch_daily_bars, analyze_ticker, _sma, _sma_series

UNIVERSE = "ACHR,AKAM,AMC,AMD,ANET,APLD,APP,APPS,ARM,ASML,AVAV,AVGO,BB,BBAI,BE,COHR,CRM,CRWD,DELL,DY,GLW,GME,HOOD,HYLN,INOD,INTC,IONQ,IREN,JMIA,JOBY,KTOS,LCID,LITE,LUNR,MARA,MELI,MRVL,MSTR,MU,NVDA,PLTR,QBTS,QUBT,RDDT,RGTI,RIOT,RIVN,RKLB,SE,SEDG,SHOP,SMCI,SNDK,SNOW,SOFI,SOUN,TLN,TSLA,TSM,TTWO,VRRM,VRT,WDC,WOLF,ZM,ZS".split(",")

WARMUP, SAMPLE, TOUCH_HZN, TOUCH_TOL, FWD = 220, 5, 15, 0.004, 10
TP, SL = 3.0, 1.0     # objetivo +3% / stop −1% (lo que pidió Oscar)

OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dip_system_events.csv")


def spy_regime_series(spy_bars):
    """date(YYYY-MM-DD) -> spy_healthy(bool): SPY sobre su SMA200 y SMA50 con pendiente +."""
    closes = [float(b["c"]) for b in spy_bars]
    dates = [b["t"][:10] for b in spy_bars]
    sma50 = _sma_series(closes, 50)
    out = {}
    for i in range(len(closes)):
        if i < 200:
            out[dates[i]] = None; continue
        s200 = sum(closes[i - 199:i + 1]) / 200
        # pendiente SMA50: comparar valor actual vs 10 ruedas atrás
        j = i - 49                       # índice dentro de sma50 (empieza en idx 49)
        slope_up = j >= 10 and sma50[j] > sma50[j - 10]
        out[dates[i]] = bool(closes[i] > s200 and slope_up)
    return out


def race(highs, lows, closes, t0, entry):
    """Devuelve (outcome, ret_exit_pct). outcome in {win,loss,none}."""
    for t in range(t0, min(t0 + FWD + 1, len(highs))):
        up   = highs[t] >= entry * (1 + TP / 100)
        down = lows[t]  <= entry * (1 - SL / 100)
        if down and not up: return "loss", -SL
        if up and not down: return "win", TP
        if up and down:     return "loss", -SL    # conservador
    tend = min(t0 + FWD, len(closes) - 1)
    return "none", round((closes[tend] / entry - 1) * 100, 2)


def run():
    spy_bars = fetch_daily_bars("SPY", limit=600)
    spy_map = {b["t"][:10]: float(b["c"]) for b in spy_bars}
    spy_reg = spy_regime_series(spy_bars)

    rows = []
    for ti, tk in enumerate(UNIVERSE, 1):
        bars = fetch_daily_bars(tk, limit=600)
        if len(bars) < WARMUP + TOUCH_HZN + FWD + 5:
            continue
        highs = [float(b["h"]) for b in bars]
        lows  = [float(b["l"]) for b in bars]
        closes = [float(b["c"]) for b in bars]
        dates = [b["t"][:10] for b in bars]

        last = len(bars) - TOUCH_HZN - FWD - 1
        for j in range(WARMUP, last, SAMPLE):
            res = analyze_ticker(tk, bars[:j + 1], spy_map=spy_map)
            if not res:
                continue
            risk = res["risk"]
            asof_date = dates[j]
            spy_h = spy_reg.get(asof_date)
            shorts = res.get("short_supports") or []
            structs = res.get("struct_supports") or []
            nearest_price = (shorts or structs)[0]["price"] if (shorts or structs) else None
            act = res.get("actionability")

            seen = set()
            for src, sup in [("short", s) for s in shorts] + [("struct", s) for s in structs]:
                p = sup["price"]
                key = round(p, 1)
                if key in seen:
                    continue
                seen.add(key)
                touch_t = None
                for t in range(j + 1, min(j + 1 + TOUCH_HZN, len(bars) - FWD)):
                    if lows[t] <= p * (1 + TOUCH_TOL):
                        touch_t = t; break
                if touch_t is None:
                    continue
                outcome, ret_exit = race(highs, lows, closes, touch_t, p)
                fwd_hi = max(highs[touch_t:touch_t + FWD + 1])
                fwd_lo = min(lows[touch_t:touch_t + FWD + 1])
                rows.append({
                    "tk": tk, "asof": asof_date, "src": src,
                    "strength": sup["strength"], "n_types": sup["label"].count("+") + 1,
                    "touches": sup.get("touches", 0), "reaction_hist": sup.get("reaction_pct", 0),
                    "dist_pct": sup["dist_pct"], "is_nearest": int(abs(p - nearest_price) < 1e-6) if nearest_price else 0,
                    "actionability": act if act is not None else "",
                    "trend_healthy": int(risk["trend_healthy"]), "rsi": round(risk["rsi"], 1),
                    "drawdown": risk["drawdown"], "vs_spy": risk["vs_spy"], "beta": risk["beta"],
                    "idiosyncratic": int(risk["idiosyncratic"]), "vol_ratio": risk["vol_ratio"],
                    "spy_healthy": "" if spy_h is None else int(spy_h),
                    "outcome": outcome, "ret_exit": ret_exit,
                    "mfe": round((fwd_hi / p - 1) * 100, 2), "mae": round((fwd_lo / p - 1) * 100, 2),
                })
        print(f"  [{ti}/{len(UNIVERSE)}] {tk}: {len(rows)} eventos acum.")

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\n{len(rows)} eventos -> {os.path.basename(OUT_CSV)}  (TP={TP} SL={SL} FWD={FWD})")

    # ── Análisis de descubrimiento ──
    def winrate(evs):
        dec = [e for e in evs if e["outcome"] in ("win", "loss")]
        return (round(100 * sum(e["outcome"] == "win" for e in dec) / len(dec)) if dec else None,
                len(dec), len(evs))

    def _pnl(e):
        if e["outcome"] == "win":  return TP
        if e["outcome"] == "loss": return -SL
        return e["ret_exit"]

    def expectancy(evs):
        """retorno medio por trade (incluye none al ret_exit)."""
        pnl = [_pnl(e) for e in evs]
        return round(st.mean(pnl), 3) if pnl else None

    print("\n=== GLOBAL (TP%.0f/SL%.0f) ===" % (TP, SL))
    wr, ndec, ntot = winrate(rows)
    print(f"  n={ntot} decididos={ndec}  winrate={wr}%  expectancy/trade={expectancy(rows)}%")

    def bucket_report(title, keyfn, order=None):
        g = defaultdict(list)
        for e in rows:
            g[keyfn(e)].append(e)
        keys = order or sorted(g.keys(), key=lambda x: (x is None, x))
        print(f"\n=== {title} ===")
        print(f"  {'bucket':16s} {'n':>6s} {'winrate':>8s} {'exp/trade':>10s}")
        for k in keys:
            if k not in g: continue
            wr, nd, nt = winrate(g[k])
            print(f"  {str(k):16s} {nt:>6d} {str(wr)+'%':>8s} {str(expectancy(g[k]))+'%':>10s}")

    bucket_report("ESTRELLAS (actual)", lambda e: e["strength"], order=[1,2,3,4,5])
    bucket_report("REGIMEN SPY", lambda e: e["spy_healthy"], order=[1,0,""])
    bucket_report("TREND TICKER sano", lambda e: e["trend_healthy"], order=[1,0])
    bucket_report("IDIOSINCRATICO (cae+que beta)", lambda e: e["idiosyncratic"], order=[0,1])
    bucket_report("FUENTE soporte", lambda e: e["src"], order=["short","struct"])
    bucket_report("CONFLUENCIA (#tipos)", lambda e: min(e["n_types"],4), order=[1,2,3,4])
    bucket_report("REBOTE HIST fuerte(>=3%)", lambda e: int(e["reaction_hist"] >= 3.0), order=[1,0])
    bucket_report("RSI bucket", lambda e: ("RSI<30" if e["rsi"]<30 else "30-45" if e["rsi"]<45 else "45-60" if e["rsi"]<60 else "60+"),
                  order=["RSI<30","30-45","45-60","60+"])
    bucket_report("DIST al soporte", lambda e: ("<2%" if e["dist_pct"]<2 else "2-5%" if e["dist_pct"]<5 else "5-10%" if e["dist_pct"]<10 else "10%+"),
                  order=["<2%","2-5%","5-10%","10%+"])


if __name__ == "__main__":
    run()
