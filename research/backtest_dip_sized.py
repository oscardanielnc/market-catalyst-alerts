#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SISTEMA Caídas — backtest operable: TP 3% / SL 1%, monto escalado por CONFIANZA + régimen + fees.

Lee research/dip_system_events.csv (cada toque de soporte, sin lookahead) y:
  1. VALIDA las estrellas REFORMULADAS (support_confidence) — ¿separan el winrate? (vs viejas)
  2. SIMULA estrategias de sizing y mide expectancy NETA de fees + curva de equity / drawdown.

Outcome por evento: win=+TP, loss=-SL, none=ret_exit (cierre a FWD). Fees = round-trip (entrada+salida).
"""
import os, sys, csv, statistics as st
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dip_levels import support_confidence

TP, SL = 3.0, 1.0
CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dip_system_events.csv")


def load():
    rows = []
    for r in csv.DictReader(open(CSV)):
        sh = r["spy_healthy"]
        spy_healthy = None if sh == "" else bool(int(sh))
        stars, conf = support_confidence(
            float(r["rsi"]), float(r["dist_pct"]), spy_healthy,
            int(r["strength"]), bool(int(r["idiosyncratic"])))   # CSV.strength = confluencia vieja
        gross = TP if r["outcome"] == "win" else (-SL if r["outcome"] == "loss" else float(r["ret_exit"]))
        rows.append({**r, "spy_healthy": spy_healthy, "new_stars": stars, "conf": conf,
                     "gross": gross, "win": r["outcome"] == "win"})
    return rows


def wr(evs):
    dec = [e for e in evs if e["outcome"] in ("win", "loss")]
    return round(100 * sum(e["win"] for e in dec) / len(dec)) if dec else None


def main():
    rows = load()
    print(f"Eventos: {len(rows)}  (TP={TP} SL={SL})\n")

    # 1) VALIDACIÓN estrellas nuevas vs viejas
    print("=== VALIDACIÓN: estrellas NUEVAS (RSI+dist+régimen) ===")
    print(f"  {'estrellas':10s} {'n':>6s} {'winrate':>8s} {'gross/trade':>12s}")
    byn = defaultdict(list)
    for e in rows: byn[e["new_stars"]].append(e)
    for s in range(1, 6):
        evs = byn.get(s, [])
        if evs:
            print(f"  {s}*{'':7s} {len(evs):>6d} {str(wr(evs))+'%':>8s} "
                  f"{round(st.mean([x['gross'] for x in evs]),3):>11}%")
    print("  (recordatorio estrellas VIEJAS: 31/34/33/34/35% — planas)\n")

    # 2) SIZING: peso por confianza. Tiers simples por estrellas nuevas.
    WEIGHT = {5: 1.0, 4: 0.6, 3: 0.3, 2: 0.0, 1: 0.0}   # 1-2★ = no operar

    RISK = 1.0   # arriesga 1% de capital base por unidad de peso (peso 1.0 => -1% si toca SL)

    def sim(name, rows_sub, fee):
        """expectancy ponderada (neta de fee) + P&L ADITIVO sobre base fija (robusto a solapamiento).
        Por trade: retorno_capital = peso * RISK * (gross - fee) / SL  (en % del capital base)."""
        deployed = [(e, WEIGHT[e["new_stars"]]) for e in rows_sub if WEIGHT[e["new_stars"]] > 0]
        if not deployed:
            print(f"  {name:36s} sin trades"); return
        sumw = sum(w for _, w in deployed)
        exp_w = sum(w * (e["gross"] - fee) for e, w in deployed) / sumw       # %/trade ponderado, neto
        winr = wr([e for e, _ in deployed])
        # curva aditiva ordenada por fecha
        cum = 0.0; peak = 0.0; maxdd = 0.0
        for e, w in sorted(deployed, key=lambda x: x[0]["asof"]):
            cum += w * RISK * (e["gross"] - fee) / SL
            peak = max(peak, cum); maxdd = max(maxdd, peak - cum)
        print(f"  {name:36s} n={len(deployed):>5d} win={str(winr)+'%':>5}  "
              f"exp_neta={exp_w:+.3f}%/trade  P&L_total={cum:+7.0f}%cap  maxDD={maxdd:4.0f}%")

    print("=== SIZING (peso 5*=1.0 / 4*=0.6 / 3*=0.3 / 1-2*=NO; riesgo 1%cap/unidad) ===")
    print("  (P&L_total = suma de % de capital ganados sobre base fija; no compone)")
    for fee, lbl in [(0.0, "fee 0 (senal pura)"), (0.10, "fee 0.10% Binance perp"), (0.50, "fee 0.50% eToro")]:
        print(f"\n  -- {lbl} --")
        sim("TODOS los regimenes", rows, fee)
        sim("SOLO regimen SPY sano", [e for e in rows if e["spy_healthy"] is True], fee)
        sim("SOLO 5* (alta confianza)", [e for e in rows if e["new_stars"] == 5], fee)
        sim("5* + regimen sano", [e for e in rows if e["new_stars"] == 5 and e["spy_healthy"] is True], fee)


if __name__ == "__main__":
    main()
