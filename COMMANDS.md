# COMMANDS.md
# Minervini AI — End-User Command Reference

> **Project path:** `/home/ubuntu/projects/minervini_ai`
> All commands are run from the project root unless stated otherwise.
> Always activate the virtual environment first.

---

## Table of Contents

1. [First — Activate the Virtual Environment](#1-first--activate-the-virtual-environment)
2. [Daily Pipeline — `scripts/run_daily.py`](#2-daily-pipeline--scriptsrun_dailypy)
3. [Bootstrap — `scripts/bootstrap.py`](#3-bootstrap--scriptsbootstrappy)
4. [Backtest — `scripts/backtest_runner.py`](#4-backtest--scriptsbacktest_runnerpy)
5. [Rebuild Features — `scripts/rebuild_features.py`](#5-rebuild-features--scriptsrebuild_featurespy)
6. [Paper Trading Reset](#6-paper-trading-reset)
7. [API Server](#7-api-server)
8. [Streamlit Dashboard](#8-streamlit-dashboard)
9. [Testing](#9-testing)
10. [Linting & Formatting](#10-linting--formatting)
11. [Makefile Shortcuts](#11-makefile-shortcuts)
12. [SQLite — Quick Queries](#12-sqlite--quick-queries)
13. [Log Inspection](#13-log-inspection)
14. [Systemd Services — Start / Stop / Status](#14-systemd-services--start--stop--status)
15. [Git Workflow](#15-git-workflow)
16. [Environment & Secrets](#16-environment--secrets)

---

## 1. First — Activate the Virtual Environment

Run this at the start of every terminal session before any other command.

```bash
cd /home/ubuntu/projects/minervini_ai
source .venv/bin/activate
```

Confirm you are inside the venv:

```bash
which python   # → /home/ubuntu/projects/minervini_ai/.venv/bin/python
```

---

## 2. Daily Pipeline — `scripts/run_daily.py`

The main entry point for every trading-day run. Resolves the symbol universe,
initialises the database, logs the run, and (Phase 2+) computes features and
runs the SEPA screener.

### Basic usage

```bash
# Run for today (IST) — full universe + watchlist
python scripts/run_daily.py --date today

# Run for a specific past date (backfill / weekend testing)
python scripts/run_daily.py --date 2024-01-15
```

### Date options

```bash
# "today" resolves to today's date in IST
python scripts/run_daily.py --date today

# Any ISO date — shows a WARNING if the date is a weekend or NSE holiday,
# but does NOT abort (backfill runs on non-trading days are valid)
python scripts/run_daily.py --date 2024-03-25
```

### Inline symbols (highest priority — overrides all other sources)

```bash
# Analyse only these symbols, ignore universe.yaml and watchlist
python scripts/run_daily.py --symbols "RELIANCE,TCS,INFY"
python scripts/run_daily.py --symbols "DIXON,TATAELXSI,CDSL" --date 2024-06-01
```

### Watchlist file (persists symbols to SQLite, then runs)

```bash
# Load from a CSV file — column named "symbol" or first column
python scripts/run_daily.py --watchlist mylist.csv

# JSON array format: ["RELIANCE", "TCS", ...]
python scripts/run_daily.py --watchlist mylist.json

# Excel (first sheet, "symbol" column or column A)
python scripts/run_daily.py --watchlist mylist.xlsx

# Plain text (one symbol per line; # lines are comments)
python scripts/run_daily.py --watchlist mylist.txt
```

### Scope — what to scan

```bash
# Scan both universe and watchlist (default)
python scripts/run_daily.py --scope all

# Scan universe symbols only (skip watchlist)
python scripts/run_daily.py --scope universe

# Scan watchlist symbols only (skip full universe)
python scripts/run_daily.py --scope watchlist

# Shorthand for --scope watchlist
python scripts/run_daily.py --watchlist-only
```

### Dry-run — inspect without writing

```bash
# Resolve and print all symbols, print the summary table, then exit.
# Does NOT write to the database or run any pipeline steps.
python scripts/run_daily.py --dry-run
python scripts/run_daily.py --date 2024-01-15 --dry-run
python scripts/run_daily.py --symbols "RELIANCE,DIXON" --dry-run
python scripts/run_daily.py --watchlist mylist.csv --dry-run
python scripts/run_daily.py --watchlist-only --dry-run
```

### Custom paths

```bash
# Use a different settings file
python scripts/run_daily.py --config config/prod_settings.yaml

# Use a different SQLite database file
python scripts/run_daily.py --db data/custom.db

# Combine — non-default config and DB
python scripts/run_daily.py \
    --config config/prod_settings.yaml \
    --db /mnt/data/minervini_prod.db \
    --date today
```

### Combined examples

```bash
# Watchlist-only dry-run for today
python scripts/run_daily.py --watchlist-only --dry-run

# Persist a new watchlist file, run today, watch only those symbols
python scripts/run_daily.py --watchlist mylist.csv --watchlist-only

# Full run with custom config and explicit date
python scripts/run_daily.py --date 2024-01-15 --config config/settings.yaml

# Ad-hoc check of three symbols on a past date, no DB write
python scripts/run_daily.py --symbols "DIXON,CDSL,DMART" --date 2024-06-01 --dry-run
```

---

## 3. Bootstrap — `scripts/bootstrap.py`

> **Phase 1 — planned, not yet built.**

Downloads full price history (5–10 years) for all symbols and computes all
features from scratch. Run once on first setup, and again if feature files are
corrupted.

```bash
# Bootstrap all symbols defined in config/universe.yaml
python scripts/bootstrap.py --universe config

# Bootstrap the full Nifty 500
python scripts/bootstrap.py --universe nifty500

# Bootstrap specific symbols only
python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY"

# Force a full recompute even if feature files already exist
python scripts/bootstrap.py --universe config --force

# Use a specific date as the "today" anchor
python scripts/bootstrap.py --universe config --date 2024-01-15
```

> **Estimated time:** 5–15 min for 500 symbols, 60–90 min for 2 000 symbols.
> Run overnight or over a weekend. The daily incremental update afterwards
> takes ~30 seconds.

---

## 4. Backtest — `scripts/backtest_runner.py`

> **Phase 8 — planned, not yet built.**

Walk-forward backtester over a historical date range. Tests the SEPA strategy
on past data without lookahead bias.

```bash
# Basic backtest — Nifty 500, 5-year window, 7% trailing stop
python scripts/backtest_runner.py \
    --start 2019-01-01 \
    --end   2024-01-01 \
    --universe nifty500 \
    --trailing-stop 0.07

# Fixed stop instead of trailing stop
python scripts/backtest_runner.py \
    --start 2019-01-01 \
    --end   2024-01-01 \
    --universe nifty500 \
    --fixed-stop 0.05

# Backtest on a custom symbol list
python scripts/backtest_runner.py \
    --start 2020-01-01 \
    --end   2024-01-01 \
    --symbols "DIXON,TATAELXSI,CDSL,DMART"

# Include per-regime breakdown (Bull / Bear / Sideways)
python scripts/backtest_runner.py \
    --start 2019-01-01 \
    --end   2024-01-01 \
    --universe nifty500 \
    --trailing-stop 0.07 \
    --regime-breakdown

# Output to a specific report directory
python scripts/backtest_runner.py \
    --start 2019-01-01 \
    --end   2024-01-01 \
    --output reports/backtest_2019_2024/
```

Makefile shortcut (pass dates as variables):

```bash
make backtest START=2019-01-01 END=2024-01-01
```

---

## 5. Rebuild Features — `scripts/rebuild_features.py`

> **Phase 9 — planned, not yet built.**

Recomputes all features from scratch using already-downloaded processed
OHLCV data. Faster than a full bootstrap because it skips re-downloading.

```bash
# Rebuild all symbols in the universe
python scripts/rebuild_features.py --universe nifty500

# Rebuild a specific symbol only
python scripts/rebuild_features.py --symbol RELIANCE

# Rebuild from config/universe.yaml
python scripts/rebuild_features.py --universe config
```

Makefile shortcut:

```bash
make rebuild
```

---

## 6. Paper Trading Reset

> **Phase 7 — planned, not yet built.**

Clears the paper trading portfolio and trade history, resetting to the initial
capital configured in `settings.yaml`.

```bash
# Interactive reset — prompts for confirmation
python -c "from paper_trading.simulator import reset_portfolio; reset_portfolio(confirm=True)"
```

Makefile shortcut:

```bash
make paper-reset
```

---

## 7. API Server

> **Phase 10 — planned, not yet built.**

Starts the FastAPI server that exposes screener results, watchlist management,
and paper trading data over HTTP.

### Development (auto-reload on file changes)

```bash
uvicorn api.main:app --reload --port 8000
```

### Production (multi-worker, bound to all interfaces)

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Makefile shortcut:

```bash
make api
```

### Key API endpoints (once built)

```
# Health check
curl http://localhost:8000/api/v1/health

# Top-ranked setups for today
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/stocks/top

# Top setups — filter to A+ quality only
curl -H "X-API-Key: <read_key>" "http://localhost:8000/api/v1/stocks/top?quality=A%2B"

# Full SEPA result for one symbol
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/stock/DIXON

# Historical scores for a symbol (last 30 days)
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/stock/DIXON/history

# Stocks that passed Trend Template today
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/stocks/trend

# Stocks with a qualified VCP pattern
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/stocks/vcp

# View the current watchlist
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/watchlist

# Add a single symbol to the watchlist
curl -X POST -H "X-API-Key: <admin_key>" http://localhost:8000/api/v1/watchlist/DIXON

# Remove a symbol from the watchlist
curl -X DELETE -H "X-API-Key: <admin_key>" http://localhost:8000/api/v1/watchlist/DIXON

# Add multiple symbols at once
curl -X POST \
     -H "X-API-Key: <admin_key>" \
     -H "Content-Type: application/json" \
     -d '{"symbols": ["RELIANCE","TCS","INFY"]}' \
     http://localhost:8000/api/v1/watchlist/bulk

# Upload a watchlist file
curl -X POST \
     -H "X-API-Key: <admin_key>" \
     -F "file=@mylist.csv" \
     http://localhost:8000/api/v1/watchlist/upload

# Clear the entire watchlist (requires admin key)
curl -X DELETE -H "X-API-Key: <admin_key>" http://localhost:8000/api/v1/watchlist

# Trigger a manual run — all symbols
curl -X POST \
     -H "X-API-Key: <admin_key>" \
     -H "Content-Type: application/json" \
     -d '{"scope": "all"}' \
     http://localhost:8000/api/v1/run

# Trigger a manual run — watchlist only
curl -X POST \
     -H "X-API-Key: <admin_key>" \
     -H "Content-Type: application/json" \
     -d '{"scope": "watchlist"}' \
     http://localhost:8000/api/v1/run

# Trigger a manual run — ad-hoc inline symbols
curl -X POST \
     -H "X-API-Key: <admin_key>" \
     -H "Content-Type: application/json" \
     -d '{"symbols": ["DIXON","CDSL"]}' \
     http://localhost:8000/api/v1/run

# Paper trading portfolio summary
curl -H "X-API-Key: <read_key>" http://localhost:8000/api/v1/portfolio

# Paper trade history — all trades
curl -H "X-API-Key: <read_key>" "http://localhost:8000/api/v1/portfolio/trades?status=all"

# System meta (universe size, last run, A+/A count)
curl http://localhost:8000/api/v1/meta
```

---

## 8. Streamlit Dashboard

> **Phase 11 — planned, not yet built.**

Launches the visual dashboard in a browser. No SSH or API key needed once
running — access it from any machine on the same network.

```bash
streamlit run dashboard/app.py --server.port 8501
```

Makefile shortcut:

```bash
make dashboard
```

Open in a browser:

```
http://<server-ip>:8501
```

Dashboard pages (once built):

| URL path | Contents |
|---|---|
| `/` (Watchlist) | Daily A+/A candidates, file upload, manual entry, [Run Now] button |
| `/Screener` | Full universe table with quality / stage / RS filters; CSV export |
| `/Stock` | Single stock deep-dive — chart, Trend Template checklist, VCP, LLM brief |
| `/Portfolio` | Paper trading P&L, open positions, equity curve |
| `/Backtest` | Backtest results, per-regime breakdown |

---

## 9. Testing

### Run the full test suite

```bash
pytest tests/ -v
```

### Run with coverage report

```bash
pytest tests/ -v --cov=. --cov-report=term-missing
```

### Run a single test file

```bash
pytest tests/unit/test_sqlite_store.py -v
pytest tests/unit/test_parquet_store.py -v
```

### Run a single test function

```bash
pytest tests/unit/test_sqlite_store.py::test_log_run -v
```

### Run only unit tests (skip integration)

```bash
pytest tests/unit/ -v
```

### Run only integration tests

```bash
pytest tests/integration/ -v
```

### Run tests matching a keyword

```bash
pytest tests/ -v -k "watchlist"
pytest tests/ -v -k "stage or trend_template"
```

Makefile shortcut:

```bash
make test
```

---

## 10. Linting & Formatting

### Check for linting errors (no changes written)

```bash
ruff check .
```

### Auto-fix linting errors

```bash
ruff check . --fix
```

### Format all Python files

```bash
ruff format .
```

### Check formatting only (no changes — useful in CI)

```bash
ruff format --check .
```

### Run both lint check and format check together

```bash
ruff check . && ruff format --check .
```

Makefile shortcut:

```bash
make lint     # check only
make format   # apply fixes
```

---

## 11. Makefile Shortcuts

> Run from the project root. `make help` lists all targets if the Makefile
> includes a help target.

```bash
make install          # install project + dev deps in editable mode
make test             # full test suite with coverage
make lint             # ruff check (no changes)
make format           # ruff format (applies changes)
make daily            # python scripts/run_daily.py --date today
make rebuild          # python scripts/rebuild_features.py --universe nifty500
make paper-reset      # reset paper trading portfolio (with confirmation)
make api              # start FastAPI dev server on port 8000
make dashboard        # start Streamlit dashboard on port 8501
make backtest START=YYYY-MM-DD END=YYYY-MM-DD   # run backtester over date range
```

---

## 12. SQLite — Quick Queries

The default database is at `data/minervini.db`. Pass the path to `sqlite3`.

```bash
# Open the database in interactive mode
sqlite3 data/minervini.db

# List all tables
sqlite3 data/minervini.db ".tables"

# Pretty-print output (run inside sqlite3 interactive session, or prepend to one-liners)
sqlite3 -column -header data/minervini.db "<query>"
```

### Run history

```bash
# Last 10 pipeline runs
sqlite3 -column -header data/minervini.db \
  "SELECT id, run_date, run_mode, scope, status, duration_sec, a_plus_count, a_count
   FROM run_history ORDER BY id DESC LIMIT 10;"

# Failed runs
sqlite3 -column -header data/minervini.db \
  "SELECT id, run_date, status, error_msg FROM run_history WHERE status = 'failed';"

# Today's run summary
sqlite3 -column -header data/minervini.db \
  "SELECT * FROM run_history WHERE run_date = date('now') ORDER BY id DESC LIMIT 1;"
```

### Watchlist

```bash
# View entire watchlist
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, added_via, last_score, last_quality, added_at FROM watchlist ORDER BY symbol;"

# Count watchlist symbols
sqlite3 data/minervini.db "SELECT COUNT(*) FROM watchlist;"

# Find a specific symbol
sqlite3 -column -header data/minervini.db \
  "SELECT * FROM watchlist WHERE symbol = 'DIXON';"
```

### Screener results

```bash
# Today's top 20 by score
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, setup_quality, score, stage, rs_rating, vcp_qualified
   FROM screener_results
   WHERE run_date = date('now')
   ORDER BY score DESC
   LIMIT 20;"

# Today's A+ and A setups
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, setup_quality, score, entry_price, stop_loss, risk_pct
   FROM screener_results
   WHERE run_date = date('now') AND setup_quality IN ('A+', 'A')
   ORDER BY score DESC;"

# Historical scores for one symbol (last 30 days)
sqlite3 -column -header data/minervini.db \
  "SELECT run_date, setup_quality, score, stage, rs_rating
   FROM screener_results
   WHERE symbol = 'DIXON'
   ORDER BY run_date DESC
   LIMIT 30;"

# Count results per quality tier for today
sqlite3 -column -header data/minervini.db \
  "SELECT setup_quality, COUNT(*) AS count
   FROM screener_results
   WHERE run_date = date('now')
   GROUP BY setup_quality
   ORDER BY count DESC;"
```

---

## 13. Log Inspection

Application logs are written to `logs/minervini.log`. Console output goes to
stderr and is visible directly in the terminal.

```bash
# Live-tail the log file
tail -f logs/minervini.log

# Show last 100 lines
tail -100 logs/minervini.log

# Filter for ERROR and CRITICAL only
grep -E '"level":("|)"(ERROR|CRITICAL)' logs/minervini.log

# Filter log lines for a specific symbol
grep '"symbol":"DIXON"' logs/minervini.log

# Count WARNING lines in today's log
grep '"level":"WARNING"' logs/minervini.log | grep "$(date +%Y-%m-%d)" | wc -l
```

### systemd journal (after Phase 9 — when services are set up)

```bash
# Live log from the daily pipeline service
journalctl -u minervini-daily.service -f

# Live log from the API server
journalctl -u minervini-api.service -f

# Live log from the Streamlit dashboard
journalctl -u minervini-dashboard.service -f

# All logs since midnight
journalctl -u minervini-daily.service --since today

# Last 50 lines from the API service
journalctl -u minervini-api.service -n 50
```

---

## 14. Systemd Services — Start / Stop / Status

> Applicable from Phase 9 onward, after the service files are installed.
> All `systemctl` commands require `sudo`.

### Daily pipeline (timer-triggered, runs Mon–Fri at 15:35 IST)

```bash
sudo systemctl status  minervini-daily.timer    # is the timer active?
sudo systemctl start   minervini-daily.timer    # enable the timer now
sudo systemctl stop    minervini-daily.timer    # pause the timer
sudo systemctl enable  minervini-daily.timer    # auto-start on reboot
sudo systemctl disable minervini-daily.timer    # remove from auto-start

# Run the pipeline immediately (bypass the timer schedule)
sudo systemctl start minervini-daily.service
```

### API server (always running)

```bash
sudo systemctl status  minervini-api.service
sudo systemctl start   minervini-api.service
sudo systemctl stop    minervini-api.service
sudo systemctl restart minervini-api.service    # pick up code changes
sudo systemctl enable  minervini-api.service
```

### Streamlit dashboard (always running)

```bash
sudo systemctl status  minervini-dashboard.service
sudo systemctl start   minervini-dashboard.service
sudo systemctl stop    minervini-dashboard.service
sudo systemctl restart minervini-dashboard.service
sudo systemctl enable  minervini-dashboard.service
```

---

## 15. Git Workflow

```bash
# Check what has changed
git status
git diff

# Stage changes interactively (review each hunk before committing)
git add -p

# Stage everything
git add .

# Commit with a conventional commit message
git commit -m "feat: add VCP contraction depth calculation"
git commit -m "fix: handle missing RS rating gracefully"
git commit -m "chore: update requirements.txt"
git commit -m "test: add unit tests for trend template"
git commit -m "docs: update COMMANDS.md with API endpoints"

# Push to remote
git push

# Pull latest changes
git pull

# View recent commit history
git log --oneline -20

# Check which branch you are on
git branch

# Create and switch to a feature branch
git checkout -b feat/vcp-detection
```

---

## 16. Environment & Secrets

### Copy the example env file and fill in your values

```bash
cp .env.example .env
nano .env           # or: vim .env
```

### Generate secure random API keys

```bash
# API_READ_KEY (for GET endpoints)
python -c "import secrets; print(secrets.token_hex(32))"

# API_ADMIN_KEY (for POST /api/v1/run and admin operations)
python -c "import secrets; print(secrets.token_hex(32))"
```

### Check which environment variables are set

```bash
grep -v "^#" .env | grep -v "^$"
```

### Verify a specific key is loaded correctly at runtime

```bash
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('GROQ_API_KEY', 'NOT SET'))"
```

### Control log verbosity at runtime (no config change needed)

```bash
# Show DEBUG-level logs in the console
LOG_LEVEL=DEBUG python scripts/run_daily.py --date today

# Emit logs in machine-readable JSON format
LOG_FORMAT=json python scripts/run_daily.py --date today

# Combine both
LOG_LEVEL=DEBUG LOG_FORMAT=json python scripts/run_daily.py --date today
```

### Switch the feature computation backend (Phase 2+ — when Polars is available)

```bash
# Use the pandas backend (default)
FEATURE_BACKEND=pandas python scripts/run_daily.py --date today

# Use the Polars backend (after migration is complete)
FEATURE_BACKEND=polars python scripts/run_daily.py --date today
```

---

## Quick Reference Card

```
── Environment ───────────────────────────────────────────────────────
source .venv/bin/activate            activate venv (every session)

── Daily Run ─────────────────────────────────────────────────────────
python scripts/run_daily.py --date today
python scripts/run_daily.py --date 2024-01-15
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON"
python scripts/run_daily.py --watchlist mylist.csv
python scripts/run_daily.py --watchlist-only
python scripts/run_daily.py --dry-run

── Scope ─────────────────────────────────────────────────────────────
--scope all          universe + watchlist (default)
--scope universe     universe only
--scope watchlist    watchlist only
--watchlist-only     shorthand for --scope watchlist

── Servers ───────────────────────────────────────────────────────────
uvicorn api.main:app --reload --port 8000    FastAPI dev server
streamlit run dashboard/app.py --server.port 8501    Streamlit

── Make ──────────────────────────────────────────────────────────────
make daily           run today's screen
make test            full test suite
make lint            check code style
make format          auto-format code
make api             start API server
make dashboard       start dashboard
make paper-reset     reset paper portfolio

── Tests ─────────────────────────────────────────────────────────────
pytest tests/ -v
pytest tests/unit/ -v
pytest tests/ -v -k "watchlist"
pytest tests/ -v --cov=. --cov-report=term-missing

── SQLite ────────────────────────────────────────────────────────────
sqlite3 data/minervini.db ".tables"
sqlite3 -column -header data/minervini.db \
  "SELECT symbol, setup_quality, score FROM screener_results
   WHERE run_date = date('now') ORDER BY score DESC LIMIT 20;"

── Logs ──────────────────────────────────────────────────────────────
tail -f logs/minervini.log
journalctl -u minervini-api.service -f
```

---

*Refer to `DEV_SETUP.md` for first-time environment setup.*
*Refer to `PROJECT_DESIGN.md` for architecture and design decisions.*
