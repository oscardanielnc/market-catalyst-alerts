#!/usr/bin/env python3
"""
daily_shape_scan.py — FASE 1 del estudio "momentum de continuación intradía".

Idea de Oscar (2026-06-24): si la dirección del día se define a cierta hora y se mantiene,
se podría ENTRAR a esa hora montándose a la dirección ya marcada y SALIR con una ganancia
leve pero probable. Esta herramienta BARRE muchos tickers (watchlist) del último ~mes/2 meses
y rankea quién tiene la tendencia mejor definida, desde qué hora y hasta qué hora aguanta.

Métrica central (continuación): para cada día, la "señal" en t_in = signo del movimiento
acumulado desde la apertura (y_t_in). El trade = signal * (y_t_out - y_t_in) → cuánto capturas
montándote a la dirección-hasta-ahora desde t_in hasta t_out. Se barre una grilla de (t_in, t_out)
y se reporta la MEJOR ventana por ticker: win-rate, amplitud media (%), hora entrada/salida.

OJO (honestidad): esta señal favorece tickers en tendencia durante la ventana medida (en un mes
alcista, "seguir el movimiento" gana). Por eso reporto también % de días verdes (sesgo de un lado)
para distinguir "edge real de continuación" de "simplemente venía subiendo". El filtro de costos,
régimen y días malos es la FASE 2.

Uso:
  python research/daily_shape_scan.py TICK1,TICK2,...        # 60 días por defecto
  python research/daily_shape_scan.py TICK1,TICK2,... 45     # 45 días lookback
Salida: research/daily_shape_scan_results.csv (APPEND incremental) + leaderboard en consola.
Local/standalone — NO se commitea ni despliega.
"""
import os
import sys
import csv
import time
import requests
from datetime import datetime, timezone, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except Exception:
    pass
import pytz
ET = pytz.timezone("America/New_York")

ALPACA_BASE = "https://data.alpaca.markets"
_KEY = os.environ.get("ALPACA_API_KEY", "")
_SEC = os.environ.get("ALPACA_SECRET_KEY", "")
CSV_OUT = os.path.join(_HERE, "daily_shape_scan_results.csv")

OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
LEN_MIN = CLOSE_MIN - OPEN_MIN  # 390
TINS = [30, 45, 60, 90, 120, 150]            # horas de entrada candidatas
MIN_DAYS = 20                                 # mínimo de sesiones para rankear


def _hdrs():
    return {"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SEC}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hm(t):
    tot = OPEN_MIN + t
    return f"{tot // 60:02d}:{tot % 60:02d}"


def fetch_1min(ticker, start, retries=3):
    for attempt in range(retries):
        bars, params = [], {"timeframe": "1Min", "start": start, "feed": "sip",
                            "limit": 1000, "sort": "asc"}
        try:
            while True:
                r = requests.get(f"{ALPACA_BASE}/v2/stocks/{ticker}/bars",
                                 params=params, headers=_hdrs(), timeout=40)
                r.raise_for_status()
                data = r.json()
                bars.extend(data.get("bars") or [])
                tok = data.get("next_page_token")
                if not tok:
                    return bars
                params["page_token"] = tok
                time.sleep(0.15)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.5)
                continue
            print(f"    [!] {ticker}: fallo fetch tras {retries} intentos: {e}")
            return bars
    return []


def session_days(ticker, lookback_days):
    """[{date, weekday, pm:{x->y%}, yclose}] por día de sesión completo."""
    start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    raw = fetch_1min(ticker, _iso(start))
    by_day = {}
    for b in raw:
        ts = b["t"].replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts).astimezone(ET)
        except Exception:
            continue
        mins = dt.hour * 60 + dt.minute
        if mins < OPEN_MIN or mins > CLOSE_MIN:
            continue
        by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append((mins - OPEN_MIN, float(b["o"]), float(b["c"])))
    days = []
    for date_str, items in sorted(by_day.items()):
        items.sort(key=lambda it: it[0])
        if len(items) < 60:
            continue
        ref = items[0][1]  # open del día
        if not ref:
            continue
        pm = {x: round((c / ref - 1) * 100, 4) for x, o, c in items}
        xclose = items[-1][0]
        wd = datetime.strptime(date_str, "%Y-%m-%d").weekday()
        days.append({"date": date_str, "weekday": wd, "pm": pm, "yclose": pm[xclose]})
    return days


