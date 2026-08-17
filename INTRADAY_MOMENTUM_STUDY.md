# ⏱️ Estudio: Momentum de continuación intradía (¿estrategia diaria?)
# Doc vivo · LOCAL (no se commitea ni despliega) · Inicio: 2026-06-24 · Dueño: Oscar

## 💡 La idea (Oscar, 2026-06-24)
Si la dirección del día se DEFINE a cierta hora y luego SE MANTIENE, se podría **entrar a esa hora
montándose a la dirección ya marcada y salir con una ganancia leve pero probable** — un edge de
continuación intradía, repetible a diario. Origen: la herramienta `daily_shape.py` mostró que el
"% de días en que el signo a las 2h ya coincide con el del cierre" llega a ~80-87% (WDC, LITE, NVDA,
TSLA, AAPL, QQQ) y sube monótonamente durante el día; el lock-in mediano cae temprano (9:54-10:27).

## 🎯 Plan por fases
- **FASE 1 (en curso) — Identificar candidatos.** Barrer toda la watchlist (~69 tickers) del último
  ~mes/2 meses y rankear quién tiene la tendencia mejor definida: a qué hora entrar, hasta qué hora
  aguanta, win-rate y amplitud media capturada. Herramienta: `research/daily_shape_scan.py` →
  `research/daily_shape_scan_results.csv`. Métrica: continuación = signo del movimiento acumulado en
  t_in × (y_t_out − y_t_in), mejor ventana (t_in,t_out) por amplitud media con win≥55%.
