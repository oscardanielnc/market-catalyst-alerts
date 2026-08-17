#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CARTERA + APALANCAMIENTO + RIESGO DE RUINA — dip TP3/SL1 en crypto perps (FLAT).

El edge ya está validado (research/backtest_dip_crypto_oos.py). La pregunta ahora: ¿cuánto
apalancamiento aguanta sin volar la cuenta, y cuál es el sizing óptimo?

Lee dip_crypto_touches.csv (cada trade: pnl% realizado bajo TP3/SL1). Modelo HONESTO y simple:
  - sizing por FRACCIÓN FIJA: arriesgo r% del equity por trade. Con SL=1%, la fracción de notional
    es f = r/SL (= apalancamiento efectivo). equity *= (1 + f * pnl%/100).
  - se procesan los trades EN ORDEN DE FECHA, uno tras otro (simplificación: ignora solapamiento
    y por tanto la FRECUENCIA real; el foco es el perfil riesgo/ruina por nivel de apalancamiento).
  - RUINA = en algún trade 1 + f*pnl/100 <= 0 (pérdida que borra el equity → margin call).
  - Monte Carlo: barajar el orden N veces para la DISTRIBUCIÓN de drawdown y P(ruina/quiebre).
  - Fracción de KELLY a partir de la distribución de retornos por trade.

OJO: el CAGR aquí asume compounding secuencial (1 trade a la vez); con varios concurrentes la
frecuencia y el riesgo cambian. Lo robusto es el RANKING riesgo-ruina entre apalancamientos.
"""
import os, sys, csv, math, statistics as st

CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dip_crypto_touches.csv")
FEE = 0.10
SL = 1.0
DAYS = 320          # ~historia cubierta (2025-07 -> 2026-06) para anualizar
MC = 3000           # iteraciones Monte Carlo
# generador congruencial propio (Math.random no disponible/reproducibilidad)
_seed = 12345
def _rand():
    global _seed
    _seed = (1103515245 * _seed + 12345) & 0x7fffffff
    return _seed / 0x7fffffff


def load_pnls():
    rows = list(csv.DictReader(open(CSV)))
    rows.sort(key=lambda r: r["date"])
    # retorno NETO del instrumento por trade (ya incluye outcome); resta fee round-trip
    return [float(r["pnl"]) - FEE for r in rows]


def kelly(pnls):
    """Kelly para apuesta con retorno por trade r_i (en % del notional). f* maximiza E[log(1+f*r)].
    Búsqueda numérica de f en notional-fraction; convertimos a apalancamiento (f) y a riesgo% (f*SL)."""
    rs = [p / 100.0 for p in pnls]
    best_f, best_g = 0.0, -1e9
    f = 0.0
    while f <= 30.0:                       # apalancamiento 0..30x
        g = 0.0; ok = True
        for r in rs:
            v = 1 + f * r
            if v <= 1e-9:
                ok = False; break
            g += math.log(v)
        if ok and g > best_g:
            best_g, best_f = g, f
        f += 0.25
    return best_f


def sim(pnls, lev):
    """Equity secuencial con apalancamiento `lev`. Devuelve (mult_final, maxDD%, ruina_bool)."""
    eq = 1.0; peak = 1.0; maxdd = 0.0; ruin = False
    for p in pnls:
        eq *= (1 + lev * p / 100.0)
        if eq <= 0:
            ruin = True; eq = 1e-9; break
        peak = max(peak, eq); maxdd = max(maxdd, (peak - eq) / peak)
    return eq, maxdd * 100, ruin


def mc_stats(pnls, lev):
    """Monte Carlo barajando orden: P(ruina), P(DD>50%), percentiles del multiplicador final."""
    finals, dds, ruins = [], [], 0
    arr = pnls[:]
    for _ in range(MC):
        # Fisher-Yates con _rand
        for i in range(len(arr) - 1, 0, -1):
            j = int(_rand() * (i + 1))
            arr[i], arr[j] = arr[j], arr[i]
        eq, dd, ruin = sim(arr, lev)
        finals.append(eq); dds.append(dd)
        if ruin: ruins += 1
    finals.sort()
    return {
        "p_ruin": round(100 * ruins / MC, 1),
        "p_dd50": round(100 * sum(d >= 50 for d in dds) / MC, 1),
        "med_mult": finals[len(finals)//2],
        "p05_mult": finals[int(0.05*len(finals))],
        "med_dd": round(st.median(dds), 0),
    }


def main():
    pnls = load_pnls()
    n = len(pnls)
    exp = st.mean(pnls)
    wins = sum(1 for p in pnls if p > 0)
    print(f"{n} trades  exp neto={exp:+.3f}%/trade  win(pnl>0)={round(100*wins/n)}%  "
          f"peor trade={min(pnls):.1f}%  mejor={max(pnls):.1f}%\n")

    kf = kelly(pnls)
    print(f"Fraccion de KELLY (full): {kf:.2f}x apalancamiento  (= arriesgar {kf*SL:.1f}%/trade)")
    print(f"  -> recomendado HALF-KELLY: {kf/2:.2f}x  (menos varianza, casi mismo crecimiento)\n")

    print("=== BARRIDO DE APALANCAMIENTO (orden real + Monte Carlo, neto de fee) ===")
    print(f"  {'lev':>5s} {'riesgo/tr':>9s} {'mult_real':>10s} {'maxDD_real':>11s} "
          f"{'P(ruina)':>9s} {'P(DD>50%)':>10s} {'mult_med':>9s} {'mult_p05':>9s}")
    for lev in [1, 2, 3, 5, 8, 10, 15, 20]:
        mult, dd, ruin = sim(pnls, lev)
        mc = mc_stats(pnls, lev)
        mult_s = f"{mult:8.1f}x" if mult < 1e6 else f"{mult:.0e}"
        medm = f"{mc['med_mult']:7.1f}x" if mc['med_mult'] < 1e6 else f"{mc['med_mult']:.0e}"
        p05m = f"{mc['p05_mult']:7.2f}x" if mc['p05_mult'] < 1e6 else f"{mc['p05_mult']:.0e}"
        print(f"  {lev:>4d}x {lev*SL:>7.0f}% {mult_s:>10s} {dd:>9.0f}% "
              f"{str(mc['p_ruin'])+'%':>9s} {str(mc['p_dd50'])+'%':>10s} {medm:>9s} {p05m:>9s}")

    print("\n  Notas: 'riesgo/tr' = pérdida si el stop aguanta a -1%; los GAP-loss (~-3%/-5%) hacen")
    print("  la pérdida real mayor -> por eso la ruina aparece antes de lo que sugiere el SL nominal.")
    print("  El compounding secuencial INFLA el multiplicador; fiarse del RANKING riesgo, no del x.")


if __name__ == "__main__":
    main()
