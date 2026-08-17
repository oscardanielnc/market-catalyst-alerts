# Dip-Crypto — Paper-forward en vivo y plan de decisión

**Estado: 🟢 VIVO en la VM desde 2026-06-26 (cron horario).** Objetivo: medir EN VIVO el
slippage/fill real del stop, la única incógnita que decide si el edge dip-buy TP3/SL1 en crypto
perps es operable o no. 100% observación, NO ejecuta órdenes.

---

## 1. Qué encontramos (resumen del backtest, 2026-06-25)

Sistema: comprar en soportes (motor `utils/dip_levels.py`), TP +3% / SL −1%, en perps 24/7 de
Binance (`binanceusdm`, SYM/USDT:USDT). Universo: 24 cryptos líquidas.

- **Edge por trade VALIDADO:** +0.34%/trade neto (FLAT), winrate ~37-38%, breakeven 27.5%.
  Sobrevivió: out-of-sample (in +0.25% → OOS +0.43%), walk-forward (4/5 bloques +), cross-sectional
  (ambas mitades de monedas +), **21/24 monedas con expectancy positiva**. Es lo más sólido del
  proyecto — edge real y amplio, no espejismo de muestra chica.
- **El stop SE RESPETA en perps 24/7** (pérdida media −1.34% vs −3.0% en acciones; en acciones el
  gap nocturno lo mataba). Confirmado.
- **PERO el resultado de cartera es FRÁGIL al slippage.** Prueba de sensibilidad (`backtest_dip_
  crypto_calendar.py`): slip 0 → 0.15 → 0.30% tira el Sharpe 6.4 → 4.2 → **1.7** y el CAGR
  2046% → 477% → 95%. El edge es fino y de alta frecuencia → unos bps de slippage por trade se
  comen casi todo. **Un Sharpe de 6 en backtest es ALARMA, no luz verde** (inflado por slippage
  cero + correlación de crypto tratada como diversificación).
- **Apalancamiento:** Kelly 3.25x, half-Kelly ~1.6x. **Techo de ruina ≈4.2x** (peor trade −23.9%
  → a 5x borra la cuenta). El stop −1% NO es el riesgo real; la cola es −24%. **≤2x sensato.**

**Conclusión honesta:** hay base real, pero su supervivencia depende del slippage real, que NO se
saca de un backtest. Por eso este paper-forward.

---

## 2. El paper-forward (`pilot/dip_crypto_paper.py`)

- **Cron en la VM:** `0 * * * *` (cada hora) → `venv/bin/python pilot/dip_crypto_paper.py --cycle`,
  log en `data/dip_crypto_paper.log`, DB en `data/dip_crypto_paper.db`.
- **Cada ciclo:** 1h Binance → resample daily → soportes (`analyze_ticker`). Si el precio TOCA un
  soporte y no hay posición abierta en esa moneda (1/moneda) → abre paper (entry=soporte límite,
  TP+3%, SL−1%, guarda `market_at_signal`). Monitorea abiertas: detecta TP/SL en 1h y **mide el
  FILL REAL con velas de 1m** del momento del cruce → `slippage = pnl_real − pnl_ideal`. Timeout 8d.
- **Tablas:** `positions` (open/closed con entry/exit/outcome/pnl/ideal/slippage/dur), `cycles` (log).

**Cómo revisar (desde la VM):**
```
ssh -i sentinel-prod.key opc@213.35.121.9
cd /home/opc/oportunity-alert && venv/bin/python pilot/dip_crypto_paper.py --report
```

---

## 3. Criterio de decisión (GO / NO-GO) — leer a las 2-4 semanas

La métrica que decide es el **SLIPPAGE MEDIO EN VIVO** (pnl_real − pnl_ideal por trade):

| Slippage medio en vivo | Veredicto | Acción |
|---|---|---|
| ~0 a −0.10% | ✅ Edge vive (Sharpe ~3-4) | Avanzar a tamaño minúsculo real, ≤2x, half-Kelly |
| −0.10 a −0.30% | ⚠️ Edge fino pero positivo (Sharpe ~1.5-2) | Seguir en paper más tiempo; reconsiderar frecuencia/selección |
| ≤ −0.45% | ❌ Breakeven o pierde | Descartar dip-crypto; pivotar a alternativas (§4) |

Validaciones cruzadas a mirar también:
- **winrate en vivo** ≈ 37-38% (si cae mucho < 30%, el régimen cambió o el modelo de toque falla).
- **exp_neta en vivo** vs backtest +0.34%/trade.
- **correlación:** si casi todas las posiciones abren/cierran juntas, confirma que son ~1 apuesta
  (riesgo real >> el que sugiere "24 monedas"). Medir nº de apuestas independientes efectivas.

---

## 4. Alternativas si dip-crypto NO da resultado (para "buscar otras")

Por orden de atractivo dado lo aprendido (preferir edges con base estadística + bajo coste/fricción):

1. **Funding-rate harvest en perps (market-neutral):** cobrar el funding de perps muy demandados
   (long spot / short perp o viceversa). Edge estructural, NO direccional → no depende de predecir
   precio. Mucho menos sensible al slippage que el dip-buy. **Candidato fuerte y distinto.**
2. **Overnight semis (ya en paper "🧪 Paper Overnight"):** el otro edge semi-validado del proyecto
   (prima overnight de semis en perps tradfi). Revisar su scoreboard en paralelo.
3. **Earnings PED (Piloto):** MU = playbook real (0.94). Edge por-evento, calendario-conocible.
4. **Mean-reversion con salida más ancha (+3/−3):** el +3/−3 dio 61% win y casi no gapea; más robusto
   al slippage que el +3/−1 aunque peor ratio nominal. Backtestear en crypto.
5. **Stat-arb / pairs en crypto:** explotar la CORRELACIÓN (que aquí es problema) en vez de pelearla
   — pares cointegrados, market-neutral.
6. **"Mitad segura": DCA en índice (BTC/ETH o SPY):** no necesita edge ni IA; resuelve la mitad de
   bajo riesgo del objetivo de Oscar por pura matemática (aporte periódico, baja rotación).

---

## 5. Scripts del estudio (en `research/` salvo el motor en `utils/` y el paper en `pilot/`)
- `backtest_dip_bounce.py` — ¿rebota en soportes? (61% +3 antes de −3)
- `backtest_dip_system.py` — descubrimiento de qué predice (RSI, distancia, régimen)
- `backtest_dip_sized.py` — TP3/SL1 + sizing + sensibilidad a fees
- `backtest_dip_stress.py` — gap-through en ACCIONES (mató el stop ahí)
- `backtest_dip_perps.py` — stock-perps 24/7 (stop respetado, muestra corta)
- `backtest_dip_crypto.py` (+`_oos`, `_portfolio`, `_calendar`) — crypto: validación, ruina, calendario
- `pilot/dip_crypto_paper.py` — **paper-forward en vivo (este)**
- Datos: `research/dip_crypto_touches.csv` (entry/exit/dur por trade)

*Última actualización: 2026-06-25/26 — Oscar + Claude Code.*
