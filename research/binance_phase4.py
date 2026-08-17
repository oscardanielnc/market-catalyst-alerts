#!/usr/bin/env python3
"""
binance_phase4.py — FASE 4: ¿el patrón horario overnight es EDGE real o solo drift alcista?

Re-test a fondo de SNDK, MU, MRVL, AMD sobre la ventana overnight-LONG (00:00->12:00 Lima) que
salió en FASE 3. Cuatro pruebas que separan "edge horario" de "estuve long en un semi que subía":

  1. DRIFT-ADJUSTED: excess(h,D) = retorno medio de entrar a la hora h por D horas
     MENOS el retorno medio de un hold de D horas entrando a hora ALEATORIA (pool de todas las horas).
     Si excess ~ 0 -> la ventana solo captura la deriva del activo (no es efecto-hora).
  2. FUNDING real: se baja el historial de funding de Binance (cada 8h) y se RESTA el costo de los
     settlements cruzados por el hold (un long paga funding positivo). Rompe el supuesto "no funding"
     de Oscar para holds de 8-12h.
  3. RÉGIMEN: net partido por si el activo venía SUBIENDO (retorno 24h previo >=0) o BAJANDO.
  4. OOS: split train/test 60/40 sobre la ventana objetivo.

Costo round-trip Binance: 0.08% taker (0.04%×2). Salida: research/binance_phase4_results.csv + consola.
Local/standalone — NO se commitea ni despliega.
"""
import os
import sys
import csv
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_OUT = os.path.join(_HERE, "binance_phase4_results.csv")

TICKERS = ["SNDK", "MU", "MRVL", "AMD"]
TARGET_HOUR_LIMA = 0          # 00:00 Lima
TARGET_DUR = 12               # 12h
DURS = [2, 3, 4, 6, 8, 12]
FEE_RT = 0.08                 # % round-trip taker
LIMA_OFFSET = -5


