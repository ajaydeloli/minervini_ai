# PROJECT_DESIGN.md
# Minervini SEPA Stock Analysis System

> **Version:** 1.5.0  
> **Last Updated:** 2026-04-13  
> **Methodology:** Mark Minervini's Specific Entry Point Analysis (SEPA)  
> **Target Market:** NSE / Indian Equities (adaptable to any market)

---

## Table of Contents

1. [Project Vision](#1-project-vision)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Data Pipeline Design](#4-data-pipeline-design)
5. [Incremental Update Strategy](#5-incremental-update-strategy)
6. [Module Specifications](#6-module-specifications)
7. [Minervini Rule Engine](#7-minervini-rule-engine)
8. [LLM Integration Layer](#8-llm-integration-layer)
9. [Fundamentals Layer](#9-fundamentals-layer)
10. [News Sentiment Layer](#10-news-sentiment-layer)
11. [Paper Trading Simulator](#11-paper-trading-simulator)
12. [API Layer (FastAPI)](#12-api-layer-fastapi)
13. [Frontend](#13-frontend)
14. [Phase-by-Phase Roadmap](#14-phase-by-phase-roadmap)
15. [Technology Stack & Polars Upgrade Path](#15-technology-stack--polars-upgrade-path)
16. [Configuration & Environment](#16-configuration--environment)
17. [Testing Strategy](#17-testing-strategy)
18. [Deployment & Operations](#18-deployment--operations)
19. [Design Principles & Anti-Patterns](#19-design-principles--anti-patterns)

---

## 🏗️ Build Status

> **Last audited:** 2026-04-13 — Phase 12 complete.

| Phase | Name | Status | Tests | Notes |
|---|---|---|---|---|
| **1** | Foundation | ✅ **COMPLETE** | storage, ingestion, universe | `nse_bhav.py` intentionally deferred; yfinance covers all needs |
| **2** | Feature Engineering | ✅ **COMPLETE** | all feature unit tests passing | Formal 500-symbol benchmark not yet run (10-symbol bench exists) |
| **3** | Rule Engine | ✅ **COMPLETE** | 504+ passing | `screener/pipeline.py` + `screener/results.py` built here (moved from Phase 4) |
| **4** | Reports, Charts & Alerts | ✅ **COMPLETE** | daily_watchlist, telegram, risk_reward, email, webhook tests pass | All modules built and wired; `run_daily.py` → `runner.py` ✅; `risk_reward.py` wired ✅; `email_alert.py` ✅; `webhook_alert.py` ✅; Agg backend fix ✅ |
| **5** | Fundamentals & News | ✅ **COMPLETE** | fundamentals + news unit tests pass | Screener.in scraper + 7-day cache; 7-condition fundamental template; RSS keyword scorer; wired into scorer + pipeline |
| **6** | LLM Narrative Layer | ✅ **COMPLETE** | llm explainer unit tests pass | All providers built; explainer wired into runner Step 5b; narrative column in HTML report; graceful degradation confirmed |
| **7** | Paper Trading Simulator | ✅ **COMPLETE** | 29 unit tests passing | `portfolio.py` + `order_queue.py` + `simulator.py` + `report.py`; wired into `pipeline/runner.py`; IST market-hours aware; pyramiding logic |
| **8** | Backtesting Engine | ✅ **COMPLETE** | backtest unit tests passing | `engine.py` + `portfolio.py` + `metrics.py` + `regime.py` + `report.py`; `backtest_runner.py` CLI; walk-forward; trailing stop with VCP floor; NSE regime calendar; parameter sweep |
| **9** | Hardening & Production | ✅ **COMPLETE** | run_history, run_meta, benchmark tests pass | Prometheus endpoint not built (optional); no GitHub Actions CI (local `make test` satisfies spec); all other deliverables complete |
| **10** | API Layer (FastAPI) | ✅ **COMPLETE** | 21 unit tests passing (20 required + 1 slow) | `python-multipart` installed; all routers tested with `TestClient`; DB mocked in-memory; auth + rate-limit verified |
| **11** | Streamlit Dashboard MVP | ✅ **COMPLETE** | No unit tests (UI layer — Streamlit convention) | `dashboard/app.py` + 5 pages + 3 components + `deploy/minervini-dashboard.service`; dark-theme, watchlist ★ badge, file-upload, [Run Now] trigger, candlestick + MA + VCP chart, TT checklist, fund. scorecard, paper portfolio, backtest viewer |
| **12** | Next.js Production Frontend | ✅ **COMPLETE** | No unit tests (UI layer) |


### What Was Built in Phase 12

| Deliverable | File | Notes | Status |
|---|---|---|---|
| Vercel config | `frontend/vercel.json` | `buildCommand`, `outputDirectory`, `framework`, env secret references (`@minervini_api_url`, `@minervini_read_key`, `@minervini_admin_key`) | ✅ |
| Next.js config migration | `frontend/next.config.mjs` | Converted `next.config.ts` → `.mjs` for Next.js 14 compatibility; rewrites proxy + image remote patterns preserved | ✅ |
| Password gate middleware | `frontend/middleware.ts` | Optional HttpOnly cookie gate; activates only when `NEXT_PUBLIC_REQUIRE_AUTH=true`; no-op otherwise | ✅ |
| Login page | `frontend/app/login/page.tsx` | Minimal password form; submits to `/api/auth/login`; redirects to `/` on success | ✅ |
| Login Route Handler | `frontend/app/api/auth/login/route.ts` | `POST /api/auth/login`; validates against `SITE_PASSWORD` env var; sets HttpOnly cookie (7-day TTL) | ✅ |
| Dashboard mobile fix | `frontend/app/page.tsx` | Best setups table: Stage, RS, VCP, BO columns hidden on mobile (`hidden sm:table-cell`); only Symbol, Score, Quality shown | ✅ |
| Symbol page mobile fix | `frontend/app/screener/[symbol]/page.tsx` | Tab labels abbreviated on mobile: `Trend Template→TT`, `Fundamentals→Fund`, `AI Brief→AI`; tooltip formatter type-fixed | ✅ |
| EquityCurve min-height | `frontend/components/EquityCurve.tsx` | Chart wrapper div gets `min-h-[250px] h-[250px]`; `ResponsiveContainer` uses `height="100%"` | ✅ |
| 404 page | `frontend/app/not-found.tsx` | "This symbol or page doesn't exist." with ← Back to Screener button | ✅ |
| Global error boundary | `frontend/app/error.tsx` | Client component; shows error message + digest + [Retry] + [Go to Dashboard] buttons | ✅ |
| Screener skeleton | `frontend/app/screener/loading.tsx` | Page-level pulse skeleton: header, filter bar, result count, 12 table rows | ✅ |
| Watchlist skeleton | `frontend/app/watchlist/loading.tsx` | Three-section skeleton: add-symbols card, watchlist table rows, results placeholder | ✅ |
| Portfolio skeleton | `frontend/app/portfolio/loading.tsx` | 2×3 KPI grid, 250px chart placeholder, tab bar, 5 trade rows | ✅ |
| README rewrite | `frontend/README.md` | Prerequisites, local dev setup, env var table, optional auth gate, Vercel deployment steps, architecture diagram, scripts table, project structure | ✅ |
| Production build | — | `npm run build` → 0 TypeScript errors; 12 routes compiled (7 static, 3 dynamic, 2 API) | ✅ |

### What Was Built in Phase 10

| Deliverable | File | Notes | Status |
|---|---|---|---|
| FastAPI test suite | `tests/unit/test_api.py` | 21 tests — health, stocks, watchlist, portfolio, run, auth, rate-limit | ✅ |
| `python-multipart` dep fix | `.venv` | Required by FastAPI form endpoints in `watchlist.py`; was missing from venv | ✅ |
| Health router tests (4) | tests 01–04 | Status valid/degraded, meta with/without read key | ✅ |
| Stocks router tests (4) | tests 05–08 | Top list, quality filter, symbol detail, auth guard | ✅ |
| Watchlist router tests (6) | tests 09–14 | GET, POST valid/invalid symbol, bulk add, DELETE, auth guards | ✅ |
| Portfolio router tests (2) | tests 15–16 | Portfolio summary + open trades filter | ✅ |
| Run endpoint tests (2) | tests 17–18 | Admin-only 202 accepted; read-key 403 forbidden | ✅ |
| Auth tests (2) | tests 19–20 | Wrong key → 403; open mode (no env keys set) → 200 without key | ✅ |
| Rate-limit test (1, slow) | test 21 | 101 burst GETs → at least one 429; marked `@pytest.mark.slow` | ✅ |

### What Was Built in Phase 11

| Deliverable | File | Notes | Status |
|---|---|---|---|
| Streamlit entry point | `dashboard/app.py` | Dark-theme CSS injection (JetBrains Mono, CSS vars); sidebar with market status bar, last-run info, quick stats (A+/A/watchlist counts); home page KPI row + A+ preview table | ✅ |
| Watchlist page | `dashboard/pages/01_Watchlist.py` | File upload widget (CSV/JSON/XLSX/TXT); manual symbol entry; persistent watchlist table with ★ badge; [Run Watchlist Now] button → `POST /api/v1/run`; today's results with watchlist symbols first | ✅ |
| Screener page | `dashboard/pages/02_Screener.py` | Full universe results table; quality/stage/RS/sector filters; export to CSV; watchlist filter checkbox | ✅ |
| Stock deep-dive page | `dashboard/pages/03_Stock.py` | Cached PNG → live mplfinance fallback; score gauge + stage + RS badge; tabbed detail: TT checklist, fundamentals, news sentiment, LLM brief, score history | ✅ |
| Portfolio page | `dashboard/pages/04_Portfolio.py` | 5 KPI cards (total value, return%, win rate, open positions, realised P&L); open positions table; closed trades history; cumulative P&L equity curve | ✅ |
| Backtest page | `dashboard/pages/05_Backtest.py` | Backtest equity curve with regime shading (Bull/Bear/Sideways); per-regime stats table; parameter sweep comparison | ✅ |
| Charts component | `dashboard/components/charts.py` | `render_candlestick_chart()` — mplfinance + MA ribbon + VCP gold zone + stage label + quality badge + entry/stop hlines; `render_cached_chart()` — serve pre-built PNGs; `render_equity_curve()` — cumulative P&L; `render_backtest_equity_curve()` — regime-shaded portfolio curve | ✅ |
| Tables component | `dashboard/components/tables.py` | `render_sepa_results_table()` — pandas Styler + quality/score colour coding + ★ watchlist row highlight + WL filter checkbox; `render_portfolio_table()` — open positions with P&L colour; `render_trades_history_table()` — closed trades + R-Multiple colour; `render_backtest_summary_table()` — regime breakdown | ✅ |
| Metrics component | `dashboard/components/metrics.py` | `render_score_gauge()` — metric + progress bar + quality badge; `render_trend_template_checklist()` — 8-condition two-column grid; `render_fundamental_scorecard()` — 7-condition checklist; `render_vcp_summary()` — grade + metric cards; `render_run_summary_kpis()` — 4 KPI row; `render_portfolio_kpis()` — 5 KPI row | ✅ |
| systemd service | `deploy/minervini-dashboard.service` | `Type=simple; Restart=always`; `streamlit run dashboard/app.py --server.port 8501 --server.headless true`; depends on `minervini-api.service` | ✅ |

### What Was Built in Phase 3

| Module | File | Key Capability | Status |
|---|---|---|---|
| Stage detection | `rules/stage.py` | Hard gate — Stage 1/2/3/4 with confidence score; NaN → RuleEngineError | ✅ |
| Trend Template | `rules/trend_template.py` | All 8 Minervini conditions; configurable thresholds; fail-loud | ✅ |
| VCP qualification | `rules/vcp_rules.py` | Grade A/B/C/FAIL + 0–100 score; passes through feature-layer failures | ✅ |
| Entry trigger | `rules/entry_trigger.py` | Pivot breakout + volume confirmation; NaN pivot → graceful non-trigger | ✅ |
| Stop loss | `rules/stop_loss.py` | VCP base-low (primary) + ATR fallback + max-risk cap | ✅ |
| SEPA scorer | `rules/scorer.py` | Weighted composite 0–100; Stage 2 hard gate; SEPAResult + to_dict() | ✅ |
| Screener pipeline | `screener/pipeline.py` | `run_screen()` with ProcessPoolExecutor parallel execution | ✅ |
| Screener results | `screener/results.py` | `persist_results()` → `sepa_results` SQLite table; `load_results()` query helper | ✅ |
| Stage unit tests | `tests/unit/test_stage_detection.py` | 25 tests — all 4 stages, NaN errors, parametrized condition failures | ✅ |
| Scorer unit tests | `tests/unit/test_scorer.py` | 19 tests — hard gate, A+ logic, weighted sum, stop/risk propagation | ✅ |
| Screener pipeline tests | `tests/unit/test_screener_pipeline.py` | 14 tests — stage gate, None handling, sort order, mock executor | ✅ |
| Screener results tests | `tests/unit/test_screener_results.py` | persist + load + duplicate-skip tests | ✅ |
| Integration tests | `tests/integration/test_known_setups.py` | 6 regression tests — Stage 4 blocked despite all-8-TT-pass, A+ pipeline, partial-TT | ✅ |

### What Was Built in Phase 4 (Audit Result)

| Module | File | Status | Notes |
|---|---|---|---|
| R:R estimator | `rules/risk_reward.py` | ✅ Complete | `compute_rr()` called in `_screen_single()` after `compute_stop_loss()`; `rr_ratio`, `target_price`, `reward_pct`, `has_resistance` wired into `SEPAResult`, `to_dict()`, and `sepa_results` schema |
| Batch screener wiring | `screener/pipeline.py` | ✅ Complete | Already listed under Phase 3 above |
| Results persistence | `screener/results.py` | ✅ Complete | Already listed under Phase 3 above |
| Daily watchlist report | `reports/daily_watchlist.py` | ✅ Complete | CSV + HTML; Jinja2; watchlist priority sort |
| HTML template | `reports/templates/watchlist.html.j2` | ✅ Complete | Dark-mode, A+/A table, badges, star marker |
| Chart generator | `reports/chart_generator.py` | ✅ Complete | Candlestick + MA ribbon + stage + quality badge + entry/stop + VCP base zone rectangle; Agg backend `matplotlib.use("Agg")` moved to top of `_generate_chart_impl()` before mplfinance import |
| Telegram alerts | `alerts/telegram_alert.py` | ✅ Complete | MarkdownV2, watchlist star, quality filter, error handling |
| Email alerts | `alerts/email_alert.py` | ✅ Complete | `EmailAlert(BaseAlert)` with SMTP (port 587 STARTTLS / 465 SSL); multipart plain+HTML; same send() interface as TelegramAlert; credentials from env vars |
| Webhook alerts | `alerts/webhook_alert.py` | ✅ Complete | `WebhookAlert(BaseAlert)`; Slack-compatible JSON blocks + plain format; multi-URL; partial-failure counting |
| Pipeline runner | `pipeline/runner.py` | ✅ Complete | 13-step orchestrator; all outputs wired |
| Scheduler | `pipeline/scheduler.py` | ✅ Complete | APScheduler Mon–Fri 15:35 IST |
| `run_daily.py` Phase 4 wiring | `scripts/run_daily.py` | ✅ Complete | CLI delegates to `pipeline.runner.run(context)`; `RunContext` built from CLI args; all 13 steps (features → screen → reports → charts → Telegram + Email + Webhook → finish_run) fire on every `python scripts/run_daily.py` call |
| Risk/reward unit tests | `tests/unit/test_risk_reward.py` | ✅ 30+ tests | All target fallback paths covered |
| Watchlist report tests | `tests/unit/test_daily_watchlist.py` | ✅ 5 tests | CSV columns, HTML generation, sort order |
| Telegram alert tests | `tests/unit/test_telegram_alert.py` | ✅ 10 tests | Disabled path, filter, star prefix, HTTP errors |

### Phase 4 Completed Fixes (Applied 2026-04-09)

All six remaining items from the original Phase 4 audit have been resolved:

1. ✅ **[Priority 1] Wire `scripts/run_daily.py` → `pipeline/runner.py`** — `run_daily.py` now builds a `RunContext` from CLI args and delegates all pipeline work to `pipeline_run(context)`. Reports, charts, and all three alert channels fire on every CLI / scheduled run.
2. ✅ **[Priority 2] Wire `rules/risk_reward.py` into `screener/pipeline._screen_single()`** — `compute_rr()` is called at step 8b after `compute_stop_loss()`. `SEPAResult` extended with `rr_ratio`, `target_price`, `reward_pct`, `has_resistance`. `to_dict()` updated. `sepa_results` table includes all four columns (with migration guard for pre-Phase-4 databases).
3. ✅ **[Priority 3] Fix `chart_generator.py` Agg backend bug** — `import matplotlib; matplotlib.use("Agg")` moved to the very top of `_generate_chart_impl()`, before `import mplfinance`. Headless rendering now works on all servers.
4. ✅ **[Priority 4] Build `alerts/email_alert.py`** — `EmailAlert(BaseAlert)` implemented with SMTP STARTTLS/SSL, multipart plain-text + HTML bodies, quality filter, watchlist-star logic. Wired into pipeline/runner.py Step 11b.
5. ✅ **VCP base zone drawn on charts** — Shaded gold rectangle (`alpha=0.08`) + dashed border (`alpha=0.6`) now drawn in `_generate_chart_impl()` when `vcp_qualified=True`, spanning `base_bars` candles from entry pivot to `base_window_low`.
6. ✅ **Build `alerts/webhook_alert.py`** — `WebhookAlert(BaseAlert)` dispatches Slack-compatible JSON blocks (or plain JSON) to one or more webhook URLs. Wired into pipeline/runner.py Step 11c.

### Remaining Work Before Phase 5

Phase 4 is **fully complete**. The only outstanding items are enhancements beyond the original scope:

- **Email alert unit tests** — `tests/unit/test_email_alert.py` (mirrors pattern of `test_telegram_alert.py`)
- **Webhook alert unit tests** — `tests/unit/test_webhook_alert.py`
- **Formal 500-symbol feature benchmark** — performance baseline for Phase 2 (10-symbol bench exists)

### What Was Built in Phase 5

| Module | File | Key Capability | Status |
|---|---|---|---|
| Fundamentals scraper | `ingestion/fundamentals.py` | Screener.in HTTP scraper; 7-day JSON cache per symbol in `data/fundamentals/`; returns PE, ROE, D/E, EPS values, sales growth, promoter holding | ✅ |
| Fundamental template | `rules/fundamental_template.py` | 7 Minervini fundamental conditions (EPS positive, EPS accelerating, sales growth ≥10%, ROE ≥15%, D/E ≤1.0, promoter holding ≥35%, positive profit growth); `FundamentalResult` with `passes`, `conditions_met`, `conditions` dict, `fundamental_score` | ✅ |
| News ingestion | `ingestion/news.py` | RSS feed fetcher (MoneyControl, ET, BS); 30-min cache; keyword sentiment scorer; `compute_news_score()` → −100..+100 float | ✅ |
| Symbol aliases | `config/symbol_aliases.yaml` | Alias map for news article matching (e.g. RELIANCE → "reliance industries", "ril") | ✅ |
| Scorer wiring | `rules/scorer.py` | `evaluate()` accepts `fundamental_result` and `news_score`; `fundamental_score` and rescaled `news_score_val` feed into weighted composite; `SEPAResult.fundamental_pass`, `.fundamental_details`, `.news_score` populated; `to_dict()` serialises all three | ✅ |
| HTML report | `reports/templates/watchlist.html.j2` | Fund. column: pass/fail badge + conditions met + ROE / D/E / EPS Accel / Sales / Promoter; News column: colour-coded Positive / Neutral / Negative / N/A badge; safe for empty `fundamental_details` | ✅ |
| CSV export | `reports/daily_watchlist.py` | `_CSV_COLUMNS` extended with `fundamental_pass`, `fundamental_details`, `news_score` | ✅ |
| Telegram alert | `alerts/telegram_alert.py` | Per-stock `Fundamentals: ✅ (N/7)` or `❌ (N/7)` or `N/A` line added after VCP/Breakout line | ✅ |
| Fundamentals unit tests | `tests/unit/test_fundamentals.py` | Known PE/ROE/EPS fixture values → expected pass/fail per condition | ✅ |
| News unit tests | `tests/unit/test_news.py` | Keyword scorer: bullish/bearish article fixtures → expected score ranges | ✅ |

---

## 1. Project Vision

### 1.1 Goal

Build a **production-grade, fully automated stock screening and analysis system** based on Mark Minervini's SEPA (Specific Entry Point Analysis) methodology. The system screens thousands of stocks daily, identifies Stage 2 breakout candidates, scores setups, generates human-readable trade briefs, and optionally triggers alerts.

### 1.2 What SEPA Requires (System Perspective)

Minervini's methodology demands the following computable signals:

| Criteria Category | Signals Required |
|---|---|
| **Trend Template** | 8 conditions: price vs. MAs, MA slopes, 52w high/low proximity |
| **Stage Detection** | Explicit Stage 1/2/3/4 classification — only Stage 2 is buyable |
| **Volatility Contraction Pattern (VCP)** | Pivot detection, contraction count, volume dry-up |
| **Relative Strength** | RS Rating vs. benchmark index (Nifty 500) |
| **Fundamentals** | EPS acceleration, sales growth, ROE, D/E, promoter holding (Phase 3) |
| **News Sentiment** | LLM-scored RSS + NewsData.io articles per symbol (Phase 3) |
| **Volume Confirmation** | Breakout volume vs. 50-day avg; accumulation/distribution |
| **Entry Trigger** | Pivot breakout with tight stop-loss |

### 1.3 Core Design Mandates

- **Rules are code, not prompts.** The Minervini rule engine is pure Python — deterministic and testable.
- **LLM is a narrator, not a decision maker.** AI generates explanatory text only; it never scores or filters.
- **Modularity first.** Every module has a single responsibility and can be swapped independently.
- **Reproducibility.** Every screen run is logged with inputs, outputs, and timestamps.
- **Fail loudly.** Data quality issues raise exceptions; they are never silently swallowed.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR                               │
│                    (pipeline/runner.py)                             │
└────────────────────────┬────────────────────────────────────────────┘
                         │ triggers
          ┌──────────────┼──────────────────┐
          ▼              ▼                  ▼
   ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
   │  INGESTION  │ │   FEATURES   │ │   SCREENER   │
   │  (data/)    │ │ (features/)  │ │  (screener/) │
   └──────┬──────┘ └──────┬───────┘ └──────┬───────┘
          │               │                │
          ▼               ▼                ▼
   ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
   │  Raw Store  │ │Feature Store │ │  Candidates  │
   │ (Parquet /  │ │(Parquet /    │ │  (JSON /     │
   │  SQLite)    │ │ SQLite)      │ │  SQLite)  ◀──┼──────────────┐
   └─────────────┘ └──────────────┘ └──────┬───────┘              │
                                           │                      │
                               ┌───────────┼───────────┐          │
                               ▼           ▼           ▼          │
                        ┌──────────┐ ┌─────────┐ ┌─────────┐     │
                        │ RULE     │ │  LLM    │ │ ALERTS  │     │
                        │ ENGINE   │ │EXPLAINER│ │(notif/) │     │
                        │(rules/)  │ │ (llm/)  │ │         │     │
                        └──────────┘ └─────────┘ └─────────┘     │
                               │           │                      │
                               └─────┬─────┘                      │
                                     ▼                            │
                              ┌─────────────┐                     │
                              │   REPORTS   │                     │
                              │ (reports/)  │                     │
                              └─────────────┘                     │
                                                                   │
                    ┌──────────────────────────────────────────────┘
                    │  reads SQLite + Parquet (never writes)
                    ▼
             ┌─────────────┐
             │  API LAYER  │
             │  (FastAPI)  │
             │  port 8000  │
             └──────┬──────┘
                    │ HTTP /api/v1/*
          ┌─────────┴─────────┐
          ▼                   ▼
   ┌─────────────┐     ┌──────────────┐
   │  STREAMLIT  │     │  NEXT.JS     │
   │  DASHBOARD  │     │  FRONTEND    │
   │  port 8501  │     │  (Vercel)    │
   └─────────────┘     └──────────────┘
```

### 2.1 Data Flow Summary

```
Raw OHLCV (NSE/Yahoo)
    │
    ▼ ingestion/
Validated + Cleaned Parquet (per symbol)
    │
    ▼ features/
Technical Indicators (MAs, ATR, RS, pivots, VCP metrics)
    │
    ▼ screener/
Trend Template Pass/Fail per symbol
    │
    ▼ rules/ (pure rule engine)
SEPA Score + VCP stage + setup quality tag
    │
    ├──▶ llm/explainer.py  →  Human-readable trade brief (optional)
    │
    ▼ reports/
Daily Watchlist + Alert Dispatch
```

---

## 3. Directory Structure

```
minervini_ai/
│
├── config/
│   ├── settings.yaml               # All tunable parameters
│   ├── universe.yaml               # Stock universe definition
│   └── logging.yaml                # Log levels per module
│
├── data/
│   ├── raw/                        # Immutable raw downloads (Parquet)
│   │   └── {symbol}/
│   │       └── YYYY-MM-DD.parquet
│   ├── processed/                  # Cleaned, validated OHLCV
│   │   └── {symbol}.parquet
│   ├── fundamentals/               # Screener.in cache (JSON, 7-day TTL)
│   │   └── {symbol}.json
│   ├── news/                       # News cache (JSON, 30-min TTL)
│   │   └── market_news.json
│   └── metadata/
│       └── symbol_info.csv         # Sector, industry, mktcap, listing date
│
├── ingestion/
│   ├── __init__.py
│   ├── base.py                     # Abstract DataSource interface
│   ├── nse_bhav.py                 # NSE Bhavcopy downloader
│   ├── yfinance_source.py          # yfinance adapter
│   ├── validator.py                # Schema + OHLCV sanity checks
│   ├── universe_loader.py          # Unified symbol resolver (universe + watchlist)
│   ├── fundamentals.py             # Screener.in scraper + 7-day cache
│   └── news.py                     # RSS + NewsData.io + LLM sentiment
│
├── features/
│   ├── __init__.py
│   ├── moving_averages.py          # SMA 10/21/50/150/200, EMA 21, slopes
│   ├── atr.py                      # Average True Range + % ATR
│   ├── relative_strength.py        # RS vs Nifty500 (Minervini RS Rating)
│   ├── volume.py                   # Vol ratios, accumulation/distribution
│   ├── pivot.py                    # Swing high/low pivot detection
│   ├── vcp.py                      # VCP pattern metrics (contractions, tightness)
│   └── feature_store.py            # Compute + persist features per symbol
│
├── rules/
│   ├── __init__.py
│   ├── stage.py                    # Stage 1/2/3/4 detection (explicit gate)
│   ├── trend_template.py           # All 8 Minervini Trend Template checks
│   ├── fundamental_template.py     # 7 Minervini fundamental conditions
│   ├── vcp_rules.py                # VCP qualification rules
│   ├── entry_trigger.py            # Pivot breakout detection
│   ├── stop_loss.py                # Stop calculation (VCP base_low / ATR)
│   ├── risk_reward.py              # R:R estimator
│   └── scorer.py                   # Aggregate score (0–100) + setup_quality tag
│
├── screener/
│   ├── __init__.py
│   ├── pipeline.py                 # Orchestrates feature → rules per batch
│   ├── batch.py                    # Parallel execution wrapper
│   └── results.py                  # Candidate model + persistence
│
├── paper_trading/
│   ├── __init__.py
│   ├── simulator.py                # Core engine: entry, exit, pyramiding
│   ├── portfolio.py                # Portfolio state + P&L tracking
│   ├── order_queue.py              # Pending order queue (market-hours aware)
│   └── report.py                   # Paper trading performance report
│
├── llm/
│   ├── __init__.py
│   ├── explainer.py                # Generates narrative from rule outputs (ONLY use of LLM)
│   ├── prompt_templates/
│   │   ├── trade_brief.j2          # Jinja2 template for trade brief
│   │   └── watchlist_summary.j2    # Daily watchlist narrative
│   └── llm_client.py               # Multi-provider adapter (Anthropic/OpenAI/Groq/Ollama)
│
├── pipeline/
│   ├── __init__.py
│   ├── runner.py                   # Main entry point: daily / backtest modes
│   ├── scheduler.py                # APScheduler / cron wrapper
│   └── context.py                  # RunContext: date, mode, config snapshot
│
├── backtest/
│   ├── __init__.py
│   ├── engine.py                   # Walk-forward backtester (trailing stop + regime)
│   ├── portfolio.py                # Position sizing + portfolio tracking
│   ├── metrics.py                  # CAGR, Sharpe, max drawdown, win rate
│   ├── regime.py                   # Market regime labelling (Bull/Bear/Sideways)
│   └── report.py                   # Backtest HTML/CSV report with regime breakdown
│
├── alerts/
│   ├── __init__.py
│   ├── base.py                     # Abstract Alert interface
│   ├── telegram_alert.py           # Telegram bot dispatcher
│   ├── email_alert.py              # SMTP alert
│   └── webhook_alert.py            # Generic webhook (Slack, Discord)
│
├── reports/
│   ├── __init__.py
│   ├── daily_watchlist.py          # Generate daily watchlist CSV + HTML
│   ├── chart_generator.py          # Candlestick + MA + VCP chart (matplotlib)
│   └── templates/
│       └── watchlist.html.j2       # HTML report template
│
├── storage/
│   ├── __init__.py
│   ├── parquet_store.py            # Read/write Parquet helpers (atomic append)
│   └── sqlite_store.py             # Results + run history in SQLite
│
├── utils/
│   ├── __init__.py
│   ├── logger.py                   # Structured logging setup
│   ├── date_utils.py               # Trading calendar utilities
│   ├── math_utils.py               # Pure numeric helpers (no pandas)
│   └── exceptions.py               # Custom exception hierarchy
│
├── tests/
│   ├── unit/
│   │   ├── test_trend_template.py
│   │   ├── test_stage_detection.py
│   │   ├── test_vcp_rules.py
│   │   ├── test_features.py
│   │   ├── test_fundamentals.py
│   │   ├── test_news.py
│   │   └── test_validator.py
│   ├── integration/
│   │   ├── test_pipeline_e2e.py
│   │   └── test_screener_batch.py
│   └── fixtures/
│       ├── sample_ohlcv.parquet    # Deterministic test data
│       └── sample_fundamentals.json
│
├── notebooks/
│   ├── 01_exploratory_analysis.ipynb
│   ├── 02_vcp_pattern_research.ipynb
│   ├── 03_backtest_analysis.ipynb
│   └── 04_regime_analysis.ipynb
│
├── api/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, CORS, startup events
│   ├── auth.py                     # X-API-Key authentication middleware
│   ├── rate_limit.py               # Per-IP rate limiting (slowapi)
│   ├── routers/
│   │   ├── stocks.py               # /api/v1/stocks/* endpoints
│   │   ├── watchlist.py            # /api/v1/watchlist endpoints
│   │   ├── portfolio.py            # /api/v1/portfolio endpoints
│   │   └── health.py               # /api/v1/health + /api/v1/meta
│   ├── schemas/
│   │   ├── stock.py                # Pydantic response models
│   │   ├── portfolio.py            # Paper trading response models
│   │   └── common.py               # APIResponse envelope, pagination
│   └── deps.py                     # Shared FastAPI dependencies
│
├── dashboard/
│   ├── app.py                      # Streamlit entry point
│   ├── pages/
│   │   ├── 01_Watchlist.py         # Daily A+/A candidates
│   │   ├── 02_Screener.py          # Full universe table with filters
│   │   ├── 03_Stock.py             # Single stock deep-dive
│   │   ├── 04_Portfolio.py         # Paper trading portfolio
│   │   └── 05_Backtest.py          # Backtest results viewer
│   └── components/
│       ├── charts.py               # mplfinance helpers
│       ├── tables.py               # Styled screener tables
│       └── metrics.py              # Score card widgets
│
├── frontend/                       # Next.js (Phase 12 — built after Streamlit MVP)
│   ├── app/
│   │   ├── page.tsx                # Dashboard home
│   │   ├── screener/page.tsx       # Full screener table
│   │   ├── screener/[symbol]/page.tsx
│   │   ├── watchlist/page.tsx
│   │   └── portfolio/page.tsx
│   ├── components/
│   │   ├── StockTable.tsx
│   │   ├── CandlestickChart.tsx    # lightweight-charts (TradingView)
│   │   ├── TrendTemplateCard.tsx
│   │   ├── VCPCard.tsx
│   │   ├── ScoreGauge.tsx
│   │   └── PortfolioSummary.tsx
│   ├── lib/
│   │   ├── api.ts                  # Typed API client
│   │   └── types.ts                # TypeScript types
│   └── public/
│
├── scripts/
│   ├── run_daily.py                # CLI: --date, --watchlist, --symbols, --watchlist-only, --scope
│   ├── bootstrap.py                # CLI: full history download + feature compute
│   ├── backtest_runner.py          # CLI: run backtest over date range
│   └── rebuild_features.py        # CLI: recompute all features from scratch
│
├── .env.example
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── Makefile
└── PROJECT_DESIGN.md              # ← this file
```

---

## 4. Data Pipeline Design

### 4.1 Stage 1 — Ingestion

```
Source (NSE Bhavcopy / yfinance)
    │
    ▼
ingestion/validator.py
    ├── Schema check (columns: date, open, high, low, close, volume)
    ├── OHLCV sanity (high >= low, close within [low, high], volume > 0)
    ├── Gap detection (missing trading days)
    └── Corporate action flags (bonus, split, dividend)
    │
    ▼
data/processed/{symbol}.parquet
    (adj_close, adj_open, adj_high, adj_low, volume)
```

**Key rule:** Raw data is written once and never modified. `data/raw/` is append-only. All cleaning happens in `data/processed/`.

### 4.2 Stage 2 — Feature Engineering

Each feature module is a **pure function**: `compute(df: pd.DataFrame, config: dict) -> pd.DataFrame`. No side effects. No global state.

```
data/processed/{symbol}.parquet
    │
    ▼ features/moving_averages.py
    │   SMA_10, SMA_21, SMA_50, SMA_150, SMA_200, EMA_21
    │   MA_slope_50, MA_slope_200 (linear regression over N days)
    │
    ▼ features/relative_strength.py
    │   RS_raw = symbol_return_63d / benchmark_return_63d
    │   RS_rating = percentile rank vs. universe (0–99)
    │
    ▼ features/atr.py
    │   ATR_14, ATR_pct (ATR as % of close)
    │
    ▼ features/volume.py
    │   vol_50d_avg, vol_ratio (today / 50d_avg)
    │   up_vol_days, down_vol_days (20d window)
    │   acc_dist_score
    │
    ▼ features/pivot.py
    │   swing_highs[], swing_lows[]
    │   last_pivot_high, last_pivot_low
    │
    ▼ features/vcp.py
        contraction_count, max_contraction_pct
        base_length_weeks, vol_dry_up_flag
        tightness_score (% range of last 3 weeks)
```

**Output:** `data/features/{symbol}.parquet` — a wide DataFrame with all indicators appended as columns.

### 4.3 Stage 3 — Rule Engine

The rule engine operates on the **most recent row** of each symbol's feature DataFrame. It outputs a structured result object — no DataFrames, no I/O.

**Stage detection runs first and is a hard gate.** A stock that fails Stage 2 classification is immediately eliminated — even if all 8 trend template conditions pass.

```python
# rules/scorer.py
@dataclass
class SEPAResult:
    symbol: str
    date: date
    stage: int                         # 1 / 2 / 3 / 4
    stage_label: str                   # "Stage 2 — Advancing"
    stage_confidence: int              # 0–100
    trend_template_pass: bool
    trend_template_details: dict[str, bool]   # all 8 conditions
    fundamental_pass: bool             # 7-condition fundamental template
    fundamental_details: dict[str, bool]
    vcp_qualified: bool
    vcp_details: dict[str, Any]
    breakout_triggered: bool
    entry_price: float | None
    stop_loss: float | None            # VCP base_low preferred; ATR fallback
    risk_pct: float | None
    rs_rating: int
    news_score: float | None           # -100 to +100, None if news disabled
    setup_quality: Literal["A+", "A", "B", "C", "FAIL"]
    score: int   # 0–100
```

### 4.4 Stage 4 — Fundamentals & News (Optional Enrichment)

These run after the rule engine pass/fail, enriching only candidates that passed Stage 2 + Trend Template. They are optional — if `fundamentals.enabled: false` in config, they are skipped entirely.

```
SEPAResult candidates (Stage 2 + TT pass)
    │
    ├──▶ ingestion/fundamentals.py   → PE, ROE, D/E, EPS accel, sales growth,
    │                                  FII trend, promoter holding
    │                                  (7-day cache → data/fundamentals/)
    │
    └──▶ ingestion/news.py           → RSS feeds + NewsData.io → LLM sentiment
                                       (30-min cache → data/news/)
```

### 4.5 Stage 5 — LLM Explainer (Narrative Only)

```python
# llm/explainer.py
def generate_trade_brief(result: SEPAResult, ohlcv_tail: pd.DataFrame) -> str:
    """
    Input:  A fully scored SEPAResult + recent price history
    Output: A plain-English trade brief string

    The LLM receives ONLY the structured result dict.
    It cannot modify scores, filters, or rankings.
    """
```

The LLM prompt contains the structured rule outputs and asks for a narrative explanation only. The prompt explicitly prohibits the model from making recommendations or changing the setup quality rating.

### 4.6 Stage 6 — Output & Alerts

```
SEPAResult list (sorted by score desc)
    │
    ├──▶ reports/daily_watchlist.py     → watchlist_{date}.csv
    │                                   → watchlist_{date}.html
    │
    ├──▶ reports/chart_generator.py     → charts/{symbol}_{date}.png
    │                                   (candlestick + MA ribbons + VCP markup)
    │
    ├──▶ alerts/telegram_alert.py       → Telegram message per A+/A setup
    │
    └──▶ storage/sqlite_store.py        → run_history table (auditable log)
```

---

## 5. Incremental Update Strategy

This is one of the most important operational decisions in the system. The difference between a **bootstrap run** (first-ever setup) and a **daily run** (every trading day) is enormous — both in time and in what work is actually needed.

### 5.1 Bootstrap vs. Daily Run

| Dimension | Bootstrap Run | Daily Run |
|---|---|---|
| **When** | Once, on first setup. Repeat monthly as sanity check. | Every trading day at 15:35 IST |
| **What it does** | Downloads full history (5–10 years), computes all features from scratch | Appends today's single OHLCV row, recomputes only the new indicator values |
| **Symbols** | All (~500 or ~2000) | All |
| **Data loaded per symbol** | Full history (1200–2500 rows) | Last 300 rows only (enough for SMA200 + lookback buffer) |
| **Estimated time (500 symbols)** | 5–15 min | ~30 seconds |
| **Estimated time (2000 symbols)** | 60–90 min | ~2–3 minutes |
| **Triggered by** | `python scripts/bootstrap.py` | `python scripts/run_daily.py --date today` (or systemd timer) |

The 60–90 minute figure only applies to the bootstrap. It runs **once**, ideally overnight on first setup. Daily runs are always incremental and fast.

### 5.2 How Incremental Updates Work

The feature store is the core of the incremental strategy. Each symbol's feature Parquet file is a **cumulative record** — it is never rewritten from scratch after bootstrap.

```
Daily flow per symbol:
─────────────────────────────────────────────────────────────
1. Download today's single OHLCV row from NSE Bhavcopy
2. Append to data/processed/{symbol}.parquet
3. Load ONLY the last N rows needed for computation:
      SMA_200      → needs 200 rows
      RS_rating    → needs 63 rows (quarterly return window)
      VCP metrics  → needs ~260 rows (52 weeks)
      ATR_14       → needs 14 rows
      ─────────────────────────────────────
      Buffer total → load last 300 rows max
4. Compute new indicator values for today's row only
5. Append the new feature row to data/features/{symbol}.parquet
6. Rule engine reads only the LAST ROW of the feature file
─────────────────────────────────────────────────────────────
```

**Key principle:** We never load 10 years of data just to compute today's SMA. We load a rolling 300-row window — a constant cost regardless of how old the dataset gets.

### 5.3 Feature Store Interface

```python
# features/feature_store.py

def bootstrap(symbol: str, config: AppConfig) -> None:
    """
    Full history computation. Run once on setup, or to repair corruption.
    Reads all of data/processed/{symbol}.parquet.
    Writes full data/features/{symbol}.parquet.
    """

def update(symbol: str, run_date: date, config: AppConfig) -> None:
    """
    Incremental daily update. Fast path — always use this for daily runs.
    Reads last 300 rows of data/processed/{symbol}.parquet.
    Appends exactly one new row to data/features/{symbol}.parquet.
    Raises FeatureStoreOutOfSyncError if today's row already exists (idempotent guard).
    """

def needs_bootstrap(symbol: str) -> bool:
    """
    Returns True if feature file is missing or corrupted.
    pipeline/runner.py calls this before update() and falls back to bootstrap() if needed.
    """
```

### 5.4 Runner Mode Logic

`pipeline/runner.py` dispatches to the right mode automatically:

```python
# pipeline/runner.py

def run(run_date: date, config: AppConfig) -> RunResult:
    universe = load_universe(config)

    for symbol in universe:
        if needs_bootstrap(symbol):
            logger.warning(f"{symbol}: feature store missing, running bootstrap")
            bootstrap(symbol, config)          # slow path, rare
        else:
            update(symbol, run_date, config)   # fast path, every day

    results = run_screen(universe, run_date, config)
    persist_results(results)
    dispatch_alerts(results, config)
    return RunResult(...)
```

The bootstrap fallback means **the daily runner is self-healing** — if a symbol's feature file is deleted or corrupted, it automatically rebuilds on the next run without manual intervention.

### 5.5 Scheduled Jobs

| Job | Schedule | Script | Notes |
|---|---|---|---|
| Daily screen | Mon–Fri 15:35 IST | `run_daily.py` | Incremental update + screen |
| Monthly bootstrap | 1st of month, 02:00 IST | `bootstrap.py --universe all` | Full recompute, sanity check |
| Weekend backtest | Saturday 03:00 IST | `backtest_runner.py` | Optional, resource-heavy |

All three are managed by `pipeline/scheduler.py` using APScheduler, and backed by systemd timers on ShreeVault.

### 5.6 Parquet Layout for Incremental Appends

The feature Parquet files use a **row-append pattern**. PyArrow's `write_to_dataset` with partitioning is deliberately avoided here — a single flat Parquet file per symbol is simpler and fast enough for 2000+ rows per symbol.

```python
# storage/parquet_store.py

def append_row(path: Path, new_row: pd.DataFrame) -> None:
    """
    Appends a single row to an existing Parquet file.
    Strategy: read → concat → write (atomic via temp file + rename).
    For files > 5000 rows, uses pyarrow ParquetWriter for efficiency.
    """
    if path.exists():
        existing = pd.read_parquet(path)
        updated = pd.concat([existing, new_row], ignore_index=False)
    else:
        updated = new_row

    tmp = path.with_suffix(".tmp.parquet")
    updated.to_parquet(tmp, index=True, engine="pyarrow")
    tmp.replace(path)   # atomic rename — no partial writes
```

The atomic rename ensures the file is never left in a corrupt state if the process is killed mid-write.

---

## 6. Module Specifications

### 6.1 `ingestion/base.py` — Abstract Data Source

```python
from abc import ABC, abstractmethod
import pandas as pd

class DataSource(ABC):
    @abstractmethod
    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Returns OHLCV DataFrame with DatetimeIndex."""
        ...

    @abstractmethod
    def fetch_universe(self) -> list[str]:
        """Returns list of all tradable symbols."""
        ...
```

All data sources implement this interface. New data providers (Zerodha, Breeze, etc.) require only a new adapter class — zero changes to pipeline logic.

### 6.2 `rules/trend_template.py` — Minervini's 8 Conditions

```python
def check_trend_template(row: pd.Series, config: TrendTemplateConfig) -> TrendTemplateResult:
    """
    Minervini Trend Template — all 8 conditions must pass.

    1. Price > SMA_150 AND Price > SMA_200
    2. SMA_150 > SMA_200
    3. SMA_200 trending up for at least 1 month (slope > 0)
    4. SMA_50 > SMA_150 AND SMA_50 > SMA_200
    5. Price > SMA_50
    6. Price >= 25% above 52-week low
    7. Price within 25% of 52-week high
    8. RS Rating >= 70 (top 30% of universe)

    Returns: TrendTemplateResult with pass/fail per condition.
    All thresholds are configurable via config.
    """
```

### 6.3 `features/vcp.py` — VCP Detection

A VCP (Volatility Contraction Pattern) is detected by finding:

1. **Pivot-to-pivot contractions:** Each successive correction is shallower than the previous.
2. **Volume dry-up:** Volume in later contractions is lower than in earlier contractions.
3. **Base tightness:** The price range in the final weeks narrows significantly.

The detector is designed as a **pluggable interface** from day one. The rule-based implementation is the default. A future CNN-based detector can be swapped in via config with zero changes to the screener or pipeline.

```python
# features/vcp.py

from abc import ABC, abstractmethod

class VCPDetector(ABC):
    """
    Abstract VCP detector interface.
    All implementations must return VCPMetrics — the screener never
    knows or cares which detector is running underneath.
    """
    @abstractmethod
    def detect(self, df: pd.DataFrame, config: VCPConfig) -> VCPMetrics: ...


class RuleBasedVCPDetector(VCPDetector):
    """
    Current default. Deterministic, auditable, zero dependencies.
    Uses pivot detection + contraction math + volume ratio analysis.

    Returns:
        contraction_count: int          # number of VCP legs (ideally 2–4)
        max_depth_pct: float            # deepest correction in base
        final_depth_pct: float          # shallowest (most recent) correction
        vol_contraction_ratio: float    # vol in last leg / vol in first leg
        base_length_weeks: int
        is_valid_vcp: bool              # passes all VCP qualification rules
    """
    def detect(self, df: pd.DataFrame, config: VCPConfig) -> VCPMetrics:
        ...


class CNNVCPDetector(VCPDetector):
    """
    Future upgrade — Phase 12+.
    Loads a trained CNN model and runs inference on a rendered chart image.
    Requires: labeled training data (paper trading results), PyTorch.
    Same VCPMetrics output — zero changes to screener or pipeline.
    DO NOT implement until 6+ months of paper trading labels are available.
    """
    def detect(self, df: pd.DataFrame, config: VCPConfig) -> VCPMetrics:
        ...


# Detector selected via config: vcp.detector: "rule_based" | "cnn"
DETECTORS = {
    "rule_based": RuleBasedVCPDetector,
    "cnn":        CNNVCPDetector,
}
```

### 6.4 `screener/pipeline.py` — Batch Screener

```python
def run_screen(
    universe: list[str],
    run_date: date,
    config: AppConfig,
    n_workers: int = 8
) -> list[SEPAResult]:
    """
    For each symbol in universe:
        1. Load features (lazy — only loads what rules need)
        2. Apply trend template
        3. If passes trend template → apply VCP rules
        4. If VCP qualified → check for breakout trigger
        5. Score and tag setup quality
    
    Uses ProcessPoolExecutor for CPU-bound feature computation.
    Returns list of SEPAResult sorted by score descending.
    """
```

---

### 6.5 Custom Watchlist — First-Class Concept

### The Two-List Model

The system maintains two distinct symbol lists that serve different purposes and should never be conflated:

```
Universe (config/universe.yaml)          Watchlist (SQLite: watchlist table)
────────────────────────────────         ──────────────────────────────────
Nifty 500 / NSE 2000                     Your personal curated symbols
Scanned every trading day                Scanned every trading day (priority)
Source of new opportunities              Symbols you're actively tracking
Changed rarely (monthly rebalance)       Changed frequently (add/remove anytime)
Defined in config file                   Persisted in SQLite, managed via CLI/API/UI
```

Both lists are scanned on every daily run. Watchlist results are shown first in reports and alerts, ranked above universe results of equal score.

### Entry Points — Three Ways to Provide a Custom Watchlist

**1. CLI — file flag (most flexible)**

```bash
# Analyse a specific file instead of (or alongside) the full universe
python scripts/run_daily.py --watchlist mylist.csv
python scripts/run_daily.py --watchlist mylist.json
python scripts/run_daily.py --watchlist mylist.xlsx

# Analyse inline symbols (quick ad-hoc check)
python scripts/run_daily.py --symbols "RELIANCE,TCS,INFY,DIXON"

# Watchlist only — skip full universe scan entirely
python scripts/run_daily.py --watchlist-only

# Watchlist + universe (default when watchlist exists)
python scripts/run_daily.py --date today
```

Supported file formats for `--watchlist`:

| Format | Structure | Example |
|---|---|---|
| `.csv` | One symbol per row, column named `symbol` or first column | `RELIANCE\nTCS\nDIXON` |
| `.json` | Array of strings | `["RELIANCE", "TCS", "DIXON"]` |
| `.xlsx` | First sheet, `symbol` column or column A | Standard Excel |
| `.txt` | One symbol per line | `RELIANCE\nTCS\n` |

**2. API — bulk upload + management**

```
── Watchlist Management ───────────────────────────────────────────────
GET    /api/v1/watchlist
       Returns all watchlist symbols with their latest SEPA scores.

POST   /api/v1/watchlist/{symbol}
       Add a single symbol. Returns updated watchlist.

DELETE /api/v1/watchlist/{symbol}
       Remove a symbol. Returns updated watchlist.

POST   /api/v1/watchlist/bulk
       Add multiple symbols at once.
       Body: { "symbols": ["RELIANCE", "TCS", "DIXON"] }

POST   /api/v1/watchlist/upload
       Upload a file (CSV / JSON / XLSX). Parses and merges into watchlist.
       Content-Type: multipart/form-data
       Returns: { "added": 12, "skipped": 2, "invalid": ["XYZ123"], "watchlist": [...] }

DELETE /api/v1/watchlist
       Clear entire watchlist (requires admin key).

── Watchlist-Scoped Run ───────────────────────────────────────────────
POST   /api/v1/run
       Body: { "scope": "watchlist" }   → analyse watchlist only
       Body: { "scope": "universe" }    → analyse full universe
       Body: { "scope": "all" }         → both (default)
       Body: { "symbols": ["RELIANCE", "TCS"] }  → inline ad-hoc list
       Requires admin key.
```

**3. Streamlit Dashboard — file upload widget**

```
Watchlist page
├── Market status bar (Nifty price, last run time)
├── ── Custom Watchlist ──────────────────────────────────────
│   ├── File upload widget (.csv / .json / .xlsx / .txt)
│   ├── Manual entry text box ("RELIANCE, TCS, DIXON")
│   ├── Current watchlist table (symbol, score, last updated)
│   ├── [Add Symbol] [Remove] [Clear All] buttons
│   └── [Run Watchlist Now] button → calls POST /api/v1/run scope=watchlist
├── ── Today's Results ───────────────────────────────────────
│   ├── Watchlist A+/A setups (shown first, highlighted)
│   ├── Universe A+/A setups
│   └── Telegram alert preview
```

### `ingestion/universe_loader.py` — Unified Symbol Resolver

The universe loader is the single place where all symbol sources are merged and deduplicated before a run:

```python
# ingestion/universe_loader.py

def resolve_symbols(
    config: AppConfig,
    cli_watchlist_file: Path | None = None,
    cli_symbols: list[str] | None = None,
    scope: Literal["all", "universe", "watchlist"] = "all",
) -> RunSymbols:
    """
    Resolves the final symbol list for a pipeline run.

    Priority and merge logic:
      1. cli_symbols (--symbols flag)  → highest priority, overrides everything
      2. cli_watchlist_file (--watchlist flag) → merged into persistent watchlist
      3. SQLite watchlist table → always included (unless scope="universe")
      4. config/universe.yaml → full universe (unless scope="watchlist")

    Returns RunSymbols:
        watchlist: list[str]    # from SQLite + CLI input (scanned first, shown first)
        universe:  list[str]    # from config/universe.yaml (filtered)
        all:       list[str]    # deduplicated union, watchlist symbols first
        scope:     str          # "all" | "universe" | "watchlist"
    """

def load_watchlist_file(path: Path) -> list[str]:
    """
    Parse a watchlist file (.csv / .json / .xlsx / .txt).
    Validates each symbol (uppercase, alphanumeric, 1–20 chars).
    Returns list of valid symbols. Logs and skips invalid entries.
    Raises WatchlistParseError if file is empty or unreadable.
    """

def validate_symbol(symbol: str) -> bool:
    """NSE symbol validation: uppercase letters + digits, 1–20 chars."""
```

### SQLite Watchlist Table

```sql
CREATE TABLE watchlist (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL UNIQUE,
    note        TEXT,                    -- optional user note ("strong breakout candidate")
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_via   TEXT NOT NULL,           -- 'cli' | 'api' | 'dashboard' | 'file_upload'
    last_score  REAL,                    -- latest SEPA score (updated after each run)
    last_quality TEXT,                   -- latest setup quality tag
    last_run_at TIMESTAMP
);
```

### Watchlist Behaviour in Reports and Alerts

- Watchlist symbols appear **first** in the daily HTML report with a "★ Watchlist" badge.
- Telegram alert always includes watchlist symbols that scored >= B, even if the universe scan produced more A+ setups.
- Chart files are always generated for watchlist symbols, regardless of score (so you can check any symbol you're tracking).
- Historical score tracking (`/api/v1/stock/{symbol}/history`) works for all watchlist symbols even if they never appear in universe scan results.

---

### 7.1 Stage Detection (Hard Gate — runs first)

Stage detection is the **first filter** in the rule engine. A stock not in Stage 2 is immediately eliminated regardless of trend template conditions. This prevents buying stocks that look technically OK but are actually topping or declining.

```python
# rules/stage.py
def detect_stage(df: pd.DataFrame, config: StageConfig) -> StageResult:
    """
    Classifies the stock into one of Minervini's 4 stages.
    Stage 2 is the ONLY stage where buying is permitted.

    Stage 1 — Basing / Neglect:
        Price below both MAs, MAs flat, range-bound. Wait.

    Stage 2 — Advancing / Momentum:  ← THE ONLY BUY STAGE
        Price > SMA50 > SMA200, both MAs trending up.
        MA200 slope > 0 over last 20 days.

    Stage 3 — Topping / Distribution:
        Price lost SMA50, still above SMA200, SMA50 declining.
        Tighten stops — do not initiate new positions.

    Stage 4 — Declining / Markdown:
        Price below both MAs, both MAs declining. Never buy.

    Returns StageResult with: stage (int), label (str),
        confidence (0–100), reason (str), ma_slopes.
    """
```

**Stage 2 criteria:**
- Price > SMA_50 AND Price > SMA_200
- SMA_50 > SMA_200 (stack correct)
- SMA_200 slope > 0 over last 20 trading days
- SMA_50 slope > 0 over last 10 trading days

### 7.2 Trend Template Conditions (Configurable Thresholds)

| # | Condition | Default Threshold | Config Key |
|---|---|---|---|
| 1 | Price > SMA_150 AND SMA_200 | strict | `tt.price_above_ma` |
| 2 | SMA_150 > SMA_200 | strict | `tt.ma_order` |
| 3 | SMA_200 slope up (N days) | 20 days | `tt.ma200_slope_lookback` |
| 4 | SMA_50 > SMA_150 AND SMA_200 | strict | `tt.ma50_order` |
| 5 | Price > SMA_50 | strict | `tt.price_above_50` |
| 6 | Price >= N% above 52w low | 25% | `tt.pct_above_52w_low` |
| 7 | Price within N% of 52w high | 25% | `tt.pct_below_52w_high` |
| 8 | RS Rating >= N | 70 | `tt.min_rs_rating` |

**Note on SMA_150:** SMA_150 must be explicitly computed in `features/moving_averages.py`. Do not fall back to a computed approximation — it requires exactly 150 rows of history.

### 7.3 VCP Qualification Rules

| Rule | Condition | Config Key |
|---|---|---|
| Detector | rule_based (default) / cnn (future) | `vcp.detector` |
| Min contractions | >= 2 legs | `vcp.min_contractions` |
| Declining depth | Each leg < previous | `vcp.require_declining_depth` |
| Volume dry-up | Last leg vol < first leg | `vcp.require_vol_contraction` |
| Base length | 3–52 weeks | `vcp.min_weeks`, `vcp.max_weeks` |
| Final tightness | Last 3 weeks range < 10% | `vcp.tightness_pct` |
| Max depth | Deepest leg <= 50% | `vcp.max_depth_pct` |

### 7.4 Setup Quality Scoring

The composite score is calculated from explicit, auditable weight constants. All weights sum to 1.0 and are configurable in `settings.yaml`.

```python
# rules/scorer.py — explicit weight constants (all configurable)
SCORE_WEIGHTS = {
    "rs_rating":    0.30,   # Relative Strength vs universe — most predictive
    "trend":        0.25,   # Trend Template conditions met / 8
    "vcp":          0.25,   # VCP quality + tightness + volume dry-up
    "volume":       0.10,   # Breakout volume + accumulation score
    "fundamental":  0.07,   # EPS accel + ROE + sales growth (Phase 5)
    "news":         0.03,   # News sentiment score (Phase 5)
}
# Stage 2 is a hard gate — non-Stage-2 scores 0 regardless of weights
```

```
Score breakdown (each component 0–100, then weighted):

RS Rating Score     (wt=0.30):  RS percentile rank mapped 0→100
Trend Score         (wt=0.25):  conditions_met / 8 × 100
VCP Score           (wt=0.25):  contraction quality + tightness + vol dry-up
Volume Score        (wt=0.10):  breakout vol ratio + acc/dist signal
Fundamental Score   (wt=0.07):  7-condition template score (Phase 5)
News Score          (wt=0.03):  -100→+100 sentiment, rescaled 0→100

Final = Σ (component × weight) × Stage2_gate
Stage2_gate = 1 if Stage 2, else 0

Setup Quality Tag:
  A+  →  Score >= 85 AND Stage 2 AND all 8 TT conditions pass AND VCP valid
  A   →  Score >= 70 AND Stage 2 AND all 8 conditions pass
  B   →  Score >= 55 AND Stage 2 AND >= 6 conditions pass
  C   →  Score >= 40 AND Stage 2
  FAIL → Not Stage 2 OR Score < 40 OR fewer than 6 conditions
```

---

## 8. LLM Integration Layer

### 8.1 Design Mandate

The LLM layer is **strictly isolated** from the rule engine. It:

- **Receives:** A fully computed `SEPAResult` dict + the last 20 rows of OHLCV.
- **Produces:** A plain-English narrative (trade brief, watchlist summary).
- **Never modifies** scores, rankings, or pass/fail outcomes.
- **Fails gracefully:** If LLM is unavailable, the pipeline continues without narratives.

### 8.2 Trade Brief Template (`llm/prompt_templates/trade_brief.j2`)

```
You are a stock analyst assistant explaining a Minervini SEPA setup.

Given the following structured analysis for {{ symbol }}:
- Setup Quality: {{ setup_quality }} (Score: {{ score }}/100)
- Stage: {{ stage_label }} (confidence {{ stage_confidence }}%)
- Trend Template: {{ trend_template_pass }} ({{ conditions_passed }}/8 conditions)
- VCP: {{ vcp_summary }}
- RS Rating: {{ rs_rating }} (top {{ 100 - rs_rating }}% of market)
- Fundamentals: {{ fundamental_summary }}
- News Sentiment: {{ news_score }}
- Breakout: {{ breakout_status }}
- Entry: {{ entry_price }}, Stop: {{ stop_loss }} (Risk: {{ risk_pct }}%)

Write a concise 3–4 sentence trade brief explaining WHY this setup is or is not 
notable. Focus on what the chart is saying technically. Do NOT make a buy/sell 
recommendation. Do NOT change the setup quality rating. Tone: professional, factual.
```

### 8.3 LLM Client Abstraction

```python
class LLMClient(ABC):
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 300) -> str: ...

class AnthropicClient(LLMClient): ...   # claude-haiku-4-5 (cheapest)
class OpenAIClient(LLMClient): ...
class GroqClient(LLMClient): ...        # llama-3.3-70b-versatile (free tier)
class OpenRouterClient(LLMClient): ... # deepseek-r1:free (best reasoning, free)
class OllamaClient(LLMClient): ...     # local model fallback (zero API cost)
```

Provider is selected via `config/settings.yaml:llm.provider`. **Groq is recommended as the default** — free, fast, and sufficient for narrative generation. Anthropic/OpenAI are available for higher quality when cost is acceptable.

---

## 9. Fundamentals Layer

### 9.1 Design

Fundamentals are fetched from Screener.in via HTTP scraping, cached for 7 days per symbol (fundamentals change quarterly, not daily), and evaluated against Minervini's 7 fundamental conditions. This layer runs **after** the rule engine — only on stocks that passed Stage 2 + Trend Template.

**Data source:** Screener.in (free, no API key required). Consolidated view preferred, standalone fallback.

### 9.2 `ingestion/fundamentals.py` — Screener.in Scraper

```python
def fetch_fundamentals(symbol: str, force_refresh: bool = False) -> dict | None:
    """
    Fetch and cache fundamental data from Screener.in.
    Cache TTL: 7 days (fundamentals change quarterly).
    Returns None gracefully if fetch fails — pipeline continues without it.

    Fields returned:
        pe_ratio, pb_ratio, roe, roce, debt_to_equity,
        promoter_holding, eps, eps_values (last 4 quarters),
        eps_growth_rates, eps_accelerating (bool),
        sales_growth_yoy (float %), profit_growth,
        fii_holding_pct, fii_trend ("rising" / "flat" / "falling"),
        latest_revenue, latest_profit
    """
```

### 9.3 `rules/fundamental_template.py` — 7 Fundamental Conditions

```python
def check_fundamental_template(fundamentals: dict) -> FundamentalResult:
    """
    Minervini-style 7 fundamental conditions (soft gate — informs score, does
    not block signal unless configured as a hard gate).

    F1: EPS positive           — latest EPS > 0
    F2: EPS accelerating       — most recent QoQ growth > previous QoQ growth
    F3: Sales growth >= 10% YoY
    F4: ROE >= 15%
    F5: D/E ratio <= 1.0
    F6: Promoter holding >= 35%
    F7: Positive profit growth

    Returns: passes (bool), conditions_met (0–7), hard_fails list,
             per-condition detail lines, all parsed numeric values.
    """
```

**Hard gate vs. soft gate:** By default, fundamentals are a **soft gate** — failing reduces the score but does not block the signal. Set `fundamentals.hard_gate: true` in config to make it a hard gate (all 7 conditions must pass for a BUY signal).

### 9.4 Caching Strategy

```
data/fundamentals/{symbol}.json
    {
        "symbol": "DIXON",
        "fetched_at": "2024-01-15T10:30:00+05:30",
        "pe_ratio": "32.5",
        "roe": "28.3",
        "eps_accelerating": true,
        "fii_trend": "rising",
        ...
    }
```

Cache is checked before every fetch. If `fetched_at` is within 7 days, the cached file is returned directly — no HTTP request. Cache is invalidated automatically on expiry; manual invalidation via `--force-refresh` flag.

---

## 10. News Sentiment Layer

### 10.1 Design

News sentiment is an **optional lightweight signal** that informs the composite score. It does not gate signals — a bad news score cannot block a strong technical setup. It can however push a borderline B-quality setup up to A or down to C.

**LLM use here is justified:** Unlike rule evaluation (deterministic, auditable), sentiment requires reading and understanding unstructured text. LLM is the right tool. However, it falls back gracefully to keyword scoring if the LLM is unavailable.

### 10.2 `ingestion/news.py` — Feed Fetcher + Scorer

```python
# Data sources
RSS_FEEDS = [
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.moneycontrol.com/rss/business.xml",
]
# Optional: NewsData.io API (requires NEWSDATA_API_KEY in .env)

def fetch_market_news(force_refresh: bool = False) -> list[dict]:
    """
    Fetch RSS + NewsData.io articles. Cache for 30 minutes.
    Initial scoring uses fast keyword heuristics (no LLM cost).
    Returns list of article dicts with sentiment, score, source.
    """

def fetch_symbol_news(symbol: str, all_news: list = None) -> list[dict]:
    """
    Filter market news for a specific symbol using alias matching.
    Re-scores matched articles with LLM for accuracy.
    Falls back to keyword scoring if LLM unavailable.
    """

def compute_news_score(articles: list[dict]) -> float:
    """
    Aggregate article sentiments into a -100 to +100 score.
    Used as input to the composite SEPA scorer.
    """
```

### 10.3 Symbol Alias Matching

Each symbol has a list of aliases for matching in article text:

```python
SYMBOL_ALIASES = {
    "RELIANCE":   ["reliance industries", "ril ", "reliance jio", "reliance retail"],
    "TCS":        ["tcs", "tata consultancy"],
    "HDFCBANK":   ["hdfc bank", "hdfcbank"],
    # ... etc.
}
```

Aliases are maintained in `config/symbol_aliases.yaml` — not hardcoded in the module.

### 10.4 Sentiment Pipeline

```
RSS feeds + NewsData.io
    │
    ▼ Keyword scoring (fast, free — all articles)
    │   Bullish keywords: surge, rally, upgrade, order win, buyback, dividend...
    │   Bearish keywords: probe, fraud, miss, downgrade, resignation, sebi...
    │
    ▼ Symbol alias filter (only articles mentioning the symbol)
    │
    ▼ LLM re-scoring (per matched article — Groq free tier)
    │   Understands context: "SEBI probe on a competitor" is not bearish for symbol
    │
    ▼ compute_news_score() → float (-100 to +100)
    │
    └──▶ SEPAResult.news_score (contributes 0–5 pts to composite score)
```

---

## 11. Paper Trading Simulator

### 11.1 Purpose

Paper trading sits between the screener and the backtester. It validates live signals in real-time without risking capital. Run it for at least 4–8 weeks before considering live execution.

```
Screener signals → Paper Trading → validate → Backtester → validate → Live (optional)
```

### 11.2 `paper_trading/simulator.py` — Core Engine

```python
def enter_trade(decision: SEPAResult, portfolio: Portfolio) -> Trade | None:
    """
    Enter a paper position.
    - Respects market hours (9:15–15:30 IST Mon–Fri)
    - Outside hours: queues to order_queue.py for next open
    - Enforces minimum score and confidence thresholds
    - Max 10 open positions at once
    - Position sizing: 2% portfolio risk per trade
    """

def pyramid_position(decision: SEPAResult, portfolio: Portfolio) -> Trade | None:
    """
    Add to an existing winning position on VCP Grade A breakout.
    Rules:
    - Must already hold the symbol
    - VCP quality must be 'A'
    - Volume ratio < 0.4 (volume dried up in base)
    - Price within 2% above VCP pivot
    - Max one pyramid add per position (tracked via pyramided flag)
    - Add qty capped at 50% of original position
    """

def check_exits(current_prices: dict, portfolio: Portfolio) -> list[Trade]:
    """
    Auto-exit positions that hit target or stop loss.
    Called at each price update during market hours.
    """
```

### 11.3 `paper_trading/order_queue.py` — Market-Hours Aware Queue

Signals generated outside market hours (e.g. from the 15:35 IST daily screen) are queued and executed at the next market open (9:15 IST). This prevents fills at unrealistic after-hours prices.

```python
def queue_order(symbol: str, order_type: str, decision: SEPAResult) -> None:
    """Persist to data/paper_trading/pending_orders.json."""

def execute_pending_orders(current_prices: dict) -> list[Trade]:
    """
    Called at market open (9:15 IST).
    Executes all queued orders at current open prices.
    """
```

### 11.4 Portfolio State

```
data/paper_trading/
├── portfolio.json          # cash, positions, total_trades, win/loss counts
├── trades.json             # full trade history (open + closed)
└── pending_orders.json     # queued orders for next market open
```

Starting capital: Rs 1,00,000 (configurable via `paper_trading.initial_capital`).

### 11.5 Performance Metrics

```python
def get_portfolio_summary(current_prices: dict) -> dict:
    """
    Returns:
        cash, open_value, total_value, initial_capital,
        total_return, total_return_pct, realised_pnl, unrealised_pnl,
        total_trades, win_rate, open_trades, closed_trades,
        positions (list with unrealised P&L per position)
    """
```

---

## 12. API Layer (FastAPI)

### 12.1 Purpose

The API layer exposes screener results, stock details, and paper trading state over HTTP. It enables the frontend, mobile access, and any future external integrations — all without touching the core pipeline logic.

The API is **read-only for pipeline outputs** — it queries SQLite and Parquet files but never modifies them. The pipeline writes; the API reads.

### 12.2 Directory Structure

```
api/
├── __init__.py
├── main.py                 # FastAPI app, CORS, startup
├── auth.py                 # API key authentication middleware
├── rate_limit.py           # Per-IP rate limiting (slowapi)
├── routers/
│   ├── stocks.py           # /api/v1/stocks/* endpoints
│   ├── watchlist.py        # /api/v1/watchlist endpoints
│   ├── portfolio.py        # /api/v1/portfolio endpoints (paper trading)
│   └── health.py           # /api/v1/health + /api/v1/meta
├── schemas/
│   ├── stock.py            # Pydantic response models
│   ├── portfolio.py        # Paper trading response models
│   └── common.py           # Pagination, error envelopes
└── deps.py                 # Shared FastAPI dependencies (DB session, cache)
```

### 12.3 Endpoints

```
── Screener ──────────────────────────────────────────────────────────
GET  /api/v1/stocks/top
     Returns today's top-ranked SEPA candidates, sorted by score.
     Query params: quality (A+|A|B|C), limit (default 20), date

GET  /api/v1/stocks/trend
     All stocks that passed Trend Template today.
     Query params: min_rs, stage, limit, date

GET  /api/v1/stocks/vcp
     Stocks with a qualified VCP pattern.
     Query params: min_quality (A|B|C), limit, date

GET  /api/v1/stock/{symbol}
     Full SEPAResult for a single symbol on a given date.
     Query params: date (default today)

GET  /api/v1/stock/{symbol}/history
     Historical SEPA scores for a symbol over the last N trading days.
     Query params: days (default 30)

── Watchlist Management ───────────────────────────────────────────────
GET    /api/v1/watchlist
       Returns all watchlist symbols with latest SEPA scores.
       Query params: sort (score|symbol|added_at), limit

POST   /api/v1/watchlist/{symbol}
       Add a single symbol. Returns updated watchlist.
       Body (optional): { "note": "strong VCP forming" }

DELETE /api/v1/watchlist/{symbol}
       Remove a symbol. Returns updated watchlist.

POST   /api/v1/watchlist/bulk
       Add multiple symbols at once.
       Body: { "symbols": ["RELIANCE", "TCS", "DIXON"] }
       Returns: { "added": 3, "already_exists": 0, "invalid": [] }

POST   /api/v1/watchlist/upload
       Upload file (.csv / .json / .xlsx / .txt). Parses + merges into watchlist.
       Content-Type: multipart/form-data
       Returns: { "added": 12, "skipped": 2, "invalid": ["XYZ123"], "watchlist": [...] }

DELETE /api/v1/watchlist
       Clear entire watchlist. Requires admin key.

── Paper Trading ──────────────────────────────────────────────────────
GET  /api/v1/portfolio
     Current paper trading portfolio summary (value, P&L, positions).

GET  /api/v1/portfolio/trades
     Full paper trade history. Query params: status (open|closed|all)

── System ────────────────────────────────────────────────────────────
GET  /api/v1/health
     { "status": "ok", "last_run": "2024-01-15T15:35:00+05:30" }

GET  /api/v1/meta
     { "universe_size": 500, "watchlist_size": 18, "last_screen_date": "2024-01-15",
       "a_plus_count": 3, "a_count": 12 }

POST /api/v1/run          (admin only — requires elevated API key)
     Trigger a manual screen run.
     Body: { "scope": "all" }          → universe + watchlist (default)
     Body: { "scope": "watchlist" }    → watchlist symbols only
     Body: { "scope": "universe" }     → universe only, skip watchlist
     Body: { "symbols": ["RELIANCE"] } → ad-hoc inline symbol list
```

### 12.4 Response Shape

All endpoints return a consistent envelope:

```python
# api/schemas/common.py
class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: T
    meta: dict | None = None   # pagination, run_date, etc.
    error: str | None = None
```

Example for `GET /api/v1/stocks/top`:

```json
{
  "success": true,
  "data": [
    {
      "symbol": "DIXON",
      "score": 91,
      "setup_quality": "A+",
      "stage": 2,
      "rs_rating": 88,
      "vcp_qualified": true,
      "entry_price": 14200.0,
      "stop_loss": 13100.0,
      "risk_pct": 7.7,
      "trend_template_pass": true,
      "conditions_met": 8
    }
  ],
  "meta": { "date": "2024-01-15", "total": 3 }
}
```

### 12.5 Authentication & Rate Limiting

```python
# api/auth.py
# Simple API key auth — key stored in .env, passed as X-API-Key header.
# Two tiers:
#   read_key  → GET endpoints only
#   admin_key → all endpoints including POST /api/v1/run

# api/rate_limit.py
# Per-IP limits using slowapi (wraps limits library):
#   Read endpoints:  100 requests / minute
#   Admin endpoints: 10 requests / minute
```

### 12.6 Running the API

```bash
# Development
uvicorn api.main:app --reload --port 8000

# Production (systemd or Docker)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
```

The API reads from the same SQLite and Parquet files the pipeline writes. No separate database. No shared state between API and pipeline — the pipeline owns writes, the API owns reads.

---

## 13. Frontend

### 13.1 Two-Stage Approach

The frontend follows a deliberate MVP → Production progression. The Streamlit dashboard is built first to validate the UI concept quickly; Next.js is built when the feature set is stable and you need a proper shareable app.

```
Phase A (MVP):       Streamlit dashboard
  → Python-native, zero JS, built alongside the pipeline
  → Runs on ShreeVault at port 8501
  → Reads directly from SQLite + Parquet (no API needed)

Phase B (Production): Next.js + Tailwind frontend
  → Talks to FastAPI via /api/v1/*
  → Deployable on Vercel (free) or VPS
  → Shareable URL, mobile-friendly
```

### 13.2 Streamlit MVP (`dashboard/`)

```
dashboard/
├── app.py                  # Main Streamlit app (entry point)
├── pages/
│   ├── 01_Watchlist.py     # Daily A+/A candidates table
│   ├── 02_Screener.py      # Full universe table with filters
│   ├── 03_Stock.py         # Single stock deep-dive (chart + scores)
│   ├── 04_Portfolio.py     # Paper trading portfolio
│   └── 05_Backtest.py      # Backtest results viewer
└── components/
    ├── charts.py            # mplfinance chart helpers
    ├── tables.py            # Styled screener tables
    └── metrics.py           # Score card widgets
```

**Key screens:**

```
Watchlist page
├── Market status bar (Nifty price, last run time)
├── ── Custom Watchlist Manager ──────────────────────────────────────
│   ├── File upload widget (.csv / .json / .xlsx / .txt)
│   ├── Manual entry text box ("RELIANCE, TCS, DIXON" → Add)
│   ├── Current watchlist table (symbol, score, quality, note, added_at)
│   ├── [Remove] button per row, [Clear All] button
│   └── [Run Watchlist Now] button → POST /api/v1/run scope=watchlist
├── ── Today's Results ───────────────────────────────────────────────
│   ├── ★ Watchlist A+/A setups (highlighted, shown first)
│   ├── Universe A+/A setups
│   └── Telegram alert preview

Screener page
├── Filters: quality, stage, min RS, sector, min price
├── Full results table (sortable)
└── Export to CSV button

Stock deep-dive page
├── Candlestick chart (90 days, MA ribbons, VCP markup, stage label)
├── Trend Template checklist (8 conditions, pass/fail)
├── Fundamental scorecard (7 conditions)
├── VCP metrics (contraction count, depths, vol ratio)
└── LLM trade brief (if enabled)

Portfolio page
├── P&L summary cards (total return, win rate, open positions)
├── Open positions table (with unrealised P&L)
├── Closed trades history
└── Equity curve chart
```

### 13.3 Next.js Production Frontend (`frontend/`)

Built only after Phase A (Streamlit MVP) has been validated. Talks exclusively to the FastAPI layer.

```
frontend/
├── app/
│   ├── page.tsx                 # Landing / dashboard home
│   ├── screener/
│   │   ├── page.tsx             # Full screener table
│   │   └── [symbol]/page.tsx    # Stock deep-dive
│   ├── watchlist/page.tsx       # Saved watchlist
│   └── portfolio/page.tsx       # Paper trading portfolio
├── components/
│   ├── StockTable.tsx           # Sortable, filterable results table
│   ├── CandlestickChart.tsx     # Chart (lightweight-charts / Recharts)
│   ├── TrendTemplateCard.tsx    # 8-condition checklist card
│   ├── VCPCard.tsx              # VCP metrics card
│   ├── ScoreGauge.tsx           # Visual score gauge (0–100)
│   └── PortfolioSummary.tsx     # P&L cards + equity curve
├── lib/
│   ├── api.ts                   # Typed API client (fetch wrappers)
│   └── types.ts                 # TypeScript types from API schemas
└── public/
```

**Technology choices for Next.js:**

| Concern | Choice | Rationale |
|---|---|---|
| Charts | lightweight-charts (TradingView) | Native candlestick, fast, free |
| Styling | Tailwind CSS | Utility-first, no component library lock-in |
| Data fetching | SWR | Stale-while-revalidate, perfect for polling screener |
| State | React useState / Context | No Redux needed at this scale |
| Deployment | Vercel (free tier) | Zero-config, automatic HTTPS |

### 13.4 Frontend Feature Checklist

| Feature | Streamlit MVP | Next.js |
|---|---|---|
| Daily watchlist table | ✓ | ✓ |
| Full screener with filters | ✓ | ✓ |
| Candlestick chart + MA ribbons | ✓ | ✓ |
| VCP contraction zones on chart | ✓ | ✓ |
| Stage label on chart | ✓ | ✓ |
| Trend Template checklist | ✓ | ✓ |
| Fundamental scorecard | ✓ | ✓ |
| LLM trade brief | ✓ | ✓ |
| Paper trading portfolio | ✓ | ✓ |
| Backtest results viewer | ✓ | ✓ |
| Mobile-friendly layout | ✗ | ✓ |
| Shareable URL (no SSH) | ✗ | ✓ |
| Real-time auto-refresh | ✗ | ✓ (SWR polling) |
| CNN/ML pattern overlay (future) | ✗ | ✓ (planned) |

---

## 14. Phase-by-Phase Roadmap

### Phase 1 — Foundation (Weeks 1–3)
**Goal:** Raw data flowing into clean, queryable storage.

- [x] Set up project skeleton (all directories, `__init__.py`, `pyproject.toml`)
- [x] Implement `ingestion/base.py` abstract interface
- [ ] Implement `ingestion/nse_bhav.py` (NSE Bhavcopy daily download) ← **NOT BUILT** — deferred to Phase 4; yfinance covers backfill
- [x] Implement `ingestion/yfinance_source.py` (historical backfill)
- [x] Implement `ingestion/validator.py` (schema + sanity checks)
- [x] Implement `ingestion/universe_loader.py` — unified symbol resolver with watchlist + universe merge
- [x] Implement `load_watchlist_file()` — parse CSV / JSON / XLSX / TXT watchlist files
- [x] SQLite `watchlist` table (symbol, note, added_via, last_score, last_quality)
- [x] Implement `storage/parquet_store.py` with atomic append support
- [x] Implement `utils/logger.py`, `utils/date_utils.py`, `utils/exceptions.py`, `utils/math_utils.py`
- [x] Write `scripts/run_daily.py` with `--watchlist`, `--symbols`, `--watchlist-only`, `--scope` flags (Phase 1 skeleton — feature + screen hooks wired in Phase 2/3)
- [x] Write `scripts/bootstrap.py` skeleton (full history download — feature compute wired in Phase 2)
- [x] `config/settings.yaml` with all Phase 1 parameters including watchlist config
- [x] Unit tests for storage layer (`test_parquet_store.py`, `test_sqlite_store.py`, `conftest.py`)
- [x] Unit tests for `load_watchlist_file()` and `resolve_symbols()` (`test_universe_loader.py`)
- [x] **Deliverable:** `python scripts/run_daily.py --watchlist mylist.csv` resolves symbols from file. `python scripts/run_daily.py --symbols "RELIANCE,DIXON"` resolves inline symbols. Default run merges watchlist + universe. (Feature compute + screening wired in Phases 2–3.)

**Phase 1 status: ✅ COMPLETE** — one item intentionally deferred:
- `ingestion/nse_bhav.py` — NSE Bhavcopy downloader deferred to Phase 4 (yfinance covers all backfill needs through Phase 3)

---

### Phase 2 — Feature Engineering (Weeks 4–6)
**Goal:** All Minervini-relevant indicators computed and stored.

- [x] `features/moving_averages.py` — SMA 10/21/50/150/200, EMA 21, slopes (SMA_150 explicit, no fallback)
- [x] `features/relative_strength.py` — RS raw + RS rating (vs. Nifty 500)
- [x] `features/atr.py` — ATR 14, ATR%
- [x] `features/volume.py` — vol ratio, acc/dist, up/down vol days
- [x] `features/pivot.py` — swing high/low detection (ZigZag method, configurable sensitivity)
- [x] `features/vcp.py` — contraction detection, tightness, vol dry-up
- [x] `features/feature_store.py` — `bootstrap()` + `update()` + `needs_bootstrap()` (see Section 5)
- [x] Unit tests for all feature modules with fixture data (`test_moving_averages.py`, `test_relative_strength.py`, `test_atr.py`, `test_volume.py`, `test_pivot.py`, `test_vcp.py`, `test_feature_store.py`)
- [ ] Benchmark: bootstrap for 500 symbols < 15 min; daily incremental update < 30 seconds ← **not formally benchmarked yet**
- [x] **Deliverable:** Feature pipeline fully wired — `bootstrap()` computes full history, `update()` appends one row per run, all modules tested with fixture data.

**Phase 2 status: ✅ COMPLETE** — one item pending:
- Formal benchmark run (500 symbols) not yet executed; performance is expected to meet targets but needs verification against live data volume.

---

### Phase 3 — Rule Engine (Weeks 7–9)
**Goal:** Deterministic, fully testable SEPA screening logic.

- [x] `rules/stage.py` — Stage 1/2/3/4 detection with confidence score (runs first, hard gate)
- [x] `rules/trend_template.py` — all 8 conditions, configurable thresholds
- [x] `rules/vcp_rules.py` — VCP qualification rules (grade A/B/C/FAIL + 0–100 score)
- [x] `rules/entry_trigger.py` — pivot breakout detection with volume confirmation
- [x] `rules/stop_loss.py` — stop below VCP base_low (primary) + ATR fallback + max-risk cap
- [x] `rules/scorer.py` — weighted scoring + `SEPAResult` dataclass (stage hard gate; fundamentals/news placeholders)
- [x] Unit tests: `test_stage_detection.py` (25 tests), `test_trend_template.py` (29 tests), `test_vcp_rules.py` (46 tests), `test_scorer.py` (19 tests) — all synthetic pd.Series, no file I/O
- [x] Integration test: `tests/integration/test_known_setups.py` — Stage 4 hard gate regression, A+ full pipeline, partial-TT smoke tests (6 tests)
- [x] `rules/risk_reward.py` — R:R estimator using nearest resistance ✅ built and wired into Phase 4
- [x] `screener/pipeline.py` — batch screener with parallel execution ✅
- [x] `screener/results.py` — persist results to SQLite ✅
- [x] **Deliverable:** `python scripts/run_daily.py --date 2024-01-15` produces a ranked watchlist. All non-Stage-2 stocks are correctly filtered out.

**Phase 3 status: ✅ COMPLETE** — 504+ tests passing. All rule modules complete. Screener wiring (`pipeline.py` + `results.py`) completed ahead of schedule during Phase 3/4 overlap. `risk_reward.py` built and wired into Phase 4.

---

### Phase 4 — Reports, Charts & Alerts (Weeks 10–12)
**Goal:** Human-consumable outputs and alert dispatch. Also completes the Phase 3→4 bridge items (screener wiring + R:R).

**Inherited from Phase 3 (now COMPLETE):**
- [x] `screener/pipeline.py` — batch screener: load feature row → detect_stage → check_trend_template → check_vcp → check_entry_trigger → compute_stop_loss → evaluate() → SEPAResult; parallel execution via ProcessPoolExecutor ✅
- [x] `screener/results.py` — persist SEPAResult list to SQLite (`sepa_results` table); query helpers for API/dashboard ✅
- [x] `rules/risk_reward.py` — R:R estimator built and wired into `screener/pipeline.py` ✅
- [x] `screener/pipeline.py` wired into `scripts/run_daily.py` so `--date` flag produces a printed ranked result list ✅ (reports/alerts wired via `runner.py` delegation)

**Phase 4 core deliverables:**
- [x] `reports/daily_watchlist.py` — CSV + HTML report ✅
- [x] `reports/chart_generator.py` — candlestick + MA ribbons + stage annotation + VCP base zone ✅
- [x] `reports/templates/watchlist.html.j2` — styled HTML template ✅
- [x] `alerts/telegram_alert.py` — daily watchlist to Telegram channel ✅
- [x] `alerts/email_alert.py` — SMTP summary ✅
- [x] `alerts/webhook_alert.py` — generic webhook (Slack, Discord) ✅
- [x] `pipeline/scheduler.py` — APScheduler job at market close (15:35 IST) ✅
- [x] `pipeline/runner.py` — unified 13-step entry point (daily mode); reports + charts + alerts all wired ✅
- [x] **`scripts/run_daily.py` → `pipeline/runner.py` unification** ✅ — CLI delegates all pipeline work to `pipeline.runner.run(context)`

**Phase 4 status: ✅ COMPLETE** — All modules built and wired. `risk_reward.py` fully wired into `screener/pipeline._screen_single()`. `email_alert.py` and `webhook_alert.py` built and wired. `run_daily.py` delegates to `runner.py`.

---

### Phase 5 — Fundamentals & News (Weeks 13–14)
**Goal:** Add Minervini fundamental conditions and news sentiment as scoring inputs.

- [ ] `ingestion/fundamentals.py` — Screener.in scraper with 7-day cache
- [ ] `rules/fundamental_template.py` — 7 Minervini fundamental conditions
- [ ] Unit tests for fundamental template (known PE/ROE/EPS values → expected pass/fail)
- [ ] `ingestion/news.py` — RSS + NewsData.io + keyword scorer + LLM re-scorer
- [ ] `config/symbol_aliases.yaml` — symbol → alias list for news matching
- [ ] Wire fundamental score + news score into `rules/scorer.py` composite score
- [ ] Update HTML report to show fundamental conditions per candidate
- [ ] Update Telegram alert to include fundamental summary line
- [ ] **Deliverable:** A+/A setups in the daily report show EPS acceleration status, ROE, promoter holding, and a news sentiment score alongside technical details.

---

### Phase 6 — LLM Narrative Layer (Weeks 15–16)
**Goal:** AI-generated trade briefs as an optional overlay.

**Phase 6 status: ✅ COMPLETE** — All modules built, wired, and tested. GeminiClient added as a bonus sixth provider.

- [x] `llm/llm_client.py` — abstract `LLMClient` ABC + `get_llm_client()` factory
- [x] `llm/explainer.py` — `generate_trade_brief()` + `generate_watchlist_summary()`
- [x] Jinja2 prompt templates — `trade_brief.j2` (stage, VCP, fundamentals, news, OHLCV levels in context) + `watchlist_summary.j2`
- [x] Implement `GroqClient` (default — `llama-3.3-70b-versatile`, free, fast)
- [x] Implement `AnthropicClient` (`claude-haiku-4-5`) and `OpenAIClient` (`gpt-4o-mini`)
- [x] Implement `OllamaClient` for local fallback (OpenAI-compatible endpoint)
- [x] Implement `OpenRouterClient` (`deepseek/deepseek-r1:free` for best reasoning)
- [x] Implement `GeminiClient` (`gemini-2.0-flash`) — bonus sixth provider
- [x] Wire into `pipeline/runner.py` Step 5b — iterates results, loads `ohlcv_tail` from feature Parquet, calls `generate_trade_brief()`, stamps `r.narrative`, calls `generate_watchlist_summary()` for daily summary
- [x] Add narrative field to HTML report — "Trade Brief" column in both A+/A table and All Results table; collapsible `🤖 AI Brief` details element
- [x] Token cost logging per run — all providers log `input_tokens` + `output_tokens` via `log.debug("LLM token usage", ...)`
- [x] Graceful degradation — every error path returns `None`; Step 5b wrapped in `try/except`; pipeline never aborts on LLM failure
- [x] Unit tests — `tests/unit/test_llm_explainer.py` (12 tests: disabled path, quality filter, success paths, LLMProviderError, generic exception, empty ohlcv_tail, client=None, watchlist summary success/error/empty/disabled)
- [x] **Deliverable:** HTML report includes a 3-sentence AI trade brief for each A+/A setup. Groq free tier used by default. Brief shows as collapsible `🤖 AI Brief` in the Trade Brief column; `—` when LLM is disabled or quality filtered.

---

### What Was Built in Phase 7

| Module | File | Key Capability | Status |
|---|---|---|---|
| Portfolio engine | `paper_trading/portfolio.py` | SQLite-backed positions + cash ledger; `open_position()`, `close_position()`, `reset_portfolio()`; `PaperTradingError` on bad state | ✅ |
| Order queue | `paper_trading/order_queue.py` | IST market-hours gate (`is_market_open()`); `queue_order()` upserts pending orders; `execute_pending_orders()` fills at open price; `cancel_expired_orders()` | ✅ |
| Simulator | `paper_trading/simulator.py` | `enter_trade()` (score/quality/duplicate/max-positions gates); `check_exits()` (stop-loss + target); `pyramid_position()` (VCP-A add-on); `process_screen_results()` pipeline entry point | ✅ |
| Report | `paper_trading/report.py` | `PortfolioSummary` dataclass; `get_portfolio_summary()` (unrealised + realised PnL, win rate); `format_summary_text()` (human-readable); `get_performance_by_quality()` | ✅ |
| Pipeline wiring | `pipeline/runner.py` | `process_screen_results()` called after daily screen; `get_portfolio_summary()` logged at run end | ✅ |
| Unit tests | `tests/unit/test_paper_trading.py` | 29 tests, all passing (1.94 s) — portfolio CRUD, IST market-hours, queue/execute/expire, enter/exit/pyramid gates, report metrics | ✅ |

---

### Phase 7 Completed Details (Applied 2026-04-11)

All six deliverables from the Phase 7 roadmap are resolved:

1. ✅ **`paper_trading/portfolio.py`** — SQLite schema (`paper_positions` + `paper_portfolio_state` singleton); `init_paper_trading_tables()` idempotent; `open_position()` deducts cash in same transaction with insufficient-cash guard; `close_position()` credits proceeds + increments `win_trades`; `reset_portfolio()` hard-wipes all positions.
2. ✅ **`paper_trading/order_queue.py`** — `is_market_open(dt)` checks weekday + IST 09:15–15:30; `queue_order()` uses `INSERT OR REPLACE` (one pending order per symbol); `execute_pending_orders()` fills at `current_prices[symbol]`, sizes from config `risk_per_trade_pct`; `cancel_expired_orders()` deletes rows where `expires_at < today`.
3. ✅ **`paper_trading/simulator.py`** — `enter_trade()` enforces score ≥ 70, quality ∈ {A+, A}, no duplicate symbol, `len(positions) < max_positions`; routes to `open_position()` when market open, `queue_order()` when closed; `check_exits()` triggers stop-loss or target close; `pyramid_position()` gates on VCP grade A + vol_ratio < 0.4 + price drift ≤ 2% + not-yet-pyramided.
4. ✅ **`paper_trading/report.py`** — `get_portfolio_summary()` read-only; `unrealised_pnl` from `current_prices` (fallback to entry_price); `win_rate` = wins / closed × 100; `format_summary_text()` returns multi-line string with "📊 Paper Portfolio Summary" header; `get_performance_by_quality()` grouped win-rate by setup_quality.
5. ✅ **Pyramiding logic** — `pyramid_position()` in `simulator.py`; add-on qty = `floor(original_qty × 0.5)`; `mark_pyramided()` called on the original row so the flag prevents a second add.
6. ✅ **Pipeline wiring** — `pipeline/runner.py` calls `process_screen_results(results, db_path, config)` and logs the returned summary dict; `get_portfolio_summary()` printed at run end.

---

### What Was Built in Phase 9

| Module | File | Key Capability | Status |
|---|---|---|---|
| Structured logger | `utils/logger.py` | `_JSONFormatter` (production) + `_DevFormatter` (dev/colour); `TimedRotatingFileHandler` 30-day rotation; per-module level overrides from `config/logging.yaml`; `StructuredLogger` wrapper for `key=value` kwargs | ✅ |
| Logging config | `config/logging.yaml` | Console + rotating-file handlers; third-party library silencing (yfinance, urllib3, matplotlib) | ✅ |
| Run metadata | `utils/run_meta.py` | `get_git_sha()` → 8-char short SHA via `git rev-parse --short HEAD`; `get_config_hash(path)` → 8-char MD5 fingerprint of settings.yaml; both return `'unknown'` on any error | ✅ |
| Data lineage in RunContext | `pipeline/context.py` | `config_hash()` → SHA-256 of serialised config dict; `git_sha()` → full HEAD SHA; both stored in `run_history` via `log_run()` | ✅ |
| Makefile | `Makefile` | 13 targets: `install`, `test`, `test-fast`, `lint`, `format`, `format-check`, `daily`, `backtest`, `rebuild`, `paper-reset`, `api`, `dashboard`, `clean`, `help` | ✅ |
| systemd service files | `deploy/minervini-daily.service` | `Type=oneshot`; `EnvironmentFile`; 30-min `TimeoutStartSec`; journal logging | ✅ |
| systemd timer | `deploy/minervini-daily.timer` | `OnCalendar=Mon-Fri 15:35 Asia/Kolkata`; `Persistent=true`; `RandomizedDelaySec=30` | ✅ |
| API + dashboard services | `deploy/minervini-api.service`, `deploy/minervini-dashboard.service` | `Type=simple`; `Restart=always`; uvicorn + streamlit process management | ✅ |
| Deploy scripts | `deploy/install.sh`, `deploy/uninstall.sh` | Enable/disable all three systemd units in one command | ✅ |
| Runbook | `RUNBOOK.md` | Daily ops; add watchlist symbol; add universe; corrupt store recovery; add new rule condition; add new data source; threshold tuning; common errors + fixes; performance benchmarks | ✅ |
| Feature benchmark | `scripts/benchmark_features.py` | Measures `bootstrap()` + `update()` wall-clock time across 10 synthetic symbols; extrapolates to 500/2000-symbol production runs; JSON trend-tracking output to `data/benchmarks/`; exit code 1 if update target exceeded | ✅ |
| Run history unit tests | `tests/unit/test_run_history.py` | 5 tests: full `log_run`→`finish_run` round-trip, `get_git_sha()` type/length, `get_config_hash()` 8-hex-char contract, unknown `run_id` graceful no-op, `get_last_run()` most-recent-row semantics | ✅ |
| (Optional) Prometheus endpoint | — | **Not built** — `prometheus_client` is installed as a transitive dep but no project code exposes metrics. Mark for Phase 10 if API monitoring is needed. | ⛾ |
| (Optional) GitHub Actions CI | — | **Not built** — `make test` local target satisfies the spec requirement. Add `.github/workflows/ci.yml` if/when the repo is pushed to GitHub. | ⛾ |

---


### Phase 7 — Paper Trading Simulator (Weeks 17–18)
**Goal:** Validate live signals in real-time before backtesting or going live.

- [x] `paper_trading/simulator.py` — `enter_trade()`, `exit_position()`, `check_exits()`
- [x] `paper_trading/portfolio.py` — portfolio state, P&L, win rate
- [x] `paper_trading/order_queue.py` — market-hours aware pending order queue
- [x] `paper_trading/report.py` — performance summary: return, win rate, avg R-multiple
- [x] Pyramiding logic — add to winning VCP Grade A positions (50% of original qty, one add only)
- [x] Wire into `pipeline/runner.py` — paper trades executed automatically after daily screen
- [x] Unit tests: enter/exit/pyramid scenarios with known prices (29 tests passing)
- [x] **Deliverable:** After every daily screen, A+/A signals automatically create paper trades. Portfolio state persisted in SQLite. Run for 4–8 weeks before backtesting.

**Phase 7 status: ✅ COMPLETE** — All modules built, wired, and tested.

---

### Phase 8 — Backtesting Engine (Weeks 19–22)
**Goal:** Validate strategy performance on historical data with realistic trade simulation.

- [ ] `backtest/engine.py` — walk-forward backtester (no lookahead bias)
- [ ] `backtest/portfolio.py` — position sizing (1R = 1% of portfolio), max 10 open positions
- [ ] **Trailing stop loss** — `simulate_trade()` supports `trailing_stop_pct` param:
  - Trailing stop follows peak close upward by `trailing_stop_pct` (e.g. 7%)
  - Floored at VCP `base_low` — never drops below the initial hard stop
  - Trade record notes `stop_type: "trailing" | "fixed"` for analysis
- [ ] **Market regime labelling** — `backtest/regime.py`:
  - Labels every trade Bull / Bear / Sideways using NSE calendar + 200MA slope fallback
  - NSE regime calendar covers 2014–present (documented periods with rationale)
  - Per-regime breakdown in backtest report: win rate, avg P&L, trade count
- [ ] `backtest/metrics.py` — CAGR, Sharpe ratio, max drawdown, win rate, avg R-multiple, profit factor, expectancy
- [ ] `backtest/report.py` — HTML + CSV backtest report with equity curve, regime table, VCP quality breakdown
- [ ] `scripts/backtest_runner.py` — CLI: date range, universe, strategy config, trailing stop toggle
- [ ] Parameter sweep: test trailing_stop_pct (5%, 7%, 10%, 15%) vs fixed stop
- [ ] Gate stats reporting: what % of windows passed Stage 2 / Trend Template / both
- [ ] **Deliverable:** `python scripts/backtest_runner.py --start 2019-01-01 --end 2024-01-01 --universe nifty500 --trailing-stop 0.07` produces a full report with per-regime breakdown and trailing vs. fixed stop comparison.

---

### Phase 9 — Hardening & Production (Weeks 23–26)
**Goal:** Production-ready pipeline on Ubuntu server (ShreeVault).

- [ ] Structured logging (JSON format) with log rotation
- [ ] Prometheus metrics endpoint (optional)
- [ ] Full test coverage: unit + integration + smoke tests
- [ ] CI pipeline: `make test` runs all tests in < 3 minutes
- [ ] Data lineage: every run logs data hash, config snapshot, Git commit SHA
- [ ] `Makefile` with targets: `test`, `lint`, `format`, `daily`, `backtest`, `rebuild`, `paper-reset`
- [ ] `systemd` service file for automated daily run
- [ ] Runbook: how to add a new data source, how to add a new rule condition
- [ ] **Deliverable:** Pipeline runs unattended on ShreeVault, self-monitors, alerts on failure.

---

### Phase 10 — API Layer (Weeks 27–29)
**Goal:** Expose screener results over a clean HTTP API for frontend consumption.

- [ ] `api/main.py` — FastAPI app with CORS, startup events
- [ ] `api/auth.py` — X-API-Key auth (read key + admin key)
- [ ] `api/rate_limit.py` — per-IP rate limiting via slowapi
- [ ] `api/routers/stocks.py` — `/api/v1/stocks/top`, `/trend`, `/vcp`, `/{symbol}`
- [ ] `api/routers/watchlist.py` — GET / POST / DELETE single, POST bulk, POST upload, DELETE all, scoped run
- [ ] `api/routers/portfolio.py` — paper trading portfolio endpoints
- [ ] `api/routers/health.py` — health check + meta endpoint (includes watchlist_size)
- [ ] `api/schemas/` — Pydantic response models for all endpoints
- [ ] Unit tests for all endpoints (TestClient)
- [ ] `POST /api/v1/run` with `scope` and `symbols` body params
- [ ] systemd service for uvicorn (port 8000, 2 workers)
- [ ] **Deliverable:** `curl -X POST http://shreevault:8000/api/v1/watchlist/upload -F "file=@mylist.csv"` adds all valid symbols. `POST /api/v1/run {"scope":"watchlist"}` analyses only watchlist symbols.

---

### Phase 11 — Streamlit Dashboard MVP (Weeks 30–31)
**Goal:** A visual dashboard for daily monitoring, accessible without SSH.

- [ ] `dashboard/app.py` — Streamlit entry point, multi-page layout
- [ ] `dashboard/pages/01_Watchlist.py` — file upload widget + manual entry + watchlist table + [Run Now] button
- [ ] `dashboard/pages/02_Screener.py` — full universe table with quality/stage/RS filters
- [ ] `dashboard/pages/03_Stock.py` — single stock deep-dive (chart + TT checklist + VCP + fundamentals + LLM brief)
- [ ] `dashboard/pages/04_Portfolio.py` — paper trading summary + equity curve
- [ ] `dashboard/pages/05_Backtest.py` — backtest results viewer + regime breakdown
- [ ] `dashboard/components/charts.py` — mplfinance candlestick + MA + VCP zone overlays
- [ ] Stage label annotation on chart
- [ ] Watchlist symbols highlighted with ★ badge in all result tables
- [ ] Manual run trigger button (calls `POST /api/v1/run`)
- [ ] systemd service for Streamlit (port 8501)
- [ ] **Deliverable:** Uploading `mylist.csv` via the dashboard adds all symbols to the watchlist. Clicking [Run Watchlist Now] analyses them immediately and shows results on the same page.

---

### Phase 12 — Next.js Production Frontend (Weeks 32–36)
**Goal:** A shareable, mobile-friendly web app backed by the FastAPI layer.

- [ ] `frontend/` — Next.js 14 project scaffold (App Router)
- [ ] `frontend/lib/api.ts` — typed API client (all `/api/v1/*` endpoints)
- [ ] `frontend/lib/types.ts` — TypeScript types matching Pydantic schemas
- [ ] Screener table page — sortable, filterable, live-polling via SWR
- [ ] Stock deep-dive page — TradingView lightweight-charts candlestick + MA ribbons
- [ ] VCP zone overlays on chart
- [ ] Trend Template checklist card (8 conditions, pass/fail badges)
- [ ] Fundamental scorecard card (7 conditions)
- [ ] Score gauge widget (0–100 visual indicator)
- [ ] Paper trading portfolio page — P&L cards + equity curve (Recharts)
- [ ] Mobile-responsive layout (Tailwind)
- [ ] Deploy to Vercel (free tier, automatic HTTPS)
- [ ] **Deliverable:** Public URL serves the full screener. Anyone with the API key can view today's A+/A setups, charts, and paper portfolio from any device.

---

## 15. Technology Stack & Polars Upgrade Path

### 15.1 Current Stack (pandas)

**Decision: Use pandas.** The financial ecosystem (yfinance, mplfinance, most LLM tooling) is pandas-native. At 500–2000 symbols with incremental daily updates, pandas + ProcessPoolExecutor is fast enough. The bottleneck is I/O, not DataFrame operations.

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.11+ | Ecosystem, speed of development |
| Data storage | Parquet (pyarrow) | Columnar, fast for time-series reads |
| Metadata / results | SQLite + SQLAlchemy | Zero-ops, portable, auditable |
| Data manipulation | **pandas + numpy** | Industry standard for OHLCV; ecosystem compatibility |
| Technical indicators | Custom (features/) | Full control; no TA-Lib dependency |
| Parallelism | concurrent.futures (ProcessPool) | CPU-bound feature computation across symbols |
| Scheduling | APScheduler | Simple, no Celery overhead |
| Charts | matplotlib + mplfinance | Reproducible, no JS dependency |
| HTML reports | Jinja2 | Separation of logic and template |
| LLM | Anthropic / OpenAI / Ollama | Pluggable via adapter |
| Alerting | python-telegram-bot | Direct, free, reliable |
| Config | PyYAML + pydantic | Validated, typed config objects |
| Testing | pytest + pytest-cov | Standard |
| Linting | ruff + black | Fast, consistent |
| CLI | argparse | Zero extra dependencies |

### 15.2 Polars Upgrade Plan (Future)

Polars is a strong candidate for a future performance upgrade, particularly if the universe scales to full NSE (~2000 symbols) and backtest windows grow to 10+ years. The upgrade is **planned but not premature** — profile first, then migrate where it matters.

**When to consider upgrading:**

- Daily incremental update takes > 5 minutes with 2000 symbols
- Backtest over 10-year window takes > 30 minutes
- Memory pressure causes OOM on ShreeVault during bootstrap

**Why the upgrade is low-risk:**

Every feature module already uses the interface `compute(df: pd.DataFrame, config: dict) -> pd.DataFrame`. Polars can be adopted internally per module without changing any signature — the rest of the system never sees it.

```python
# Before (pandas internals)
def compute_moving_averages(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    df["sma_50"] = df["close"].rolling(50).mean()
    return df

# After (polars internally, identical external interface)
def compute_moving_averages(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    pl_df = pl.from_pandas(df)                          # convert once on entry
    pl_df = pl_df.with_columns(
        pl.col("close").rolling_mean(50).alias("sma_50")
    )
    return pl_df.to_pandas()                            # convert once on exit
```

**Migration order (highest ROI first):**

| Priority | Module | Reason |
|---|---|---|
| 1 | `features/moving_averages.py` | Largest rolling window ops, most CPU |
| 2 | `features/relative_strength.py` | Cross-symbol ranking benefits from lazy eval |
| 3 | `features/vcp.py` | Complex multi-pass rolling logic |
| 4 | `backtest/engine.py` | Scans entire date range; biggest dataset |
| 5 | Everything else | Only if profiling shows it matters |

**Backend toggle for safe migration:**

A single env var enables running both backends in parallel to verify output parity before cutting over:

```python
# features/feature_store.py
COMPUTE_BACKEND = os.getenv("FEATURE_BACKEND", "pandas")  # "pandas" | "polars"
```

```bash
# Run both backends, diff outputs — confirm identical results before switching
FEATURE_BACKEND=polars python scripts/run_daily.py --date 2024-06-01 --dry-run
```

**Also consider DuckDB for backtesting:**

If Polars alone isn't enough for the backtester (scanning 2000 × 10yr Parquet files), DuckDB can query them in-place without loading into RAM:

```python
import duckdb
results = duckdb.query("""
    SELECT symbol, date, close, sma_50, rs_rating
    FROM 'data/features/*.parquet'
    WHERE date BETWEEN '2019-01-01' AND '2024-01-01'
      AND rs_rating >= 70
""").df()
```

This is an additive change — DuckDB sits alongside pandas/polars in the backtest layer only.

---

## 16. Configuration & Environment

### 16.1 `config/settings.yaml` Structure

```yaml
universe:
  source: "nse_bhav"              # nse_bhav | yfinance | csv
  index: "nifty500"
  min_price: 50                   # INR
  min_avg_volume: 100000          # shares/day
  min_market_cap_cr: 500          # crore INR

data:
  raw_dir: "data/raw"
  processed_dir: "data/processed"
  features_dir: "data/features"
  fundamentals_dir: "data/fundamentals"
  news_dir: "data/news"

watchlist:
  always_scan: true             # scan watchlist even when running universe
  priority_in_reports: true     # watchlist symbols shown first in reports/alerts
  always_generate_charts: true  # generate chart for every watchlist symbol regardless of score
  min_score_alert: 55           # lower threshold for watchlist alerts (vs 70 for universe)
  persist_path: "data/watchlist.db"  # SQLite file (can share with main DB)

stage:
  ma200_slope_lookback: 20        # trading days for SMA200 trend check
  ma50_slope_lookback: 10         # trading days for SMA50 trend check

trend_template:
  price_above_ma: true
  ma_order: true
  ma200_slope_lookback: 20        # trading days
  ma50_order: true
  price_above_50: true
  pct_above_52w_low: 25.0
  pct_below_52w_high: 25.0
  min_rs_rating: 70

vcp:
  min_contractions: 2
  max_contractions: 5
  require_declining_depth: true
  require_vol_contraction: true
  min_weeks: 3
  max_weeks: 52
  tightness_pct: 10.0
  max_depth_pct: 50.0

fundamentals:
  enabled: true
  hard_gate: false                # true → FAIL if any F1-F7 condition fails
  cache_days: 7
  conditions:
    min_roe: 15.0
    max_de: 1.0
    min_promoter_holding: 35.0
    min_sales_growth_yoy: 10.0

news:
  enabled: true
  cache_minutes: 30
  rss_feeds:
    - "https://www.moneycontrol.com/rss/marketreports.xml"
    - "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
    - "https://www.business-standard.com/rss/markets-106.rss"
  llm_rescore: true               # false → keyword scoring only (no LLM cost)

scoring:
  min_score_alert: 70             # alert threshold
  setup_quality_thresholds:
    a_plus: 85
    a: 70
    b: 55
    c: 40

paper_trading:
  enabled: true
  initial_capital: 100000         # INR
  max_positions: 10
  risk_per_trade_pct: 2.0         # % of portfolio risked per trade
  min_score_to_trade: 70
  min_confidence: 50

backtest:
  trailing_stop_pct: 0.07         # 7% trailing stop (null to disable)
  fixed_stop_pct: 0.05            # fallback if trailing disabled
  target_pct: 0.10
  max_hold_days: 20
  position_size_pct: 0.10

llm:
  enabled: true
  provider: "groq"                # groq | anthropic | openai | openrouter | ollama
  model: "llama-3.3-70b-versatile"
  max_tokens: 350
  only_for_quality: ["A+", "A"]  # only generate briefs for top setups

api:
  host: "0.0.0.0"
  port: 8000
  workers: 2
  rate_limit_read: "100/minute"
  rate_limit_admin: "10/minute"

dashboard:
  port: 8501

alerts:
  telegram:
    enabled: true
    min_quality: "A"
  email:
    enabled: false

scheduler:
  run_time: "15:35"               # IST (market close + 5 min)
  timezone: "Asia/Kolkata"
```

### 16.2 `.env` Variables

```bash
NSE_BHAV_BASE_URL=https://archives.nseindia.com/content/historical/EQUITIES/
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...              # free at console.groq.com
OPENROUTER_API_KEY=sk-or-...      # free models available
OLLAMA_API_KEY=                   # leave blank for local Ollama
NEWSDATA_API_KEY=                 # optional — free tier at newsdata.io
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SMTP_HOST=smtp.gmail.com
SMTP_USER=...
SMTP_PASS=...
API_READ_KEY=...                  # X-API-Key for GET endpoints
API_ADMIN_KEY=...                 # X-API-Key for POST /api/v1/run
```

---

## 17. Testing Strategy

### 17.1 Unit Tests — Rules

Every rule condition is tested independently with synthetic data:

```python
# tests/unit/test_trend_template.py
def test_condition_1_pass():
    row = make_row(close=150, sma_150=140, sma_200=130)
    result = check_trend_template(row, default_config())
    assert result.condition_1 is True

def test_condition_8_fail_low_rs():
    row = make_row(rs_rating=55)
    result = check_trend_template(row, default_config())
    assert result.condition_8 is False
    assert result.passes is False
```

### 17.2 Regression Tests — Known Setups

Historical setups known to be valid SEPA breakouts are hard-coded as fixtures. Every code change must produce the same `SEPAResult` for these fixtures.

```python
# tests/integration/test_known_setups.py
KNOWN_SETUPS = [
    ("DIXON", date(2023, 6, 15), "A+"),    # Documented VCP breakout
    ("TATAELXSI", date(2023, 3, 10), "A"),
]

@pytest.mark.parametrize("symbol, date, expected_quality", KNOWN_SETUPS)
def test_known_setup_regression(symbol, date, expected_quality):
    result = run_single(symbol, date)
    assert result.setup_quality == expected_quality

def test_stage4_blocked_despite_tt_pass():
    """A stock in Stage 4 must return FAIL even if 8/8 TT conditions pass."""
    df = make_stage4_df()    # price below declining MAs
    stage = detect_stage(df, default_config())
    result = run_rules(df)
    assert stage.stage == 4
    assert result.setup_quality == "FAIL"

def test_trailing_stop_never_drops_below_vcp_floor():
    """Trailing stop must be floored at VCP base_low."""
    trade = simulate_trade(
        df=make_trending_df(), entry_idx=10, entry_price=100,
        trailing_stop_pct=0.07, stop_loss_price=85.0   # VCP floor
    )
    # Even if trailing stop calculates 80.0, it must be floored at 85.0
    assert trade["stop_price_used"] >= 85.0
```

### 17.3 Data Quality Tests

```python
def test_validator_rejects_negative_volume():
    df = sample_ohlcv()
    df.loc[df.index[-1], "volume"] = -1
    with pytest.raises(DataValidationError):
        validate(df)

def test_fundamental_template_missing_data_graceful():
    """Fundamentals unavailable should not crash pipeline."""
    result = check_fundamental_template(None)
    assert result["passes"] is False
    assert result["conditions_met"] == 0

def test_news_score_keyword_fallback():
    """News scoring must work without LLM (keyword fallback)."""
    articles = fetch_symbol_news("RELIANCE", use_llm=False)
    score = compute_news_score(articles)
    assert -100 <= score <= 100
```

---

## 18. Deployment & Operations

### 18.1 Makefile Targets

```makefile
.PHONY: test lint format daily backtest rebuild install paper-reset api dashboard

install:
    pip install -e ".[dev]"

test:
    pytest tests/ -v --cov=. --cov-report=term-missing

lint:
    ruff check . && ruff format --check .

format:
    ruff format .

daily:
    python scripts/run_daily.py --date today

backtest:
    python scripts/backtest_runner.py --start $(START) --end $(END)

rebuild:
    python scripts/rebuild_features.py --universe nifty500

paper-reset:
    python -c "from paper_trading.simulator import reset_portfolio; reset_portfolio(confirm=True)"

api:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

dashboard:
    streamlit run dashboard/app.py --server.port 8501
```

### 18.2 systemd Service (ShreeVault)

Three separate systemd services run on ShreeVault — pipeline, API, and dashboard.

```ini
# /etc/systemd/system/minervini-daily.service  (pipeline)
[Unit]
Description=Minervini Daily Stock Screen
After=network.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/minervini_ai
EnvironmentFile=/home/ubuntu/minervini_ai/.env
ExecStart=/home/ubuntu/.venv/bin/python scripts/run_daily.py --date today
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/minervini-daily.timer
[Timer]
OnCalendar=Mon-Fri 15:35 IST
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/minervini-api.service  (FastAPI — always running)
[Unit]
Description=Minervini FastAPI Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/minervini_ai
EnvironmentFile=/home/ubuntu/minervini_ai/.env
ExecStart=/home/ubuntu/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/minervini-dashboard.service  (Streamlit — always running)
[Unit]
Description=Minervini Streamlit Dashboard
After=network.target minervini-api.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/minervini_ai
EnvironmentFile=/home/ubuntu/minervini_ai/.env
ExecStart=/home/ubuntu/.venv/bin/streamlit run dashboard/app.py --server.port 8501 --server.headless true
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable all three:
```bash
sudo systemctl enable --now minervini-daily.timer
sudo systemctl enable --now minervini-api.service
sudo systemctl enable --now minervini-dashboard.service
```

### 18.3 Run History Table (SQLite)

```sql
CREATE TABLE run_history (
    id          INTEGER PRIMARY KEY,
    run_date    DATE NOT NULL,
    run_mode    TEXT NOT NULL,           -- 'daily' | 'backtest' | 'manual'
    git_sha     TEXT,
    config_hash TEXT,
    universe_size INTEGER,
    passed_stage2 INTEGER,               -- symbols that passed Stage 2 gate
    passed_tt   INTEGER,                 -- symbols that passed Trend Template
    vcp_qualified INTEGER,
    a_plus_count  INTEGER,
    a_count       INTEGER,
    duration_sec  REAL,
    status      TEXT NOT NULL,           -- 'success' | 'partial' | 'failed'
    error_msg   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 19. Design Principles & Anti-Patterns

### 19.1 Principles

| Principle | Application |
|---|---|
| **Single Responsibility** | Each module does exactly one thing |
| **Pure Functions** | Feature computations have no side effects |
| **Immutable Raw Data** | `data/raw/` is append-only |
| **Config over Code** | All thresholds in `settings.yaml`, not hardcoded |
| **Fail Loudly** | Data quality issues raise exceptions, never silently continue |
| **LLM as Narrator** | AI explains rules; it never applies them |
| **Stage 2 Hard Gate** | Non-Stage-2 stocks are eliminated before any other check |
| **Graceful Degradation** | Fundamentals / news / LLM failures skip the layer, not the run |
| **Reproducibility** | Every run logs config + git SHA + data hashes |
| **Paper Before Live** | Paper trade for 4–8 weeks before considering live execution |

### 19.2 Anti-Patterns to Avoid

| Anti-Pattern | Why Avoided | Correct Approach |
|---|---|---|
| Watchlist = universe | Conflating the two causes confusing UX and scope bugs | Keep separate: universe (config YAML) vs. watchlist (SQLite + CLI/API/UI managed) |
| LLM scoring stocks | Non-deterministic, not auditable | Rules engine scores; LLM only narrates |
| LLM in scoring weights | Contaminates deterministic backtest | Fundamental + news scores use deterministic scrapers; LLM only for narrative |
| Skipping stage detection | Buying Stage 3/4 tops — the most common mistake | `rules/stage.py` runs first; non-Stage-2 exits immediately |
| Global mutable state | Causes bugs in parallel execution | `RunContext` passed explicitly to all workers |
| Pandas in rule engine | Slow for single-row evaluation | Rule engine uses `pd.Series` row or plain `dict` |
| One giant pipeline script | Untestable, unmaintainable | Modular packages with clean interfaces |
| TA-Lib dependency | C library, hard to install on servers | Implement all indicators in pure numpy/pandas |
| Hardcoded thresholds | Not tunable without code change | All thresholds in `settings.yaml` |
| LLM for data validation | Slow, expensive, wrong tool | Schema validation with pydantic/pandera |
| SMA_150 fallback | Silent wrong values when history < 150 rows | Raise `InsufficientDataError` explicitly |
| Postgres for this scale | Over-engineered, ops overhead | SQLite + Parquet — zero-ops, fast enough |
| Paper trading → live directly | Skips validation step | Paper trading → backtesting → live (in that order) |

---

## Appendix A — Minervini Trend Template Quick Reference

```
For a stock to qualify as a STAGE 2 candidate, ALL of the following must be true:

1.  Current price > 150-day (30-week) MA AND > 200-day (40-week) MA
2.  150-day MA > 200-day MA
3.  200-day MA trending up for at least 1 month
4.  50-day (10-week) MA > 150-day MA AND > 200-day MA
5.  Current price > 50-day MA
6.  Current price at least 25–30% above 52-week low
7.  Current price within 25% of 52-week high
8.  Relative Strength Rating >= 70 (ideally >= 80–90)

Source: "Trade Like a Stock Market Wizard", Mark Minervini, 2013
```

## Appendix B — VCP Anatomy

```
Price
  │    ████                   ████████████████  ← Breakout above pivot
  │   ██  ██                 ██
  │  ██    ██         ██████ █
  │ ██      ██       ██    ██
  │          ██   ███
  │           ████
  │
  └──────────────────────────────────────────▶ Time
         │        │      │   │
         ▼        ▼      ▼   ▼
      Contraction 1   2     3  (each smaller: ~20%, ~12%, ~6%)
      Volume:   High  Med  Low ← Vol dry-up confirms
```

**Current implementation:** `RuleBasedVCPDetector` — pivot detection + contraction math. Deterministic and auditable.

**Future upgrade path (Phase 12+):** `CNNVCPDetector` — a convolutional neural network trained on labeled VCP chart images generated from paper trading results. The `VCPDetector` abstract interface in `features/vcp.py` means this is a config switch, not a code change. Prerequisites: 6+ months of paper trading results to use as labeled training data, PyTorch, a GPU or cloud training job.

## Appendix C — Stage Classification Quick Reference

```
Stage 1 — Basing / Neglect
  • Price below both SMA50 and SMA200
  • MAs flat (slope ≈ 0)
  • Range-bound, low volume
  Action: Wait — do not buy. Monitor for Stage 2 breakout.

Stage 2 — Advancing / Momentum   ← THE ONLY BUY STAGE
  • Price > SMA50 > SMA200 (stack correct)
  • SMA200 slope > 0 (trending up)
  • SMA50 slope > 0 (trending up)
  Action: BUY setups that pass Trend Template + VCP.

Stage 3 — Topping / Distribution
  • Price lost SMA50 support
  • Still above SMA200 (temporarily)
  • SMA50 starting to decline, MA stack breaking
  Action: Tighten stops on existing positions. Do not add.

Stage 4 — Declining / Markdown
  • Price below both SMA50 and SMA200
  • Both MAs declining
  • Strong downtrend
  Action: NEVER buy. Exit any remaining positions immediately.
```

## Appendix D — 7 Minervini Fundamental Conditions

```
F1: EPS positive           — latest quarterly EPS > 0
F2: EPS accelerating       — most recent QoQ EPS growth > previous QoQ growth
F3: Sales growth >= 10% YoY — annual revenue growing at least 10%
F4: ROE >= 15%             — return on equity meets Minervini minimum
F5: D/E ratio <= 1.0       — not excessively leveraged
F6: Promoter holding >= 35%— management has meaningful skin in the game
F7: Positive profit growth — year-on-year profit growth > 0

Data source: Screener.in (consolidated view preferred, standalone fallback)
Cache TTL: 7 days (fundamentals change quarterly — daily fetch is unnecessary)
Hard gate: configurable (default: soft gate — informs score, does not block)
```

## Appendix E — NSE Market Regime Calendar

Used by `backtest/regime.py` to label each trade Bull / Bear / Sideways.
Falls back to 200MA slope when trade date is outside the defined ranges.

```
Period                  Label      Rationale
──────────────────────  ─────────  ─────────────────────────────────────────────
May 2014 – Jan 2018     Bull       Modi wave + GST implementation + recovery
Feb 2018 – Mar 2019     Sideways   IL&FS crisis, NBFC stress, mid-cap collapse
Apr 2019 – Jan 2020     Bull       Pre-COVID recovery, broad-based rally
Feb 2020 – Mar 2020     Bear       COVID crash (-38% in 40 days)
Apr 2020 – Dec 2021     Bull       V-shaped recovery, liquidity-driven rally
Jan 2022 – Dec 2022     Sideways   Fed rate hike cycle, FII selling, war impact
Jan 2023 – Sep 2024     Bull       Earnings recovery, domestic flows, capex theme
Oct 2024 – Mar 2025     Sideways   Global uncertainty, election-driven volatility
Apr 2025 – present      Unknown    Use 200MA slope fallback

Slope fallback rule:
  SMA200 slope > +0.05% over 20 days → Bull
  SMA200 slope < -0.05% over 20 days → Bear
  Otherwise                          → Sideways

Strategy expectation by regime:
  Bull     : Highest win rate, Minervini SEPA performs best
  Sideways : Choppy, lower win rate, tighter position sizing advised
  Bear     : High stop-hit rate, reduce position size or stay cash
```

---

*This document is the single source of truth for the Minervini AI project architecture. Update it whenever a design decision changes.*