- **FASE 2 (pendiente) — Backtest a fondo de los mejores.** ¿Sobrevive a costos/spread/fees? ¿Cuánta
  amplitud real (1%? 2%?)? ¿Aguanta días malos? Señales de alerta para NO operar (régimen/centinela).
  Segmentar por día de semana y por régimen (risk-on/off). Detectar patrones curiosos ("siempre sube
  hasta las 10am y luego sigue o baja pero el cierre es verde", etc.).

## ⚠️ Caveats conocidos (honestidad)
- La señal "seguir el movimiento-hasta-ahora" FAVORECE tickers en tendencia durante la ventana medida
  (en un mes alcista, longs ganan). Por eso el screener reporta **% días verdes**: si win% alto va con
  verde% ~50 → continuación genuina; si verde% muy alto → puede ser solo sesgo direccional del período.
- La ventana 2026 (abr-jun) trae régimen con tendencia/selloff → los niveles (80% @ 2h) pueden estar
  inflados; la FORMA del hallazgo (predictibilidad sube durante el día) es robusta, el NIVEL no.
- Amplitudes "leves" (~1%) deben sobrevivir a costos en FASE 2 antes de declarar edge.

## 🧰 Herramientas (local, research/)
- `daily_shape.py` — superpone días normalizados a la apertura; toggle detrend (forma pura), filtro
  por día de semana, curva promedio, panel de compromiso direccional. Salida: `data/daily_shape.html`.
- `daily_shape_scan.py` — screener multi-ticker → CSV + leaderboard. Uso:
  `python research/daily_shape_scan.py TK1,TK2,... [lookback_days]`.

## 🔁 Bitácora
- **2026-06-24** — FASE 1 completada: barrido de 69 tickers (watchlist VM) a 60 días
  (`daily_shape_scan_results.csv`). Hallazgos: (1) la hora de entrada se agrupa 10:00-11:00 ET en
  casi todos; (2) `agree@2h` 75-95% generalizado. Shortlist por calidad (win≥62, amp≥0.6, verde
  balanceado, excluidos ilíquidos/meme): **APLD, INOD, RIVN, AVAV, IREN, AKAM, MRVL, NBIS**. APLD
  el más limpio (72% win, +0.92%, 11:00→12:30). Caveats: ventana elegida in-sample (optimista),
  amplitudes grandes = trampa de liquidez (HYLN/WOLF/AMC), megacaps no dan amplitud (mueren a costos).
- **2026-06-24** — FASE 2 lanzada: `intraday_backtest.py` sobre el shortlist a 180 días, split
  train/test 60/40 (ventana elegida SOLO en train, evaluada en test out-of-sample), costos
  0.05/0.10/0.20%, baseline siempre-long, segmentación por día de semana y régimen QQQ, equity+maxDD.
  RESULTADO: el edge NO sobrevive OOS en 6/8 (overfitting clásico — los de mejor train, IREN/NBIS,
  peor test). Solo **APLD** (+0.54% neto test, supera baseline buy&hold +0.35%) y **AVAV** (+0.22%,
  baseline −0.18% → la señal aporta +0.40%) sobreviven, pero modestos (Sharpe 0.13-0.23), n chico,
  y el test entero fue régimen QQQ alcista (QQQ<SMA50 vacío → sin prueba en bajista). 2/8 ≈ borde del azar.

- **2026-06-24** — FASE 3 (pivote): Oscar operará en **Binance Futures perps tradfi** (24/7, comisión
  baja ~0.04%/lado, sin funding por ser intradía rápido) — NO eToro. Reenfoque a **estacionalidad
  horaria 24/7** (no solo horario de mercado): `binance_hourly_scan.py` barre cada hora de entrada
  (hora Lima) × duración de hold, busca ventanas con amplitud > costo, y filtra por robustez
  1ª-mitad-vs-2ª-mitad. OJO: APLD y AVAV (sobrevivientes Fase 2) NO tienen perp en Binance.
  Tickers con perp y >=30d (20): BB(200d), TSLA(148), HOOD(143), INTC(143), MSTR(136), PLTR(136),
  NVDA(91), TSM(80), MU(79), SNDK(79), AVGO(66), AMD(50), LITE/MRVL(41), RKLB(38), ARM/BE/COHR/NBIS/WDC(30).
  RESULTADO: edge overnight-long CONFIRMADO en semis (no en no-semis). Núcleo: SNDK/MU/MRVL +0.9-1.1%
  neto, INTC +0.51 (más historial), TSM +0.25 (win66), AMD +0.46. No-semis (TSLA/HOOD/MSTR/PLTR/BB)
  fallan → es efecto SECTORIAL, no prima universal. Régimen (Binance corto): rendía más tras días rojos.

- **2026-06-24 — Cross-check Alpaca 4 años (overnight_xcheck.py):** el efecto overnight de semis es
  DURABLE (+0.20%/día semis vs +0.07% controles, 4 años) y el overnight > intradía. PERO recientemente
  AMPLIFICADO 3-6× (últimos 90d) → los +1% de Binance son reales pero recientes; revierten hacia ~+0.2%.
  CORRECCIÓN: el "rebote tras días rojos" NO se sostiene en 4 años (ahí es más fuerte tras VERDES) → era
  artefacto del trimestre. Mecanismo = prima/momentum overnight, no rebote post-caída.

- **2026-06-24 — Ventana óptima por ticker (binance_best_window.py)** + **filtro/sizing** + **stop test**:
  · Ventanas LONG (Lima): SNDK 00→12, MU 00→12, MRVL 21→06, INTC 07→14, AMD 23→09, TSM 16→04.
    INTC=mañana US, TSM=sesión Asia (mecánicamente coherente con su geografía).
  · FILTRO QQQ: no fiable. El dato reciente da MÁS tras QQQ rojo (rebote), pero contradice los 4 años →
    NO hard-codear filtro; operar incondicional. (Descarta la idea de "solo días QQQ verde".)
  · SIZING: edge ~0.2-1% vs cola de -8/-9% (SNDK/MU/INTC) → sin leverage o ≤2x, ~1% riesgo/trade,
    máx 2 posiciones simultáneas (correlacionadas), escalar con monitor de reversión.
  · STOP -3/-4%: RESTA (los dips recuperan antes del cierre) → sin stop; riesgo por sizing.

- **2026-06-24 — FASE 6 paper-forward DESPLEGADO en la VM** (sección "🧪 Paper Overnight"):
  `utils/intraday_paper.py` + thread IntradayPaper + `/api/intraday-paper` + tab dashboard. Abre registro
  a la hora de entrada de cada ticker, sigue %max/%min en vivo (ABIERTO/CERRADO), cierra con % final +
  contexto QQQ/acción día previo. Valida out-of-sample en tiempo real (la muestra de Binance es corta).
  Estado JSON: data/intraday_paper.json. PENDIENTE: acumular semanas + monitor de reversión automático.
