#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dip_crypto_paper.py — PAPER-FORWARD en vivo del sistema dip TP3/SL1 en crypto perps.

Objetivo: medir EN VIVO lo que ningún backtest puede — el SLIPPAGE y el FILL real del stop —
para saber de qué lado de la navaja cae el edge (ver dip-bounce-backtest: el sistema vive o muere
según slippage ~0.15% vs ~0.30%+). 100% observación, NO ejecuta órdenes.

Cómo funciona (1 ciclo, pensado para cron horario en la VM):
  1. Por cada moneda: 1h Binance USDM -> resample daily -> soportes (utils.dip_levels.analyze_ticker).
  2. Si el precio TOCA un soporte (low de la última vela <= soporte) y no hay posición abierta en esa
     moneda -> abre posición PAPER: entry=soporte (límite), TP=+3%, SL=-1%. Guarda market_at_signal.
  3. Monitorea abiertas: detecta TP/SL en 1h y mide el FILL REAL con velas de 1m del momento del cruce
     -> slippage = pnl_real - pnl_ideal. Timeout a 8 días = cierre a mercado.
  4. Persiste todo en data/dip_crypto_paper.db (tablas positions, cycles).

Uso:
  python pilot/dip_crypto_paper.py --cycle     # un ciclo (cron horario)
  python pilot/dip_crypto_paper.py --report    # estadísticas en vivo acumuladas
"""
from __future__ import annotations
import os, sys, time, sqlite3, argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dip_levels import analyze_ticker

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dip_crypto_paper.db")
UNIVERSE = ["BTC","ETH","SOL","BNB","XRP","ADA","AVAX","DOGE","LINK","DOT","LTC","ATOM",
            "NEAR","APT","ARB","OP","INJ","SUI","TIA","SEI","FIL","AAVE","UNI","ICP"]
TP_PCT, SL_PCT = 3.0, 1.0
TOUCH_TOL = 0.004
HIST_DAYS = 240
TIMEOUT_H = 8 * 24
FEE = 0.10


def _ex():
    import ccxt
    return ccxt.binanceusdm({"enableRateLimit": True})


def fetch_1h(ex, coin, days=HIST_DAYS):
    sym = f"{coin}/USDT:USDT"
    since = ex.milliseconds() - days * 24 * 3600 * 1000
    out = []
    while True:
        o = ex.fetch_ohlcv(sym, "1h", since=since, limit=1000)
        if not o:
            break
        out.extend(o)
        if len(o) < 1000:
            break
        since = o[-1][0] + 3600 * 1000
        time.sleep(0.03)
    seen = {b[0]: b for b in out}
    return [seen[k] for k in sorted(seen)]


def fetch_1m_hour(ex, coin, hour_ts):
    """60 velas de 1m a partir de hour_ts (para medir el fill real del stop)."""
    sym = f"{coin}/USDT:USDT"
    try:
        return ex.fetch_ohlcv(sym, "1m", since=hour_ts, limit=60)
    except Exception:
        return []


def resample_daily(bars1h):
    days, order = {}, []
    for b in bars1h:
        d = datetime.fromtimestamp(b[0] / 1000, timezone.utc).strftime("%Y-%m-%d")
        if d not in days:
            days[d] = {"o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]}; order.append(d)
        else:
            x = days[d]; x["h"] = max(x["h"], b[2]); x["l"] = min(x["l"], b[3]); x["c"] = b[4]; x["v"] += b[5]
    return [{"t": d + "T00:00:00Z", **days[d]} for d in order]


def init_db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS positions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT, support REAL, entry REAL,
      tp REAL, sl REAL, strength INTEGER, conf INTEGER, dist_pct REAL,
      market_at_signal REAL, signal_ts INTEGER, signal_iso TEXT, status TEXT,
      exit REAL, exit_ts INTEGER, exit_iso TEXT, outcome TEXT,
      pnl_pct REAL, ideal_pnl_pct REAL, slippage_pct REAL, dur_h REAL);
    CREATE TABLE IF NOT EXISTS cycles(
      ts INTEGER, iso TEXT, coins_ok INTEGER, n_open INTEGER, n_new INTEGER, n_closed INTEGER);
    """)
    c.commit(); return c


