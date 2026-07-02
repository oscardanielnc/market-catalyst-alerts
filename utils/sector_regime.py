"""
utils/sector_regime.py — régimen sectorial/macro por CÓDIGO PURO (sin IA), cache diario.

Motivación (SCOREBOARD_DIAGNOSIS 2ª lectura 2026-07-02): las señales LONG en sector
girando a la baja aciertan 34% (vs 50-55% los SHORT). Este helper da el dato que el
pipeline necesitaba para gatear: el estado CORTO del ETF de sector del ticker
(ret_5d, posición vs EMA20, rolling_over) + régimen macro (QQQ vs su EMA20).

Misma regla de "dándose vuelta" que el radar del Brief (_sector_radar_block):
    rolling_over = ret_5d <= -2.5  ó  (bajo EMA20 y ret_5d < 0)

1 fetch batched de ETFs+QQQ por día (cache en memoria). Best-effort: nunca lanza;
si no hay datos devuelve campos en None (el caller no gatea con None).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Mapeo ticker→ETF: base del Piloto + extras del universo de noticias (small-caps
# volátiles que el Piloto no rankea pero el pipeline de alertas sí monitorea).
EXTRA_ETF = {
    "WOLF": "SMH", "APLD": "SMH", "DELL": "XLK", "ZS": "XLK", "AKAM": "XLK",
    "APPS": "XLK", "BBAI": "XLK", "SOUN": "XLK", "INOD": "XLK", "QBTS": "XLK",
    "QUBT": "XLK", "ZM": "XLK", "TTWO": "XLK", "RDDT": "XLK", "SNOW": "XLK",
    "MARA": "XLK", "RIOT": "XLK", "BTC": "XLK",
    "LUNR": "XLI", "JOBY": "XLI", "ACHR": "XLI", "AVAV": "XLI", "KTOS": "XLI",
    "FLY": "XLI", "DY": "XLI", "VRRM": "XLI", "BB": "XLK", "HYLN": "XLI",
    "GME": "XLY", "AMC": "XLY", "SE": "XLY", "MELI": "XLY", "JMIA": "XLY",
    "SHOP": "XLY", "RIVN": "XLY", "LCID": "XLY",
    "SOFI": "XLF", "TLN": "XLU", "SEDG": "XLU", "CODX": "XLV",
}

_cache: dict = {"day": None, "etfs": {}, "qqq": None}


def _etf_of(ticker: str):
    try:
        from pilot.star_score import SECTOR_ETF
    except Exception:
        SECTOR_ETF = {}
    tk = (ticker or "").upper()
    return SECTOR_ETF.get(tk) or EXTRA_ETF.get(tk)


def _refresh():
    """Recalcula métricas cortas de todos los ETFs + QQQ (1 fetch batched/día)."""
    try:
        from pilot.star_score import SECTOR_ETFS
        from pilot.momentum_signals import fetch_daily_batch, _ema
    except Exception as e:
        logger.debug(f"[SectorRegime] imports: {e}")
        return
    symbols = sorted(set(SECTOR_ETFS) | set(EXTRA_ETF.values()) | {"QQQ"})
    try:
        bars_by = fetch_daily_batch(symbols, lookback_days=60)
    except Exception as e:
        logger.debug(f"[SectorRegime] fetch: {e}")
        return
    etfs = {}
    for sym in symbols:
        closes = [b["c"] for b in (bars_by.get(sym) or []) if b.get("c")]
        if len(closes) < 21:
            continue
        last = closes[-1]
        ret_5d = round((last / closes[-6] - 1) * 100, 1) if len(closes) >= 6 and closes[-6] else None
        ema20 = _ema(closes, 20)
        vs_ema20 = round((last / ema20 - 1) * 100, 1) if ema20 else None
        rolling = (ret_5d is not None and ret_5d <= -2.5) or \
                  (vs_ema20 is not None and vs_ema20 < 0 and (ret_5d or 0) < 0)
        etfs[sym] = {"ret_5d": ret_5d, "vs_ema20": vs_ema20, "rolling_over": bool(rolling)}
    if not etfs:
        return
    q = etfs.get("QQQ") or {}
    _cache["etfs"] = etfs
    # risk_on: QQQ sobre su EMA20 y sin caída fuerte de 5d (régimen macro binario, barato)
    _cache["qqq"] = (None if q.get("vs_ema20") is None
                     else int(q["vs_ema20"] >= 0 and (q.get("ret_5d") or 0) > -2.5))
    _cache["day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_regime(ticker: str) -> dict:
    """
    Devuelve el régimen para un ticker:
      {etf, ret_5d, vs_ema20, rolling_over (bool|None), risk_on (1/0/None)}
    rolling_over=None cuando el ticker no mapea a ETF o no hay datos → NO gatear.
    """
    out = {"etf": None, "ret_5d": None, "vs_ema20": None,
           "rolling_over": None, "risk_on": None}
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _cache["day"] != today:
            _refresh()
        out["risk_on"] = _cache.get("qqq")
        etf = _etf_of(ticker)
        if etf and etf in (_cache.get("etfs") or {}):
            m = _cache["etfs"][etf]
            out.update({"etf": etf, "ret_5d": m["ret_5d"], "vs_ema20": m["vs_ema20"],
                        "rolling_over": m["rolling_over"]})
    except Exception as e:
        logger.debug(f"[SectorRegime] get_regime({ticker}): {e}")
    return out
