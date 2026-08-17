#!/usr/bin/env python3
"""
binance_hourly_scan.py — FASE 3: ¿hay ventanas HORARIAS (24/7) rentables en perps tradfi de Binance?

Contexto (Oscar, 2026-06-24): operar estos patrones en Binance Futures perps de acciones (tradfi),
que cotizan 24/7 con comisión baja (~0.04% taker/lado). Sin apertura/cierre, la pregunta cambia a
ESTACIONALIDAD HORARIA: dado cierto horario (ej. 22:00, 04:00 Lima) y cierta duración de hold,
¿hay una ventana donde la tendencia se sostenga con amplitud > costos?

Método:
  - Barras 1h de Binance USDⓈ-M (ccxt binanceusdm, símbolo TICKER/USDT:USDT), todo el historial.
  - Para cada hora de entrada (en hora LIMA, UTC-5) y cada duración D, el retorno = close(t+D)/close(t)-1.
  - Agrega sobre todos los días: media%, win% (long), n. Una ventana con media>0 y win alto = "long
    cada día a esa hora"; media<0 con win bajo = edge SHORT.
  - Anti-overfitting (lección Fase 2): además mide la 1ª mitad vs 2ª mitad del historial. Solo nos
    fiamos de ventanas que funcionan en AMBAS mitades (mismo signo, amplitud > costo en las dos).

Costo: round-trip ~0.10% (taker 0.04%×2 + slippage). Neto = |media| - 0.10.

Uso:  python research/binance_hourly_scan.py [TK1,TK2,...] [min_dias]
Default: los 20 tickers con >=30d. Salida: research/binance_hourly_scan_results.csv + consola.
Local/standalone — NO se commitea ni despliega.
"""
import os
import sys
import csv
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CSV_OUT = os.path.join(_HERE, "binance_hourly_scan_results.csv")

DEFAULT = ["BB", "TSLA", "HOOD", "INTC", "MSTR", "PLTR", "NVDA", "TSM", "MU", "SNDK",
           "AVGO", "AMD", "LITE", "MRVL", "RKLB", "ARM", "BE", "COHR", "NBIS", "WDC"]
DURS = [1, 2, 3, 4, 6, 8, 12]       # horas de hold candidatas
FEE_RT = 0.10                        # % round-trip estimado (Binance perp)
MIN_N = 20                           # mínimo de muestras por ventana
LIMA_OFFSET = -5                     # Lima = UTC-5 (sin DST)


