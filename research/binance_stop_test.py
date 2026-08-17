#!/usr/bin/env python3
"""binance_stop_test.py — ¿un stop -3%/-4% mejora el neto de cada ventana overnight?
Usa high/low de las barras 1h dentro de la ventana para saber si el stop se habría gatillado.
Local/standalone. NO commitear."""
import os, sys, time
_HERE = os.path.dirname(os.path.abspath(__file__)); _ROOT = os.path.dirname(_HERE)
try:
    from dotenv import load_dotenv; load_dotenv(os.path.join(_ROOT, ".env"))
except Exception: pass
FEE_RT = 0.08; LIMA = -5
WIN = {"SNDK": (0, 12), "MU": (0, 12), "MRVL": (21, 9), "INTC": (7, 7), "AMD": (23, 10), "TSM": (16, 12)}
STOPS = [3.0, 4.0]


def lh(e): return int(((e // 3600) + LIMA) % 24)


def fetch_ohlc(ex, tk):
    sym = f"{tk}/USDT:USDT"; since = ex.milliseconds() - 210 * 24 * 3600 * 1000; out = []
    while True:
        try: o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception: break
        if not o: break
        out.extend(o)
        if len(o) < 1000: break
        since = o[-1][0] + 3600 * 1000; time.sleep(0.05)
    d = {b[0] // 1000: (b[1], b[2], b[3], b[4]) for b in out}   # o,h,l,c
    return d, sorted(d.keys())


def avg_fund(ex, tk):
    sym = f"{tk}/USDT:USDT"; since = ex.milliseconds() - 210 * 24 * 3600 * 1000; rs = []
    while True:
        try: fr = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
        except Exception: break
        if not fr: break
        rs += [float(x["fundingRate"]) for x in fr]
        if len(fr) < 1000: break
        since = fr[-1]["timestamp"] + 1; time.sleep(0.05)
    return (sum(rs) / len(rs) * 100) if rs else 0.0


def mean(v): return sum(v) / len(v) if v else 0.0


def main():
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    import ccxt
    ex = ccxt.binanceusdm({"enableRateLimit": True}); ex.load_markets()
    print(f"STOP TEST · fee {FEE_RT}% · stops {STOPS}\n{'tk':6}{'sinStop':>9}{'win':>5}", end="")
    for s in STOPS: print(f"{'stop'+str(int(s)):>9}{'win':>5}{'%gat':>6}", end="")
    print()
    for tk, (h, D) in WIN.items():
        close, epochs = fetch_ohlc(ex, tk)
        if len(epochs) < 24 * 30: print(f"{tk}: insuf"); continue
        cost = FEE_RT + avg_fund(ex, tk) * (D / 8.0)
        no, byS = [], {s: [] for s in STOPS}; trig = {s: 0 for s in STOPS}; nt = 0
        for e in epochs:
            if lh(e) != h: continue
            if e not in close: continue
            entry = close[e][3]            # close de la barra de entrada
            ex_e = e + D * 3600
            if ex_e not in close: continue
            lowmin = min((close[t][2] for t in range(e + 3600, ex_e + 1, 3600) if t in close), default=entry)
            ret_close = (close[ex_e][3] / entry - 1) * 100
            no.append(ret_close - cost); nt += 1
            for s in STOPS:
                if (lowmin / entry - 1) * 100 <= -s:
                    byS[s].append(-s - cost); trig[s] += 1
                else:
                    byS[s].append(ret_close - cost)
        if not nt: continue
        winp = lambda v: 100 * sum(1 for x in v if x > 0) / len(v)
        print(f"{tk:6}{mean(no):>+9.2f}{winp(no):>5.0f}", end="")
        for s in STOPS:
            print(f"{mean(byS[s]):>+9.2f}{winp(byS[s]):>5.0f}{100*trig[s]/nt:>5.0f}%", end="")
        print(f"   (n{nt})")
    print("\n%gat = % de trades donde el stop se habría gatillado. Fill al stop = optimista (puede gapear).")


if __name__ == "__main__":
    main()
