#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BACKTEST de la seccion CAIDAS — ¿rebotan las acciones en sus soportes?

Pregunta honesta (3 partes):
  1. Cuando el precio LLEGA a un soporte calculado por dip_levels, ¿rebota? cuanto?
  2. ¿Mas ESTRELLAS (strength 1-5) = mejor rebote / menos ruptura?
  3. ¿Mayor ACCIONABILIDAD (0-100) = mejor rebote?

Metodo (SIN lookahead):
  - Para cada ticker y cada dia "as-of" j (muestreo cada SAMPLE dias), se calculan los
    soportes usando SOLO barras[:j+1] (analyze_ticker usa el ultimo cierre como "hoy").
  - Para cada soporte (precio p, strength s), se busca hacia adelante el PRIMER dia t en
    [j+1, j+TOUCH_HZN] donde low[t] <= p*(1+TOUCH_TOL): el precio "llego" al soporte.
  - Si llego: entry = p. En los siguientes FWD dias se mide
        MFE = max(high)/entry - 1   (rebote)
        MAE = min(low)/entry  - 1   (ruptura por debajo del soporte)
    Outcomes: hit(+3%)=MFE>=3 ; held=MAE>=-3% (soporte aguanto) ; broke=MAE<=-5% (fallo).
  - Se agrega por estrellas (cada soporte) y por accionabilidad (soporte mas cercano).