def best_window(days):
    """Barre (t_in, t_out) y devuelve la mejor ventana de continuación por amplitud media."""
    touts = list(range(60, LEN_MIN, 30)) + [LEN_MIN - 1]
    best = None
    for tin in TINS:
        for tout in touts:
            if tout <= tin:
                continue
            rets = []
            for d in days:
                pm = d["pm"]
                if tin in pm and tout in pm:
                    sig = 1.0 if pm[tin] >= 0 else -1.0
                    rets.append(sig * (pm[tout] - pm[tin]))
            if len(rets) < MIN_DAYS:
                continue
            avg = sum(rets) / len(rets)
            win = 100 * sum(1 for r in rets if r > 0) / len(rets)
            cand = {"tin": tin, "tout": tout, "avg": avg, "win": win, "n": len(rets),
                    "med": sorted(rets)[len(rets) // 2]}
            # score: amplitud media, exigiendo que gane más de la mitad de las veces
            if win >= 55 and (best is None or avg > best["avg"]):
                best = cand
    return best


def agree_at(days, t):
    rt = [(d["pm"][t], d["yclose"]) for d in days if t in d["pm"]]
    if not rt:
        return None
    return round(100 * sum(1 for a, b in rt if (a >= 0) == (b >= 0)) / len(rt))


def lockin_median(days):
    lks = []
    for d in days:
        cs = d["yclose"] >= 0
        xs = sorted(d["pm"].keys())
        lk = xs[-1]
        for x in reversed(xs):
            if (d["pm"][x] >= 0) == cs:
                lk = x
            else:
                break
        lks.append(lk)
    lks.sort()
    return lks[len(lks) // 2] if lks else None


def analyze_ticker(ticker, lookback):
    days = session_days(ticker, lookback)
    n = len(days)
    if n < MIN_DAYS:
        print(f"  {ticker:6} n={n} (insuficiente)")
        return None
    bw = best_window(days)
    if not bw:
        print(f"  {ticker:6} n={n} sin ventana con win>=55%")
        return None
    green = round(100 * sum(1 for d in days if d["yclose"] >= 0) / n)
    mean_close = round(sum(d["yclose"] for d in days) / n, 2)
    row = {
        "ticker": ticker, "n": n,
        "tin_min": bw["tin"], "tin_hora": hm(bw["tin"]),
        "tout_min": bw["tout"], "tout_hora": hm(bw["tout"]),
        "win_pct": round(bw["win"]), "avg_pct": round(bw["avg"], 2), "med_pct": round(bw["med"], 2),
        "agree2h": agree_at(days, 120), "pct_green": green, "mean_close": mean_close,
        "lockin_min": lockin_median(days),
    }
    print(f"  {ticker:6} n={n}  ENTRA {row['tin_hora']} -> SALE {row['tout_hora']}  "
          f"win {row['win_pct']}%  amp {row['avg_pct']:+.2f}%  verde {green}%  agree2h {row['agree2h']}%")
    return row


_CSV_COLS = ["ticker", "n", "tin_min", "tin_hora", "tout_min", "tout_hora",
             "win_pct", "avg_pct", "med_pct", "agree2h", "pct_green", "mean_close", "lockin_min"]


def append_csv(row):
    new = not os.path.exists(CSV_OUT)
    with open(CSV_OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    if not _KEY or not _SEC:
        print("ERROR: faltan ALPACA_API_KEY / ALPACA_SECRET_KEY")
        sys.exit(1)
    if len(sys.argv) < 2:
        print("Uso: python research/daily_shape_scan.py TICK1,TICK2,... [lookback_days]")
        sys.exit(1)
    tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()]
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 60
    print(f"SCAN — {len(tickers)} tickers · lookback={lookback}d · CSV={CSV_OUT}")
    rows = []
    for i, tk in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {tk} ...")
        try:
            row = analyze_ticker(tk, lookback)
        except Exception as e:
            print(f"  {tk}: error {e}")
            row = None
        if row:
            append_csv(row)
            rows.append(row)
    # leaderboard del batch
    rows.sort(key=lambda r: -r["avg_pct"])
    print("\n" + "=" * 78)
    print("LEADERBOARD del batch (por amplitud media capturada):")
    print(f"{'ticker':7}{'n':>4}  {'entrada':>7} {'salida':>7}  {'win%':>5} {'amp%':>7} {'verde%':>7} {'agr2h':>6}")
    for r in rows:
        print(f"{r['ticker']:7}{r['n']:>4}  {r['tin_hora']:>7} {r['tout_hora']:>7}  "
              f"{r['win_pct']:>5} {r['avg_pct']:>+7.2f} {r['pct_green']:>7} {r['agree2h']:>6}")
    print(f"\n(resultados acumulados en {CSV_OUT})")


if __name__ == "__main__":
    main()
