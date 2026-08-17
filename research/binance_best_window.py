#!/usr/bin/env python3
"""
binance_best_window.py — Mejor ventana horaria POR TICKER (con guardas anti-overfitting).

Para cada ticker (semis del edge overnight) barre entrada 0-23h Lima × duración 1-12h y, por ventana:
  - bruto, funding (aprox rate_8h*D/8), fee 0.08% -> NETO; win%
  - excess drift-adjusted = bruto - baseline(hold misma duración a hora aleatoria)
  - NETO en 1ª mitad vs 2ª mitad del historial (robustez out-of-sample)
Filtra a ventanas ROBUSTAS (neto>0 en AMBAS mitades, excess>0) y rankea por la mitad más floja
(conservador). Devuelve el top por ticker + la recomendada. Hora en LIMA (UTC-5).

Uso:  python research/binance_best_window.py [TK1,TK2,...]
Local/standalone — NO se commitea ni despliega.
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass

TICKERS = ["MRVL", "SNDK", "MU", "INTC", "AMD", "TSM"]
DURS = list(range(1, 13))       # 1..12 h
FEE_RT = 0.08
LIMA_OFFSET = -5
MIN_N = 20


def lima_hour(e):
    return int(((e // 3600) + LIMA_OFFSET) % 24)


def fetch_1h(ex, ticker):
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 210 * 24 * 3600 * 1000
    out = []
    while True:
        try:
            o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception as e:
            print(f"  [!] {ticker}: {e}"); break
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.05)
    seen = {b[0] // 1000: b[4] for b in out}
    return sorted(seen.items())


def avg_funding_8h(ex, ticker):
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 210 * 24 * 3600 * 1000
    rs = []
    while True:
        try:
            fr = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
        except Exception:
            break
        if not fr:
            break
        rs += [float(x["fundingRate"]) for x in fr]
        if len(fr) < 1000:
            break
        since = fr[-1]["timestamp"] + 1
        time.sleep(0.05)
    return (sum(rs) / len(rs) * 100) if rs else 0.0


def fmt(h, D):
    return f"{h:02d}:00->{(h + D) % 24:02d}:00({D}h)"


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
    summary = []
    for tk in tickers:
        series = fetch_1h(ex, tk)
        if len(series) < 24 * 30:
            print(f"\n{tk}: historial insuficiente"); continue
        close = dict(series); epochs = [e for e, _ in series]
        mid = epochs[len(epochs) // 2]
        fund8h = avg_funding_8h(ex, tk)
        # baseline por duración (hold a hora aleatoria)
        base = {}
        for D in DURS:
            v = [(close[e + D * 3600] / close[e] - 1) * 100
                 for e in epochs if (e + D * 3600) in close and close[e]]
            base[D] = sum(v) / len(v) if v else 0.0
        # barrer ventanas
        cands = []
        for D in DURS:
            fund_cost = fund8h * (D / 8.0)            # long paga funding (aprox)
            for h in range(24):
                rs, r1, r2 = [], [], []
                for e in epochs:
                    if lima_hour(e) != h:
                        continue
                    c0 = close.get(e); c1 = close.get(e + D * 3600)
                    if not c0 or not c1:
                        continue
                    g = (c1 / c0 - 1) * 100
                    (r1 if e < mid else r2).append(g)
                    rs.append(g)
                if len(rs) < MIN_N or not r1 or not r2:
                    continue
                gross = sum(rs) / len(rs)
                net = gross - FEE_RT - fund_cost
                n1 = sum(r1) / len(r1) - FEE_RT - fund_cost
                n2 = sum(r2) / len(r2) - FEE_RT - fund_cost
                win = 100 * sum(1 for x in rs if x > 0) / len(rs)
                excess = gross - base[D]
                cands.append({"h": h, "D": D, "gross": gross, "net": net, "win": win,
                              "excess": excess, "n1": n1, "n2": n2, "n": len(rs)})
        # robustas: neto>0 ambas mitades y excess>0
        robust = [c for c in cands if c["n1"] > 0 and c["n2"] > 0 and c["excess"] > 0]
        robust.sort(key=lambda c: -min(c["n1"], c["n2"]))   # por mitad más floja (conservador)
        print(f"\n{'='*76}\n{tk}  (~{len(series)/24:.0f}d · funding {fund8h:+.4f}%/8h · {len(robust)} ventanas robustas)")
        if not robust:
            print("  sin ventana robusta (ninguna positiva neta en ambas mitades)")
            continue
        print(f"  {'ventana(Lima)':>18} {'neto':>7} {'win':>5} {'excess':>7} {'1ªmit':>7} {'2ªmit':>7} {'n':>4}")
        for c in robust[:6]:
            print(f"  {fmt(c['h'],c['D']):>18} {c['net']:>+7.2f} {c['win']:>4.0f}% {c['excess']:>+7.2f} "
                  f"{c['n1']:>+7.2f} {c['n2']:>+7.2f} {c['n']:>4}")
        b = robust[0]
        summary.append((tk, b))
        print(f"  -> RECOMENDADA: entra {b['h']:02d}:00 Lima, sale {(b['h']+b['D'])%24:02d}:00 ({b['D']}h) LONG  "
              f"neto {b['net']:+.2f}%  win {b['win']:.0f}%")
    # resumen final
    print("\n" + "=" * 76)
    print("RESUMEN — mejor ventana robusta por ticker (hora LIMA, LONG):")
    print(f"{'tk':6}{'entra':>7}{'sale':>7}{'dur':>5}{'neto':>8}{'win':>6}{'excess':>8}{'peorMitad':>10}")
    for tk, b in summary:
        print(f"{tk:6}{b['h']:>5}:00{(b['h']+b['D'])%24:>5}:00{b['D']:>4}h{b['net']:>+8.2f}{b['win']:>5.0f}%"
              f"{b['excess']:>+8.2f}{min(b['n1'],b['n2']):>+10.2f}")


if __name__ == "__main__":
    main()