Sin slippage ni fees (es un test de la SEÑAL, no de un sistema ejecutable).
Uso:  python research/backtest_dip_bounce.py
"""
import os, sys, json, statistics as st
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
from utils.dip_levels import fetch_daily_bars, analyze_ticker

UNIVERSE = "ACHR,AKAM,AMC,AMD,ANET,APLD,APP,APPS,ARM,ASML,AVAV,AVGO,BB,BBAI,BE,COHR,CRM,CRWD,DELL,DY,GLW,GME,HOOD,HYLN,INOD,INTC,IONQ,IREN,JMIA,JOBY,KTOS,LCID,LITE,LUNR,MARA,MELI,MRVL,MSTR,MU,NVDA,PLTR,QBTS,QUBT,RDDT,RGTI,RIOT,RIVN,RKLB,SE,SEDG,SHOP,SMCI,SNDK,SNOW,SOFI,SOUN,TLN,TSLA,TSM,TTWO,VRRM,VRT,WDC,WOLF,ZM,ZS".split(",")

WARMUP    = 220     # barras minimas para SMA200 + swings mayores
SAMPLE    = 5       # muestrear 1 de cada 5 dias as-of (reduce solapamiento)
TOUCH_HZN = 15      # dias para que el precio LLEGUE al soporte
TOUCH_TOL = 0.004   # low dentro de 0.4% del nivel = "llego"
FWD       = 10      # dias forward tras tocar, para medir rebote/ruptura
HIT_PCT   = 3.0     # umbral de rebote "exitoso"
HELD_PCT  = -3.0    # MAE >= -3% => soporte aguanto
BROKE_PCT = -5.0    # MAE <= -5% => soporte fallo


def med(x):  return round(st.median(x), 2) if x else None
def mean(x): return round(st.mean(x), 2) if x else None
def rate(flags): return round(100.0 * sum(flags) / len(flags), 0) if flags else None


def run():
    # SPY para beta/idiosyncratic (alineado por fecha dentro de analyze_ticker)
    spy_bars = fetch_daily_bars("SPY", limit=600)
    spy_map = {b["t"][:10]: float(b["c"]) for b in spy_bars}

    # buckets[strength] -> list of event dicts ; act_events -> nearest-support events con actionability
    by_star = defaultdict(list)
    act_events = []
    n_tickers = 0
    total_asof = 0

    for ti, tk in enumerate(UNIVERSE, 1):
        bars = fetch_daily_bars(tk, limit=600)
        if len(bars) < WARMUP + TOUCH_HZN + FWD + 5:
            print(f"  [skip] {tk}: {len(bars)} barras")
            continue
        n_tickers += 1
        lows  = [float(b["l"]) for b in bars]
        highs = [float(b["h"]) for b in bars]

        last_asof = len(bars) - TOUCH_HZN - FWD - 1
        for j in range(WARMUP, last_asof, SAMPLE):
            total_asof += 1
            res = analyze_ticker(tk, bars[:j + 1], spy_map=spy_map)
            if not res:
                continue
            sups = (res.get("short_supports") or []) + (res.get("struct_supports") or [])
            nearest = res.get("short_supports") or res.get("struct_supports")
            nearest_price = nearest[0]["price"] if nearest else None
            act = res.get("actionability")

            seen_prices = set()
            for sup in sups:
                p = sup["price"]
                # dedup soportes casi-iguales en la misma as-of (clusters solapados)
                key = round(p, 1)
                if key in seen_prices:
                    continue
                seen_prices.add(key)

                # buscar primer toque en [j+1, j+TOUCH_HZN]
                touch_t = None
                for t in range(j + 1, min(j + 1 + TOUCH_HZN, len(bars) - FWD)):
                    if lows[t] <= p * (1 + TOUCH_TOL):
                        touch_t = t
                        break
                if touch_t is None:
                    continue

                entry = p
                fwd_hi = max(highs[touch_t:touch_t + FWD + 1])
                fwd_lo = min(lows[touch_t:touch_t + FWD + 1])
                mfe = (fwd_hi / entry - 1) * 100
                mae = (fwd_lo / entry - 1) * 100
                # CARRERA path-dependent: desde el toque, ¿llega antes a +3% (rebote) o a -3% (rompe)?
                # win=rebote primero, loss=ruptura primero, none=ninguno en FWD. Esta es la
                # pregunta tradeable real: entras en el soporte, ¿sube o cae primero?
                race = "none"
                for t in range(touch_t, touch_t + FWD + 1):
                    up   = highs[t] >= entry * (1 + HIT_PCT / 100)
                    down = lows[t]  <= entry * (1 + HELD_PCT / 100)   # HELD_PCT=-3 => -3%
                    if down and not up:
                        race = "loss"; break
                    if up and not down:
                        race = "win"; break
                    if up and down:   # ambos el mismo dia: conservador = perdida (asumimos toca stop)
                        race = "loss"; break
                ev = {
                    "tk": tk, "strength": sup["strength"], "mfe": mfe, "mae": mae,
                    "hit": mfe >= HIT_PCT, "held": mae >= HELD_PCT, "broke": mae <= BROKE_PCT,
                    "race": race,
                    "reaction_hist": sup.get("reaction_pct", 0), "touches": sup.get("touches", 0),
                }
                by_star[sup["strength"]].append(ev)
                if nearest_price is not None and abs(p - nearest_price) < 1e-6 and act is not None:
                    act_events.append({**ev, "act": act})
        print(f"  [{ti}/{len(UNIVERSE)}] {tk}: ok")

    # ── Reporte ──
    print("\n" + "=" * 84)
    print(f"BACKTEST CAIDAS — {n_tickers} tickers, {total_asof} dias as-of muestreados")
    print(f"Toque=low<=soporte*(1+{TOUCH_TOL}), horizonte toque={TOUCH_HZN}d, forward={FWD}d")
    print("=" * 84)

    def block(title, groups):
        print(f"\n{title}")
        print(f"  {'bucket':14s} {'n':>5s} {'WIN +3<antes>-3':>14s} {'rebote MFE':>11s} "
              f"{'caida MAE':>10s} {'aguanto':>8s} {'rompio5%':>8s}")
        for label, evs in groups:
            if not evs:
                print(f"  {label:14s} {0:>5d}"); continue
            decided = [e for e in evs if e["race"] != "none"]
            winrate = rate([e["race"] == "win" for e in decided]) if decided else None
            print(f"  {label:14s} {len(evs):>5d} {str(winrate)+'%':>13} "
                  f"{med([e['mfe'] for e in evs]):>11} {med([e['mae'] for e in evs]):>10} "
                  f"{rate([e['held'] for e in evs]):>7}% {rate([e['broke'] for e in evs]):>7}%")

    # 1) global
    allev = [e for evs in by_star.values() for e in evs]
    block("GLOBAL (todos los soportes tocados)", [("TODOS", allev)])

    # 2) por estrellas
    block("POR ESTRELLAS (strength del soporte)",
          [(f"{s} estrella" + ("s" if s != 1 else ""), by_star.get(s, [])) for s in range(1, 6)])

    # 3) por accionabilidad (soporte mas cercano)
    def abucket(a):
        if a < 40: return "0-39"
        if a < 55: return "40-54"
        if a < 70: return "55-69"
        if a < 85: return "70-84"
        return "85-100"
    ab = defaultdict(list)
    for e in act_events:
        ab[abucket(e["act"])].append(e)
    block("POR ACCIONABILIDAD (soporte mas cercano)",
          [(b, ab.get(b, [])) for b in ["0-39", "40-54", "55-69", "70-84", "85-100"]])

    # guardar crudo para inspeccion
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_dip_bounce_results.json")
    json.dump({"by_star": {k: v for k, v in by_star.items()}, "act_events": act_events,
               "params": {"WARMUP": WARMUP, "SAMPLE": SAMPLE, "TOUCH_HZN": TOUCH_HZN,
                          "FWD": FWD, "TOUCH_TOL": TOUCH_TOL}},
              open(out, "w"), default=str)
    print(f"\nCrudo guardado en {os.path.basename(out)}")


if __name__ == "__main__":
    run()
