#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIMULACIÓN DE CARTERA CONSCIENTE DEL CALENDARIO — dip TP3/SL1 crypto perps.

Modelo realista (event-driven sobre fechas reales de entrada/salida de cada trade):
  - Capital con apalancamiento L (exposición bruta máx) y tope de C posiciones CONCURRENTES.
  - Cada posición abierta usa notional = (L/C)*equity_actual (al llenar C slots, exposición = L·equity).
  - Si una señal dispara y no hay slot libre -> se SALTA (realista: no puedes tomarlas todas).
  - Al salir: equity += notional*(pnl - fee)/100. Fee round-trip sobre notional.
  - Métricas REALES: CAGR, maxDD, Sharpe (diario), racha perdedora, fill-rate, ruina(equity<=0).

Variantes: L ∈ {1,1.6(half-Kelly),2,3} × C ∈ {5,10,20,∞}. Lee dip_crypto_touches.csv.
Caveats: asume fills a los precios del modelo 1h (sin slippage extra), que puedes colocar todas las
órdenes límite, y una sola ventana de 550d. El RANKING entre configs es más fiable que el x absoluto.
"""
import os, sys, csv, math, statistics as st

CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dip_crypto_touches.csv")
FEE = 0.10
HALF_KELLY = 1.6


def load():
    tr = []
    for r in csv.DictReader(open(CSV)):
        tr.append({"tk": r["tk"], "pnl": float(r["pnl"]),
                   "entry": int(r["entry_ts"]), "exit": int(r["exit_ts"])})
    # exit >= entry; si dur 0, empuja exit 1ms para ordenar open antes que close
    for t in tr:
        if t["exit"] <= t["entry"]:
            t["exit"] = t["entry"] + 1
    return tr


def simulate(trades, L, C, one_per_coin=False, slip=0.0):
    """Event-driven. C=None => sin tope. one_per_coin: máx 1 posición por moneda. slip: coste extra %."""
    events = []
    for i, t in enumerate(trades):
        events.append((t["entry"], 0, i))   # 0 = open primero
        events.append((t["exit"], 1, i))
    events.sort(key=lambda e: (e[0], e[1]))

    equity = 1.0; peak = 1.0; maxdd = 0.0
    open_pos = {}; open_coins = {}; taken = 0; skipped = 0; ruin = False
    eq_by_day = {}
    cap = C if C is not None else 10**9
    notional_div = C if C is not None else 20

    for ts, typ, i in events:
        coin = trades[i]["tk"]
        if typ == 0:
            blocked = one_per_coin and open_coins.get(coin, 0) > 0
            if len(open_pos) < cap and not blocked:
                open_pos[i] = (L / notional_div) * equity
                open_coins[coin] = open_coins.get(coin, 0) + 1
                taken += 1
            else:
                skipped += 1
        else:
            if i in open_pos:
                notional = open_pos.pop(i)
                open_coins[coin] -= 1
                pnl_net = trades[i]["pnl"] - FEE - slip
                equity += notional * pnl_net / 100.0
                if equity <= 0:
                    ruin = True; equity = 1e-9
                peak = max(peak, equity)
                maxdd = max(maxdd, (peak - equity) / peak)
                eq_by_day[ts // 86400000] = equity
        if ruin:
            break

    days_span = (events[-1][0] - events[0][0]) / 86400000.0
    cagr = (equity ** (365.0 / days_span) - 1) * 100 if days_span > 0 and equity > 0 else -100
    # Sharpe sobre retornos diarios del equity
    days = sorted(eq_by_day)
    drets = []
    for k in range(1, len(days)):
        prev, cur = eq_by_day[days[k-1]], eq_by_day[days[k]]
        if prev > 0:
            drets.append(cur / prev - 1)
    sharpe = (st.mean(drets) / st.pstdev(drets) * math.sqrt(365)) if len(drets) > 2 and st.pstdev(drets) > 0 else 0
    # racha perdedora (trades cerrados consecutivos con pnl neto < 0)
    streak = mx = 0
    for t in sorted(trades, key=lambda x: x["exit"]):
        if (t["pnl"] - FEE) < 0: streak += 1; mx = max(mx, streak)
        else: streak = 0
    return {"L": L, "C": C, "equity": equity, "cagr": cagr, "maxdd": maxdd * 100,
            "sharpe": sharpe, "taken": taken, "skipped": skipped,
            "fill": round(100 * taken / (taken + skipped)) if (taken+skipped) else 0,
            "lose_streak": mx, "ruin": ruin}


def main():
    trades = load()
    n = len(trades)
    durs = [(t["exit"] - t["entry"]) / 3600000 for t in trades]
    span = (max(t["exit"] for t in trades) - min(t["entry"] for t in trades)) / 86400000
    print(f"{n} trades, ventana {span:.0f} días (~{n/span:.0f} señales/día), "
          f"duración media {st.mean(durs):.0f}h (mediana {st.median(durs):.0f}h)\n")

    print("=== BARRIDO L (apalancamiento) × C (posiciones concurrentes) — fee 0.10% ===")
    print(f"  {'L':>5s} {'C':>4s} {'CAGR':>8s} {'maxDD':>7s} {'Sharpe':>7s} "
          f"{'fill%':>6s} {'rachaL':>7s} {'equity×':>9s} {'ruina':>6s}")
    rows = []
    for L in [1, HALF_KELLY, 2, 3]:
        for C in [5, 10, 20, None]:
            r = simulate(trades, L, C)
            rows.append(r)
            eqs = f"{r['equity']:7.1f}x" if r['equity'] < 1e6 else f"{r['equity']:.0e}"
            cstr = "inf" if C is None else str(C)
            print(f"  {L:>4.1f}x {cstr:>4s} {r['cagr']:>7.0f}% {r['maxdd']:>6.0f}% "
                  f"{r['sharpe']:>7.2f} {str(r['fill'])+'%':>6s} {r['lose_streak']:>7d} "
                  f"{eqs:>9s} {'SI' if r['ruin'] else 'no':>6s}")

    print("\n=== MODO HONESTO: 1 posición/moneda + slippage realista (descuenta la fantasía) ===")
    print(f"  {'config':34s} {'CAGR':>8s} {'maxDD':>7s} {'Sharpe':>7s} {'fill%':>6s} {'equity×':>9s}")
    for L, C, opc, slip, lbl in [
        (HALF_KELLY, 10, False, 0.0,  "half-Kelly 1.6x C=10 (ingenuo)"),
        (HALF_KELLY, 10, True,  0.0,  "+ 1 pos/moneda"),
        (HALF_KELLY, 10, True,  0.15, "+ 1 pos/moneda + slip 0.15%"),
        (HALF_KELLY, 10, True,  0.30, "+ 1 pos/moneda + slip 0.30%"),
        (1.0,        10, True,  0.30, "SIN apalancar 1x + slip 0.30%"),
    ]:
        r = simulate(trades, L, C, one_per_coin=opc, slip=slip)
        eqs = f"{r['equity']:7.1f}x" if r['equity'] < 1e6 else f"{r['equity']:.0e}"
        print(f"  {lbl:34s} {r['cagr']:>7.0f}% {r['maxdd']:>6.0f}% {r['sharpe']:>7.2f} "
              f"{str(r['fill'])+'%':>6s} {eqs:>9s}")
    print("\n  Aun con estos descuentos, el Sharpe sigue ALTO porque la correlación de crypto (todo")
    print("  sigue a BTC) hace que '~N monedas' sean en realidad ~2-4 apuestas independientes. El")
    print("  Sharpe REAL hay que dividirlo por ~sqrt(monedas_efectivas). Trátalo como SOSPECHOSO.")


if __name__ == "__main__":
    main()
