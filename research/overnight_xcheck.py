#!/usr/bin/env python3
"""
overnight_xcheck.py — Cross-check del efecto OVERNIGHT con historial LARGO de Alpaca (diario).

El edge de Binance (FASE 4-5): long overnight 00:00->12:00 Lima (05:00->17:00 UTC) en semis,
con solo ~39-78 días de muestra. Esa ventana cae mayormente en horas sin cotización US, así que
NO es replicable 1:1 en Alpaca. PERO su corazón es el "overnight gap" de las acciones (cierre->
apertura), una anomalía conocida y medible con AÑOS de barras diarias. Esto contrasta el hallazgo
con muestra mucho mayor y responde: ¿es durable o reciente? ¿rebota tras días rojos? ¿es de semis?

Por ticker (barras 1Day, ~4 años):
  - overnight = open(t)/close(t-1) - 1   |  intradía = close(t)/open(t) - 1  |  total = close/close
  - media%, win%, y qué fracción del total aporta el overnight.
  - RÉGIMEN: overnight tras día PREVIO rojo vs verde (valida el "rebote" de Binance).
  - RECIENTE (últimos 90 días) vs HISTÓRICO previo: ¿el efecto es nuevo o de siempre?

Uso:  python research/overnight_xcheck.py [TK1,TK2,...] [años]
Local/standalone — NO se commitea ni despliega. Requiere ALPACA_API_KEY/SECRET en .env.
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

ALPACA_BASE = "https://data.alpaca.markets"
_KEY = os.environ.get("ALPACA_API_KEY", "")
_SEC = os.environ.get("ALPACA_SECRET_KEY", "")

# Canasta semi (la del edge de Binance) + controles no-semi
SEMIS = ["MRVL", "SNDK", "MU", "INTC", "AMD", "TSM", "NVDA", "AVGO", "WDC", "LITE"]
CONTROLS = ["TSLA", "HOOD", "MSTR", "PLTR", "BB", "QQQ", "SPY"]
DEFAULT = SEMIS + CONTROLS
RECENT_N = 90   # días recientes para el split reciente/histórico


def _hdrs():
    return {"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SEC}


def fetch_daily(ticker, years):
    start = (datetime.now(timezone.utc) - timedelta(days=int(years * 365 + 10))).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars, params = [], {"timeframe": "1Day", "start": start, "feed": "sip", "limit": 10000, "sort": "asc"}
    try:
        while True:
            r = requests.get(f"{ALPACA_BASE}/v2/stocks/{ticker}/bars", params=params, headers=_hdrs(), timeout=40)
            r.raise_for_status()
            data = r.json()
            bars.extend(data.get("bars") or [])
            tok = data.get("next_page_token")
            if not tok:
                break
            params["page_token"] = tok
            time.sleep(0.1)
    except Exception as e:
        print(f"    [!] {ticker}: {e}")
    return [{"t": b["t"][:10], "o": float(b["o"]), "c": float(b["c"])} for b in bars if b.get("o") and b.get("c")]


def stat(v):
    if not v:
        return None
    n = len(v); m = sum(v) / n
    win = 100 * sum(1 for x in v if x > 0) / n
    return {"n": n, "mean": m, "win": win}


def analyze(ticker, years):
    bars = fetch_daily(ticker, years)
    if len(bars) < 60:
        return None
    on, intra, tot = [], [], []        # overnight, intradía, total (en %)
    on_after_red, on_after_green = [], []
    for i in range(1, len(bars)):
        c0 = bars[i - 1]["c"]; o1 = bars[i]["o"]; c1 = bars[i]["c"]
        if not c0 or not o1:
            continue
        ov = (o1 / c0 - 1) * 100
        idd = (c1 / o1 - 1) * 100
        on.append(ov); intra.append(idd); tot.append((c1 / c0 - 1) * 100)
        if i >= 2:
            prev_red = bars[i - 1]["c"] < bars[i - 2]["c"]
            (on_after_red if prev_red else on_after_green).append(ov)
    on_recent = stat(on[-RECENT_N:]); on_old = stat(on[:-RECENT_N]) if len(on) > RECENT_N else None
    return {
        "ticker": ticker, "days": len(bars),
        "span": f"{bars[0]['t']}..{bars[-1]['t']}",
        "on": stat(on), "intra": stat(intra), "tot": stat(tot),
        "on_red": stat(on_after_red), "on_green": stat(on_after_green),
        "on_recent": on_recent, "on_old": on_old,
    }


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not _KEY or not _SEC:
        print("ERROR: faltan llaves Alpaca"); sys.exit(1)
    args = sys.argv[1:]
    tickers = DEFAULT
    years = 4.0
    if args and ("," in args[0] or not args[0].replace(".", "").isdigit()):
        tickers = [t.strip().upper() for t in args[0].split(",") if t.strip()]; args = args[1:]
    if args:
        try: years = float(args[0])
        except Exception: pass
    print(f"OVERNIGHT XCHECK (Alpaca diario) — {len(tickers)} tickers · ~{years:.0f} años · overnight=open/cierre_previo")
    rows = []
    for tk in tickers:
        r = analyze(tk, years)
        if not r:
            print(f"  {tk:5} sin datos suficientes"); continue
        rows.append(r)
        sec = "SEMI " if tk in SEMIS else "ctrl "
        rec = r["on_recent"]; old = r["on_old"]
        rec_s = f"{rec['mean']:+.3f}%" if rec else "-"
        old_s = f"{old['mean']:+.3f}%" if old else "-"
        print(f"  {sec}{tk:5} {r['days']:>4}d  overnight {r['on']['mean']:+.3f}%(win{r['on']['win']:.0f}) | "
              f"intradía {r['intra']['mean']:+.3f}%(win{r['intra']['win']:.0f}) | "
              f"ON tras ROJO {r['on_red']['mean']:+.3f}% vs VERDE {r['on_green']['mean']:+.3f}% | "
              f"ON recién {rec_s} vs antes {old_s}")
    # resumen agregado semis vs controles
    def avg_on(group):
        v = [r["on"]["mean"] for r in rows if r["ticker"] in group and r["on"]]
        return sum(v) / len(v) if v else 0.0
    print("\n" + "=" * 80)
    print(f"OVERNIGHT medio — SEMIS: {avg_on(SEMIS):+.3f}%/día  |  CONTROLES: {avg_on(CONTROLS):+.3f}%/día")
    print("Clave: si overnight>0 y > intradía, y mayor tras días ROJOS, corrobora el edge de Binance.")
    print("       compara 'recién vs antes' para ver si es reciente o durable.")


if __name__ == "__main__":
    main()
