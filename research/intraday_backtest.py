#!/usr/bin/env python3
"""
intraday_backtest.py — FASE 2 del estudio de momentum de continuación intradía.

Backtest RIGUROSO del shortlist de FASE 1 (ver INTRADAY_MOMENTUM_STUDY.md). Estrategia:
  - En t_in, señal = signo del movimiento acumulado desde la apertura (sig = sign(y_t_in)).
  - Entras en esa dirección y sales en t_out. Retorno bruto = sig * (y_t_out - y_t_in)  [%].
  - Neto = bruto - costo_roundtrip.

Protocolo anti-overfitting:
  - Trae ~180 días, parte CRONOLÓGICAMENTE en TRAIN (60% inicial) y TEST (40% final, no visto).
  - La ventana (t_in, t_out) se ELIGE solo en TRAIN (maximiza neto@0.10% con win>=55%).
  - Se REPORTA en TEST (out-of-sample). Si aguanta en TEST, el edge es creíble.

Extras: net a varios niveles de costo, baseline "siempre long", segmentación por día de semana
y por régimen QQQ (día verde/rojo, QQQ sobre/bajo SMA50), curva de equity + maxDD.

Uso:  python research/intraday_backtest.py [TK1,TK2,...] [lookback_days]
Default: shortlist FASE 1 · 180 días. Salida: research/intraday_backtest_results.csv + consola.
Local/standalone — NO se commitea ni despliega.
"""
import os
import sys
import csv
import time
import math
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
CSV_OUT = os.path.join(_HERE, "intraday_backtest_results.csv")

OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
LEN_MIN = CLOSE_MIN - OPEN_MIN
TINS = [30, 45, 60, 90, 120, 150]
TOUTS = list(range(60, LEN_MIN, 30)) + [LEN_MIN - 1]
COSTS = [0.0, 0.05, 0.10, 0.20]     # % round-trip
COST_MAIN = 0.10
MIN_TRAIN = 15
SHORTLIST = ["APLD", "INOD", "RIVN", "AVAV", "IREN", "AKAM", "MRVL", "NBIS"]
WD = ["Lun", "Mar", "Mié", "Jue", "Vie"]


def _hdrs():
    return {"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SEC}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hm(t):
    tot = OPEN_MIN + t
    return f"{tot // 60:02d}:{tot % 60:02d}"


def fetch_bars(ticker, start, tf="1Min", retries=3):
    for attempt in range(retries):
        bars, params = [], {"timeframe": tf, "start": start, "feed": "sip", "limit": 1000, "sort": "asc"}
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
                time.sleep(0.12)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.5); continue
            print(f"    [!] {ticker} {tf}: {e}")
            return bars
    return []


def session_days(ticker, lookback_days):
    raw = fetch_bars(ticker, _iso(datetime.now(timezone.utc) - timedelta(days=lookback_days)))
    by_day = {}
    for b in raw:
        ts = b["t"].replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(ts).astimezone(ET)
        except Exception:
            continue
        m = dt.hour * 60 + dt.minute
        if m < OPEN_MIN or m > CLOSE_MIN:
            continue
        by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append((m - OPEN_MIN, float(b["o"]), float(b["c"])))
    days = []
    for date_str, items in sorted(by_day.items()):
        items.sort(key=lambda it: it[0])
        if len(items) < 60 or not items[0][1]:
            continue
        ref = items[0][1]
        pm = {x: (c / ref - 1) * 100 for x, o, c in items}
        days.append({"date": date_str, "weekday": datetime.strptime(date_str, "%Y-%m-%d").weekday(), "pm": pm})
    return days


def qqq_regime(lookback_days):
    """date -> {'up':bool, 'above50':bool} usando barras diarias de QQQ."""
    bars = fetch_bars("QQQ", _iso(datetime.now(timezone.utc) - timedelta(days=lookback_days + 80)), tf="1Day")
    closes = [(b["t"][:10], float(b["c"])) for b in bars]
    out = {}
    for i, (d, c) in enumerate(closes):
        if i == 0:
            continue
        up = c >= closes[i - 1][1]
        sma50 = sum(x[1] for x in closes[max(0, i - 49):i + 1]) / min(50, i + 1)
        out[d] = {"up": up, "above50": c >= sma50}
    return out