def open_coins(c):
    return {r[0] for r in c.execute("SELECT coin FROM positions WHERE status='open'")}


def resolve_open(ex, c, now_ms):
    """Revisa cada posición abierta: ¿tocó TP o SL? Mide fill real con 1m. Timeout a 8d."""
    n_closed = 0
    rows = c.execute("SELECT id,coin,support,entry,tp,sl,signal_ts FROM positions WHERE status='open'").fetchall()
    for pid, coin, support, entry, tp, sl, sig_ts in rows:
        try:
            bars = fetch_1h(ex, coin, days=12)   # suficiente para cubrir el hold (<=8d)
        except Exception:
            continue
        fwd = [b for b in bars if b[0] > sig_ts]
        hit_ts = outcome = None
        ideal = real = None
        for b in fwd:
            o, h, l = b[1], b[2], b[3]
            stop_hit, tgt_hit = (l <= sl), (h >= tp)
            if not stop_hit and not tgt_hit:
                continue
            # fill real con 1m de esa hora
            m1 = fetch_1m_hour(ex, coin, b[0])
            if stop_hit and (not tgt_hit):
                outcome, ideal = "loss", -SL_PCT
                real = _fill_stop(m1, sl, entry)
            elif tgt_hit and (not stop_hit):
                outcome, ideal = "win", TP_PCT
                real = _fill_target(m1, tp, entry)
            else:   # ambos en la misma hora -> conservador: stop primero
                outcome, ideal = "loss", -SL_PCT
                real = _fill_stop(m1, sl, entry)
            hit_ts = b[0]
            break
        if outcome is None:
            if now_ms - sig_ts >= TIMEOUT_H * 3600 * 1000 and fwd:
                outcome, ideal = "timeout", round((fwd[-1][4] / entry - 1) * 100, 2)
                real, hit_ts = ideal, fwd[-1][0]
            else:
                continue
        slip = round((real - ideal), 3)
        dur_h = round((hit_ts - sig_ts) / 3600000, 1)
        c.execute("""UPDATE positions SET status='closed', exit=?, exit_ts=?, exit_iso=?, outcome=?,
                     pnl_pct=?, ideal_pnl_pct=?, slippage_pct=?, dur_h=? WHERE id=?""",
                  (round(entry * (1 + real / 100), 6), hit_ts,
                   datetime.fromtimestamp(hit_ts/1000, timezone.utc).isoformat(),
                   outcome, real, ideal, slip, dur_h, pid))
        n_closed += 1
    c.commit(); return n_closed


def _fill_stop(m1, sl, entry):
    """Fill REAL de un stop (market). Si la vela 1m abre bajo el stop -> fill al open (slippage)."""
    for b in m1:
        o, l = b[1], b[3]
        if l <= sl:
            fill = o if o <= sl else sl     # gap = peor; si no, asume llenado en el nivel
            return round((fill / entry - 1) * 100, 3)
    return round((sl / entry - 1) * 100, 3)   # sin 1m: ideal


def _fill_target(m1, tp, entry):
    """Fill de un TP (límite): llena en tp o mejor (gap up = mejor)."""
    for b in m1:
        o, h = b[1], b[2]
        if h >= tp:
            fill = o if o >= tp else tp
            return round((fill / entry - 1) * 100, 3)
    return round((tp / entry - 1) * 100, 3)


