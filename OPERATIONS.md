# OPERATIONS.md — Minervini AI

> **Version:** 1.0 · **System:** ShreeVault (Ubuntu 24.04)
> **Project path:** `/home/ubuntu/projects/minervini_ai`
> **Python:** 3.11+ · **DB:** `data/minervini.db`
>
> This is the single authoritative operations guide. It replaces
> `COMMANDS.md`, `DEV_SETUP.md`, and `RUNBOOK.md`.
> For architecture and design decisions see `PROJECT_DESIGN.md`.
> For a high-level feature overview see `README.md`.

---

## Table of Contents

1. [Overview](#1-overview)
2. [First-Time Environment Setup](#2-first-time-environment-setup)
3. [Project Structure](#3-project-structure)
4. [Configuration Reference](#4-configuration-reference)
5. [CLI Scripts Reference](#5-cli-scripts-reference)
6. [Makefile Targets](#6-makefile-targets)
7. [Services](#7-services)
8. [Watchlist Management](#8-watchlist-management)
9. [API Endpoints](#9-api-endpoints)
10. [Paper Trading](#10-paper-trading)
11. [Backtesting](#11-backtesting)
12. [Alerts](#12-alerts)
13. [Testing](#13-testing)
14. [Code Quality — Linting & Formatting](#14-code-quality--linting--formatting)
15. [Production Deployment (systemd)](#15-production-deployment-systemd)
16. [Docker Deployment](#16-docker-deployment)
17. [SQLite Quick Reference](#17-sqlite-quick-reference)
18. [Log Inspection](#18-log-inspection)
19. [Day-to-Day Operations Runbook](#19-day-to-day-operations-runbook)
20. [Git Workflow](#20-git-workflow)
21. [Editor Setup — VS Code Remote](#21-editor-setup--vs-code-remote)

---

## 1. Overview

Minervini AI is a fully automated stock screening system for NSE/Indian equities
built on Mark Minervini's SEPA methodology. It runs every trading day at 15:35 IST,
screens hundreds of stocks, scores each setup 0–100, generates LLM trade briefs,
and dispatches alerts through Telegram or email.

All 12 build phases are complete:

| Phase | Name |
|---|---|
| 1 | Foundation — ingestion, storage, universe |
| 2 | Feature Engineering — MAs, ATR, RS, VCP, pivots |
| 3 | Rule Engine — Stage, Trend Template, VCP, scorer |
| 4 | Reports, Charts & Alerts |
| 5 | Fundamentals & News Sentiment |
| 6 | LLM Narrative Layer |
| 7 | Paper Trading Simulator |
| 8 | Backtesting Engine |
| 9 | Hardening & Production (systemd, CI, Prometheus) |
| 10 | API Layer (FastAPI) |
| 11 | Streamlit Dashboard MVP |
| 12 | Next.js Production Frontend |

---

## 2. First-Time Environment Setup

### 2.1 System Prerequisites

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y \
    build-essential git curl wget unzip software-properties-common \
    libssl-dev libffi-dev libxml2-dev libxslt1-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev liblzma-dev libncurses-dev \
    sqlite3 libsqlite3-dev \
    fonts-dejavu-core fontconfig \
    htop tree jq
```

### 2.2 Python Setup

```bash
# Verify Python 3.11+ is available
python3 --version

# If not present, install via deadsnakes
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11
```

### 2.3 Clone and Enter the Project

```bash
git clone https://github.com/ajaydeloli/minervini_ai.git
cd /home/ubuntu/projects/minervini_ai
```

### 2.4 Virtual Environment & Dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip setuptools wheel
pip install -r requirements.txt        # full pinned lockfile
pip install -e .                       # install project in editable mode
```

Verify:
```bash
which python          # → .venv/bin/python
python -c "import ingestion, features, rules; print('All packages importable ✓')"
python -c "import yfinance as yf; df=yf.download('RELIANCE.NS', period='5d', progress=False); print('yfinance OK —', len(df), 'rows')"
```

### 2.5 Environment Variables

```bash
cp .env.example .env
nano .env      # fill in your values
```

Minimum needed for Phases 1–5 (data + screening): **nothing** — yfinance and
Screener.in are free with no key. Add the extras when you reach that phase:

| Variable | When Needed |
|---|---|
| `GROQ_API_KEY` | LLM trade briefs (Phase 6) — free at console.groq.com |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram alerts (Phase 4) |
| `API_READ_KEY` + `API_ADMIN_KEY` | API server (Phase 10) — any random string |
| `NEWSDATA_API_KEY` | Optional news feed |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Alternative LLM providers |

Generate secure API keys:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2.6 Bootstrap Historical Data (One-Time)

Run once on initial setup. Downloads 5 years of OHLCV history and computes all
features for the symbols defined in `config/universe.yaml`.

```bash
python scripts/bootstrap.py --universe config
# ~5–15 min for the default 19-symbol universe
```

### 2.7 Verify Full Pipeline

```bash
python scripts/run_daily.py --date today --dry-run   # smoke-test, no DB writes
pytest tests/ -v --tb=short                          # should show 900+ passing
```

---

## 3. Project Structure

```
minervini_ai/
├── api/                    # FastAPI REST layer (port 8000)
│   ├── routers/            # stocks, watchlist, portfolio, health, run, backtest
│   └── schemas/            # Pydantic response models
├── alerts/                 # Telegram, email (SMTP), webhook dispatchers
├── backtest/               # Walk-forward backtester + regime labelling
├── config/
│   ├── settings.yaml       # All tunable runtime parameters
│   ├── universe.yaml       # Stock universe definition (symbols + filters)
│   ├── logging.yaml        # Log levels per module
│   └── symbol_aliases.yaml # Symbol → news alias map
├── dashboard/              # Streamlit MVP (port 8501)
│   ├── pages/              # 01_Watchlist, 02_Screener, 03_Stock, 04_Portfolio, 05_Backtest
│   └── components/         # charts.py, tables.py, metrics.py
├── data/                   # All runtime data (gitignored)
│   ├── raw/                # Immutable raw OHLCV Parquet (append-only)
│   ├── processed/          # Cleaned, validated OHLCV per symbol
│   ├── features/           # Feature-engineered Parquet per symbol
│   ├── fundamentals/       # Screener.in cache (7-day TTL)
│   ├── news/               # RSS news cache (30-min TTL)
│   ├── charts/             # Generated candlestick PNGs
│   ├── paper_trading/      # Portfolio state, trade history, pending orders
│   ├── backtests/          # Backtest HTML/CSV reports
│   ├── benchmarks/         # Feature pipeline benchmark JSON results
│   ├── metadata/           # symbol_info.csv (sector/industry/mktcap)
│   └── reports/            # Daily watchlist HTML + CSV
├── deploy/                 # systemd service + timer unit files + install.sh
├── features/               # Feature modules: moving_averages, atr, relative_strength,
│                           #   pivot, vcp, volume, feature_store
├── frontend/               # Next.js 14 production frontend (Vercel-deployable)
│   └── app/                # Dashboard, Screener /[symbol], Watchlist, Portfolio, Backtest
├── ingestion/              # Data sources (yfinance, nse_bhav, fundamentals, news)
├── llm/                    # LLM explainer + multi-provider client + Jinja2 prompt templates
├── notebooks/              # Jupyter research notebooks
├── paper_trading/          # Simulator, portfolio, order queue, report
├── pipeline/               # runner.py (13-step orchestrator), scheduler, context
├── reports/                # Daily watchlist CSV/HTML generator, chart generator
├── rules/                  # Stage, Trend Template, VCP rules, scorer, stop_loss, R:R
├── screener/               # Batch pipeline, results persistence (sepa_results table)
├── scripts/
│   ├── run_daily.py        # Main CLI entry point
│   ├── bootstrap.py        # Full history download + feature compute
│   ├── backtest_runner.py  # Backtest CLI
│   ├── rebuild_features.py # Recompute features from existing OHLCV
│   ├── benchmark_features.py  # Feature pipeline performance benchmark
│   └── show_run_history.py    # Print run_history from SQLite
├── storage/                # parquet_store.py, sqlite_store.py
├── tests/
│   ├── unit/               # 35+ test files covering all modules
│   └── integration/        # Known-setup regression + pipeline e2e tests
├── utils/                  # logger, date_utils, exceptions, math_utils, env_check, run_meta
├── .env.example            # Template for environment variables
├── Makefile                # Convenience targets
├── pyproject.toml          # Project metadata + ruff/pytest config
├── requirements.txt        # Pinned runtime dependencies (full lockfile)
├── requirements-dev.txt    # Dev/test extras (references requirements.txt)
└── docker-compose.yml      # Docker: api + dashboard + scheduler services
```

---

## 4. Configuration Reference

### 4.1 `config/settings.yaml` — Annotated Reference

The full current state of every tunable section:

```yaml
universe:
  source: "yfinance"              # nse_bhav | yfinance | csv
  index: "nifty500"
  min_price: 50                   # INR minimum price filter
  min_avg_volume: 100000          # shares/day minimum volume filter
  min_market_cap_cr: 500          # crore INR

data:
  raw_dir: "data/raw"
  processed_dir: "data/processed"
  features_dir: "data/features"
  fundamentals_dir: "data/fundamentals"
  news_dir: "data/news"

chart:
  output_dir: "data/charts"       # must match _CHART_DIR in dashboard/components/charts.py
  lookback_days: 90               # number of trading-day candles to render

watchlist:
  always_scan: true
  priority_in_reports: true
  always_generate_charts: true

stage:
  ma200_slope_lookback: 20
  ma50_slope_lookback: 10

trend_template:
  ma200_slope_lookback: 20
  pct_above_52w_low: 25.0         # stock must be >= 25% above 52-week low
  pct_below_52w_high: 25.0        # stock must be within 25% of 52-week high
  min_rs_rating: 70               # Relative Strength Rating minimum (0–99)

vcp:
  detector: "rule_based"          # rule_based | cnn (cnn reserved)
  min_contractions: 2
  max_contractions: 5
  require_declining_depth: true   # each successive leg must be shallower
  require_vol_contraction: true   # last-leg avg volume < first-leg avg volume
  min_weeks: 3
  max_weeks: 52
  tightness_pct: 10.0             # max final-leg depth (%) — tighter = higher quality
  max_depth_pct: 50.0             # max any-leg depth (%)
  vol_contraction_ratio: 0.7      # last-leg vol / first-leg vol must be < this
  min_contraction_depth_pct: 3.0  # minimum % pullback to count as a contraction
  pivot_sensitivity: 0.05         # ZigZag threshold as fraction of price (5%)
  pivot_window: 5                 # bars on each side to confirm a swing pivot

fundamentals:
  enabled: true
  hard_gate: false                # if true, FAIL any stock that fails fundamentals
  cache_days: 7
  conditions:
    min_roe: 15.0
    max_de: 1.0
    min_promoter_holding: 35.0
    min_sales_growth_yoy: 10.0

news:
  enabled: true
  cache_minutes: 30
  llm_rescore: false

scoring:
  weights:                        # must sum to 1.0
    rs_rating: 0.30
    trend: 0.25
    vcp: 0.25
    volume: 0.10
    fundamental: 0.07
    news: 0.03
  min_score_alert: 70             # minimum score to trigger alerts
  setup_quality_thresholds:
    a_plus: 85                    # score >= 85 → A+
    a: 70                         # score >= 70 → A
    b: 55                         # score >= 55 → B
    c: 40                         # score >= 40 → C
                                  # score <  40 → FAIL

paper_trading:
  enabled: false                  # enable via `make paper-start`
  initial_capital: 100000         # INR starting capital
  max_positions: 10
  risk_per_trade_pct: 2.0
  min_score_to_trade: 70
  min_confidence: 50

backtest:
  trailing_stop_pct: 0.07
  fixed_stop_pct: 0.05
  target_pct: 0.10
  max_hold_days: 20
  position_size_pct: 0.10
  weekend_auto_run: false         # auto Saturday backtests

llm:
  enabled: true                   # requires GROQ_API_KEY (or another provider key)
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
    enabled: false               # enable after setting TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  email:
    enabled: false

scheduler:
  run_time: "15:35"
  timezone: "Asia/Kolkata"
```

### 4.2 `config/universe.yaml`

```yaml
mode: "list"            # "list" | "nifty500" | "nse_all"

symbols:
  - RELIANCE
  - TCS
  # ... add more symbols here

filters:
  min_price_inr: 50
  min_avg_daily_volume: 100000
  min_listing_years: 1
```

### 4.3 Required `.env` Keys

```bash
# LLM providers (need at least one for LLM phase)
GROQ_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OPENROUTER_API_KEY=

# Alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=

# API authentication
API_READ_KEY=        # any random hex — generate: python -c "import secrets; print(secrets.token_hex(32))"
API_ADMIN_KEY=       # different random hex for admin endpoints

# Optional
NEWSDATA_API_KEY=
```

### 4.4 Runtime Overrides via Environment Variables

```bash
# Change log verbosity without editing config
LOG_LEVEL=DEBUG python scripts/run_daily.py --date today
LOG_FORMAT=json python scripts/run_daily.py --date today
LOG_LEVEL=DEBUG LOG_FORMAT=json python scripts/run_daily.py --date today

# Switch the feature computation backend
FEATURE_BACKEND=pandas python scripts/run_daily.py --date today   # default
FEATURE_BACKEND=polars python scripts/run_daily.py --date today   # future migration path
```

---

## 5. CLI Scripts Reference

### Activate the Virtual Environment First

```bash
cd /home/ubuntu/projects/minervini_ai
source .venv/bin/activate
which python   # → .venv/bin/python  ✓
```

---

### 5.1 `scripts/run_daily.py` — Daily Pipeline

The main entry point. Runs: feature computation → SEPA screen → reports → alerts.

```
usage: run_daily.py [--date YYYY-MM-DD] [--watchlist PATH]
                    [--symbols "SYM1,SYM2"] [--watchlist-only]
                    [--scope {all,universe,watchlist}]
                    [--dry-run] [--skip-features]
                    [--config PATH] [--db PATH]
```

| Argument | Default | Description |
|---|---|---|
| `--date YYYY-MM-DD` | `today` | Run date; accepts ISO date or literal `"today"` |
| `--symbols "A,B,C"` | — | Inline symbols; overrides all other sources |
| `--watchlist PATH` | — | Watchlist file (.csv/.json/.xlsx/.txt); persisted to SQLite |
| `--watchlist-only` | `false` | Shorthand for `--scope watchlist` |
| `--scope` | `all` | `all` (universe + watchlist), `universe`, `watchlist` |
| `--dry-run` | `false` | Resolve symbols, print plan, exit — no DB writes |
| `--skip-features` | `false` | In dry-run: skip feature bootstrap plan preview |
| `--config PATH` | `config/settings.yaml` | Path to settings file |
| `--db PATH` | `data/minervini.db` | Path to SQLite database |

**Examples:**

```bash
# Full run for today (universe + watchlist)
python scripts/run_daily.py --date today

# Backfill a specific past date
python scripts/run_daily.py --date 2024-06-01

# Screen only your watchlist
python scripts/run_daily.py --date today --watchlist-only

# Ad-hoc check of specific symbols — no writes
python scripts/run_daily.py --symbols "DIXON,CDSL,TATAELXSI" --dry-run

# Load a new watchlist file, persist it, scan only those symbols
python scripts/run_daily.py --watchlist mylist.csv --watchlist-only

# Non-default config and DB paths
python scripts/run_daily.py --config config/prod.yaml --db data/prod.db
```

**Watchlist file formats:**

| Format | Structure |
|---|---|
| `.csv` | Column named `symbol`, or first column |
| `.json` | `["RELIANCE", "TCS", "DIXON"]` |
| `.xlsx` | First sheet, `symbol` column or column A |
| `.txt` | One symbol per line; `#` lines are comments |

**Exit codes:** `0` success / dry-run · `1` domain error (bad date, bad symbols, config failure)

---

### 5.2 `scripts/bootstrap.py` — Full History Download

Downloads N years of OHLCV history and computes all features from scratch.
Run once on initial setup; re-run if feature files are corrupted.

```
usage: bootstrap.py [--universe {nifty500,config,all}]
                    [--symbols "SYM1,SYM2"] [--watchlist PATH]
                    [--watchlist-only]
                    [--years N] [--workers N]
                    [--force] [--dry-run] [--skip-features]
                    [--date YYYY-MM-DD]
                    [--config PATH] [--db PATH] [--output-dir PATH]
```

| Argument | Default | Description |
|---|---|---|
| `--universe {config,nifty500,all}` | `config` | `config` and `list` are aliases (read `universe.yaml`); `nifty500` uses Nifty 500; `all` adds watchlist |
| `--symbols "A,B,C"` | — | Inline symbols; overrides universe |
| `--watchlist PATH` | — | Load watchlist file; persist to SQLite |
| `--watchlist-only` | `false` | Bootstrap only SQLite watchlist symbols |
| `--years N` | `5` | Years of history to download |
| `--workers N` | `4` | Parallel download threads |
| `--force` | `false` | Re-download even when ≥200 rows already present |
| `--dry-run` | `false` | Print what would be downloaded; no writes |
| `--skip-features` | `false` | Download OHLCV only, skip feature computation |
| `--date YYYY-MM-DD` | `today` | Anchor date (right edge of download window) |
| `--config PATH` | `config/settings.yaml` | |
| `--db PATH` | `data/minervini.db` | |
| `--output-dir PATH` | `data/processed` | Parquet output directory |

**Examples:**

```bash
# Bootstrap universe from universe.yaml (both forms are identical)
python scripts/bootstrap.py --universe config
python scripts/bootstrap.py

# Full Nifty 500 (uses placeholder list)
python scripts/bootstrap.py --universe nifty500

# Universe + watchlist combined
python scripts/bootstrap.py --universe all

# Force full re-download, 10 years, 8 workers
python scripts/bootstrap.py --universe config --force --years 10 --workers 8

# Download OHLCV only, skip features
python scripts/bootstrap.py --symbols "RELIANCE,DIXON" --skip-features

# Dry-run to preview
python scripts/bootstrap.py --universe config --dry-run
```

> **Estimated time:** 5–15 min for ~500 symbols, 60–90 min for 2 000+.
> Run overnight or over a weekend. Daily incremental updates take ~30 seconds.

---

### 5.3 `scripts/backtest_runner.py` — Walk-Forward Backtest

```
usage: backtest_runner.py --start YYYY-MM-DD --end YYYY-MM-DD
                           [--trailing-stop FLOAT] [--no-trailing]
                           [--sweep] [--universe NAME]
                           [--output-dir PATH] [--db-path PATH]
                           [--config PATH] [--label STR]
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--start YYYY-MM-DD` | ✅ | — | Backtest start date (inclusive) |
| `--end YYYY-MM-DD` | ✅ | — | Backtest end date (inclusive) |
| `--trailing-stop FLOAT` | | config value | Override trailing stop (e.g. `0.07` for 7%) |
| `--no-trailing` | | `false` | Disable trailing stop; use fixed stop only |
| `--sweep` | | `false` | Run parameter sweep across [5%, 7%, 10%, 15%, fixed] |
| `--universe NAME` | | `nifty500` | Universe identifier (informational label) |
| `--output-dir PATH` | | `reports/backtest/` | Report output directory |
| `--db-path PATH` | | `data/minervini.db` | SQLite database path |
| `--config PATH` | | `config/settings.yaml` | Settings path |
| `--label STR` | | `run` | Label appended to output filenames |

**Examples:**

```bash
# Standard 5-year backtest with 7% trailing stop
python scripts/backtest_runner.py \
    --start 2019-01-01 --end 2024-01-01 \
    --universe nifty500 --trailing-stop 0.07

# Fixed stop (no trailing)
python scripts/backtest_runner.py \
    --start 2019-01-01 --end 2024-01-01 --no-trailing

# Parameter sweep across all stop values
python scripts/backtest_runner.py \
    --start 2022-01-01 --end 2024-01-01 --sweep \
    --output-dir reports/backtest/ --label sweep_2022_2024

# Makefile shortcut
make backtest START=2019-01-01 END=2024-01-01
```

Output: HTML + CSV report in `reports/backtest/` with equity curve, per-regime
breakdown, and CAGR / Sharpe / max drawdown / win rate.

---

### 5.4 `scripts/rebuild_features.py` — Recompute Feature Store

Recomputes all feature Parquet files from existing processed OHLCV data.
Does **not** re-download price data.

```
usage: rebuild_features.py (--universe KEY | --symbols SYM1,SYM2)
                            [--since YYYY-MM-DD]
                            [--dry-run] [--workers N]
                            [--config PATH]
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--universe KEY` | ✅* | — | Universe key (e.g. `nifty500`); mutually exclusive with `--symbols` |
| `--symbols SYM1,SYM2` | ✅* | — | Comma-separated symbols; mutually exclusive with `--universe` |
| `--since YYYY-MM-DD` | | — | Only rebuild symbols whose feature file is older than this date |
| `--dry-run` | | `false` | Print which symbols would be rebuilt; no files written |
| `--workers N` | | `4` | Parallel processes |
| `--config PATH` | | `config/settings.yaml` | |

**Examples:**

```bash
# Rebuild all universe symbols
python scripts/rebuild_features.py --universe nifty500

# Rebuild from config/universe.yaml
python scripts/rebuild_features.py --universe config

# Rebuild specific symbols
python scripts/rebuild_features.py --symbols RELIANCE,TCS,DIXON

# Only rebuild files older than a date
python scripts/rebuild_features.py --universe nifty500 --since 2024-01-01

# Dry-run to preview
python scripts/rebuild_features.py --universe nifty500 --dry-run

# Makefile shortcut
make rebuild
```

---

### 5.5 `scripts/benchmark_features.py` — Feature Pipeline Benchmark

Measures wall-clock time for `bootstrap()` and `update()` and tests against
design targets (bootstrap < 15 min/500 symbols; update < 50 ms/symbol).

```
usage: benchmark_features.py [--use-cache] [--live]
                              [--symbols N]
                              [--bench-dir PATH] [--json-out PATH]
```

| Argument | Default | Description |
|---|---|---|
| `--use-cache` | `false` | Reuse previously written Parquet files; skip OHLCV regeneration |
| `--live` | `false` | Download real NSE data (internet required, ~5 min); exits 1 if any symbol >100 ms |
| `--symbols N` | 10 (synthetic) / 20 (live) | Number of symbols to benchmark |
| `--bench-dir PATH` | `data/benchmark_run/` | Directory for intermediate files |
| `--json-out PATH` | `data/benchmarks/feature_pipeline_<date>.json` | Results JSON path |

**Examples:**

```bash
# Default synthetic mode (fast, no internet)
python scripts/benchmark_features.py

# Reuse cached OHLCV (profile update() only)
python scripts/benchmark_features.py --use-cache

# 20 synthetic symbols
python scripts/benchmark_features.py --symbols 20

# Live mode against real NSE data
python scripts/benchmark_features.py --live

# Live mode with a subset of 10 symbols
python scripts/benchmark_features.py --live --symbols 10

# Makefile shortcuts
make benchmark                       # synthetic
make benchmark ARGS="--live"         # live NSE mode
make benchmark ARGS="--symbols 5"    # 5 synthetic symbols
```

Results are written to `data/benchmarks/feature_pipeline_<date>.json` for
trend tracking.

---

### 5.6 `scripts/show_run_history.py` — Pipeline Run History

Print recent pipeline runs from the `run_history` SQLite table.

```
usage: show_run_history.py [--n N] [--date YYYY-MM-DD] [--db PATH]
```

| Argument | Default | Description |
|---|---|---|
| `--n N` | `10` | Number of recent runs to show |
| `--date YYYY-MM-DD` | — | Filter to runs for a specific date |
| `--db PATH` | `data/minervini.db` | SQLite database path |

**Examples:**

```bash
python scripts/show_run_history.py               # last 10 runs
python scripts/show_run_history.py --n 30        # last 30 runs
python scripts/show_run_history.py --date 2026-04-11   # specific date
```

Output columns: `date | mode | status | symbols | A+ | A | duration | git_sha`

---

## 6. Makefile Targets

Run from the project root. `.venv` must exist.

```bash
make help          # show all targets with descriptions
make install       # pip install -e ".[dev]"
make test          # full pytest suite with coverage (--cov)
make test-fast     # pytest -x -q (stop on first failure)
make lint          # ruff check . (no auto-fix)
make format        # ruff format . (applies changes)
make format-check  # ruff format --check . (CI-safe, no changes)
make daily         # python scripts/run_daily.py --date today
make backtest START=YYYY-MM-DD END=YYYY-MM-DD   # backtest over date range
make rebuild       # rebuild_features.py --universe nifty500
make paper-reset   # reset paper-trading portfolio (reads initial_capital from config)
make paper-start   # set paper_trading.enabled: true in settings.yaml
make paper-status  # print current portfolio summary
make api           # uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
make dashboard     # streamlit run dashboard/app.py --server.port 8501
make benchmark     # benchmark_features.py (synthetic)
make clean         # remove __pycache__, .pytest_cache, .coverage, htmlcov/
```

---

## 7. Services

### 7.1 API Server (FastAPI — port 8000)

```bash
# Development (hot-reload)
uvicorn api.main:app --reload --port 8000

# Production (multi-worker)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

# Makefile shortcut
make api
```

Interactive docs: `http://localhost:8000/docs`

### 7.2 Streamlit Dashboard (port 8501)

```bash
streamlit run dashboard/app.py --server.port 8501
# Open: http://<server-ip>:8501

make dashboard   # equivalent
```

| Page | Path | Contents |
|---|---|---|
| Watchlist | `/01_Watchlist` | File upload, manual entry, daily A+/A results, Run Now |
| Screener | `/02_Screener` | Full universe table with filters, CSV export |
| Stock | `/03_Stock` | Candlestick chart, Trend Template checklist, fundamentals, LLM brief |
| Portfolio | `/04_Portfolio` | Paper trading P&L, open positions, equity curve |
| Backtest | `/05_Backtest` | Equity curve, per-regime stats, parameter sweep |

### 7.3 Next.js Frontend (Vercel)

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
npm run build        # production build (0 TypeScript errors)
```

Set in Vercel dashboard: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_API_READ_KEY`

| Page | Route |
|---|---|
| Dashboard | `/` |
| Screener | `/screener` |
| Stock deep-dive | `/screener/[symbol]` |
| Watchlist | `/watchlist` |
| Portfolio | `/portfolio` |
| Backtest | `/backtest` |

---

## 8. Watchlist Management

Watchlist symbols are always scanned, appear first in reports with a ★ badge,
always get charts generated, and trigger alerts at a lower threshold than the
full universe.

### Adding Symbols

```bash
# Via CLI file (persists to SQLite; supported formats: .csv, .json, .xlsx, .txt)
python scripts/run_daily.py --watchlist mylist.csv

# Inline symbols (ad-hoc only — NOT persisted)
python scripts/run_daily.py --symbols "DIXON,CDSL,TATAELXSI"

# Directly in SQLite
sqlite3 data/minervini.db \
  "INSERT INTO watchlist (symbol, note, added_via) VALUES ('DIXON', 'VCP forming', 'cli');"

# Via API (requires admin key)
curl -X POST -H "X-API-Key: $API_ADMIN_KEY" http://localhost:8000/api/v1/watchlist/DIXON

# Bulk add via API
curl -X POST \
     -H "X-API-Key: $API_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"symbols": ["DIXON","CDSL","RELIANCE"]}' \
     http://localhost:8000/api/v1/watchlist/bulk

# Upload file via API
curl -X POST -H "X-API-Key: $API_ADMIN_KEY" \
     -F "file=@mylist.csv" http://localhost:8000/api/v1/watchlist/upload
```

### Viewing the Watchlist

```bash
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, last_score, last_quality, added_at FROM watchlist ORDER BY symbol;"

# Count
sqlite3 data/minervini.db "SELECT COUNT(*) FROM watchlist;"
```

### Removing Symbols

```bash
sqlite3 data/minervini.db "DELETE FROM watchlist WHERE symbol = 'DIXON';"

# Via API
curl -X DELETE -H "X-API-Key: $API_ADMIN_KEY" http://localhost:8000/api/v1/watchlist/DIXON

# Clear all (admin key required)
curl -X DELETE -H "X-API-Key: $API_ADMIN_KEY" http://localhost:8000/api/v1/watchlist
```

### `watchlist` Table Schema

```sql
symbol       TEXT UNIQUE NOT NULL
note         TEXT                              -- optional
added_at     TEXT DEFAULT (current timestamp)
added_via    TEXT  -- 'cli' | 'api' | 'dashboard' | 'file_upload'
last_score   REAL                              -- updated after each run
last_quality TEXT  -- 'A+' | 'A' | 'B' | 'C' | 'FAIL'
last_run_at  TEXT
```

---

## 9. API Endpoints

Authentication: pass your key as the `X-API-Key` header.
- **Read key** (`API_READ_KEY`) — all GET endpoints
- **Admin key** (`API_ADMIN_KEY`) — POST/DELETE endpoints

```
── Screener ───────────────────────────────────────────────────────────────
GET  /api/v1/stocks/top           Top setups (?quality=A%2B &limit=20 &date=YYYY-MM-DD)
GET  /api/v1/stocks/trend         All Trend Template passes today
GET  /api/v1/stocks/vcp           Stocks with a qualified VCP pattern
GET  /api/v1/stock/{symbol}       Full SEPAResult for one symbol
GET  /api/v1/stock/{symbol}/history   Historical scores (last 30 days)

── Watchlist ──────────────────────────────────────────────────────────────
GET    /api/v1/watchlist               List all watchlist symbols
POST   /api/v1/watchlist/{symbol}      Add one symbol
DELETE /api/v1/watchlist/{symbol}      Remove one symbol
POST   /api/v1/watchlist/bulk          {"symbols": [...]}
POST   /api/v1/watchlist/upload        Upload CSV/JSON/XLSX file
DELETE /api/v1/watchlist               Clear all (admin key required)

── Paper Trading ──────────────────────────────────────────────────────────
GET  /api/v1/portfolio             Portfolio summary (P&L, positions)
GET  /api/v1/portfolio/trades      Trade history (?status=open|closed|all)

── System ─────────────────────────────────────────────────────────────────
GET  /api/v1/health                {"status":"ok","last_run":"..."}
GET  /api/v1/meta                  Universe size, A+ count, last run date
GET  /metrics                      Prometheus metrics endpoint

── Trigger a run (admin key required) ────────────────────────────────────
POST /api/v1/run                   {"scope":"all"|"universe"|"watchlist"}
                                   {"symbols":["DIXON","TCS"]}
```

**Example calls:**

```bash
# Top setups today, A+ only
curl -H "X-API-Key: $API_READ_KEY" \
  "http://localhost:8000/api/v1/stocks/top?quality=A%2B"

# Full SEPA result for DIXON
curl -H "X-API-Key: $API_READ_KEY" http://localhost:8000/api/v1/stock/DIXON

# Trigger watchlist-only run
curl -X POST \
  -H "X-API-Key: $API_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scope":"watchlist"}' \
  http://localhost:8000/api/v1/run
```

All responses use a consistent envelope:
```json
{ "success": true, "data": {...}, "meta": {"date": "2026-04-19", "total": 3} }
```

---

## 10. Paper Trading

Paper trading automatically follows each daily screen — no manual action needed
once enabled.

### Enable

```bash
make paper-start
# Sets paper_trading.enabled: true in config/settings.yaml
```

### Configure (`config/settings.yaml`)

```yaml
paper_trading:
  enabled: true           # toggled by make paper-start
  initial_capital: 100000 # INR starting capital
  max_positions: 10
  risk_per_trade_pct: 2.0
  min_score_to_trade: 70  # only A+/A setups (score >= 70) are entered
  min_confidence: 50
```

### How Entry Works

- Score ≥ 70, quality ∈ {A+, A}, no duplicate symbol, ≤ 10 open positions.
- Signals generated after 15:30 IST are queued to `data/paper_trading/pending_orders.json`
  and filled at the next open (09:15 IST). Never fills at after-hours prices.
- Pyramiding: adds 50% of original qty when VCP grade = A, volume ratio < 0.4,
  and price is within 2% of pivot. One pyramid per position.

### Status & Reset

```bash
make paper-status    # print current portfolio summary
make paper-reset     # reset all positions and P&L; reads initial_capital from config
```

> ⚠ Run paper trading for at least 4–8 weeks before comparing results to
> the backtester. Short windows are dominated by a single market regime.

---

## 11. Backtesting

Walk-forward backtester with trailing stop and NSE market regime labelling
(Bull / Bear / Sideways based on `data/features/NIFTY500.parquet`).

```bash
# Standard run
python scripts/backtest_runner.py \
  --start 2019-01-01 --end 2024-01-01 --trailing-stop 0.07

# Parameter sweep
python scripts/backtest_runner.py \
  --start 2022-01-01 --end 2024-01-01 --sweep

# Makefile shortcut
make backtest START=2019-01-01 END=2024-01-01
```

Output: HTML + CSV in `reports/backtest/` reporting CAGR, Sharpe ratio,
max drawdown, win rate, avg R-multiple, profit factor, expectancy.

---

## 12. Alerts

### Telegram

```bash
# In .env:
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_CHAT_ID=<your channel/chat ID>
```

```yaml
# In config/settings.yaml:
alerts:
  telegram:
    enabled: true
```

Test the bot manually:
```bash
curl "https://api.telegram.org/bot<TOKEN>/getMe"
# Find your chat ID:
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

### Email (SMTP)

```bash
# In .env:
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your-app-password
```

### Webhook (Slack / Discord / custom)

Set `WEBHOOK_URLS` in `.env` as a comma-separated list of URLs.
The dispatcher sends Slack-compatible JSON blocks.

---

## 13. Testing

```bash
# Full test suite with coverage
pytest tests/ -v --cov=. --cov-report=term-missing

# Stop on first failure, quiet output
pytest tests/ -x -q

# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Single test file
pytest tests/unit/test_trend_template.py -v

# Single test function
pytest tests/unit/test_sqlite_store.py::test_log_run -v

# Keyword filter
pytest tests/ -v -k "watchlist or stage"

# Makefile shortcuts
make test       # full with coverage
make test-fast  # stop on first failure
```

The suite covers all 8 Trend Template conditions, Stage 1–4 detection, VCP rules,
scorer + quality tags, screener pipeline, paper trading, API (21 TestClient tests),
storage layer (Parquet + SQLite), and all feature modules.

---

## 14. Code Quality — Linting & Formatting

```bash
# Check linting (no changes)
ruff check .

# Auto-fix linting issues
ruff check . --fix

# Format all Python files (applies changes)
ruff format .

# Check formatting only — CI-safe, no changes
ruff format --check .

# Run both checks together
ruff check . && ruff format --check .

# Makefile shortcuts
make lint          # ruff check (no fix)
make format        # ruff format (applies fixes)
make format-check  # CI-safe check only
```

Config in `pyproject.toml`: `line-length = 100`, `target-version = "py311"`.

---

## 15. Production Deployment (systemd)

All unit files live in `deploy/`. Editing a file there takes effect after
`sudo systemctl daemon-reload`.

### Services

| Unit | Type | Purpose |
|---|---|---|
| `minervini-daily.timer` | Timer | Fires Mon–Fri at 15:35 IST (`Persistent=true`) |
| `minervini-daily.service` | Oneshot | Runs `run_daily.py --date today` |
| `minervini-api.service` | Always-on | FastAPI on port 8000 (2 workers) |
| `minervini-dashboard.service` | Always-on | Streamlit on port 8501 |

### One-Time Install

```bash
# .env must exist and be filled in first
sudo bash deploy/install.sh
```

The script symlinks unit files → `/etc/systemd/system/`, reloads the daemon,
enables and starts all services. It is idempotent — safe to run again.

### Status

```bash
systemctl status minervini-daily.timer minervini-api.service minervini-dashboard.service

# When does the timer fire next?
systemctl list-timers --all | grep minervini

# Is the API responding?
curl -s http://localhost:8000/health | python3 -m json.tool
```

### Common Service Operations

```bash
# Restart after code changes
sudo systemctl restart minervini-api.service minervini-dashboard.service

# Trigger the daily run immediately (bypass timer)
sudo systemctl start minervini-daily.service
journalctl -u minervini-daily.service -f    # watch it run

# Stop everything
sudo systemctl stop minervini-api.service minervini-dashboard.service minervini-daily.timer

# Enable / disable auto-start on boot
sudo systemctl enable  minervini-api.service
sudo systemctl disable minervini-daily.timer
```

### Deploying Code Changes

```bash
git pull

# Python-only changes:
sudo systemctl restart minervini-api.service minervini-dashboard.service
# (daily service picks up new code automatically on its next timer fire)

# After editing a file in deploy/:
sudo systemctl daemon-reload
sudo systemctl restart minervini-api.service minervini-dashboard.service
```

### Uninstall

```bash
sudo bash deploy/uninstall.sh
```

---

## 16. Docker Deployment

Covers FastAPI backend, Streamlit dashboard, and daily scheduler.
The Next.js frontend is deployed separately to Vercel.

```bash
# 1. Fill in secrets
cp .env.example .env && nano .env

# 2. Build the image
docker-compose build

# 3. Start all services
docker-compose up -d

# 4. Bootstrap data (first run only)
docker-compose exec api python scripts/bootstrap.py

# 5. View logs
docker-compose logs -f api
docker-compose logs -f scheduler
```

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | FastAPI — 2 uvicorn workers |
| Dashboard | http://localhost:8501 | Streamlit (headless) |
| Scheduler | *(no port)* | Runs daily at 15:35 IST |

```bash
# Restart a single service
docker-compose restart api

# One-off command
docker-compose exec api python -c "from api.main import app; print('OK')"

# Stop (data volume preserved)
docker-compose down

# Destroy everything including data volume (destructive!)
docker-compose down -v
```

---

## 17. SQLite Quick Reference

Database: `data/minervini.db`

### Tables

| Table | Description |
|---|---|
| `run_history` | One row per pipeline run (date, mode, status, duration, A+/A counts) |
| `watchlist` | User-curated symbols with last score/quality |
| `screener_results` | Rich per-(symbol, run_date) results — used by API/dashboard |
| `sepa_results` | Lean per-(symbol, date) results — written by screener/results.py |

### Useful Queries

```bash
# Open interactive session
sqlite3 data/minervini.db

# List all tables
sqlite3 data/minervini.db ".tables"

# Pretty-print output
sqlite3 -column -header data/minervini.db "<query>"
```

**Run history:**
```sql
-- Last 10 runs
SELECT id, run_date, run_mode, scope, status, duration_sec, a_plus_count, a_count
  FROM run_history ORDER BY id DESC LIMIT 10;

-- Failed runs
SELECT id, run_date, status, error_msg FROM run_history WHERE status = 'failed';

-- Today's run summary
SELECT * FROM run_history WHERE run_date = date('now') ORDER BY id DESC LIMIT 1;
```

**Screener results:**
```sql
-- Today's top 20 by score
SELECT symbol, setup_quality, score, stage, rs_rating, vcp_qualified
  FROM screener_results
 WHERE run_date = date('now')
 ORDER BY score DESC LIMIT 20;

-- Today's A+ and A setups
SELECT symbol, setup_quality, score, entry_price, stop_loss, risk_pct
  FROM screener_results
 WHERE run_date = date('now') AND setup_quality IN ('A+', 'A')
 ORDER BY score DESC;

-- Historical scores for one symbol (last 30 days)
SELECT run_date, setup_quality, score, stage, rs_rating
  FROM screener_results
 WHERE symbol = 'DIXON'
 ORDER BY run_date DESC LIMIT 30;

-- Count per quality tier today
SELECT setup_quality, COUNT(*) AS count
  FROM screener_results
 WHERE run_date = date('now')
 GROUP BY setup_quality ORDER BY count DESC;
```

**Watchlist:**
```sql
-- View full watchlist
SELECT symbol, added_via, last_score, last_quality, added_at
  FROM watchlist ORDER BY symbol;

-- Count
SELECT COUNT(*) FROM watchlist;
```

---

## 18. Log Inspection

Application logs: `logs/minervini.log` (rotating, 10 MB × 5 backups)

```bash
# Live-tail
tail -f logs/minervini.log

# Last 100 lines
tail -100 logs/minervini.log

# Filter for errors
grep -E '"level":"(ERROR|CRITICAL)"' logs/minervini.log

# Filter for a specific symbol
grep '"symbol":"DIXON"' logs/minervini.log

# Count warnings today
grep '"level":"WARNING"' logs/minervini.log | grep "$(date +%Y-%m-%d)" | wc -l
```

**systemd journal (after deploy/install.sh):**

```bash
# Live log from the daily pipeline
journalctl -u minervini-daily.service -f

# Live log from the API
journalctl -u minervini-api.service -f

# All logs since midnight
journalctl -u minervini-daily.service --since today

# Last 50 lines from the dashboard
journalctl -u minervini-dashboard.service -n 50

# Logs for a specific date range
journalctl -u minervini-daily.service \
  --since "2026-04-11 00:00:00" --until "2026-04-11 23:59:59"
```

---

## 19. Day-to-Day Operations Runbook

### 19.1 Activate venv (every session)

```bash
cd /home/ubuntu/projects/minervini_ai
source .venv/bin/activate
```

### 19.2 Manual Daily Screen

```bash
# Full run for today
python scripts/run_daily.py --date today

# Specific past date (backfill)
python scripts/run_daily.py --date 2026-04-10

# Watchlist only
python scripts/run_daily.py --date today --watchlist-only

# Inline symbols
python scripts/run_daily.py --date today --symbols "DIXON,TATAELXSI,CDSL"
```

### 19.3 Check if Last Run Succeeded

```bash
# From run_history
sqlite3 -column -header data/minervini.db \
  "SELECT id, run_date, status, duration_sec, a_plus_count, a_count, error_msg
   FROM run_history ORDER BY id DESC LIMIT 5;"
```

Status meanings:
- `success` → clean run
- `partial` → `run_screen` failed for some symbols; results may be incomplete
- `failed` → pipeline aborted early; check `error_msg` and logs

```bash
# Via systemd journal
journalctl -u minervini-daily.service --since "yesterday" --no-pager
```

### 19.4 Re-Run a Failed Screen

```bash
# Re-run features + screen for a past date (safe — update() is idempotent)
python scripts/run_daily.py --date 2026-04-10
```

> The runner is self-healing: if a symbol's feature file is missing for the
> target date, it auto-bootstraps before screening.

### 19.5 Adding a New Symbol Universe

Edit `config/universe.yaml`:
```yaml
mode: "nifty500"   # or "nse_all" | "list"

# For mode: "list", add to symbols:
symbols:
  - NEWCO1
  - NEWCO2
```

Then bootstrap new symbols:
```bash
# Only bootstraps symbols without feature files — safe to run on a live universe
python scripts/bootstrap.py --universe config

# Dry-run first to verify
python scripts/run_daily.py --date today --dry-run
```

### 19.6 Adding a New Rule to the Trend Template

1. Add the condition in `rules/trend_template.py` inside `check_trend_template()`.
2. Add the threshold in `config/settings.yaml` under `trend_template:`.
3. Add unit tests in `tests/unit/test_trend_template.py`.
4. Run regression suite — **all 6 tests in `tests/integration/test_known_setups.py`
   must pass before merging.**

```bash
python -m pytest tests/unit/test_trend_template.py -v
python -m pytest tests/integration/test_known_setups.py -v
make test
```

### 19.7 Recovering from a Corrupt Feature Store

**Detect:**
```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/features/DIXON.parquet')
print(df.tail(3)); print('Rows:', len(df))
"
# ArrowInvalid / EOFError → file is corrupt
```

**Rebuild one symbol:**
```bash
python scripts/rebuild_features.py --symbols DIXON
```

**Rebuild the full universe:**
```bash
make rebuild
# or:
python scripts/rebuild_features.py --universe nifty500
```

> Safe to run at any time — Parquet writes use an atomic temp-file rename;
> no old file is ever left in a partial state.

### 19.8 Common Errors & Fixes

| Error | Cause | Fix |
|---|---|---|
| `InsufficientDataError` | Feature computation (e.g. SMA_200) needs more rows than available — common for newly listed stocks | Check history length: `python -c "import pandas as pd; df=pd.read_parquet('data/processed/XYZ.parquet'); print(len(df))"`. Exclude via `min_listing_years` filter. Symbol is skipped gracefully. |
| `RuleEngineError` | A feature column expected by `rules/stage.py` or `rules/trend_template.py` is `NaN` in the last row | Inspect feature file: `python -c "import pandas as pd; print(pd.read_parquet('data/features/XYZ.parquet').tail(1).to_dict('records'))"`. If NaN columns, run: `python scripts/rebuild_features.py --symbols XYZ` |
| `FeatureStoreOutOfSyncError` | `feature_store.update()` found `run_date` already present — idempotent guard triggered when a daily run is executed twice for the same date | Expected behaviour — runner logs `WARNING` and skips the symbol. To force recompute: `python scripts/rebuild_features.py --symbols XYZ` then re-run the daily screen. |
| `TelegramAlertError` | Invalid token, wrong chat ID, network timeout, or API rate limit | Check `.env` values. Test: `curl "https://api.telegram.org/bot<TOKEN>/getMe"`. Rate limit: wait 30s and retry. Pipeline continues regardless. |
| `BacktestDataError` | Historical OHLCV missing for symbol in the requested date range | Bootstrap: `python scripts/bootstrap.py --universe config`. Verify: `python -c "import pandas as pd; df=pd.read_parquet('data/processed/XYZ.parquet'); print(df.index.min(), df.index.max())"`. Shorten `--start` if data doesn't exist. |
| `"No module named X"` on fresh clone | Project not installed in editable mode | `source .venv/bin/activate && pip install -e ".[dev]"` |

### 19.9 Tuning Scoring Thresholds

Key knobs in `config/settings.yaml → scoring`:

```yaml
scoring:
  weights:                  # all weights must sum to 1.0
    rs_rating:   0.30
    trend:       0.25
    vcp:         0.25
    volume:      0.10
    fundamental: 0.07
    news:        0.03
  min_score_alert: 70       # minimum score to trigger Telegram/email alert
  setup_quality_thresholds:
    a_plus: 85
    a: 70
    b: 55
    c: 40
```

> ⚠ Do not change thresholds mid-paper-trading window. P&L attribution is only
> valid within a single threshold regime. Record `run_history.config_hash` at
> the start of each paper-trading period before making changes.

### 19.10 Performance Benchmarks

Expected run times:

| Operation | Symbols | Target |
|---|---|---|
| Daily run (incremental update) | 500 | ~30 seconds |
| Daily run (incremental update) | 2000 | ~2–3 minutes |
| Bootstrap (full history) | 500 | 5–15 minutes |
| Bootstrap (full history) | 2000 | 60–90 minutes |

Measure:
```bash
python scripts/benchmark_features.py        # feature-layer benchmark
time python scripts/run_daily.py --date today   # end-to-end timing

# Check last run duration
sqlite3 data/minervini.db \
  "SELECT run_date, duration_sec, a_plus_count FROM run_history ORDER BY id DESC LIMIT 5;"
```

**Warning signs if daily run exceeds 5 minutes:**
- Check for oversized Parquet files: `ls -lh data/processed/ | sort -rh | head -10`
- Check for unexpected bootstraps: `grep "bootstrapping" logs/minervini.log | wc -l`
  (>10 on a normal daily run means feature files are being corrupted)
- Check LLM latency: if `llm.enabled: true`, ~1–3s per A+/A result adds up.
  Set `only_for_quality: ["A+"]` or reduce `max_tokens` if needed.

---

## 20. Git Workflow

```bash
# Check what has changed
git status
git diff

# Stage changes interactively
git add -p

# Stage everything
git add .

# Commit with a conventional commit message
git commit -m "feat: add VCP contraction depth calculation"
git commit -m "fix: handle missing RS rating gracefully"
git commit -m "chore: update requirements.txt"
git commit -m "test: add unit tests for trend template"
git commit -m "docs: update OPERATIONS.md"

# Push / pull
git push
git pull

# Recent history
git log --oneline -20

# Create and switch to a feature branch
git checkout -b feat/vcp-detection
```

---

## 21. Editor Setup — VS Code Remote

Connect to ShreeVault from your local machine using VS Code Remote SSH,
then open the project folder. The virtual environment is already configured.

### Recommended Extensions

| Extension | ID |
|---|---|
| Remote - SSH | `ms-vscode-remote.remote-ssh` |
| Python | `ms-python.python` |
| Pylance | `ms-python.vscode-pylance` |
| Ruff | `charliermarsh.ruff` |
| YAML | `redhat.vscode-yaml` |

### `.vscode/settings.json` (already committed)

```json
{
    "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
    "python.terminal.activateEnvironment": true,
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.fixAll.ruff": "explicit",
            "source.organizeImports.ruff": "explicit"
        }
    },
    "ruff.organizeImports": true,
    "editor.rulers": [100]
}
```

### `.vscode/launch.json` — Debug Configurations (already committed)

| Configuration | Command |
|---|---|
| Run daily screen | `run_daily.py --date today` |
| Bootstrap universe | `bootstrap.py --universe config` |
| Run FastAPI (dev) | `uvicorn api.main:app --reload --port 8000` |
| Pytest | `pytest tests/ -v` |

---

## Quick Reference Card

```
── Activate (every session) ──────────────────────────────────────────────
source .venv/bin/activate

── Daily Run ─────────────────────────────────────────────────────────────
python scripts/run_daily.py --date today
python scripts/run_daily.py --date 2024-01-15
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON"
python scripts/run_daily.py --watchlist mylist.csv
python scripts/run_daily.py --watchlist-only
python scripts/run_daily.py --dry-run

── Scope Flags ───────────────────────────────────────────────────────────
--scope all          universe + watchlist (default)
--scope universe     universe only
--scope watchlist    watchlist only
--watchlist-only     shorthand for --scope watchlist

── Bootstrap ─────────────────────────────────────────────────────────────
python scripts/bootstrap.py --universe config
python scripts/bootstrap.py --universe config --force --years 10 --workers 8
python scripts/bootstrap.py --symbols "RELIANCE,TCS" --skip-features
python scripts/bootstrap.py --universe config --dry-run

── Servers ───────────────────────────────────────────────────────────────
uvicorn api.main:app --reload --port 8000   (or: make api)
streamlit run dashboard/app.py --server.port 8501   (or: make dashboard)

── Make ──────────────────────────────────────────────────────────────────
make daily           make test          make test-fast
make lint            make format        make format-check
make api             make dashboard     make rebuild
make paper-start     make paper-status  make paper-reset
make benchmark       make clean

── Backtest ──────────────────────────────────────────────────────────────
make backtest START=2019-01-01 END=2024-01-01
python scripts/backtest_runner.py --start 2019-01-01 --end 2024-01-01 --sweep

── Rebuild Features ──────────────────────────────────────────────────────
python scripts/rebuild_features.py --universe nifty500
python scripts/rebuild_features.py --symbols DIXON,CDSL

── Benchmark ─────────────────────────────────────────────────────────────
python scripts/benchmark_features.py
python scripts/benchmark_features.py --live

── Run History ───────────────────────────────────────────────────────────
python scripts/show_run_history.py           # last 10 runs
python scripts/show_run_history.py --n 30
python scripts/show_run_history.py --date 2026-04-11

── Tests ─────────────────────────────────────────────────────────────────
pytest tests/ -v --cov=. --cov-report=term-missing
pytest tests/unit/ -v
pytest tests/ -v -k "watchlist or stage"

── SQLite ────────────────────────────────────────────────────────────────
sqlite3 data/minervini.db ".tables"
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, setup_quality, score FROM screener_results \
   WHERE run_date = date('now') ORDER BY score DESC LIMIT 20;"
sqlite3 -column -header data/minervini.db \
  "SELECT id, run_date, status, duration_sec, a_plus_count \
   FROM run_history ORDER BY id DESC LIMIT 10;"

── Logs ──────────────────────────────────────────────────────────────────
tail -f logs/minervini.log
journalctl -u minervini-api.service -f
journalctl -u minervini-daily.service --since today

── systemd ───────────────────────────────────────────────────────────────
sudo systemctl restart minervini-api.service
sudo systemctl start   minervini-daily.service   # manual trigger
systemctl list-timers --all | grep minervini
```

---

*Built on Mark Minervini's SEPA methodology — "Trade Like a Stock Market Wizard" (2013)*