def trade_rets(days, tin, tout, cost):
    rets = []
    for d in days:
        pm = d["pm"]
        if tin in pm and tout in pm:
            sig = 1.0 if pm[tin] >= 0 else -1.0
            rets.append((d, sig * (pm[tout] - pm[tin]) - cost))
    return rets


def stats(rets):
    """rets: lista de floats. -> dict win/avg/med/sum/maxdd/sharpe/n."""
    if not rets:
        return None
    n = len(rets)
    avg = sum(rets) / n
    sd = (sum((r - avg) ** 2 for r in rets) / n) ** 0.5
    sr = sorted(rets)
    med = sr[n // 2]
    win = 100 * sum(1 for r in rets if r > 0) / n
    # equity acumulada (suma simple de %), maxDD
    eq = 0.0; peak = 0.0; mdd = 0.0
    for r in rets:
        eq += r; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    return {"n": n, "win": win, "avg": avg, "med": med, "sum": eq, "maxdd": mdd,
            "sharpe": (avg / sd) if sd > 0 else 0.0}


def best_window_train(train):
    best = None
    for tin in TINS:
        for tout in TOUTS:
            if tout <= tin:
                continue
            rs = [r for _, r in trade_rets(train, tin, tout, COST_MAIN)]
            if len(rs) < MIN_TRAIN:
                continue
            st = stats(rs)
            if st["win"] >= 55 and (best is None or st["avg"] > best[2]):
                best = (tin, tout, st["avg"])
    return (best[0], best[1]) if best else None


def backtest(ticker, days, qqq):
    n = len(days)
    if n < 2 * MIN_TRAIN:
        print(f"  {ticker}: n={n} insuficiente"); return None
    cut = int(n * 0.6)
    train, test = days[:cut], days[cut:]
    win = best_window_train(train)
    if not win:
        print(f"  {ticker}: sin ventana en train"); return None
    tin, tout = win
    tr = [r for _, r in trade_rets(train, tin, tout, COST_MAIN)]
    te_pairs = trade_rets(test, tin, tout, COST_MAIN)
    te = [r for _, r in te_pairs]
    st_tr, st_te = stats(tr), stats(te)
    if not st_te:
        print(f"  {ticker}: sin trades en test"); return None
    # net por costo (test, bruto = cost 0)
    by_cost = {c: stats([r for _, r in trade_rets(test, tin, tout, c)]) for c in COSTS}
    # baseline siempre-long (test, neto main)
    base = stats([(test[i]["pm"][tout] - test[i]["pm"][tin]) - COST_MAIN
                  for i in range(len(test)) if tin in test[i]["pm"] and tout in test[i]["pm"]])
    # por dia de semana (test, neto main)
    wd_stats = {}
    for wd in range(5):
        rs = [r for d, r in te_pairs if d["weekday"] == wd]
        wd_stats[wd] = stats(rs)
    # por regimen QQQ (test, neto main)
    up = [r for d, r in te_pairs if qqq.get(d["date"], {}).get("up")]
    dn = [r for d, r in te_pairs if qqq.get(d["date"]) and not qqq[d["date"]]["up"]]
    a50 = [r for d, r in te_pairs if qqq.get(d["date"], {}).get("above50")]
    b50 = [r for d, r in te_pairs if qqq.get(d["date"]) and not qqq[d["date"]]["above50"]]

    print(f"\n  {ticker}  train n={st_tr['n']} · test n={st_te['n']}  | ventana ENTRA {hm(tin)} -> SALE {hm(tout)}")
    print(f"    TRAIN neto@{COST_MAIN}: win {st_tr['win']:.0f}%  avg {st_tr['avg']:+.2f}%")
    print(f"    TEST  neto@{COST_MAIN}: win {st_te['win']:.0f}%  avg {st_te['avg']:+.2f}%  med {st_te['med']:+.2f}%  "
          f"suma {st_te['sum']:+.1f}%  maxDD {st_te['maxdd']:.1f}%  sharpe {st_te['sharpe']:.2f}")
    print(f"    TEST por costo: " + " · ".join(f"{c:.2f}%→{by_cost[c]['avg']:+.2f}%" for c in COSTS))
    print(f"    baseline siempre-LONG (test neto@{COST_MAIN}): avg {base['avg']:+.2f}%  win {base['win']:.0f}%"
          if base else "    baseline: n/d")
    print("    por día semana (test avg): " + " ".join(
        f"{WD[w]} {wd_stats[w]['avg']:+.2f}%(n{wd_stats[w]['n']})" if wd_stats[w] else f"{WD[w]} -" for w in range(5)))
    def seg(name, rs):
        s = stats(rs); return f"{name} {s['avg']:+.2f}%(n{s['n']},win{s['win']:.0f})" if s else f"{name} -"
    print("    por régimen QQQ (test avg): " + " | ".join(
        [seg("QQQ-verde", up), seg("QQQ-rojo", dn), seg("QQQ>SMA50", a50), seg("QQQ<SMA50", b50)]))

    return {"ticker": ticker, "tin": hm(tin), "tout": hm(tout),
            "train_win": round(st_tr["win"]), "train_avg": round(st_tr["avg"], 3),
            "test_n": st_te["n"], "test_win": round(st_te["win"]), "test_avg_net": round(st_te["avg"], 3),
            "test_med": round(st_te["med"], 3), "test_sum": round(st_te["sum"], 1),
            "test_maxdd": round(st_te["maxdd"], 1), "test_sharpe": round(st_te["sharpe"], 2),
            "test_avg_gross": round(by_cost[0.0]["avg"], 3),
            "baseline_long_avg": round(base["avg"], 3) if base else None,
            "qqq_green_avg": round(stats(up)["avg"], 3) if up else None,
            "qqq_red_avg": round(stats(dn)["avg"], 3) if dn else None,
            "_test_eq": te}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # evitar crash cp1252 con →/—/· en Windows
    except Exception:
        pass
    if not _KEY or not _SEC:
        print("ERROR: faltan llaves Alpaca"); sys.exit(1)
    args = sys.argv[1:]
    tickers = SHORTLIST
    lookback = 180
    if args:
        if "," in args[0] or not args[0].isdigit():
            tickers = [t.strip().upper() for t in args[0].split(",") if t.strip()]; args = args[1:]
        if args and args[0].isdigit():
            lookback = int(args[0])
    print(f"BACKTEST FASE 2 — {tickers} · lookback={lookback}d · train/test 60/40 · costo principal {COST_MAIN}%")
    print("Bajando régimen QQQ ...")
    qqq = qqq_regime(lookback)
    rows = []
    port_eq = {}  # date-index portfolio: lista de listas de rets por ticker (test)
    for i, tk in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {tk} ...")
        try:
            days = session_days(tk, lookback)
            row = backtest(tk, days, qqq)
        except Exception as e:
            print(f"  {tk}: error {e}"); row = None
        if row:
            rows.append(row)
    # CSV
    cols = ["ticker", "tin", "tout", "train_win", "train_avg", "test_n", "test_win", "test_avg_net",
            "test_avg_gross", "test_med", "test_sum", "test_maxdd", "test_sharpe",
            "baseline_long_avg", "qqq_green_avg", "qqq_red_avg"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    # leaderboard out-of-sample
    rows.sort(key=lambda r: -(r["test_avg_net"] or -9))
    print("\n" + "=" * 86)
    print(f"LEADERBOARD OUT-OF-SAMPLE (test, neto@{COST_MAIN}%) — ordenado por amplitud neta")
    print(f"{'tk':6}{'entra':>7}{'sale':>7}{'trWin':>7}{'teWin':>7}{'teNeto':>8}{'bruto':>7}{'suma':>7}{'maxDD':>7}{'shrp':>6}{'QQQrojo':>9}")
    for r in rows:
        print(f"{r['ticker']:6}{r['tin']:>7}{r['tout']:>7}{r['train_win']:>6}%{r['test_win']:>6}%"
              f"{r['test_avg_net']:>+8.2f}{r['test_avg_gross']:>+7.2f}{r['test_sum']:>+7.1f}{r['test_maxdd']:>7.1f}"
              f"{r['test_sharpe']:>6.2f}{(r['qqq_red_avg'] if r['qqq_red_avg'] is not None else 0):>+9.2f}")
    print(f"\nCSV -> {CSV_OUT}")
    print("Lectura: si teNeto > 0 y cercano a trWin/trAvg => el edge sobrevive fuera de muestra y a costos.")


if __name__ == "__main__":
    main()
