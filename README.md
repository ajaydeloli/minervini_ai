# Minervini AI — SEPA Stock Analysis System

> **Version:** 1.6.0 · **Target Market:** NSE / Indian Equities · **Python:** 3.11+

A **production-grade, fully automated stock screening and analysis system** built on
Mark Minervini's **SEPA (Specific Entry Point Analysis)** methodology. The system screens
hundreds of NSE-listed stocks every trading day, identifies Stage 2 breakout candidates,
scores each setup on a 0–100 scale, generates human-readable trade briefs via LLM, and
dispatches alerts through Telegram, email, and webhooks — all without manual intervention.

---

## Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [Architecture Overview](#2-architecture-overview)
3. [Build Status](#3-build-status)
4. [Project Structure](#4-project-structure)
5. [Prerequisites](#5-prerequisites)
6. [Installation](#6-installation)
7. [Configuration](#7-configuration)
8. [Running the System](#8-running-the-system)
9. [Watchlist Management](#9-watchlist-management)
10. [API Server](#10-api-server)
11. [Streamlit Dashboard](#11-streamlit-dashboard)
12. [Next.js Frontend](#12-nextjs-frontend)
13. [Paper Trading](#13-paper-trading)
14. [Backtesting](#14-backtesting)
15. [Alerts](#15-alerts)
16. [Testing](#16-testing)
17. [Linting & Formatting](#17-linting--formatting)
18. [Production Deployment (systemd)](#18-production-deployment-systemd)
19. [Technology Stack](#19-technology-stack)
20. [Documentation](#20-documentation)

---

## 1. What This System Does

| Capability | Detail |
|---|---|
| **Universe screening** | Scans Nifty 500 / custom list every trading day at 15:35 IST |
| **Stage detection** | Hard-gates Stage 1 / 2 / 3 / 4 — only Stage 2 is buyable |
| **Trend Template** | All 8 Minervini conditions with configurable thresholds |
| **VCP detection** | Pivot-to-pivot contraction, volume dry-up, base tightness |
| **Fundamentals** | 7 Minervini conditions scraped from Screener.in (7-day cache) |
| **News sentiment** | RSS + NewsData.io keyword scoring, LLM re-scoring per symbol |
| **Composite scoring** | Weighted 0–100 score → A+ / A / B / C / FAIL quality tag |
| **LLM trade briefs** | 3-sentence AI narrative per A+/A setup (Groq free tier default) |
| **Reports** | Daily HTML + CSV watchlist with charts |
| **Alerts** | Telegram, email (SMTP), generic webhooks (Slack / Discord) |
| **Paper trading** | Automatic entry/exit/pyramiding simulation on every daily run |
| **Backtesting** | Walk-forward backtester with trailing stop + market regime labelling |
| **REST API** | FastAPI layer for frontend consumption (port 8000) |
| **Streamlit dashboard** | Visual monitoring UI — no SSH needed (port 8501) |
| **Next.js frontend** | Mobile-friendly production web app (Vercel-deployable) |

### Core Design Mandates

- **Rules are code, not prompts.** The SEPA rule engine is pure Python — deterministic and testable.
- **LLM is a narrator, not a decision-maker.** AI generates explanatory text only; it never scores or filters.
- **Stage 2 is a hard gate.** Any stock not in Stage 2 is eliminated immediately, regardless of other conditions.
- **Fail loudly.** Data quality issues raise exceptions; they are never silently swallowed.
- **Reproducibility.** Every screen run is logged with inputs, outputs, config hash, and Git SHA.

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
          ▼               ▼                ▼
   Raw Parquet     Feature Parquet    SEPA Candidates
                                           │
                         ┌─────────────────┼──────────────┐
                         ▼                 ▼              ▼
                    ┌─────────┐      ┌──────────┐   ┌─────────┐
                    │  RULE   │      │   LLM    │   │ ALERTS  │
                    │ ENGINE  │      │EXPLAINER │   │         │
                    └────┬────┘      └──────────┘   └─────────┘
                         ▼
                   ┌──────────────┐
                   │   REPORTS    │
                   │  HTML + CSV  │
                   └──────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
       ┌─────────────┐      ┌──────────────┐
       │  FASTAPI    │      │  STREAMLIT   │
       │  port 8000  │      │  port 8501   │
       └──────┬──────┘      └──────────────┘
              │
              ▼
       ┌──────────────┐
       │  NEXT.JS     │
       │  (Vercel)    │
       └──────────────┘
```

### Data Flow

```
Raw OHLCV (NSE / yfinance)
    ▼ ingestion/
Validated + Cleaned Parquet (per symbol)
    ▼ features/
Technical Indicators (MAs, ATR, RS, pivots, VCP metrics)
    ▼ screener/
Trend Template pass/fail → Stage detection → VCP → Entry trigger
    ▼ rules/
SEPA Score (0–100) + Setup Quality (A+ / A / B / C / FAIL)
    ├──▶ llm/explainer.py  →  AI trade brief (optional)
    ▼ reports/
Daily Watchlist (HTML + CSV) + Chart PNGs + Alert dispatch
```

---

## 3. Build Status

All 12 phases are complete as of **2026-04-13**.

| Phase | Name | Status |
|---|---|---|
| 1 | Foundation — ingestion, storage, universe | ✅ Complete |
| 2 | Feature Engineering — MAs, ATR, RS, VCP, pivots | ✅ Complete |
| 3 | Rule Engine — Stage, Trend Template, VCP, scorer | ✅ Complete |
| 4 | Reports, Charts & Alerts | ✅ Complete |
| 5 | Fundamentals & News Sentiment | ✅ Complete |
| 6 | LLM Narrative Layer (Groq / Anthropic / OpenAI / Ollama) | ✅ Complete |
| 7 | Paper Trading Simulator | ✅ Complete |
| 8 | Backtesting Engine (walk-forward + trailing stop) | ✅ Complete |
| 9 | Hardening & Production (systemd, CI, Prometheus) | ✅ Complete |
| 10 | API Layer (FastAPI, 21 unit tests) | ✅ Complete |
| 11 | Streamlit Dashboard MVP (5 pages, 3 components) | ✅ Complete |
| 12 | Next.js Production Frontend (Vercel-deployable) | ✅ Complete |

---

## 4. Project Structure

```
minervini_ai/
├── api/                    # FastAPI REST layer (port 8000)
│   ├── routers/            # stocks, watchlist, portfolio, health
│   └── schemas/            # Pydantic response models
├── alerts/                 # Telegram, email (SMTP), webhook dispatchers
├── backtest/               # Walk-forward backtester + regime labelling
├── config/
│   ├── settings.yaml       # All tunable parameters
│   ├── universe.yaml       # Stock universe definition
│   ├── logging.yaml        # Log levels per module
│   └── symbol_aliases.yaml # Symbol → news alias map
├── dashboard/              # Streamlit MVP (port 8501)
│   ├── pages/              # 5 pages: Watchlist, Screener, Stock, Portfolio, Backtest
│   └── components/         # charts.py, tables.py, metrics.py
├── data/                   # All runtime data (gitignored)
│   ├── raw/                # Immutable raw OHLCV Parquet (append-only)
│   ├── processed/          # Cleaned, validated OHLCV per symbol
│   ├── features/           # Feature-engineered Parquet per symbol
│   ├── fundamentals/       # Screener.in cache (7-day TTL)
│   ├── news/               # RSS news cache (30-min TTL)
│   ├── charts/             # Generated candlestick PNGs
│   └── paper_trading/      # Portfolio state, trade history, pending orders
├── deploy/                 # systemd service + timer unit files
├── features/               # Feature modules (MAs, ATR, RS, pivots, VCP)
├── frontend/               # Next.js 14 production frontend
│   └── app/                # Dashboard, Screener, Watchlist, Portfolio pages
├── ingestion/              # Data sources (yfinance, fundamentals, news)
├── llm/                    # LLM explainer + multi-provider client
│   └── prompt_templates/   # Jinja2 templates (trade_brief, watchlist_summary)
├── notebooks/              # Jupyter notebooks for research
├── paper_trading/          # Simulator, portfolio, order queue, report
├── pipeline/               # runner.py (13-step orchestrator), scheduler, context
├── reports/                # Daily watchlist CSV/HTML generator, chart generator
│   └── templates/          # Jinja2 HTML template
├── rules/                  # Stage, Trend Template, VCP, scorer, stop loss, R:R
├── screener/               # Batch pipeline + results persistence
├── scripts/
│   ├── run_daily.py        # Main CLI entry point
│   ├── bootstrap.py        # Full history download + feature compute
│   ├── backtest_runner.py  # Backtest CLI
│   └── rebuild_features.py # Recompute features from existing OHLCV
├── storage/                # Parquet + SQLite helpers
├── tests/
│   ├── unit/               # 500+ unit tests
│   └── integration/        # Known-setup regression tests
├── utils/                  # Logger, date utils, math utils, exceptions
├── .env.example            # Template for environment variables
├── Makefile                # Convenience targets
├── pyproject.toml          # Project metadata + tool config
├── requirements.txt        # Runtime dependencies
├── requirements-dev.txt    # Dev + test dependencies
├── COMMANDS.md             # End-user command reference
├── DEV_SETUP.md            # First-time environment setup guide
├── PROJECT_DESIGN.md       # Full architecture + design decisions
└── RUNBOOK.md              # Operations guide
```

---

## 5. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Ubuntu | 24.04 (recommended) | Other Debian-based distros work |
| Python | 3.11+ | Install via `deadsnakes/ppa` if not present |
| Git | Any recent | For clone + version tracking |
| SQLite | 3.x | Usually pre-installed on Ubuntu |
| fonts-dejavu-core | Any | Required by mplfinance for chart generation |

**Optional (needed for specific phases):**

| Requirement | When Needed | Get It |
|---|---|---|
| Groq API key | LLM trade briefs (Phase 6) | [console.groq.com](https://console.groq.com) — free |
| Telegram bot token | Telegram alerts (Phase 4) | `@BotFather` on Telegram |
| Node.js 20 LTS | Next.js frontend (Phase 12) | via `nvm` |

---

## 6. Installation

### 6.1 Clone the repository

```bash
git clone https://github.com/ajaydeloli/minervini_ai.git
cd minervini_ai
```

### 6.2 Create and activate a virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 6.3 Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements-dev.txt   # includes runtime + dev/test deps
pip install -e .                       # install project in editable mode
```

### 6.4 Set up environment variables

```bash
cp .env.example .env
nano .env   # fill in your values (see Section 7 for what's needed)
```

Minimum required to run a daily screen (Phases 1–5): **nothing** — yfinance and Screener.in are both free with no API key.

### 6.5 Verify the installation

```bash
python -c "import ingestion; import features; import rules; print('All packages importable ✓')"
python -c "import yfinance as yf; df=yf.download('RELIANCE.NS', period='5d', progress=False); print('yfinance OK —', len(df), 'rows')"
pytest tests/ -v --tb=short   # should show 500+ passing
```

For full first-time setup instructions (OS packages, venv, config files, VS Code remote, systemd), see **[DEV_SETUP.md](DEV_SETUP.md)**.

---

## 7. Configuration

All runtime parameters live in `config/settings.yaml`. Key sections:

```yaml
universe:
  source: "yfinance"        # data source: yfinance | nse_bhav | csv
  index: "nifty500"
  min_price: 50             # INR minimum price filter
  min_avg_volume: 100000    # shares/day minimum volume filter

trend_template:
  pct_above_52w_low: 25.0   # stock must be >= 25% above 52-week low
  pct_below_52w_high: 25.0  # stock must be within 25% of 52-week high
  min_rs_rating: 70         # Relative Strength Rating minimum (0–99)

scoring:
  weights:
    rs_rating: 0.30         # weights must sum to 1.0
    trend: 0.25
    vcp: 0.25
    volume: 0.10
    fundamental: 0.07
    news: 0.03
  setup_quality_thresholds:
    a_plus: 85
    a: 70
    b: 55
    c: 40

llm:
  enabled: false            # set to true after adding a GROQ_API_KEY
  provider: "groq"          # groq | anthropic | openai | openrouter | ollama
  model: "llama-3.3-70b-versatile"
  only_for_quality: ["A+", "A"]

paper_trading:
  enabled: false            # enable when ready to start simulating
  initial_capital: 100000   # INR
  risk_per_trade_pct: 2.0
```

### Required `.env` variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | LLM phase only | Free at console.groq.com |
| `ANTHROPIC_API_KEY` | Optional LLM | claude-haiku-4-5 |
| `OPENAI_API_KEY` | Optional LLM | gpt-4o-mini |
| `TELEGRAM_BOT_TOKEN` | Alerts phase | From @BotFather |
| `TELEGRAM_CHAT_ID` | Alerts phase | Your chat/channel ID |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` | Email alerts | Gmail or any SMTP |
| `API_READ_KEY` | API phase | Any random hex string |
| `API_ADMIN_KEY` | API phase | Different random hex string |
| `NEWSDATA_API_KEY` | Optional news | Free tier at newsdata.io |

Generate secure API keys:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 8. Running the System

### Activate the virtual environment first (every session)

```bash
cd /home/ubuntu/projects/minervini_ai
source .venv/bin/activate
```

### 8.1 Bootstrap — first-time full history download

Run once on initial setup. Downloads 5–10 years of OHLCV and computes all features.

```bash
# Bootstrap universe defined in config/universe.yaml  (two equivalent forms)
python scripts/bootstrap.py --universe config    # ~5–15 min for 500 symbols
python scripts/bootstrap.py --universe list      # identical — "config" and "list" are aliases

# Bootstrap universe + watchlist together
python scripts/bootstrap.py --universe all

# Use a historical anchor date (end of download window; --years counts back from here)
python scripts/bootstrap.py --universe config --date 2024-01-15

# Force re-download even when processed files already exist
python scripts/bootstrap.py --universe config --force

# Dry-run — print what would be downloaded, no writes
python scripts/bootstrap.py --universe config --dry-run

# Download OHLCV only, skip feature computation
python scripts/bootstrap.py --universe config --skip-features
```

> **`--universe` values:** `config` and `list` are identical (both read `config/universe.yaml`).
> `nifty500` uses the Nifty 500 placeholder. `all` adds the SQLite watchlist on top.
>
> **`--date`:** Defaults to today (IST). Pass any `YYYY-MM-DD` date to anchor the right
> edge of the download window — useful for reproducible historical setups.

### 8.2 Daily screen

```bash
# Full universe + watchlist (default)
python scripts/run_daily.py --date today

# Specific past date (backfill)
python scripts/run_daily.py --date 2024-01-15

# Screen only specific symbols
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON"

# Load a watchlist file and screen those symbols only
python scripts/run_daily.py --watchlist mylist.csv --watchlist-only

# Dry-run: resolve symbols and print the plan — no DB writes, no pipeline
python scripts/run_daily.py --date today --dry-run
```

### 8.3 Scope options

| Flag | Effect |
|---|---|
| `--scope all` | Universe + watchlist (default) |
| `--scope universe` | Universe only |
| `--scope watchlist` | Watchlist only |
| `--watchlist-only` | Shorthand for `--scope watchlist` |
| `--symbols "A,B,C"` | Overrides all other sources |

### 8.4 Makefile shortcuts

```bash
make daily          # run today's screen
make test           # full test suite with coverage
make lint           # ruff check (no changes)
make format         # ruff format (applies fixes)
make api            # start FastAPI on port 8000
make dashboard      # start Streamlit on port 8501
make paper-reset    # reset paper trading portfolio
make rebuild        # recompute all features from existing OHLCV
```

---

## 9. Watchlist Management

The system maintains two independent symbol lists:

| List | Source | Purpose |
|---|---|---|
| **Universe** | `config/universe.yaml` | Full scan pool — changed rarely |
| **Watchlist** | SQLite `watchlist` table | Your curated symbols — managed dynamically |

Watchlist symbols are always scanned, appear first in reports with a ★ badge, always get charts generated, and have a lower alert threshold (`min_score_alert: 55` vs 70 for universe).

### Adding symbols

```bash
# Via CLI file (CSV / JSON / XLSX / TXT)
python scripts/run_daily.py --watchlist mylist.csv

# Inline (not persisted — ad-hoc only)
python scripts/run_daily.py --symbols "DIXON,CDSL,TATAELXSI"

# Via SQLite directly
sqlite3 data/minervini.db \
  "INSERT INTO watchlist (symbol, note, added_via) VALUES ('DIXON', 'VCP forming', 'cli');"

# Via API (Phase 10+)
curl -X POST -H "X-API-Key: <admin_key>" http://localhost:8000/api/v1/watchlist/DIXON

# Upload a file via API
curl -X POST -H "X-API-Key: <admin_key>" \
     -F "file=@mylist.csv" http://localhost:8000/api/v1/watchlist/upload
```

### Supported watchlist file formats

| Format | Structure |
|---|---|
| `.csv` | Column named `symbol` or first column |
| `.json` | `["RELIANCE", "TCS", "DIXON"]` |
| `.xlsx` | First sheet, `symbol` column or column A |
| `.txt` | One symbol per line; `#` lines are comments |

### Viewing the watchlist

```bash
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, last_score, last_quality, added_at FROM watchlist ORDER BY symbol;"
```

---

## 10. API Server

The FastAPI layer exposes all screener data over HTTP. It is **read-only** for pipeline data — it queries SQLite and Parquet but never writes to them.

### Start the server

```bash
# Development (auto-reload)
uvicorn api.main:app --reload --port 8000

# Production (multi-worker)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
```

### Authentication

Pass your API key as the `X-API-Key` header:
- **Read key** (`API_READ_KEY`) — all GET endpoints
- **Admin key** (`API_ADMIN_KEY`) — POST endpoints including `/api/v1/run`

### Key endpoints

```
── Screener ───────────────────────────────────────────────────────────
GET  /api/v1/stocks/top          Top-ranked setups (filter: quality, limit, date)
GET  /api/v1/stocks/trend        All Trend Template passes today
GET  /api/v1/stocks/vcp          Stocks with a qualified VCP pattern
GET  /api/v1/stock/{symbol}      Full SEPAResult for one symbol
GET  /api/v1/stock/{symbol}/history  Historical scores (last 30 days)

── Watchlist ──────────────────────────────────────────────────────────
GET    /api/v1/watchlist                 List all watchlist symbols
POST   /api/v1/watchlist/{symbol}        Add one symbol
DELETE /api/v1/watchlist/{symbol}        Remove one symbol
POST   /api/v1/watchlist/bulk            Add multiple: {"symbols": [...]}
POST   /api/v1/watchlist/upload          Upload CSV/JSON/XLSX file
DELETE /api/v1/watchlist                 Clear all (admin key required)

── Paper Trading ──────────────────────────────────────────────────────
GET  /api/v1/portfolio                   Portfolio summary (P&L, positions)
GET  /api/v1/portfolio/trades            Trade history (?status=open|closed|all)

── System ─────────────────────────────────────────────────────────────
GET  /api/v1/health                      {"status":"ok","last_run":"..."}
GET  /api/v1/meta                        Universe size, A+ count, last run date
GET  /metrics                            Prometheus metrics endpoint

── Trigger a run (admin only) ─────────────────────────────────────────
POST /api/v1/run                         {"scope":"all"|"universe"|"watchlist"}
                                         {"symbols":["DIXON","TCS"]}
```

### Example calls

```bash
# Top setups for today, A+ only
curl -H "X-API-Key: $API_READ_KEY" \
  "http://localhost:8000/api/v1/stocks/top?quality=A%2B"

# Full SEPA result for DIXON
curl -H "X-API-Key: $API_READ_KEY" \
  http://localhost:8000/api/v1/stock/DIXON

# Trigger a watchlist-only run
curl -X POST \
  -H "X-API-Key: $API_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scope":"watchlist"}' \
  http://localhost:8000/api/v1/run
```

All responses use a consistent envelope:
```json
{
  "success": true,
  "data": { ... },
  "meta": { "date": "2024-01-15", "total": 3 }
}
```

---

## 11. Streamlit Dashboard

A visual monitoring UI — no SSH, no API key required once running.

```bash
streamlit run dashboard/app.py --server.port 8501
# Open: http://<server-ip>:8501
```

| Page | Contents |
|---|---|
| **Watchlist** | File upload widget, manual symbol entry, daily A+/A results, [Run Now] button |
| **Screener** | Full universe table with quality / stage / RS / sector filters, CSV export |
| **Stock** | Candlestick chart (MA ribbons + VCP zone), Trend Template checklist, fundamentals, LLM brief |
| **Portfolio** | Paper trading P&L, open positions, equity curve |
| **Backtest** | Backtest equity curve with regime shading, per-regime stats, parameter sweep |

---

## 12. Next.js Frontend

A mobile-friendly production web app that talks exclusively to the FastAPI layer.

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
npm run build        # production build (0 TypeScript errors)
```

Deploy to Vercel (zero-config):
```bash
# Set environment variables in Vercel dashboard:
# NEXT_PUBLIC_API_URL, NEXT_PUBLIC_API_READ_KEY
vercel --prod
```

| Page | Route |
|---|---|
| Dashboard home | `/` |
| Full screener | `/screener` |
| Stock deep-dive | `/screener/[symbol]` |
| Watchlist | `/watchlist` |
| Paper portfolio | `/portfolio` |

---

## 13. Paper Trading

Paper trading automatically runs after every daily screen — no manual action required once enabled.

```yaml
# config/settings.yaml
paper_trading:
  enabled: true
  initial_capital: 100000    # INR — starting capital
  max_positions: 10
  risk_per_trade_pct: 2.0    # 2% of portfolio risked per trade
  min_score_to_trade: 70     # only enter A+/A setups
```

**Entry rules:** Score ≥ 70, quality ∈ {A+, A}, no duplicate symbol, ≤ 10 open positions.

**Market-hours awareness:** Signals generated after 15:30 IST are queued to `pending_orders.json` and filled at the next open (9:15 IST). Never fills at after-hours prices.

**Pyramiding:** Adds 50% of original qty when VCP grade = A, volume ratio < 0.4, and price is within 2% of pivot. One pyramid per position.

**Reset the paper portfolio:**
```bash
make paper-reset
```

**View portfolio via API:**
```bash
curl -H "X-API-Key: $API_READ_KEY" http://localhost:8000/api/v1/portfolio
```

---

## 14. Backtesting

Walk-forward backtester with trailing stop and NSE market regime labelling (Bull / Bear / Sideways).

```bash
# 5-year backtest, Nifty 500, 7% trailing stop
python scripts/backtest_runner.py \
  --start 2019-01-01 \
  --end   2024-01-01 \
  --universe nifty500 \
  --trailing-stop 0.07

# Makefile shortcut
make backtest START=2019-01-01 END=2024-01-01
```

Output: HTML report + CSV in `data/backtests/` with equity curve, per-regime breakdown (win rate, avg P&L, trade count), and parameter sweep comparison.

Key metrics reported: CAGR, Sharpe ratio, max drawdown, win rate, average R-multiple, profit factor, expectancy.

---

## 15. Alerts

### Telegram

```bash
# .env
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_CHAT_ID=<your channel/chat ID>
```

```yaml
# config/settings.yaml
alerts:
  telegram:
    enabled: true
    min_quality: "A"    # alert on A+ and A setups
```

### Email (SMTP)

```bash
# .env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your-app-password
```

### Webhook (Slack / Discord / custom)

Set `WEBHOOK_URLS` in `.env` as a comma-separated list. The webhook dispatcher sends Slack-compatible JSON blocks.

---

## 16. Testing

```bash
# Full test suite (500+ tests)
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=. --cov-report=term-missing

# Unit tests only
pytest tests/unit/ -v

# Specific module
pytest tests/unit/test_trend_template.py -v

# Keyword filter
pytest tests/ -v -k "watchlist or stage"

# Makefile
make test
```

The test suite covers:

- All 8 Trend Template conditions (parametrized pass/fail per condition)
- Stage 1/2/3/4 detection + Stage 4 hard gate regression
- VCP qualification rules (Grade A/B/C/FAIL)
- Composite scorer + quality tag thresholds
- Screener pipeline (parallel execution, sort order, None handling)
- Paper trading (enter/exit/pyramid gates, market-hours queue, P&L)
- API endpoints (21 TestClient tests — health, stocks, watchlist, portfolio, auth, rate-limit)
- Storage layer (Parquet atomic append, SQLite run history)
- Feature modules (MAs, ATR, RS, volume, pivots, VCP)

---

## 17. Linting & Formatting

```bash
# Check for linting errors (no changes)
ruff check .

# Auto-fix issues
ruff check . --fix

# Format all files
ruff format .

# CI-safe check (exits non-zero if formatting needed)
ruff format --check .

# Run both checks together
ruff check . && ruff format --check .
```

The project targets `line-length = 100` and `python 3.11` — configured in `pyproject.toml`.

---

## 18. Production Deployment (systemd)

Three systemd services run on the server. All unit files are in `deploy/`.

| Service | Type | Purpose |
|---|---|---|
| `minervini-daily.timer` | Timer | Fires Mon–Fri 15:35 IST (`Persistent=true`) |
| `minervini-daily.service` | Oneshot | Runs `run_daily.py --date today` |
| `minervini-api.service` | Always-on | FastAPI on port 8000 |
| `minervini-dashboard.service` | Always-on | Streamlit on port 8501 |

### Install (one-time)

```bash
sudo bash deploy/install.sh
```

### Common operations

```bash
# Check status of all services
systemctl status minervini-daily.timer minervini-api.service minervini-dashboard.service

# View logs in real time
journalctl -u minervini-api.service -f
journalctl -u minervini-daily.service --since today

# Trigger a manual run immediately (bypasses timer schedule)
sudo systemctl start minervini-daily.service

# Restart after code changes
sudo systemctl restart minervini-api.service minervini-dashboard.service

# Check when the timer fires next
systemctl list-timers --all | grep minervini
```

### Deploy code changes

```bash
git pull
sudo systemctl restart minervini-api.service minervini-dashboard.service
# The daily service picks up new code automatically on next timer fire
```

For full systemd setup details, see **[DEV_SETUP.md § 12](DEV_SETUP.md)**.
For operational procedures, error fixes, and performance benchmarks, see **[RUNBOOK.md](RUNBOOK.md)**.

---

## 19. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.11+ | Ecosystem + speed of development |
| Data storage | Parquet (pyarrow) | Columnar, fast for time-series reads |
| Metadata / results | SQLite | Zero-ops, portable, auditable |
| Data manipulation | pandas + numpy | OHLCV ecosystem compatibility |
| Technical indicators | Custom (features/) | Full control, no TA-Lib dependency |
| Parallelism | ProcessPoolExecutor | CPU-bound feature computation |
| Charts | matplotlib + mplfinance | Reproducible, no JS dependency |
| HTML reports | Jinja2 | Clean separation of logic and template |
| LLM (default) | Groq — llama-3.3-70b-versatile | Free, fast, sufficient for narratives |
| LLM (alternatives) | Anthropic, OpenAI, OpenRouter, Ollama, Gemini | All pluggable via config |
| Alerting | python-telegram-bot | Direct, free, reliable |
| Config | PyYAML + pydantic | Validated, typed config objects |
| API | FastAPI + uvicorn | Fast, auto-documented, type-safe |
| Rate limiting | slowapi | Per-IP limits on API endpoints |
| Scheduling | APScheduler | No Celery overhead |
| Dashboard (MVP) | Streamlit | Python-native, zero JS |
| Frontend (prod) | Next.js 14 + Tailwind | Mobile-friendly, Vercel-deployable |
| Charts (frontend) | TradingView lightweight-charts | Native candlestick, fast, free |
| Testing | pytest + pytest-cov | Standard |
| Linting | ruff | Fast, consistent |
| CI | GitHub Actions | Lint + unit tests (Python 3.11 + 3.12 matrix) |
| Monitoring | Prometheus `/metrics` | Gauge-based run stats |

### Polars upgrade path

The pandas stack is intentional for now (ecosystem compatibility, sufficient speed at 500–2000 symbols). Every feature module already uses the interface `compute(df: pd.DataFrame, config: dict) -> pd.DataFrame` — Polars can be swapped in per-module without touching anything else. A `FEATURE_BACKEND=polars` env-var toggle is planned for when the universe scales.

---

## 20. Documentation

| Document | Purpose |
|---|---|
| **[PROJECT_DESIGN.md](PROJECT_DESIGN.md)** | Full architecture, module specs, data flow, phase roadmap, design principles — the canonical reference |
| **[DEV_SETUP.md](DEV_SETUP.md)** | First-time environment setup: OS packages, Python, venv, config files, VS Code remote, systemd install |
| **[COMMANDS.md](COMMANDS.md)** | End-user command reference: every CLI flag, API curl examples, SQLite queries, log inspection |
| **[RUNBOOK.md](RUNBOOK.md)** | Operations guide: daily ops, adding symbols, recovering corrupt stores, adding rules, tuning thresholds, error fixes |
| **[frontend/README.md](frontend/README.md)** | Next.js frontend setup, env vars, Vercel deployment, project structure |

---

## Quick Reference

```bash
# ── Activate (every session) ───────────────────────────────────────────
source .venv/bin/activate

# ── Daily run ─────────────────────────────────────────────────────────
python scripts/run_daily.py --date today
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON"
python scripts/run_daily.py --watchlist mylist.csv --watchlist-only
python scripts/run_daily.py --date today --dry-run

# ── Servers ───────────────────────────────────────────────────────────
uvicorn api.main:app --reload --port 8000    # API
streamlit run dashboard/app.py --server.port 8501    # Dashboard

# ── Make ──────────────────────────────────────────────────────────────
make daily          make test           make lint
make format         make api            make dashboard
make rebuild        make paper-reset

# ── SQLite queries ────────────────────────────────────────────────────
# Today's top 20 by score:
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, setup_quality, score, stage, rs_rating FROM sepa_results
   WHERE run_date = date('now') ORDER BY score DESC LIMIT 20;"

# Last 5 pipeline runs:
sqlite3 -column -header data/minervini.db \
  "SELECT run_date, status, duration_sec, a_plus_count, a_count
   FROM run_history ORDER BY id DESC LIMIT 5;"

# ── Logs ──────────────────────────────────────────────────────────────
tail -f logs/minervini.log
journalctl -u minervini-api.service -f
journalctl -u minervini-daily.service --since today
```

---

*Built on Mark Minervini's SEPA methodology — "Trade Like a Stock Market Wizard" (2013)*