def scan_new(ex, c, now_ms):
    """Busca toques de soporte nuevos y abre posiciones paper (1 por moneda)."""
    busy = open_coins(c)
    coins_ok = n_new = 0
    btc = fetch_1h(ex, "BTC")
    btc_map = {b[0]: b for b in btc}
    btc_daily = resample_daily(btc)
    btc_dmap = {b["t"][:10]: float(b["c"]) for b in btc_daily}
    for coin in UNIVERSE:
        try:
            bars = btc if coin == "BTC" else fetch_1h(ex, coin)
            if len(bars) < (HIST_DAYS // 3) * 24:
                continue
            daily = btc_daily if coin == "BTC" else resample_daily(bars)
            res = analyze_ticker(coin, daily, spy_map=btc_dmap)
            if not res:
                continue
            coins_ok += 1
            if coin in busy:
                continue
            last = bars[-1]
            sups = (res.get("short_supports") or []) + (res.get("struct_supports") or [])
            for sup in sups:
                p = sup["price"]
                if last[3] <= p * (1 + TOUCH_TOL) and last[4] >= p * (1 - 0.02):
                    # tocó este 1h y el precio sigue cerca -> abre paper
                    c.execute("""INSERT INTO positions(coin,support,entry,tp,sl,strength,conf,dist_pct,
                                 market_at_signal,signal_ts,signal_iso,status)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?, 'open')""",
                              (coin, p, p, round(p*(1+TP_PCT/100),6), round(p*(1-SL_PCT/100),6),
                               sup.get("strength", 0), sup.get("conf_score") or 0, sup.get("dist_pct"),
                               last[4], now_ms, datetime.fromtimestamp(now_ms/1000, timezone.utc).isoformat()))
                    n_new += 1
                    busy.add(coin)
                    break
            time.sleep(0.02)
        except Exception as e:
            print(f"  [!] {coin}: {e}")
    c.commit(); return coins_ok, n_new


def cycle():
    c = init_db()
    ex = _ex(); ex.load_markets()
    now = ex.milliseconds()
    n_closed = resolve_open(ex, c, now)
    coins_ok, n_new = scan_new(ex, c, now)
    n_open = len(open_coins(c))
    c.execute("INSERT INTO cycles VALUES(?,?,?,?,?,?)",
              (now, datetime.fromtimestamp(now/1000, timezone.utc).isoformat(), coins_ok, n_open, n_new, n_closed))
    c.commit()
    print(f"[dip-paper] ciclo {datetime.fromtimestamp(now/1000, timezone.utc).strftime('%Y-%m-%d %H:%M')}Z "
          f"| coins_ok={coins_ok} nuevas={n_new} cerradas={n_closed} abiertas={n_open}")


def report():
    if not os.path.exists(DB):
        print("Sin datos aún (corre --cycle primero)."); return
    c = sqlite3.connect(DB)
    nopen = c.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
    closed = c.execute("""SELECT outcome,pnl_pct,ideal_pnl_pct,slippage_pct,dur_h,coin
                          FROM positions WHERE status='closed'""").fetchall()
    cyc = c.execute("SELECT MIN(iso),MAX(iso),COUNT(*) FROM cycles").fetchone()
    print(f"PAPER dip-crypto | ciclos={cyc[2]} ({cyc[0]} -> {cyc[1]})")
    print(f"Posiciones: ABIERTAS={nopen}  CERRADAS={len(closed)}")
    if not closed:
        print("Aún sin cierres — el slippage en vivo aparece cuando cierren las primeras."); return
    wins = [r for r in closed if r[0] == "win"]
    loss = [r for r in closed if r[0] == "loss"]
    wr = round(100*len(wins)/(len(wins)+len(loss))) if (wins or loss) else None
    pnls = [r[1] - FEE for r in closed]
    slips = [r[3] for r in closed if r[0] in ("win","loss")]
    avg = lambda x: round(sum(x)/len(x), 3) if x else None
    print(f"  winrate={wr}%  exp_neta={avg(pnls)}%/trade  (backtest esperaba ~+0.34%)")
    print(f"  SLIPPAGE medio={avg(slips)}%  (vivo)  | peor={min(slips) if slips else None}%")
    print(f"  -> Lectura: slip ~0.0/-0.1% = edge vive; ~-0.3% = marginal; <=-0.45% = breakeven/pierde")
    print(f"  duracion media={avg([r[4] for r in closed])}h")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    else:
        cycle()
