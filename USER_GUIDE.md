# Minervini AI — User Guide

> **Project:** Minervini SEPA Stock Analysis System
> **Version:** 1.6.0
> **Methodology:** Mark Minervini's Specific Entry Point Analysis (SEPA)
> **Target Market:** NSE / Indian Equities (adaptable to any market)

---

## Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [Prerequisites](#2-prerequisites)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [First-Time Setup (Bootstrap)](#5-first-time-setup-bootstrap)
6. [Running the Daily Pipeline](#6-running-the-daily-pipeline)
7. [Dashboard — Streamlit (MVP)](#7-dashboard--streamlit-mvp)
8. [Frontend — Next.js (Production)](#8-frontend--nextjs-production)
9. [API — FastAPI](#9-api--fastapi)
10. [Watchlist Management](#10-watchlist-management)
11. [Paper Trading Simulator](#11-paper-trading-simulator)
12. [Backtesting](#12-backtesting)
13. [Scheduled Automation](#13-scheduled-automation)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. What This System Does

Minervini AI is a **production-grade, fully automated stock screening and analysis system** based on Mark Minervini's SEPA methodology. Every trading day (Mon–Fri), it:

1. **Downloads** fresh OHLCV price data for your stock universe (Nifty 500 by default)
2. **Computes** technical indicators: Moving Averages (SMA 10/21/50/150/200), ATR, Relative Strength vs Nifty 500, volume ratios, VCP metrics
3. **Screens** using Minervini's rules:
   - **Stage Detection** (hard gate — only Stage 2 stocks are candidates)
   - **Trend Template** (all 8 conditions must pass)
   - **VCP Qualification** (Volatility Contraction Pattern)
   - **Entry Trigger** (breakout confirmation)
4. **Scores** each candidate 0–100 and tags setup quality: A+, A, B, C, FAIL
5. **Generates** daily watchlist reports (CSV + HTML)
6. **Alerts** via Telegram / Email / Webhook for top setups
7. **Paper trades** A+/A signals automatically (optional)
8. **Exposes** results via REST API and web UI

---

## 2. Prerequisites

### System Requirements

| Requirement | Details |
|---|---|
| OS | Ubuntu 20.04+ (or macOS with Python 3.11+) |
| Python | 3.11 or 3.12 |
| Disk space | ~5 GB for full Nifty 500 universe with 5 years of history |
| Internet | Required for daily data downloads and LLM calls |
| Memory | 4 GB minimum, 8 GB recommended |

### Required Accounts (at least one LLM provider recommended)

| Service | Purpose | Cost |
|---|---|---|
| **Groq** (recommended) | LLM narrative generation | Free tier (free tier sufficient) |
| Telegram Bot | Alert dispatch | Free |

Sign up at [console.groq.com](https://console.groq.com) for a free API key.

---

## 3. Installation

### 3.1 Clone the Repository

```bash
git clone <repo-url>
cd minervini_ai
```

### 3.2 Create a Virtual Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
```

### 3.3 Install Dependencies

```bash
# Install the project in editable mode with all dependencies
pip install -e ".[dev]"

# Or without dev dependencies (faster, no tests/linting tools)
pip install -e .
```

### 3.4 Verify Installation

```bash
python -c "from rules.stage import detect_stage; print('OK')"
```

If you see `OK`, the installation succeeded.

---

## 4. Configuration

### 4.1 Copy the Environment File

```bash
cp .env.example .env
```

### 4.2 Fill in Your API Keys

Open `.env` in your editor and set the keys you have:

```env
# ── LLM Provider (at least one required for AI trade briefs) ──────────────
GROQ_API_KEY=gsk_your_key_here          # Recommended — free at console.groq.com

# ── Telegram Alerts (optional) ────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# ── API Authentication ───────────────────────────────────────────────────
API_READ_KEY=your_read_key          # Any random string, e.g.: openssl rand -hex 32
API_ADMIN_KEY=your_admin_key        # Different random string for admin endpoints
```

All other variables are optional. The system works without them — LLM briefs and Telegram alerts will be skipped gracefully.

### 4.3 Review Settings.yaml

Key settings in `config/settings.yaml` you may want to adjust:

```yaml
universe:
  index: "nifty500"              # or "nifty200", "nse_all"
  min_price: 50                  # Minimum price in INR

watchlist:
  always_scan: true              # Always include watchlist in daily runs
  priority_in_reports: true      # Show watchlist symbols first

scoring:
  setup_quality_thresholds:
    a_plus: 85                    # Score >= 85 → A+
    a: 70                        # Score >= 70 → A
    b: 55                        # Score >= 55 → B
    c: 40                        # Score >= 40 → C

llm:
  enabled: true                  # Set to false to disable AI briefs
  provider: "groq"               # groq | anthropic | openai | openrouter | ollama

paper_trading:
  enabled: true
  initial_capital: 100000        # Starting capital in INR
```

---

## 5. First-Time Setup (Bootstrap)

On first install (or when adding new symbols), you need to download full price history and compute all technical indicators. This is called a **bootstrap run**.

> **Important:** Bootstrap downloads several years of historical data per symbol. It takes:
> - ~5–15 minutes for 500 symbols
> - ~60–90 minutes for 2000 symbols
> Run this once, ideally overnight.

### 5.1 Run Bootstrap

```bash
# Bootstrap the full Nifty 500 universe
python scripts/bootstrap.py --universe config

# Bootstrap specific symbols only
python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY,DIXON"
```

This will:
1. Download historical OHLCV data for each symbol (via yfinance)
2. Validate and clean the data
3. Compute all technical indicators
4. Store results in `data/processed/` and `data/features/` as Parquet files

### 5.2 Verify Bootstrap Completed

```bash
# Check that feature files were created
ls data/features/ | wc -l    # Should show symbol count, e.g. 500

# Run a quick sanity check
python -c "
import pandas as pd
df = pd.read_parquet('data/features/RELIANCE.parquet')
print(f'Rows: {len(df)}, Columns: {list(df.columns[:5])}...')
print(f'Latest date: {df.index[-1]}')
"
```

### 5.3 Run Your First Daily Screen

```bash
# After bootstrap completes, run the daily pipeline
python scripts/run_daily.py --date today

# Or use the Make target
make daily
```

You should see output like:

```
=== Minervini SEPA Pipeline ===
Date: 2026-04-13, Mode: daily, Scope: all
Universe: 500 symbols | Watchlist: 12 symbols
Running screen... done.
Results: 3 A+ | 12 A | 8 B | 22 C | 455 FAIL
Pipeline run complete in 28.4s
```

---

## 6. Running the Daily Pipeline

### 6.1 Standard Daily Run

After the initial bootstrap, run this every trading day at or after 15:35 IST (market close):

```bash
python scripts/run_daily.py --date today
```

The pipeline is **incremental** — it only downloads today's single row per symbol and appends it to existing feature files. It takes ~30 seconds for 500 symbols.

### 6.2 Run Options

```bash
# Run for a specific date (reprocess past data)
python scripts/run_daily.py --date 2026-04-10

# Run watchlist only (skip full universe scan)
python scripts/run_daily.py --date today --watchlist-only

# Inline ad-hoc symbols (not persisted to watchlist)
python scripts/run_daily.py --date today --symbols "DIXON,TATAELXSI"

# Use a custom watchlist file
python scripts/run_daily.py --date today --watchlist mylist.csv

# Preview what would run without executing
python scripts/run_daily.py --date today --dry-run
```

### 6.3 Understanding the Output

Each run produces:

| Output | Location | Description |
|---|---|---|
| HTML Report | `reports/output/watchlist_YYYY-MM-DD.html` | Dark-themed ranked list of candidates |
| CSV Report | `reports/output/watchlist_YYYY-MM-DD.csv` | Raw data for spreadsheet analysis |
| Charts | `reports/output/charts/` | Candlestick PNG per A+/A candidate |
| Telegram Alert | (if configured) | Formatted message with top setups |
| SQLite Results | `data/minervini.db` | Persisted `sepa_results` + `run_history` |

---

## 7. Dashboard — Streamlit (MVP)

The Streamlit dashboard is a Python-native UI that reads directly from SQLite and Parquet files. It requires no API.

### 7.1 Start the Dashboard

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Launch Streamlit
make dashboard

# Or directly:
python -m streamlit run dashboard/app.py --server.port 8501
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

### 7.2 Dashboard Pages

| Page | URL | Description |
|---|---|---|
| **Home** | `/` | Overview: market status, last run time, A+/A count, top candidates |
| **Watchlist** | `/watchlist` | Manage your watchlist, upload CSV, run watchlist screen, see results |
| **Screener** | `/screener` | Full universe results table with filters (quality, stage, RS rating, sector) |
| **Stock** | `/stock` | Single stock deep-dive: candlestick chart, Trend Template checklist, VCP metrics, fundamentals, AI brief |
| **Portfolio** | `/portfolio` | Paper trading portfolio: open positions, P&L, equity curve, trade history |
| **Backtest** | `/backtest` | Backtest results viewer: equity curve with regime shading, per-regime stats |

### 7.3 Managing the Watchlist via Dashboard

1. Go to the **Watchlist** page
2. **Add symbols** via:
   - File upload: upload a `.csv`, `.json`, `.xlsx`, or `.txt` file with one symbol per row
   - Manual entry: type symbols in the text box (comma-separated) and click "Add"
3. Click **[Run Watchlist Now]** to immediately screen your watchlist
4. Results appear on the same page, with watchlist symbols highlighted with a ★ badge

### 7.4 Stopping the Dashboard

```bash
# Press Ctrl+C in the terminal where Streamlit is running
```

---

## 8. Frontend — Next.js (Production)

The Next.js frontend is a modern, mobile-first web app that talks to the FastAPI backend. It is the production UI.

### 8.1 Prerequisites for Frontend

| Requirement | Version |
|---|---|
| Node.js | 18+ |
| npm | 9+ |
| FastAPI server | Must be running (or deployed) |

### 8.2 Setup

```bash
cd frontend
npm install
```

### 8.3 Configure Environment

```bash
cp .env.local.example .env.local
```

Edit `.env.local`:

```env
# Base URL of the FastAPI server
NEXT_PUBLIC_API_URL=http://localhost:8000

# API key for read-only endpoints (safe to expose in browser)
NEXT_PUBLIC_API_READ_KEY=your_read_key_here

# Admin key for triggering runs (server-side only, never sent to browser)
API_ADMIN_KEY=your_admin_key_here
```

### 8.4 Run Locally

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### 8.5 Pages

| Route | Description |
|---|---|
| `/` | Dashboard: KPI cards, best setups, quick actions |
| `/screener` | Full universe screener with quality/stage/RS filters |
| `/screener/[symbol]` | Single stock deep-dive: candlestick chart, tabs for TT/Fundamentals/AI/History |
| `/watchlist` | Watchlist management with today's results |
| `/portfolio` | Paper portfolio: P&L cards, equity curve, trade list |

### 8.6 Optional Password Gate

To protect the frontend with a password:

```env
# In .env.local
NEXT_PUBLIC_REQUIRE_AUTH=true
SITE_PASSWORD=your-secret-password
```

Visitors will be redirected to `/login` and must enter the password to access the app.

### 8.7 Deploying to Vercel

```bash
cd frontend
npm i -g vercel
vercel login
vercel link
vercel env add NEXT_PUBLIC_API_URL       production
vercel env add NEXT_PUBLIC_API_READ_KEY  production
vercel env add API_ADMIN_KEY             production
vercel --prod
```

Vercel will automatically build and deploy from `frontend/vercel.json`.

---

## 9. API — FastAPI

The FastAPI server exposes all screener results over HTTP, enabling the Next.js frontend and any future integrations.

### 9.1 Start the API

```bash
# Development (with hot-reload)
make api

# Or directly:
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Production (2 workers)
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the Swagger UI.

### 9.2 Key Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/health` | Health check + last run info |
| GET | `/api/v1/meta` | Universe size, watchlist size, A+/A counts |
| GET | `/api/v1/stocks/top?quality=A+&limit=20` | Top-ranked candidates |
| GET | `/api/v1/stock/{symbol}` | Full SEPA result for a single symbol |
| GET | `/api/v1/stock/{symbol}/history?days=30` | Historical score for a symbol |
| GET | `/api/v1/watchlist` | All watchlist symbols with scores |
| POST | `/api/v1/watchlist/{symbol}` | Add one symbol to watchlist |
| DELETE | `/api/v1/watchlist/{symbol}` | Remove one symbol from watchlist |
| POST | `/api/v1/watchlist/bulk` | Add multiple symbols at once |
| POST | `/api/v1/watchlist/upload` | Upload watchlist file (multipart/form-data) |
| GET | `/api/v1/portfolio` | Paper trading portfolio summary |
| POST | `/api/v1/run` | **Admin only** — trigger a manual screen run |

### 9.3 Authentication

The API uses two API keys set in `.env`:

- `API_READ_KEY` — for read-only endpoints (GET requests)
- `API_ADMIN_KEY` — for admin operations (POST/PUT/DELETE, including triggering runs)

Pass the key as a header:

```bash
# Read request
curl -H "X-API-Key: your_read_key" http://localhost:8000/api/v1/stocks/top

# Admin request (trigger a run)
curl -X POST -H "X-API-Key: your_admin_key" \
  -H "Content-Type: application/json" \
  -d '{"scope": "watchlist"}' \
  http://localhost:8000/api/v1/run
```

### 9.4 Rate Limits

- Read endpoints: 100 requests per minute per IP
- Admin endpoints: 10 requests per minute per IP

---

## 10. Watchlist Management

The system distinguishes between:

- **Universe** (configured in `config/universe.yaml`): your stock pool, scanned every day, changed rarely
- **Watchlist** (stored in SQLite): your personal curated symbols, managed via CLI/API/dashboard, changed frequently

Both are scanned every day. Watchlist symbols appear **first** in reports with a ★ badge.

### 10.1 Adding Symbols to Watchlist

**Via CLI:**
```bash
# Inline SQL (quick)
sqlite3 data/minervini.db \
  "INSERT INTO watchlist (symbol, note, added_via) VALUES ('DIXON', 'Strong VCP', 'cli');"
```

**Via API:**
```bash
# Add one symbol
curl -X POST -H "X-API-Key: your_admin_key" \
  http://localhost:8000/api/v1/watchlist/DIXON \
  -d '{"note": "Watching for breakout"}'

# Add multiple symbols at once
curl -X POST -H "X-API-Key: your_admin_key" \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["RELIANCE", "TCS", "INFY"]}' \
  http://localhost:8000/api/v1/watchlist/bulk

# Upload a file
curl -X POST -H "X-API-Key: your_admin_key" \
  -F "file=@mylist.csv" \
  http://localhost:8000/api/v1/watchlist/upload
```

**Supported file formats for `--watchlist` / upload:**

| Format | Structure | Example |
|---|---|---|
| `.csv` | Column named `symbol` or first column | `RELIANCE\nTCS\nDIXON` |
| `.json` | Array of strings | `["RELIANCE", "TCS", "DIXON"]` |
| `.xlsx` | First sheet, `symbol` column | Standard Excel |
| `.txt` | One symbol per line | `RELIANCE\nTCS\n` |

### 10.2 Removing Symbols

```bash
# Via API
curl -X DELETE -H "X-API-Key: your_admin_key" \
  http://localhost:8000/api/v1/watchlist/DIXON

# Clear entire watchlist
curl -X DELETE -H "X-API-Key: your_admin_key" \
  http://localhost:8000/api/v1/watchlist
```

### 10.3 Viewing Watchlist

```bash
curl -H "X-API-Key: your_read_key" \
  http://localhost:8000/api/v1/watchlist
```

---

## 11. Paper Trading Simulator

Paper trading automatically enters positions for A+/A signals after each daily screen, using simulated capital. It lets you validate signals in real-time without risking real money.

**Rule:** Run paper trading for 4–8 weeks before considering backtesting or live execution.

### 11.1 How It Works

After each daily screen, if `paper_trading.enabled: true` in `settings.yaml`:

1. A+/A candidates with score ≥ 70 enter paper trades
2. Orders outside market hours (9:15–15:30 IST Mon–Fri) are queued for next open
3. Stop loss = VCP base_low (primary) or ATR-based (fallback)
4. Target = 2× risk (2R) by default
5. Pyramiding: add 50% more to a winning position if VCP Grade A on a pullback

### 11.2 Portfolio Configuration

```yaml
paper_trading:
  enabled: true
  initial_capital: 100000    # INR — change to match your planned capital
  max_positions: 10         # Max open trades at once
  risk_per_trade_pct: 2.0    # % of portfolio risked per trade (2% = one max loss)
  min_score_to_trade: 70     # Only trade A+ and A setups
  min_confidence: 50
```

### 11.3 Viewing Paper Portfolio

**Streamlit Dashboard → Portfolio page:**
```
Total Value: Rs 1,12,450
Return: +12.45% | Win Rate: 68%
Open Positions: 4 | Closed Trades: 18
Realised P&L: +Rs 12,450 | Unrealised P&L: +Rs 3,200
```

**API:**
```bash
curl -H "X-API-Key: your_read_key" \
  http://localhost:8000/api/v1/portfolio

curl -H "X-API-Key: your_read_key" \
  "http://localhost:8000/api/v1/portfolio/trades?status=open"
```

### 11.4 Resetting Paper Portfolio

```bash
make paper-reset
```

This wipes all positions and resets capital to `paper_trading.initial_capital`. Use when starting a new evaluation period.

---

## 12. Backtesting

The backtester runs your SEPA strategy over historical data to measure performance. It uses walk-forward testing to avoid lookahead bias.

### 12.1 Running a Backtest

```bash
# Full Nifty 500, 3-year window
make backtest START=2021-01-01 END=2024-01-01

# Watchlist only
python scripts/backtest_runner.py \
  --start 2021-01-01 \
  --end 2024-01-01 \
  --scope watchlist

# Specific symbols
python scripts/backtest_runner.py \
  --start 2021-01-01 \
  --end 2024-01-01 \
  --symbols "RELIANCE,TCS,INFY"
```

### 12.2 Key Metrics Reported

| Metric | Description |
|---|---|
| **CAGR** | Compound Annual Growth Rate |
| **Sharpe Ratio** | Risk-adjusted return |
| **Max Drawdown** | Largest peak-to-trough decline |
| **Win Rate** | % of profitable trades |
| **Avg R-Multiple** | Average profit as multiple of risk |
| **Profit Factor** | Gross profit / gross loss |
| **Per-Regime Stats** | Performance in Bull / Bear / Sideways markets |

### 12.3 Backtest Report

Output is saved to `data/backtests/`:
- `backtest_YYYY-MM-DD_YYYY-MM-DD.html` — full HTML report with equity curve
- `backtest_YYYY-MM-DD_YYYY-MM-DD.csv` — raw trade log

The **Streamlit Dashboard → Backtest page** visualises the equity curve with regime shading (green=Bull, red=Bear, grey=Sideways).

---

## 13. Scheduled Automation

On a Linux server (Ubuntu), you can automate the daily pipeline to run automatically at market close every trading day.

### 13.1 systemd Timer (Recommended)

Three services are set up in `deploy/`:

```bash
# Enable the daily timer (runs at 15:35 IST Mon–Fri)
sudo systemctl enable --now minervini-daily.timer

# Enable the API (runs continuously, auto-restarts)
sudo systemctl enable --now minervini-api.service

# Enable the dashboard (runs continuously, auto-restarts)
sudo systemctl enable --now minervini-dashboard.service
```

Check status:
```bash
systemctl list-timers --all | grep minervini
journalctl -u minervini-daily.service --since "today" --no-pager
```

### 13.2 Checking Run History

```bash
sqlite3 data/minervini.db \
  "SELECT run_date, status, duration_sec, a_plus_count, a_count
   FROM run_history ORDER BY run_id DESC LIMIT 5;"
```

| status | Meaning |
|---|---|
| `success` | Clean run, all symbols processed |
| `partial` | `run_screen` failed, results may be empty |
| `failed` | Pipeline aborted early, check logs |

### 13.3 Manual Trigger

```bash
# Trigger immediately (bypasses the timer)
sudo systemctl start minervini-daily.service

# View logs
journalctl -u minervini-daily.service -f
```

---

## 14. Troubleshooting

### "No module named X" After Fresh Clone

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

### InsufficientDataError

A symbol doesn't have enough historical data for SMA_200. This is common for newly listed stocks. Either:
- Exclude it via `min_listing_years: 1` in `config/universe.yaml`
- Accept it will be skipped — the error is caught and logged gracefully

### FeatureStoreOutOfSyncError

This means the daily run was already executed for that date (idempotent guard). It is expected behaviour — nothing to fix. If you need to recompute: `python scripts/rebuild_features.py --symbols XYZ`

### Telegram Alert Not Sending

1. Verify `.env` has both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
2. Test the bot: `curl "https://api.telegram.org/bot<TOKEN>/getMe"`
3. Check chat ID: `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"`
4. Pipeline continues if Telegram fails — it is non-blocking

### LLM Briefs Not Generating

1. Verify at least one LLM API key is set in `.env` (Groq recommended)
2. Check `settings.yaml`: `llm.enabled: true` and `llm.provider: "groq"`
3. If `llm.only_for_quality` is set, only A+ and/or A setups will get briefs

### Dashboard Shows "No Data"

1. Confirm the pipeline ran successfully today: `make daily`
2. Check `data/minervini.db` has recent entries:
   ```bash
   sqlite3 data/minervini.db "SELECT COUNT(*) FROM sepa_results WHERE date = '2026-04-13';"
   ```
3. Check the feature files exist: `ls data/features/ | wc -l`

### API Returns 403

- Read endpoints need `API_READ_KEY`
- Admin endpoints (POST/PUT/DELETE) need `API_ADMIN_KEY`
- Check you are passing the header correctly: `-H "X-API-Key: your_key"`

### Slow Daily Runs (> 5 minutes)

Common causes:
1. **Unexpected bootstraps** — feature files are missing for many symbols. Check: `grep "bootstrapping" logs/minervini.log | wc -l`. If high, something is deleting feature files.
2. **Network bottleneck** — yfinance downloads are slow before market hours. Always run after 15:35 IST.
3. **LLM latency** — Step 5b (narrative generation) adds ~1–3s per A+ result. Set `only_for_quality: ["A+"]` in `settings.yaml` to limit.

---

## Quick Reference

```bash
# Install
pip install -e ".[dev]"

# Daily run
make daily

# Start API
make api

# Start Dashboard
make dashboard

# Run tests
make test

# Paper portfolio reset
make paper-reset

# Rebuild all features
make rebuild

# Backtest
make backtest START=2021-01-01 END=2024-01-01

# View run history
sqlite3 data/minervini.db "SELECT * FROM run_history ORDER BY run_id DESC LIMIT 5;"

# Check latest results
sqlite3 data/minervini.db "SELECT symbol, score, setup_quality FROM sepa_results WHERE date = '2026-04-13' AND setup_quality IN ('A+', 'A') ORDER BY score DESC LIMIT 10;"
```