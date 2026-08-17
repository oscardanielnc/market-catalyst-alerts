#!/usr/bin/env python3
"""
binance_filter_sizing.py — ¿Conviene filtrar por QQQ? + estadística de pérdidas para el sizing.

Para cada ticker en su ventana recomendada (overnight LONG), mide el NETO segmentado por el estado
del QQQ CONOCIDO al momento de entrar (sin lookahead: usa el último cierre diario de QQQ <= entrada):
  - QQQ día previo VERDE vs ROJO
  - QQQ sobre vs bajo su SMA50
  - (control) el propio activo día previo verde/rojo
Y la distribución de resultados por trade (media, desv, peor, percentil 5, % de trades < -1% / < -2%)
para fundamentar cuánto arriesgar por operación.

Local/standalone — NO se commitea ni despliega. Requiere ccxt (Binance) y ALPACA_*/.env (QQQ diario).
"""
import os
import sys
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

_KEY = os.environ.get("ALPACA_API_KEY", "")
_SEC = os.environ.get("ALPACA_SECRET_KEY", "")
ALP = "https://data.alpaca.markets"
FEE_RT = 0.08
LIMA_OFFSET = -5

WIN = {"SNDK": (0, 12), "MU": (0, 12), "MRVL": (21, 9), "INTC": (7, 7), "AMD": (23, 10), "TSM": (16, 12)}


def lima_hour(e):
    return int(((e // 3600) + LIMA_OFFSET) % 24)


def fetch_1h(ex, ticker):
    sym = f"{ticker}/USDT:USDT"
    since = ex.milliseconds() - 210 * 24 * 3600 * 1000
    out = []
    while True:
        try:
            o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        except Exception:
            break
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.05)
    return sorted({b[0] // 1000: b[4] for b in out}.items())


def avg_fund(ex, ticker):
    sym = f"{ticker}/USDT:USDT"; since = ex.milliseconds() - 210 * 24 * 3600 * 1000; rs = []
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
        since = fr[-1]["timestamp"] + 1; time.sleep(0.05)
    return (sum(rs) / len(rs) * 100) if rs else 0.0


def qqq_daily():
    """Lista (close_ts_utc, close, ret_prev, above_sma50) — close_ts = fecha a las 20:00 UTC (cierre US)."""
    start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{ALP}/v2/stocks/QQQ/bars",
                     params={"timeframe": "1Day", "start": start, "feed": "sip", "limit": 10000, "sort": "asc"},
                     headers={"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SEC}, timeout=40)
    bars = r.json().get("bars", [])
    closes = [(b["t"][:10], float(b["c"])) for b in bars]
    out = []
    for i in range(1, len(closes)):
        d, c = closes[i]
        ts = int(datetime.strptime(d + " 20:00", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
        ret = c / closes[i - 1][1] - 1
        sma50 = sum(x[1] for x in closes[max(0, i - 49):i + 1]) / min(50, i + 1)
        out.append((ts, c, ret, c >= sma50))
    return out


def qqq_state_at(qqq, e):
    """Último cierre QQQ conocido en epoch e (sin lookahead)."""
    prev = None
    for ts, c, ret, above in qqq:
        if ts <= e:
            prev = (ret, above)
        else:
            break
    return prev


def seg(vals):
    if not vals:
        return "n/d"
    n = len(vals); m = sum(vals) / n
    win = 100 * sum(1 for x in vals if x > 0) / n
    return f"{m:+.2f}%(win{win:.0f},n{n})"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import ccxt
    ex = ccxt.binanceusdm({"enableRateLimit": True}); ex.load_markets()
    qqq = qqq_daily()
    print(f"QQQ diario cargado: {len(qqq)} días\n")
    pooled = {"qup": [], "qdn": [], "qa": [], "qb": []}
    for tk, (h, D) in WIN.items():
        series = fetch_1h(ex, tk); close = dict(series); epochs = [e for e, _ in series]
        if len(series) < 24 * 30:
            print(f"{tk}: insuficiente"); continue
        fund = avg_fund(ex, tk) * (D / 8.0)
        trades = []   # (net, qqq_ret_prev, qqq_above, self_prev_up)
        for e in epochs:
            if lima_hour(e) != h:
                continue
            c0 = close.get(e); c1 = close.get(e + D * 3600)
            if not c0 or not c1:
                continue
            net = (c1 / c0 - 1) * 100 - FEE_RT - fund
            qs = qqq_state_at(qqq, e)
            cprev = close.get(e - 24 * 3600)
            self_up = (cprev is not None and c0 > cprev)
            trades.append((net, qs, self_up))
        if not trades:
            print(f"{tk}: sin trades"); continue
        allnet = [t[0] for t in trades]
        qup = [t[0] for t in trades if t[1] and t[1][0] >= 0]
        qdn = [t[0] for t in trades if t[1] and t[1][0] < 0]
        qa = [t[0] for t in trades if t[1] and t[1][1]]
        qb = [t[0] for t in trades if t[1] and not t[1][1]]
        sup = [t[0] for t in trades if t[2]]
        sdn = [t[0] for t in trades if not t[2]]
        pooled["qup"] += qup; pooled["qdn"] += qdn; pooled["qa"] += qa; pooled["qb"] += qb
        sr = sorted(allnet); n = len(sr)
        worst = sr[0]; p5 = sr[max(0, n // 20)]
        losers = [x for x in allnet if x < 0]
        avg_los = sum(losers) / len(losers) if losers else 0
        lt1 = 100 * sum(1 for x in allnet if x < -1) / n
        lt2 = 100 * sum(1 for x in allnet if x < -2) / n
        print(f"{'='*72}\n{tk}  ventana {h:02d}:00->{(h+D)%24:02d}:00 Lima ({D}h)  ·  TODOS: {seg(allnet)}")
        print(f"  FILTRO QQQ:  prev VERDE {seg(qup)}  |  prev ROJO {seg(qdn)}")
        print(f"               QQQ>SMA50 {seg(qa)}  |  QQQ<SMA50 {seg(qb)}")
        print(f"  (control) activo prev: VERDE {seg(sup)} | ROJO {seg(sdn)}")
        print(f"  PÉRDIDAS (sizing): peor {worst:+.2f}%  p5 {p5:+.2f}%  pérdida media {avg_los:+.2f}%  "
              f"·  trades<-1%: {lt1:.0f}%  <-2%: {lt2:.0f}%")
    print(f"\n{'='*72}\nPOOL (toda la canasta):")
    print(f"  QQQ prev VERDE {seg(pooled['qup'])}  |  prev ROJO {seg(pooled['qdn'])}")
    print(f"  QQQ>SMA50 {seg(pooled['qa'])}  |  QQQ<SMA50 {seg(pooled['qb'])}")


if __name__ == "__main__":
    main()