def lima_hour(epoch_sec):
    return int(((epoch_sec // 3600) + LIMA_OFFSET) % 24)


def fetch_1h(ex, ticker):
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 210 * 24 * 3600 * 1000
    out = []
    while True:
        try:
            o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception as e:
            print(f"    [!] {ticker} ohlcv: {e}"); break
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.05)
    seen = {}
    for b in out:
        seen[b[0] // 1000] = (b[4], b[5])   # close, volume(base)
    return [(e, cv[0], cv[1]) for e, cv in sorted(seen.items())]


def fetch_funding(ex, ticker):
    """Lista (epoch_sec, rate) del historial de funding (cada 8h)."""
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 210 * 24 * 3600 * 1000
    out = []
    while True:
        try:
            fr = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
        except Exception as e:
            print(f"    [!] {ticker} funding: {e}"); break
        if not fr:
            break
        for x in fr:
            out.append((x["timestamp"] // 1000, float(x["fundingRate"])))
        if len(fr) < 1000:
            break
        since = fr[-1]["timestamp"] + 1
        time.sleep(0.05)
    return sorted(set(out))


def funding_cost_pct(funding, entry_e, exit_e):
    """Costo de funding (%) que paga un LONG entre entry y exit (suma de rates de settlements cruzados)."""
    return sum(r for ts, r in funding if entry_e < ts <= exit_e) * 100


def window_trades(close, epochs, funding, hour, dur, side=1):
    """Retornos por día para entrar a 'hour' Lima por 'dur' h. side=+1 long/-1 short.
       Devuelve lista de dicts {gross, fund, net, entry_e, prev24}."""
    out = []
    for e in epochs:
        if lima_hour(e) != hour:
            continue
        c0 = close.get(e); c1 = close.get(e + dur * 3600)
        if not c0 or not c1:
            continue
        gross = side * (c1 / c0 - 1) * 100
        fund = side * funding_cost_pct(funding, e, e + dur * 3600)   # long paga rate+, short recibe
        net = gross - fund - FEE_RT
        cprev = close.get(e - 24 * 3600)
        prev24 = (c0 / cprev - 1) if cprev else None
        out.append({"gross": gross, "fund": fund, "net": net, "entry_e": e, "prev24": prev24})
    return out


def agg(rets, key="net"):
    v = [r[key] for r in rets]
    if not v:
        return None
    n = len(v); mean = sum(v) / n
    win = 100 * sum(1 for x in v if x > 0) / n
    return {"n": n, "mean": round(mean, 3), "win": round(win)}


def baseline_dur(close, epochs, dur):
    """Retorno BRUTO medio de un hold de 'dur' h entrando a CUALQUIER hora (pool) -> baseline drift."""
    v = []
    for e in epochs:
        c0 = close.get(e); c1 = close.get(e + dur * 3600)
        if c0 and c1:
            v.append((c1 / c0 - 1) * 100)
    return sum(v) / len(v) if v else 0.0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import ccxt
    tickers = TICKERS
    if len(sys.argv) > 1 and sys.argv[1].strip():
        tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()]
    ex = ccxt.binanceusdm({"enableRateLimit": True}); ex.load_markets()
    rows = []
    for tk in tickers:
        print(f"\n{'='*78}\n{tk}")
        series = fetch_1h(ex, tk)
        funding = fetch_funding(ex, tk)
        if len(series) < 24 * 30:
            print("  historial insuficiente"); continue
        close = {e: c for e, c, v in series}
        vol = {e: v for e, c, v in series}
        epochs = [e for e, c, v in series]
        # liquidez: volumen USDT medio del bar de 1h a la hora de entrada (00:00 Lima)
        liq = [vol[e] * close[e] for e in epochs if lima_hour(e) == TARGET_HOUR_LIMA and close[e]]
        liq_usdt = (sum(liq) / len(liq)) if liq else 0.0
        avg_fund_8h = (sum(r for _, r in funding) / len(funding) * 100) if funding else 0.0
        print(f"  {len(series)} barras (~{len(series)/24:.0f}d) · funding medio/8h: {avg_fund_8h:+.4f}% · {len(funding)} settlements")
        print(f"  liquidez @00:00 Lima: ~${liq_usdt/1e6:.2f}M USDT/h de volumen (proxy de profundidad)")

        # --- ventana OBJETIVO 00:00 Lima x 12h ---
        tr = window_trades(close, epochs, funding, TARGET_HOUR_LIMA, TARGET_DUR, side=1)
        a_net = agg(tr, "net"); a_gross = agg(tr, "gross")
        mean_fund = sum(r["fund"] for r in tr) / len(tr) if tr else 0.0
        base12 = baseline_dur(close, epochs, TARGET_DUR)
        excess = (a_gross["mean"] - base12) if a_gross else 0.0
        print(f"  OBJETIVO 00:00->12:00 Lima LONG:")
        print(f"    bruto {a_gross['mean']:+.2f}%  funding -{mean_fund:.2f}%  fee -{FEE_RT}%  =>  NETO {a_net['mean']:+.2f}%  win {a_net['win']}%  n{a_net['n']}")
        print(f"    baseline (hold 12h hora aleatoria) bruto {base12:+.2f}%  ->  EXCESS por-hora {excess:+.2f}%  "
              f"{'(hay efecto-hora)' if excess>0.05 else '(SOLO drift, no efecto-hora)'}")

        # --- OOS sobre la objetivo ---
        cut = len(tr) // 2 if tr else 0
        tr_sorted = sorted(tr, key=lambda r: r["entry_e"])
        oos_tr, oos_te = agg(tr_sorted[:cut]), agg(tr_sorted[cut:])
        if oos_tr and oos_te:
            print(f"    OOS neto: 1ª mitad {oos_tr['mean']:+.2f}% (win{oos_tr['win']}) | 2ª mitad {oos_te['mean']:+.2f}% (win{oos_te['win']})")

        # --- régimen: activo venía subiendo vs bajando (prev24) ---
        up = agg([r for r in tr if r["prev24"] is not None and r["prev24"] >= 0], "net")
        dn = agg([r for r in tr if r["prev24"] is not None and r["prev24"] < 0], "net")
        print(f"    régimen (prev 24h): SUBIENDO {up['mean']:+.2f}%(n{up['n']},win{up['win']})  |  "
              f"BAJANDO {dn['mean']:+.2f}%(n{dn['n']},win{dn['win']})" if up and dn else "    régimen: muestra insuficiente")

        # --- mejor ventana DRIFT-ADJUSTED de todo el grid (¿alguna hora gana sobre su baseline?) ---
        best = None
        for D in DURS:
            bD = baseline_dur(close, epochs, D)
            for h in range(24):
                wt = window_trades(close, epochs, funding, h, D, side=1)
                if len(wt) < 20:
                    continue
                g = sum(r["gross"] for r in wt) / len(wt)
                exc = g - bD
                nt = sum(r["net"] for r in wt) / len(wt)
                if best is None or exc > best["exc"]:
                    best = {"h": h, "D": D, "exc": round(exc, 3), "net": round(nt, 3),
                            "gross": round(g, 3), "base": round(bD, 3), "n": len(wt)}
        if best:
            print(f"    MEJOR drift-adjusted del grid: {best['h']:02d}:00->{(best['h']+best['D'])%24:02d}:00 ({best['D']}h)  "
                  f"excess {best['exc']:+.2f}%  (bruto {best['gross']:+.2f} vs base {best['base']:+.2f})  neto {best['net']:+.2f}%  n{best['n']}")
        rows.append({"ticker": tk, "obj_gross": a_gross["mean"], "obj_funding": round(mean_fund, 3),
                     "obj_net": a_net["mean"], "obj_win": a_net["win"], "obj_excess_hour": round(excess, 3),
                     "oos_h1": oos_tr["mean"] if oos_tr else None, "oos_h2": oos_te["mean"] if oos_te else None,
                     "reg_up": up["mean"] if up else None, "reg_down": dn["mean"] if dn else None,
                     "best_excess_win": f"{best['h']:02d}:00/{best['D']}h" if best else None,
                     "best_excess": best["exc"] if best else None, "best_excess_net": best["net"] if best else None,
                     "avg_fund_8h": round(avg_fund_8h, 4), "liq_usdt_M": round(liq_usdt / 1e6, 2)})

    cols = ["ticker", "obj_gross", "obj_funding", "obj_net", "obj_win", "obj_excess_hour",
            "oos_h1", "oos_h2", "reg_up", "reg_down", "best_excess_win", "best_excess", "best_excess_net",
            "avg_fund_8h", "liq_usdt_M"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows:
            w.writerow(r)
    print("\n" + "=" * 78)
    print("RESUMEN FASE 4 (neto = bruto - funding - fee):")
    print(f"{'tk':6}{'objNeto':>9}{'win':>5}{'excHora':>9}{'oos1':>7}{'oos2':>7}{'regSube':>9}{'regBaja':>9}{'liq$M':>8}")
    for r in rows:
        print(f"{r['ticker']:6}{r['obj_net']:>+9.2f}{r['obj_win']:>5}{r['obj_excess_hour']:>+9.2f}"
              f"{(r['oos_h1'] or 0):>+7.2f}{(r['oos_h2'] or 0):>+7.2f}{(r['reg_up'] or 0):>+9.2f}{(r['reg_down'] or 0):>+9.2f}{r['liq_usdt_M']:>8.2f}")
    print(f"\nCSV -> {CSV_OUT}")
    print("Clave: excHora>0 = hay efecto-hora real (no solo drift); regBaja>0 = sobrevive cuando el activo no venía subiendo.")


if __name__ == "__main__":
    main()
