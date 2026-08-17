# OportunityAlert

**A 24/7 event-driven trading-signal system: multi-source ingestion → deterministic gating → LLM classification → alerting, plus a measurement layer that audits every signal it emits against the market.**

Built and operated solo over ~10 weeks (May–Aug 2026). ~35k lines of Python, 149 commits, deployed on an Oracle Cloud VM under `systemd` + cron, with a FastAPI dashboard.

The system ran live for 10 weeks and produced a **negative result**: none of its four signal generators survived forward testing. That outcome is documented below, in detail, because it is the most useful thing in this repository. The measurement infrastructure did exactly what it was designed to do — it killed three strategies that had passed backtest, before any real money was committed.

> **Language note:** the code, comments and internal design docs are in Spanish (this was a personal project). This README is in English. Identifiers and module structure are readable without Spanish.

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Engineering decisions worth reviewing](#engineering-decisions-worth-reviewing)
- [The measurement layer (the actual asset)](#the-measurement-layer-the-actual-asset)
- [Results: what the live data said](#results-what-the-live-data-said)
- [Repository map](#repository-map)
- [Running it](#running-it)
- [Operations](#operations)
- [Honest limitations](#honest-limitations)
- [Where to start reading](#where-to-start-reading)

---

## What it does

A single long-running Python process ingests financial events from several sources, decides in **pure code** whether an event is worth spending an LLM call on, asks the LLM only the narrow question code cannot answer, computes risk levels itself, and pushes an actionable alert to the operator's phone (WhatsApp/SMS). Everything it emits is recorded and later scored against what the market actually did.

**Sources:** SEC EDGAR (8-K Atom feed), Finnhub news, Alpaca news (Benzinga wire, WebSocket), Reddit, Truth Social, Federal Reserve and Treasury feeds.

**Signal arms** (four independent strategies, all measured by the same ledger):

| Arm | Idea | Status at shutdown |
|---|---|---|
| **News** | Classify catalysts (FDA, contracts, earnings, upgrades) → directional call | Radar useful, no predictive edge |
| **Marea** (momentum swing) | Rules-based 50-day breakout on a liquidity-ranked universe, chandelier exit | Paper-traded, underperformed QQQ |
| **Earnings (PED)** | Pre-earnings drift candidates + calendar + scoreboard | Barely tested (n=1 live) |
| **Dip / overnight** | Support-level scanner; overnight premium on semis; crypto-perp dip TP3/SL1 | Backtest-positive, forward-negative |

**Position tracking:** read-only eToro portfolio polling every 10 min, per-strategy exit advice (chandelier for momentum, time-stop for earnings), plus news lookup to explain sudden moves.

**Dashboard:** FastAPI + a single-page HTML front-end with 10 sections — daily brief, market movers, scoreboard, news, positions, watchlist, momentum pilot, earnings, dips and overnight paper trading. Each section carries an honesty badge (`🧪 in validation` / `📐 tool` / `📡 radar`) so no panel implies more confidence than the data supports.

---

## Architecture

```
  EDGAR · Finnhub · Alpaca wire · Reddit · Truth Social · Fed · Treasury
                              │
                    ┌─────────▼─────────┐
                    │ dedup (SQLite)    │  cross-source, 120-min window
                    └─────────┬─────────┘
                              │
   LAYER 1  keyword filter (tiered vocabulary, bullish + bearish)   score ≥ 3
                              │
   DETECT   detect_event_type()  →  event mode | normal mode
                              │
   LAYER 2  conviction gates — PURE CODE, no LLM, < 1s
              Gate 1  catalyst not already priced       (0 | 3 pts)
              Gate 2  technical confirmation            (0–3 pts)
                        normal: RSI14 / EMA20 / Bollinger on daily bars
                        event:  1-min candle stabilization analysis
              Gate 3  volume confirms                   (0 | 1 pt)
                              │
              score < 4  →  DISCARD, zero LLM cost  ────────────►  logged anyway
                              │
   LAYER 3  LLM call — narrowed prompt (~55% smaller than v1)
              asks ONLY: catalyst type, direction, magnitude %
              stops/targets are NOT asked — code computes them from ATR14
                              │
   LAYER 4  post-LLM gates
              Gate 4  playbook match (informational)
              Gate 5  portfolio gate (cash, sector concentration, already held)
              Gate 6  sector-regime gate (sector ETF rolling over → downgrade LONG)
                              │
              ┌───────────────┴───────────────┐
        EVENT MODE                       NORMAL MODE
   SMS 1 "watch, don't enter"         single SMS with entry/stop/target
   queue_followup(+7 min)
   SMS 2 re-fetches price, runs
   stabilization → ENTER / WAIT / DROP
                              │
                   ┌──────────▼──────────┐
                   │  scoreboard ledger  │  every signal, every arm
                   └──────────┬──────────┘
                     hourly resolver measures
                     abnormal return vs QQQ at
                     +15m / 1h / 4h / 24h / 48h
```

Around this, ~15 supervised daemon threads run on independent cadences: source pollers, position tracker, market sentinel, gap scanner, daily brief generator, scoreboard resolver, paper-trading loops, heartbeat.

---

## Engineering decisions worth reviewing

**1. Cost as a first-class architectural constraint.**
The core rule is *code before AI, always*. RSI, EMA, ATR, Bollinger bands, "is this already priced in", volume confirmation, stop/target sizing, post-spike stabilization — all implemented as pure functions over price bars ([`utils/conviction_gates.py`](utils/conviction_gates.py), [`utils/event_gate.py`](utils/event_gate.py)). The LLM is asked exactly one thing it is uniquely good at: classifying an unstructured news item into `{catalyst type, direction, magnitude}`. Result: ~60–70% of inbound articles are discarded before any API call, and the surviving prompt is ~55% smaller. This is a cost-of-inference design problem, and it was treated as one.

**2. Provider abstraction with tiering, not vendor lock-in.**
[`utils/ai_client.py`](utils/ai_client.py) is the single entry point for every LLM call in the system. Four back-ends (DeepSeek, GLM, Gemini, Claude) behind one interface, selected by env var, with **two tiers** — a reasoning model for financial decisions, a cheap non-reasoning model for summarization — resolved by `resolve_engine_model(tier)` with per-module overrides. The OpenAI-compatible providers are spoken to over plain REST with `requests` rather than pulling in another SDK. Migrating the whole system from Gemini to DeepSeek was a config change.

**3. Failure isolation and self-healing.**
`_supervised_thread()` wraps every loop: catches everything, exponential backoff 15s → 30s → 60s, and **resets the backoff if the thread ran stable for >10 min** (otherwise a thread that failed three times months ago would stay permanently degraded — a real bug that was found and fixed). Independently, each loop calls `_beat(name, interval)` on a successful cycle, and `_stale_threads()` reports any loop whose last good cycle is older than `max(3× its interval, 30 min)` — a watchdog that catches *silent* hangs, which the try/except cannot.

**4. Circuit breaker on the third-party broker API.**
[`utils/etoro_client.py`](utils/etoro_client.py): 5 consecutive failures opens the circuit for 30 minutes; 401/403 triggers a one-time token-expiry notification (deliberately not repeated, to avoid alert spam); `health_check()` is excluded from the breaker so heartbeats can't trip it. The symbol→instrument map (~15k instruments, ~4.5s cold fetch) is pre-warmed in a background thread at startup so that cost never lands on the first inbound news item.

**5. The broker integration is read-only by construction, and that was a deliberate reversal.**
An auto-trading module existed and was **removed** — moved to `_quarantine/` along with the position-lock, and its API endpoints deleted — after a fee audit ([`research/audit_fees.py`](research/audit_fees.py)) showed 1-minute scalping was net-negative after eToro commissions. Deprecated code lives in `_deprecated/` and `_quarantine/` with READMEs explaining *why* it was retired, rather than being silently deleted. Killing your own feature on evidence is a decision I'd want a reviewer to see.

**6. Idempotency and dedup everywhere.**
SQLite-backed article dedup with cross-source windowing; per-ticker locking on delayed follow-ups (one pending follow-up per ticker, guarded by `threading.Lock`); alert cooldown flags with TTL; one alert per `ticker + rule + day` for position advice; the scoreboard resolver is idempotent and re-derives outcomes from bars, so it can be re-run safely after downtime.

**7. Statistics done carefully, not decoratively.**
The dip scanner's "is this stock falling more than the market?" chip is a **beta-adjusted abnormal return**, not a raw comparison (`excess = ret_ticker − β × ret_SPY`, β from covariance of date-aligned daily returns, clamped 0.2–4.0). Without the beta adjustment every high-volatility name flags red in a normal market pullback — a false-positive class that would have made the panel useless for exactly the stocks it was built for. The scoreboard measures **abnormal** return versus QQQ, not raw return, for the same reason.

**8. Real-world API sharp edges, documented where they bite.**
Alpaca's bars endpoint returns a single bar without an explicit `start`/`end` window (fixed by `sort=desc` + range, then reversing). eToro's portfolio endpoint 404s on the obvious host — it requires `public-api.etoro.com` *and* the `/api/v1/` prefix. Its `user_key` is a base64 blob, not a JWT, so expiry cannot be read and must be inferred from 401/403. These are recorded next to the code that depends on them.

---

## The measurement layer (the actual asset)

Anyone can write a strategy. The part of this repository I would defend in an interview is [`utils/scoreboard.py`](utils/scoreboard.py) — a permanent, append-only ledger of every signal the system ever emitted, across all four arms.

When a signal fires, it is recorded `open` with its entry timestamp, price, direction, score, source, source latency, and the market regime at t0 (`risk_on`, `sector_rolling_over`, `sector_ret_5d`). An hourly resolver then measures, from bars:

- **Abnormal return vs QQQ** at +15m, +1h, +4h, +24h, +48h — so a call isn't credited for a market-wide rally.
- **MFE / MAE** (max favorable / adverse excursion) and time-to-peak — how much of the move was actually capturable.
- **Directional accuracy**, and the return *in the direction bet*, net of an assumed cost.
- Dual price venue: Binance perpetuals (24/7) with Alpaca SIP fallback, because a 24h horizon on a Friday signal is otherwise unmeasurable.

Two findings came directly out of this design rather than out of intuition:

- The primary horizon was moved **48h → 24h** after the ledger showed average abnormal drift of +0.13% at 1–4h, +0.08% at 24h and **−1.41% at 48h** — i.e. the original horizon was measuring decay, not signal.
- MFE was found to be **misleading as a success metric**: only ~11% of the peak excursion was capturable once a stop was respected. A naive dashboard would have reported the strategy as working.

Alongside it, [`utils/intraday_paper.py`](utils/intraday_paper.py) and [`pilot/`](pilot/) run **forward paper-trading** — signals executed at the next open against a persistent simulated portfolio — precisely to escape the survivorship and lookahead bias of the backtests in [`research/`](research/).

---

## Results: what the live data said

10 weeks of live operation, ~7.6 MB of collected data: **767 resolved signal outcomes, 795 alerts, 31,697 filter-log rows, 19,131 position snapshots, 1,066 crypto paper positions.**

**Verdict: no signal generator was confirmed profitable.** All three that passed backtest died in forward testing.

| Strategy | Sample | Live result |
|---|---|---|
| Dip-crypto TP3/SL1 (perps) | n = 1,057 | 20.4% win vs 27.5% breakeven; **−0.37%/trade** |
| Overnight premium (semis) | n = 238 | **−0.272%/trade**, −65% cumulative; identical in both halves |
| News / movers / brief | n = 754 | 47.6% directional; +0.13% at 4h, nil at 24h |
| Marea momentum pilot | 6 closed | −8.4% vs QQQ −6.7% |

The most instructive detail: the crypto strategy **loses money even with slippage set to zero** (−0.18%). Slippage measured −0.188%, inside the pre-registered GO zone — so the strategy was not killed by execution costs, as the hypothesis predicted. It was killed by the edge not existing out-of-sample. Pre-registering the GO/NO-GO criteria before collecting the data is what made that distinction possible.

**What was confirmed, and is reusable:**

1. The measurement infrastructure is honest — it killed three systems without risking capital.
2. Any news edge lives **< 4h** and is gone by 24h (measured three times independently).
3. Freshness is already maxed out: 96% of signals arrived with `age < 5 min`, so paying for faster feeds fixes nothing. This closed off an expensive dead end.
4. The conviction score **does not calibrate** (score 1 → 54% accuracy, score 6 → 50%, score 9 → 33%). Using it as a filter adds noise. A dashboard without calibration checking would have shipped it as a feature.
5. Sector regime is the only second-order variable with evidence: LONG accuracy 38.2% → 50.2% with the gate enabled.
6. Cross-cutting pattern, 3 for 3: **a high-Sharpe backtest over a thin, correlated edge does not survive out-of-sample.**

The project was then deliberately shut down (service stopped, crons removed, dataset backed up) rather than left burning API credits on strategies the data had already rejected.

---

## Repository map

```
main.py                   orchestrator: ~15 supervised threads, article pipeline (2.1k lines)
config.json               watchlist, thresholds, intervals

filters/
  keyword_filter.py       tiered bullish/bearish vocabulary, pre-LLM scoring
  claude_scorer.py        narrowed LLM prompt + JSON contract + code-injected levels
utils/
  ai_client.py            4 providers, 2 tiers, one interface
  conviction_gates.py     Gates 1–3: RSI/EMA/ATR/Bollinger from raw bars
  event_gate.py           event classification + 1-min stabilization analysis
  delayed_alerts.py       follow-up worker (deduped, +7 min re-evaluation)
  etoro_client.py         READ-ONLY broker client + circuit breaker
  scoreboard.py           permanent signal→outcome ledger (abnormal return, MFE/MAE)
  metrics_store.py        SQLite: 9 tables (alerts, gates, snapshots, trades, filter log…)
  dip_levels.py           support clustering, beta-adjusted relative strength
  position_strategy.py    per-strategy exit advisor (chandelier / time-stop / manual)
  daily_brief.py          LLM strategic brief + sector radar + contagion alerts
  market_sentinel.py      intraday sector-ETF drawdown trigger
  sector_regime.py        sector-regime gate for LONG signals
sources/                  EDGAR, Finnhub, Alpaca wire, Reddit, Truth Social, Fed, Treasury
pilot/                    rules-based momentum paper-trading (spec, engine, backfill, viz)
earnings/                 pre-earnings drift arm: calendar, strategies, runner
api/
  app.py                  FastAPI: 18 endpoints, auth, read + limited write
  dashboard.html          single-page dashboard, 10 sections with honesty badges
research/                 85 backtests and audits (not imported by runtime)
_deprecated/ _quarantine/ retired code kept with READMEs explaining why it was retired
```

Design documents (Spanish) track the reasoning over time: `CLAUDE.md` (system spec), `ESTADO_ACTUAL.md` (running engineering log), `SCOREBOARD_DIAGNOSIS.md`, `NEWS_REACTION_PLAN.md`, `pilot/PILOT_SPEC.md`, `DIP_CRYPTO_PAPER.md`, `INTRADAY_MOMENTUM_STUDY.md`.

---

## Running it

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.template .env          # fill in keys — nothing is hardcoded
```

Required: `DEEPSEEK_API_KEY` (or the key for whichever `AI_ENGINE` you pick), `FINNHUB_API_KEY`.
Optional but degraded without: `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (real-time prices), Twilio or CallMeBot credentials (alerts), `data/etoro_config.json` (portfolio — see `etoro_config.example.json`).

```bash
python main.py                 # the 24/7 process
python main.py status          # health snapshot
python _run_api.py             # dashboard on :8081
python show_metrics.py --days 7

python -m pilot.run_pilot      # momentum paper-trading day
python dip_scanner.py --quiet  # daily support scan
python research/backtest_premarket.py   # any research script, from repo root
```

The system degrades rather than crashes when a dependency is missing: no Alpaca key → gates fall back to neutral scores; no Twilio → alerts are logged only; no eToro config → the portfolio gate is skipped.

---

## Operations

Deployed on an Oracle Cloud ARM VM: `systemd` unit with `Restart=always` and journald logging, plus five cron jobs for the daily batch arms (pilot 22:00 UTC, earnings 22:15, dip scanner 22:25, …). [`deploy.sh`](deploy.sh) does pull → deps → **`py_compile` gate over every file** → restart → tail logs, so a syntax error never reaches a restart. Secrets live in `/etc/opportunity-alert.env`, outside the repo.

Observability is built for a phone, not a screen: a heartbeat loop reports thread health and stale loops; every log line carries a subsystem prefix (`[GATES]`, `[PositionTracker]`, `[eToro]`, `[EventGate]`) so `journalctl | grep` isolates a subsystem; and the dashboard exposes `/api/stats` and downloadable filter logs.

---

## Honest limitations

Stated plainly, since a reviewer will find them anyway:

- **No automated test suite.** There are five executable smoke/integration scripts (`test_alert.py`, `test_etoro_connection.py`, `test_position_strategy.py`, `test_macro_regime.py`, `test_adverse_news.py`) and a `py_compile` gate in the deploy path, but no pytest suite, no CI, no coverage. For a solo project whose correctness signal came from live market data this was a conscious trade; on a team it would be indefensible.
- **`main.py` is 2,078 lines.** The article pipeline and the thread orchestration should be separate modules. It grew organically and was never paid down.
- **Mixed languages.** Spanish comments/docs with English identifiers.
- **SQLite everywhere**, single-writer. Correct at this scale, would not survive concurrency growth.
- **No type checking or linting** in the pipeline.
- **The research scripts are exploratory quality** — many were written to answer one question and then abandoned. They are kept because they document how each conclusion was reached, not as production code.

---

## Where to start reading

If you have ten minutes, in this order:

1. [`utils/scoreboard.py`](utils/scoreboard.py) — the measurement ledger: why the horizon is 24h, why abnormal return, why dual price venue.
2. [`utils/conviction_gates.py`](utils/conviction_gates.py) — the cost-control layer; indicators from raw bars, no libraries.
3. [`main.py:199-276`](main.py) — `_beat` / `_stale_threads` / `_supervised_thread`: the supervision and watchdog primitives.
4. [`utils/ai_client.py`](utils/ai_client.py) — the provider/tier abstraction.
5. [`utils/dip_levels.py`](utils/dip_levels.py) — support clustering and the beta-adjusted relative-strength chip.

---

**Author:** Oscar Navarro · [oscar@pairus.ai](mailto:oscar@pairus.ai)
**Status:** archived 2026-08-03. Not financial advice; the system never executed an order automatically, by design.
