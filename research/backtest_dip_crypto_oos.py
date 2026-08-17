#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Walk-forward / OUT-OF-SAMPLE del sistema dip TP3/SL1 en crypto perps.

Lee research/dip_crypto_touches.csv (toques con resultado TP3/SL1) y valida que el +0.245%/trade
NO sea artefacto de una ventana/moneda:
  A. Split temporal 50/50 (in-sample vs OOS).
  B. Walk-forward: 5 bloques temporales secuenciales (estabilidad en el tiempo).
  C. Cross-sectional: mitad de monedas (entrena) vs la otra mitad (held-out).
  D. Por moneda: ¿concentrado en pocas o repartido?

Sizing actual (peso 5*=1.0/4*=0.6/3*=0.3/1-2*=0). OJO: en crypto las estrellas van INVERTIDAS
(ver backtest_dip_crypto). Por eso reportamos también FLAT (peso 1 a todo) que es lo honesto aquí.
"""
import os, sys, csv, statistics as st
from collections import defaultdict

CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dip_crypto_touches.csv")
FEE = 0.10
TP, SL = 3.0, 1.0
WEIGHT = {5: 1.0, 4: 0.6, 3: 0.3, 2: 0.0, 1: 0.0}


def load():
    rows = []
    for r in csv.DictReader(open(CSV)):
        rows.append({"tk": r["tk"], "date": r["date"], "stars": int(r["stars"]),
                     "spy_h": r["spy_h"], "out": r["out"], "pnl": float(r["pnl"])})
    return rows


def metrics(evs, weighted=True):
    if weighted:
        dep = [(e, WEIGHT[e["stars"]]) for e in evs if WEIGHT[e["stars"]] > 0]
    else:
        dep = [(e, 1.0) for e in evs]
    if not dep:
        return None
    sumw = sum(w for _, w in dep)
    exp = sum(w * (e["pnl"] - FEE) for e, w in dep) / sumw
    wins = sum(1 for e, _ in dep if e["out"] in ("win", "win_gap"))
    loss = sum(1 for e, _ in dep if e["out"] in ("loss", "loss_gap"))
    wr = round(100 * wins / (wins + loss)) if (wins + loss) else None
    return {"n": len(dep), "exp": round(exp, 3), "wr": wr}


def line(label, m):
    if not m:
        print(f"  {label:30s} sin trades"); return
    print(f"  {label:30s} n={m['n']:>5d} win={str(m['wr'])+'%':>5} exp={m['exp']:+.3f}%/tr")


def main():
    rows = load()
    rows.sort(key=lambda r: r["date"])
    dmin, dmax = rows[0]["date"], rows[-1]["date"]
    print(f"{len(rows)} toques  ({dmin} -> {dmax})\n")

    print("=== GLOBAL ===")
    line("FLAT (peso 1 a todo)", metrics(rows, weighted=False))
    line("WEIGHTED (sizing por estrellas)", metrics(rows, weighted=True))

    # A. Split temporal 50/50
    mid = rows[len(rows) // 2]["date"]
    print(f"\n=== A. SPLIT TEMPORAL (corte {mid}) — FLAT ===")
    line("IN-SAMPLE (1a mitad)", metrics([r for r in rows if r["date"] < mid], weighted=False))
    line("OUT-OF-SAMPLE (2a mitad)", metrics([r for r in rows if r["date"] >= mid], weighted=False))

    # B. Walk-forward 5 bloques
    print("\n=== B. WALK-FORWARD (5 bloques temporales, FLAT) ===")
    k = 5
    n = len(rows)
    for b in range(k):
        seg = rows[b * n // k:(b + 1) * n // k]
        lo, hi = seg[0]["date"], seg[-1]["date"]
        line(f"bloque {b+1} ({lo}..{hi})", metrics(seg, weighted=False))

    # C. Cross-sectional por moneda (alternancia para mezclar liquidez/orden)
    coins = sorted(set(r["tk"] for r in rows))
    grpA = set(coins[::2]); grpB = set(coins[1::2])
    print(f"\n=== C. CROSS-SECTIONAL (held-out de monedas, FLAT) ===")
    line(f"grupo A ({len(grpA)} monedas)", metrics([r for r in rows if r["tk"] in grpA], weighted=False))
    line(f"grupo B ({len(grpB)} monedas)", metrics([r for r in rows if r["tk"] in grpB], weighted=False))

    # D. Por moneda — concentración
    print("\n=== D. POR MONEDA (FLAT, ordenado por exp) ===")
    per = []
    for c in coins:
        m = metrics([r for r in rows if r["tk"] == c], weighted=False)
        if m: per.append((c, m))
    per.sort(key=lambda x: x[1]["exp"], reverse=True)
    pos = sum(1 for _, m in per if m["exp"] > 0)
    for c, m in per:
        print(f"  {c:6s} n={m['n']:>4d} win={str(m['wr'])+'%':>5} exp={m['exp']:+.3f}%/tr")
    print(f"\n  -> {pos}/{len(per)} monedas con expectancy POSITIVA "
          f"(robustez transversal: {'ALTA' if pos/len(per) >= 0.7 else 'MEDIA' if pos/len(per) >= 0.5 else 'BAJA'})")


if __name__ == "__main__":
    main()
