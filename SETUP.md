# Minervini AI — Setup Guide

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Environment Setup](#2-environment-setup)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [Running the System](#5-running-the-system)
6. [Module Reference](#6-module-reference)
7. [Systemd Services](#7-systemd-services)
8. [Docker Deployment](#8-docker-deployment)
9. [Uninstalling](#9-uninstalling)

---

## 1. Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | 3.11 or higher |
| OS | Ubuntu/Linux (bash), macOS/Windows with Docker |
| Git | Any recent version |
| Node.js | 18+ (for frontend) |
| npm | 9+ (for frontend) |
| SQLite | 3.x (usually pre-installed) |
| Systemd | For production deployment on Linux |

### System Packages (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y build-essential curl git
```

---

## 2. Environment Setup

### 2.1 Clone the Repository

```bash
git clone https://github.com/ajaydeloli/minervini_ai.git
cd minervini_ai
```

### 2.2 Create Python Virtual Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 2.3 Frontend Setup (Node.js)

```bash
cd frontend
npm install
cd ..
```

---

## 3. Installation

### 3.1 Install Python Dependencies

```bash
# Install the project in editable mode with all dependencies
pip install -e ".[dev]"

# Or using Make
make install
```

### 3.2 Install Frontend Dependencies

```bash
cd frontend
npm install
```

---

## 4. Configuration

### 4.1 Copy Environment File

```bash
cp .env.example .env
```

### 4.2 Required Environment Variables

Edit `.env` with your API keys and settings:

```bash
# ── Data Sources ──────────────────────────────────────────────────────
NSE_BHAV_BASE_URL=https://archives.nseindia.com/content/historical/EQUITIES/
NEWSDATA_API_KEY=                 # free tier at newsdata.io

# ── LLM Providers (get at least one) ─────────────────────────────────
GROQ_API_KEY=                     # free at console.groq.com — recommended first
ANTHROPIC_API_KEY=                # paid — claude-haiku is cheapest
OPENAI_API_KEY=                   # paid
OPENROUTER_API_KEY=               # free models available at openrouter.ai
OLLAMA_API_KEY=                   # leave blank for local Ollama
GEMINI_API_KEY=                   # free tier at aistudio.google.com

# ── Alerting ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=               # from @BotFather on Telegram
TELEGRAM_CHAT_ID=                 # your chat/channel ID

# ── Email (optional) ──────────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=

# ── API Layer ─────────────────────────────────────────────────────────
API_READ_KEY=                     # any random string, e.g.: openssl rand -hex 32
API_ADMIN_KEY=                    # different random string for admin endpoints
```

### 4.3 Configuration File (config/settings.yaml)

The main configuration file at `config/settings.yaml` controls all system behavior:

```yaml
universe:
  source: "yfinance"              # nse_bhav | yfinance | csv
  index: "nifty500"
  min_price: 50                   # INR
  min_avg_volume: 100000          # shares/day
  min_market_cap_cr: 500          # crore INR

data:
  raw_dir: "data/raw"
  processed_dir: "data/processed"
  features_dir: "data/features"

chart:
  output_dir: "data/charts"
  lookback_days: 90

trend_template:
  ma200_slope_lookback: 20
  pct_above_52w_low: 25.0
  pct_below_52w_high: 25.0
  min_rs_rating: 70

vcp:
  detector: "rule_based"
  min_contractions: 2
  max_contractions: 5

fundamentals:
  enabled: true
  cache_days: 7
  conditions:
    min_roe: 15.0
    max_de: 1.0

scoring:
  weights:
    rs_rating: 0.30
    trend: 0.25
    vcp: 0.25
    volume: 0.10
    fundamental: 0.07
    news: 0.03
  min_score_alert: 70
  setup_quality_thresholds:
    a_plus: 85
    a: 70

paper_trading:
  enabled: false
  initial_capital: 100000

backtest:
  trailing_stop_pct: 0.07
  fixed_stop_pct: 0.05

llm:
  enabled: true
  provider: "groq"
  model: "llama-3.3-70b-versatile"

api:
  host: "0.0.0.0"
  port: 8000

scheduler:
  run_time: "15:35"
  timezone: "Asia/Kolkata"
```

---

## 5. Running the System

### 5.1 Quick Start Commands

| Command | Description |
|---|---|
| `make install` | Install project and all dependencies |
| `make api` | Start FastAPI server on port 8000 |
| `make dashboard` | Launch Streamlit dashboard on port 8501 |
| `make daily` | Run the daily pipeline for today |
| `make backtest START=YYYY-MM-DD END=YYYY-MM-DD` | Run backtest over date range |

### 5.2 Running Individual Components

```bash
# FastAPI Backend
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Streamlit Dashboard
python -m streamlit run dashboard/app.py --server.port 8501

# Next.js Frontend
cd frontend && npm run dev
```

### 5.3 Database Bootstrap

Before running the daily pipeline for the first time, download historical data:

```bash
# Download 5 years of OHLCV history for Nifty 500
python scripts/bootstrap.py --universe nifty500 --years 5 --workers 4

# Bootstrap watchlist only
python scripts/bootstrap.py --watchlist-only

# Custom symbol list
python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY"
```

### 5.4 Running the Daily Pipeline

```bash
# Full run for today (features → screen → reports → alerts)
python scripts/run_daily.py --date today

# Specific past date (backfill)
python scripts/run_daily.py --date 2024-01-15

# Watchlist-only scan
python scripts/run_daily.py --date today --watchlist-only

# Dry-run (preview symbols without writing)
python scripts/run_daily.py --date today --dry-run

# Load external watchlist file
python scripts/run_daily.py --watchlist /path/to/my_stocks.csv
```

---

## 6. Module Reference

### 6.1 Scripts Module

#### `scripts/bootstrap.py`
Downloads and validates N years of OHLCV history for the symbol universe.

**Arguments:**
| Argument | Description | Default |
|---|---|---|
| `--universe` | Universe source: `nifty500`, `config`, `all` | `config` |
| `--symbols` | Comma-separated inline symbols | None |
| `--watchlist` | Path to watchlist file (.csv/.json/.xlsx/.txt) | None |
| `--watchlist-only` | Bootstrap only SQLite watchlist | False |
| `--years` | Years of OHLCV history to download | `5` |
| `--workers` | Parallel download threads | `4` |
| `--force` | Re-download even when data exists | False |
| `--dry-run` | Print what would be downloaded, no writes | False |
| `--config` | Path to settings.yaml | `config/settings.yaml` |
| `--db` | SQLite database path | `data/minervini.db` |
| `--output-dir` | Output directory for Parquet files | `data/processed` |
| `--date` | End-of-window anchor date (YYYY-MM-DD or "today") | `today` |

**Examples:**
```bash
# Full universe bootstrap
python scripts/bootstrap.py --universe nifty500 --years 5 --workers 8

# Custom symbols
python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY" --years 3

# Dry run
python scripts/bootstrap.py --dry-run
```

#### `scripts/run_daily.py`
CLI entry point for the daily pipeline. Orchestrates features → screen → reports → alerts.

**Arguments:**
| Argument | Description | Default |
|---|---|---|
| `--date` | Run date (YYYY-MM-DD or "today") | `today` |
| `--watchlist` | Path to watchlist file | None |
| `--symbols` | Comma-separated inline symbols | None |
| `--watchlist-only` | Skip full universe scan | False |
| `--scope` | Symbol scope: `all`, `universe`, `watchlist` | `all` |
| `--dry-run` | Resolve symbols only, no writes | False |
| `--config` | Path to settings.yaml | `config/settings.yaml` |
| `--db` | SQLite database path | `data/minervini.db` |
| `--skip-features` | In dry-run: skip feature plan preview | False |

**Examples:**
```bash
# Full run for today
python scripts/run_daily.py --date today

# Backfill specific date
python scripts/run_daily.py --date 2024-01-15

# Watchlist only
python scripts/run_daily.py --watchlist-only --dry-run
```

#### `scripts/backtest_runner.py`
Walk-forward backtest CLI (Phase 8).

**Arguments:**
| Argument | Description | Default |
|---|---|---|
| `--start` | Backtest start date (YYYY-MM-DD) | **Required** |
| `--end` | Backtest end date (YYYY-MM-DD) | **Required** |
| `--trailing-stop` | Trailing stop fraction (e.g. 0.07 for 7%) | None |
| `--no-trailing` | Disable trailing stop, use fixed stop only | False |
| `--sweep` | Run parameter sweep across [5%, 7%, 10%, 15%, fixed] | False |
| `--universe` | Universe identifier (informational) | `nifty500` |
| `--output-dir` | Directory for report outputs | `reports/backtest/` |
| `--db-path` | SQLite database path | `data/minervini.db` |
| `--config` | settings.yaml path | `config/settings.yaml` |
| `--label` | Label appended to output filenames | "" |

**Examples:**
```bash
# Full walk-forward backtest with 7% trailing stop
python scripts/backtest_runner.py --start 2019-01-01 --end 2024-01-01 --trailing-stop 0.07

# Parameter sweep
python scripts/backtest_runner.py --start 2022-01-01 --end 2024-01-01 --sweep

# Fixed stop only
python scripts/backtest_runner.py --start 2019-01-01 --end 2024-01-01 --no-trailing
```

#### `scripts/rebuild_features.py`
Rebuilds feature store for the Nifty 500 universe from scratch.

```bash
python scripts/rebuild_features.py --universe nifty500
```

#### `scripts/benchmark_features.py`
Runs feature pipeline performance benchmark.

```bash
# Synthetic mode (default)
python scripts/benchmark_features.py

# Live mode with real NSE data (~5 min)
python scripts/benchmark_features.py --live
```

---

### 6.2 API Module (`api/`)

#### `api/main.py`
FastAPI application entry point. Wires together CORS, rate-limiting, routers (health, stocks, watchlist, portfolio, run, backtest), Prometheus metrics.

**Run:**
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
# or
python -m api.main
```

#### `api/routers/stocks.py`
Screener endpoints — all require `X-API-Key` header with `API_READ_KEY`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/stocks/top` | GET | Top N stocks by score, optional quality filter |
| `/api/v1/stocks/trend` | GET | Stocks passing the Minervini Trend Template |
| `/api/v1/stocks/vcp` | GET | VCP-qualified stocks, graded by quality tier |
| `/api/v1/stock/{symbol}` | GET | Full StockDetail for one symbol on a date |
| `/api/v1/stock/{symbol}/history` | GET | Historical SEPA scores for a symbol |
| `/api/v1/stock/{symbol}/ohlcv` | GET | OHLCV price history with SMAs |

**Query Parameters:**
- `min_quality` — Exact setup_quality filter: A+, A, B, C, or FAIL
- `limit` — Maximum results (1–100, default 20)
- `date` — Screen run date as YYYY-MM-DD (defaults to today)
- `min_rs` — Minimum RS rating (0–99)
- `stage` — Weinstein stage (1–4)
- `days` — Number of past trading days for history (1–365)

#### `api/routers/run.py`
Admin-only manual pipeline trigger endpoint.

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/v1/run` | POST | `API_ADMIN_KEY` | Trigger a manual pipeline run (non-blocking) |

**Rate limited:** 10 requests per minute (ADMIN_LIMIT).

#### `api/routers/watchlist.py`
Watchlist management endpoints.

#### `api/routers/portfolio.py`
Portfolio and paper trading endpoints.

#### `api/routers/backtest.py`
Backtest run history and report endpoints.

#### `api/routers/health.py`
Health check endpoint.

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/health` | GET | System health status |

---

### 6.3 Screener Module (`screener/`)

#### `screener/pipeline.py`
Orchestrates the full SEPA feature → rules pipeline across the universe in parallel using `ProcessPoolExecutor`.

**Public API:**
```python
from screener.pipeline import run_screen

results = run_screen(
    universe=["RELIANCE", "TCS", "INFY"],
    run_date=datetime.date(2024, 1, 15),
    config=config_dict,
    n_workers=4
)
```

**Pipeline Execution Order per Symbol:**
1. Load feature file: `storage/parquet_store.read(features_dir/{symbol}.parquet)`
2. Take last row
3. `detect_stage(row, config)` → StageResult
4. If stage != 2: create FAIL SEPAResult, skip steps 5–7
5. `check_trend_template(row, config)` → TrendTemplateResult
6. `check_vcp(row, config)` → VCPQualification
7. `check_entry_trigger(row, config)` → EntryTrigger
8. If entry triggered: `compute_stop_loss(row, entry_price, config)` → StopLossResult
9. `evaluate(...)` → SEPAResult

---

### 6.4 Pipeline Module (`pipeline/`)

#### `pipeline/runner.py`
Main pipeline orchestrator. Provides clean importable API for schedulers, tests, backtest harness, and dashboard.

**Public API:**
```python
from pipeline.runner import run, RunResult
from pipeline.context import RunContext

context = RunContext(
    run_date=datetime.date.today(),
    mode="daily",
    scope="all",
    config=config_dict,
    db_path=Path("data/minervini.db"),
)
result = run(context)
```

**RunResult Attributes:**
- `run_date` — The evaluated trading date
- `symbols_screened` — Total symbols screened
- `passed_stage2` — Symbols passing Stage 2 gate
- `passed_tt` — Symbols passing Trend Template
- `vcp_qualified` — Symbols with confirmed VCP pattern
- `a_plus_count` — Setups graded A+
- `a_count` — Setups graded A
- `duration_sec` — Wall-clock seconds
- `csv_path` — Path to watchlist CSV
- `html_path` — Path to watchlist HTML
- `alert_sent` — True if Telegram alert dispatched
- `status` — "success" | "partial" | "failed"

**Pipeline Steps:**
- Step 0: Execute pending paper-trade orders
- Step 1: Setup logging
- Step 2: Resolve symbols
- Step 3: Init DB + log run
- Step 4: Feature computation (bootstrap or update)
- Step 5: run_screen
- Step 5b: LLM narrative generation
- Step 6: persist_results (sepa_results table)
- Step 7: save_results (screener_results table)
- Step 8: update_symbol_score for watchlist symbols
- Step 9: generate_watchlist (CSV + HTML)
- Step 10: generate_chart for A+/A setups
- Step 11: Alert dispatch (Telegram + Email + Webhook)
- Step 12: finish_run
- Step 12b: Paper trading

---

### 6.5 Features Module (`features/`)

#### `feature_store.py`
Manages feature computation and storage (Parquet files in `data/features/`).

**Public API:**
```python
from features.feature_store import bootstrap, update, needs_bootstrap

# Check if bootstrap needed
if needs_bootstrap("RELIANCE", config):
    bootstrap("RELIANCE", config)
else:
    update("RELIANCE", date.today(), config)
```

#### `moving_averages.py`
Computes SMA (Simple Moving Averages): SMA 20, 50, 150, 200.

#### `volume.py`
Volume analysis including volume ratio and contracting patterns.

#### `atr.py`
Average True Range computation for volatility measurement.

#### `pivot.py`
Swing pivot detection using ZigZag algorithm.

#### `vcp.py`
Volatility Contraction Pattern detection.

#### `relative_strength.py`
Relative strength calculation against Nifty 500 benchmark.

---

### 6.6 Storage Module (`storage/`)

#### `sqlite_store.py`
SQLite operations for run history, screener results, watchlist.

**Key Functions:**
```python
from storage.sqlite_store import init_db, log_run, finish_run, save_results

init_db(Path("data/minervini.db"))
log_run(run_date, run_mode, scope, universe_size, watchlist_size)
save_results(results_list, run_date, watchlist_symbols=set())
finish_run(run_id, status, duration_sec, ...)
```

#### `parquet_store.py`
Parquet file read/write for OHLCV and feature data.

**Key Functions:**
```python
from storage.parquet_store import read, write, exists, row_count

df = read(Path("data/features/RELIANCE.parquet"))
write(df, Path("data/processed/RELIANCE.parquet"), overwrite=True)
exists(Path("data/features/RELIANCE.parquet"))
row_count(Path("data/features/RELIANCE.parquet"))
```

---

### 6.7 Reports Module (`reports/`)

#### `daily_watchlist.py`
Generates daily watchlist reports (CSV + HTML).

```python
from reports.daily_watchlist import generate_watchlist

wl_out = generate_watchlist(
    run_date=date.today(),
    results=results_list,
    config=config_dict,
    watchlist_symbols=set(watchlist_symbols),
)
# wl_out.csv_path, wl_out.html_path
```

#### `chart_generator.py`
Generates chart PNGs for A+/A setups.

```python
from reports.chart_generator import generate_chart

generate_chart(
    symbol="RELIANCE",
    run_date=date.today(),
    result=sepa_result,
    config=config_dict,
)
```

---

### 6.8 Alerts Module (`alerts/`)

#### `telegram_alert.py`
Telegram notification dispatch.

```python
from alerts.telegram_alert import TelegramAlert

TelegramAlert().send(results, run_date, config)
```

**Configuration (in settings.yaml):**
```yaml
alerts:
  telegram:
    enabled: true
```

**Environment variables:**
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your chat/channel ID

#### `email_alert.py`
Email notification via SMTP.

```python
from alerts.email_alert import EmailAlert

EmailAlert().send(results, run_date, config)
```

**Configuration:**
```yaml
alerts:
  email:
    enabled: true

# Environment variables
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-app-password
```

#### `webhook_alert.py`
Generic webhook alerts (Slack, Discord, etc.).

---

### 6.9 Dashboard Module (`dashboard/`)

#### `dashboard/app.py`
Streamlit multi-page app for visual monitoring.

**Run:**
```bash
streamlit run dashboard/app.py --server.port 8501
# or
make dashboard
```

**Features:**
- Market status display
- Last run info with quick stats
- KPI summary and A+ preview table
- Dark theme CSS

---

### 6.10 Makefile Targets

| Target | Command | Description |
|---|---|---|
| `install` | `make install` | Install project + dev deps into .venv |
| `test` | `make test` | Full pytest suite with coverage |
| `test-fast` | `make test-fast` | Stop on first failure, quiet output |
| `lint` | `make lint` | Ruff linter (no auto-fix) |
| `format` | `make format` | Ruff auto-formatter |
| `format-check` | `make format-check` | Format check only (CI-safe) |
| `daily` | `make daily` | Run daily pipeline for today |
| `backtest` | `make backtest START= END=` | Backtest over date range |
| `rebuild` | `make rebuild` | Rebuild feature store for Nifty 500 |
| `paper-reset` | `make paper-reset` | Reset paper-trading portfolio |
| `paper-start` | `make paper-start` | Enable paper trading in config |
| `paper-status` | `make paper-status` | Print paper-trading portfolio summary |
| `api` | `make api` | Start FastAPI server on :8000 |
| `dashboard` | `make dashboard` | Launch Streamlit on :8501 |
| `benchmark` | `make benchmark` | Run feature pipeline benchmark |
| `clean` | `make clean` | Remove __pycache__, caches, coverage |

---

## 7. Systemd Services

### 7.1 Service Units

| Service | Description | Runs |
|---|---|---|
| `minervini-api.service` | FastAPI backend | Always (restart on failure) |
| `minervini-dashboard.service` | Streamlit dashboard | Always (restart on failure) |
| `minervini-frontend.service` | Next.js frontend | Always (restart on failure) |
| `minervini-daily.service` | Daily pipeline (oneshot) | Triggered by timer |
| `minervini-daily.timer` | Schedule trigger (Mon-Fri 15:35 IST) | Always |

### 7.2 Installation

```bash
sudo bash deploy/install.sh
```

The install script:
1. Auto-detects `PROJECT_DIR` from script location
2. Auto-detects invoking user via `$SUDO_USER`
3. Substitutes placeholders (`@@PROJECT_DIR@@`, `@@DEPLOY_USER@@`) in unit files
4. Writes patched units to `/etc/systemd/system/`
5. Reloads systemd daemon
6. Enables and starts all units

### 7.3 Service Management

```bash
# Start all services
sudo systemctl enable --now minervini-daily.timer
sudo systemctl enable --now minervini-api.service
sudo systemctl enable --now minervini-dashboard.service
sudo systemctl enable --now minervini-frontend.service

# View status
systemctl status minervini-api.service
systemctl status minervini-daily.timer

# View logs
journalctl -u minervini-daily -n 50 -f
journalctl -u minervini-api -n 50 -f
journalctl -u minervini-dashboard -n 50 -f

# List all Minervini timers
systemctl list-timers --all | grep minervini

# Stop a service
sudo systemctl stop minervini-daily.timer

# Disable a service
sudo systemctl disable minervini-daily.timer
```

### 7.4 Uninstall

```bash
sudo bash deploy/uninstall.sh
```

This stops, disables, and removes all Minervini systemd units. Template files in `deploy/` are untouched.

---

## 8. Docker Deployment

### 8.1 Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down
```

### 8.2 Services

| Service | Port | Description |
|---|---|---|
| `api` | 8000 | FastAPI backend |
| `dashboard` | 8501 | Streamlit dashboard |
| `scheduler` | — | Daily pipeline (runs at 15:35 IST) |

### 8.3 Volume

Named volume `minervini_data` provides persistent storage for SQLite and Parquet data, independent of the bind mount at `./data`.

### 8.4 Environment

Copy `.env.example` to `.env` before running Docker:
```bash
cp .env.example .env
nano .env  # fill in your API keys
```

---

## 9. Uninstalling

### 9.1 Systemd Uninstall

```bash
sudo bash deploy/uninstall.sh
```

### 9.2 Docker Uninstall

```bash
docker-compose down -v  # removes named volume
```

### 9.3 Clean Up Python Environment

```bash
deactivate  # exit virtual environment
rm -rf .venv
```