def fetch_1h(ex, ticker):
    """Todas las barras 1h disponibles. Lista (epoch_sec, close)."""
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 210 * 24 * 3600 * 1000
    out = []
    while True:
        try:
            o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception as e:
            print(f"    [!] {ticker}: {e}"); break
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.05)
    # dedup por timestamp, ordenado
    seen = {}
    for b in out:
        seen[b[0] // 1000] = b[4]   # epoch_sec -> close
    return sorted(seen.items())


def lima_hour(epoch_sec):
    return int(((epoch_sec // 3600) + LIMA_OFFSET) % 24)


def scan_ticker(ticker, series):
    """Devuelve lista de buckets {hour,dur,n,mean,win,h1,h2} por (hora_lima_entrada, duración)."""
    close = dict(series)                       # epoch_sec -> close
    epochs = [e for e, _ in series]
    if len(epochs) < 24 * 30:
        return []
    mid = epochs[len(epochs) // 2]             # frontera 1ª/2ª mitad
    from collections import defaultdict
    acc = defaultdict(lambda: {"r": [], "r1": [], "r2": []})
    for e in epochs:
        c0 = close[e]
        if not c0:
            continue
        h = lima_hour(e)
        for D in DURS:
            ex_e = e + D * 3600
            c1 = close.get(ex_e)
            if c1 is None or not c1:
                continue
            r = (c1 / c0 - 1) * 100
            b = acc[(h, D)]
            b["r"].append(r)
            (b["r1"] if e < mid else b["r2"]).append(r)
    out = []
    for (h, D), b in acc.items():
        rs = b["r"]
        if len(rs) < MIN_N:
            continue
        mean = sum(rs) / len(rs)
        win = 100 * sum(1 for r in rs if r > 0) / len(rs)
        m1 = sum(b["r1"]) / len(b["r1"]) if b["r1"] else 0.0
        m2 = sum(b["r2"]) / len(b["r2"]) if b["r2"] else 0.0
        out.append({"ticker": ticker, "hour": h, "dur": D, "n": len(rs),
                    "mean": round(mean, 3), "win": round(win), "h1": round(m1, 3), "h2": round(m2, 3)})
    return out


def robust_score(b):
    """Ventana robusta = funciona en AMBAS mitades, mismo signo, y neta de costos en las dos."""
    s = 1 if b["mean"] >= 0 else -1
    if (b["h1"] >= 0) != (s >= 0) or (b["h2"] >= 0) != (s >= 0):
        return -9     # cambia de signo entre mitades -> no robusto
    weak = min(abs(b["h1"]), abs(b["h2"]))    # la mitad más floja
    net_weak = weak - FEE_RT                  # ¿la mitad floja aún supera costos?
    return net_weak


def fmt_h(h, D):
    return f"{h:02d}:00->{(h + D) % 24:02d}:00 ({D}h)"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import ccxt
    args = sys.argv[1:]
    tickers = DEFAULT
    if args and ("," in args[0] or not args[0].isdigit()):
        tickers = [t.strip().upper() for t in args[0].split(",") if t.strip()]
    print(f"BINANCE HOURLY SCAN — {len(tickers)} tickers · duraciones {DURS}h · fee {FEE_RT}% · hora LIMA")
    ex = ccxt.binanceusdm({"enableRateLimit": True}); ex.load_markets()
    all_rows = []
    per_best = []
    for i, tk in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {tk} ...", end=" ")
        series = fetch_1h(ex, tk)
        days = len(series) / 24 if series else 0
        buckets = scan_ticker(tk, series)
        print(f"{len(series)} barras (~{days:.0f}d), {len(buckets)} ventanas")
        if not buckets:
            continue
        for b in buckets:
            b["robust_net"] = round(robust_score(b), 3)
        all_rows.extend(buckets)
        # mejor ventana ROBUSTA del ticker
        rob = [b for b in buckets if b["robust_net"] > 0]
        rob.sort(key=lambda b: -b["robust_net"])
        if rob:
            best = rob[0]
            per_best.append(best)
            side = "LONG" if best["mean"] >= 0 else "SHORT"
            print(f"      mejor robusta: {fmt_h(best['hour'],best['dur'])} {side}  "
                  f"media {best['mean']:+.2f}%  win {best['win']}%  (h1 {best['h1']:+.2f} / h2 {best['h2']:+.2f})  n{best['n']}")
        else:
            print("      sin ventana robusta (ninguna funciona en ambas mitades neta de costos)")
    # CSV completo
    cols = ["ticker", "hour", "dur", "n", "mean", "win", "h1", "h2", "robust_net"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(all_rows, key=lambda r: -r["robust_net"]):
            w.writerow(r)
    # leaderboard global de ventanas robustas
    rob_all = [b for b in all_rows if b["robust_net"] > 0]
    rob_all.sort(key=lambda b: -b["robust_net"])
    print("\n" + "=" * 90)
    print(f"LEADERBOARD GLOBAL — ventanas ROBUSTAS (positivas netas en AMBAS mitades), hora LIMA")
    print(f"{'tk':6}{'ventana':>20}{'lado':>6}{'media%':>8}{'win%':>6}{'h1':>7}{'h2':>7}{'netoDébil':>10}{'n':>5}")
    for b in rob_all[:25]:
        side = "LONG" if b["mean"] >= 0 else "SHORT"
        print(f"{b['ticker']:6}{fmt_h(b['hour'],b['dur']):>20}{side:>6}{b['mean']:>+8.2f}{b['win']:>6}"
              f"{b['h1']:>+7.2f}{b['h2']:>+7.2f}{b['robust_net']:>+10.2f}{b['n']:>5}")
    if not rob_all:
        print("  (NINGUNA ventana robusta en toda la canasta — el patrón horario no sobrevive al split)")
    print(f"\nCSV -> {CSV_OUT}")


if __name__ == "__main__":
    main()
