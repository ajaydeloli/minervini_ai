# DEV_SETUP.md
# Minervini AI — Development Environment Setup Guide

> **Server:** ShreeVault (Ubuntu 24.04)  
> **Project path:** `/home/ubuntu/projects/minervini_ai`  
> **Python:** 3.11+  
> Run every command as the `ubuntu` user unless stated otherwise.

---

## Table of Contents

1. [System Prerequisites](#1-system-prerequisites)
2. [Python Setup](#2-python-setup)
3. [Project Skeleton](#3-project-skeleton)
4. [Python Dependencies](#4-python-dependencies)
5. [Configuration Files](#5-configuration-files)
6. [Environment Variables (.env)](#6-environment-variables-env)
7. [Git Setup](#7-git-setup)
8. [Verify the Setup](#8-verify-the-setup)
9. [Editor Setup (VS Code Remote)](#9-editor-setup-vs-code-remote)
10. [Node.js Setup (for Next.js — Phase 12 only)](#10-nodejs-setup-for-nextjs--phase-12-only)
11. [Quick Reference — Daily Commands](#11-quick-reference--daily-commands)

---

## 1. System Prerequisites

Update the system and install OS-level dependencies.

```bash
sudo apt update && sudo apt upgrade -y

# Core build tools
sudo apt install -y \
    build-essential \
    git \
    curl \
    wget \
    unzip \
    software-properties-common

# Python build deps (needed for some pip packages)
sudo apt install -y \
    libssl-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    liblzma-dev \
    libncurses-dev

# SQLite (should already be present, but ensure it is)
sudo apt install -y sqlite3 libsqlite3-dev

# Fonts for matplotlib chart generation (needed for mplfinance)
sudo apt install -y fonts-dejavu-core fontconfig

# Useful tools
sudo apt install -y htop tree jq
```

Check your Ubuntu version — this guide targets 24.04:

```bash
lsb_release -a
```

---

## 2. Python Setup

### 2.1 Check existing Python

```bash
python3 --version     # need 3.11 or higher
python3.11 --version  # check if 3.11 specifically is available
```

### 2.2 Install Python 3.11 if not present

```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Verify
python3.11 --version   # should print Python 3.11.x
```

### 2.3 Install pip for Python 3.11

```bash
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11
python3.11 -m pip --version
```

---

## 3. Project Skeleton

### 3.1 Create the project directory

```bash
mkdir -p /home/ubuntu/projects
cd /home/ubuntu/projects
```

### 3.2 Create all directories in one command

```bash
mkdir -p minervini_ai && cd minervini_ai

mkdir -p \
    ingestion \
    features \
    rules \
    screener \
    llm/prompt_templates \
    pipeline \
    backtest \
    paper_trading \
    alerts \
    reports/templates \
    api/routers \
    api/schemas \
    dashboard/pages \
    dashboard/components \
    frontend \
    storage \
    utils \
    config \
    tests/unit \
    tests/integration \
    tests/fixtures \
    notebooks \
    scripts \
    data/raw \
    data/processed \
    data/features \
    data/fundamentals \
    data/news \
    data/paper_trading \
    data/backtests \
    data/charts \
    logs
```

### 3.3 Create `__init__.py` in every Python package

```bash
for dir in ingestion features rules screener llm pipeline \
            backtest paper_trading alerts reports storage  \
            utils api dashboard; do
    touch ${dir}/__init__.py
done

# Subdirectory packages
touch api/routers/__init__.py
touch api/schemas/__init__.py
touch dashboard/components/__init__.py
```

### 3.4 Verify the structure

```bash
tree -L 2 --dirsfirst
```

Expected output (abbreviated):
```
.
├── api/
│   ├── routers/
│   ├── schemas/
│   └── __init__.py
├── backtest/
├── config/
├── dashboard/
├── data/
├── features/
├── ingestion/
├── llm/
├── logs/
├── notebooks/
├── paper_trading/
├── pipeline/
├── reports/
├── rules/
├── screener/
├── scripts/
├── storage/
├── tests/
└── utils/
```

---

## 4. Python Dependencies

### 4.1 Create the virtual environment

```bash
cd /home/ubuntu/projects/minervini_ai
python3.11 -m venv .venv
source .venv/bin/activate

# Confirm you're in the venv
which python    # should show: /home/ubuntu/projects/minervini_ai/.venv/bin/python
python --version  # should show: Python 3.11.x
```

### 4.2 Upgrade pip inside the venv

```bash
pip install --upgrade pip setuptools wheel
```

### 4.3 Create `requirements.txt`

```bash
cat > requirements.txt << 'EOF'
# ── Data ──────────────────────────────────────────────────────────────
yfinance>=0.2.40
pandas>=2.2.0
numpy>=1.26.0
pyarrow>=15.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
feedparser>=6.0.11

# ── Storage ───────────────────────────────────────────────────────────
SQLAlchemy>=2.0.0

# ── Config & Validation ───────────────────────────────────────────────
PyYAML>=6.0.1
pydantic>=2.6.0
pydantic-settings>=2.2.0
python-dotenv>=1.0.0

# ── Scheduling ────────────────────────────────────────────────────────
APScheduler>=3.10.4

# ── Charts ────────────────────────────────────────────────────────────
matplotlib>=3.8.0
mplfinance>=0.12.10b0
Pillow>=10.2.0

# ── LLM Providers ─────────────────────────────────────────────────────
anthropic>=0.25.0
openai>=1.14.0
groq>=0.5.0

# ── Alerting ──────────────────────────────────────────────────────────
python-telegram-bot>=21.0.0

# ── API Layer ─────────────────────────────────────────────────────────
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
slowapi>=0.1.9

# ── Dashboard (MVP) ───────────────────────────────────────────────────
streamlit>=1.32.0

# ── Templates ─────────────────────────────────────────────────────────
Jinja2>=3.1.3

# ── Utilities ─────────────────────────────────────────────────────────
tenacity>=8.2.3
pytz>=2024.1
python-dateutil>=2.9.0
tqdm>=4.66.2
openpyxl>=3.1.2

# ── News (optional) ───────────────────────────────────────────────────
# newsdata-io   # uncomment if you get a newsdata.io API key
EOF
```

### 4.4 Create `requirements-dev.txt`

```bash
cat > requirements-dev.txt << 'EOF'
-r requirements.txt

# ── Testing ───────────────────────────────────────────────────────────
pytest>=8.1.0
pytest-cov>=5.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0       # for FastAPI TestClient

# ── Linting & Formatting ──────────────────────────────────────────────
ruff>=0.3.0
black>=24.3.0

# ── Dev Utilities ─────────────────────────────────────────────────────
ipykernel>=6.29.0   # for notebooks
jupyterlab>=4.1.0
EOF
```

### 4.5 Install all dependencies

```bash
# Core runtime deps
pip install -r requirements.txt

# Dev deps (linting, testing, notebooks)
pip install -r requirements-dev.txt
```

This will take 3–5 minutes. Expected final line:
```
Successfully installed [list of packages]
```

### 4.6 Verify key packages

```bash
python -c "import pandas; print('pandas', pandas.__version__)"
python -c "import yfinance; print('yfinance OK')"
python -c "import fastapi; print('fastapi', fastapi.__version__)"
python -c "import streamlit; print('streamlit', streamlit.__version__)"
python -c "import pyarrow; print('pyarrow', pyarrow.__version__)"
python -c "import pydantic; print('pydantic', pydantic.__version__)"
```

All should print without errors.

---

## 5. Configuration Files

### 5.1 Create `pyproject.toml`

```bash
cat > pyproject.toml << 'EOF'
[build-system]
requires = ["setuptools>=69.0", "wheel"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "minervini_ai"
version = "0.1.0"
description = "Minervini SEPA stock analysis system"
requires-python = ">=3.11"
dependencies = []

[tool.setuptools.packages.find]
where = ["."]
exclude = ["tests*", "notebooks*", "frontend*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-v --tb=short"

[tool.ruff]
line-length = 100
target-version = "py311"
select = ["E", "F", "W", "I", "N", "UP"]
ignore = ["E501"]

[tool.black]
line-length = 100
target-version = ["py311"]
EOF
```

### 5.2 Install the project in editable mode

```bash
pip install -e ".[dev]" 2>/dev/null || pip install -e .
```

This means you can `from ingestion.nse_bhav import ...` from anywhere without path hacks.

### 5.3 Create `config/settings.yaml`

```bash
cat > config/settings.yaml << 'EOF'
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
  fundamentals_dir: "data/fundamentals"
  news_dir: "data/news"

watchlist:
  always_scan: true
  priority_in_reports: true
  always_generate_charts: true
  min_score_alert: 55

stage:
  ma200_slope_lookback: 20
  ma50_slope_lookback: 10

trend_template:
  ma200_slope_lookback: 20
  pct_above_52w_low: 25.0
  pct_below_52w_high: 25.0
  min_rs_rating: 70

vcp:
  detector: "rule_based"
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
  hard_gate: false
  cache_days: 7
  conditions:
    min_roe: 15.0
    max_de: 1.0
    min_promoter_holding: 35.0
    min_sales_growth_yoy: 10.0

news:
  enabled: false                  # enable after getting newsdata.io key
  cache_minutes: 30
  llm_rescore: false

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
    b: 55
    c: 40

paper_trading:
  enabled: false                  # enable when ready
  initial_capital: 100000
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

llm:
  enabled: false                  # enable after getting API key
  provider: "groq"
  model: "llama-3.3-70b-versatile"
  max_tokens: 350
  only_for_quality: ["A+", "A"]

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
    enabled: false
  email:
    enabled: false

scheduler:
  run_time: "15:35"
  timezone: "Asia/Kolkata"
EOF
```

### 5.4 Create `config/universe.yaml`

```bash
cat > config/universe.yaml << 'EOF'
# Stock universe definition
# The system will scan all symbols in this list every trading day.
# Start with a small handpicked list, expand to Nifty 500 later.

mode: "list"                      # "list" | "nifty500" | "nse_all"

# Handpicked starting universe — expand this as you go
symbols:
  - RELIANCE
  - TCS
  - INFY
  - HDFCBANK
  - ICICIBANK
  - SBIN
  - BAJFINANCE
  - WIPRO
  - TATAMOTORS
  - MARUTI
  - SUNPHARMA
  - AXISBANK
  - DIXON
  - TATAELXSI
  - CDSL
  - DMART
  - TITAN
  - ASIANPAINT
  - NESTLEIND
  - LTIM

# Filters applied to any symbol before it enters the universe
filters:
  min_price_inr: 50
  min_avg_daily_volume: 100000
  min_listing_years: 1            # exclude recently listed stocks
EOF
```

### 5.5 Create `config/logging.yaml`

```bash
cat > config/logging.yaml << 'EOF'
version: 1
disable_existing_loggers: false

formatters:
  standard:
    format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt: "%Y-%m-%d %H:%M:%S"
  json:
    format: '{"time":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'

handlers:
  console:
    class: logging.StreamHandler
    level: INFO
    formatter: standard
    stream: ext://sys.stdout

  file:
    class: logging.handlers.RotatingFileHandler
    level: DEBUG
    formatter: standard
    filename: logs/minervini.log
    maxBytes: 10485760              # 10 MB
    backupCount: 5

root:
  level: DEBUG
  handlers: [console, file]

loggers:
  urllib3:
    level: WARNING
  yfinance:
    level: WARNING
  matplotlib:
    level: WARNING
EOF
```

### 5.6 Create `.gitignore`

```bash
cat > .gitignore << 'EOF'
# Virtual environment
.venv/
venv/
env/

# Python
__pycache__/
*.py[cod]
*.pyo
*.pyd
.Python
*.egg-info/
dist/
build/
.eggs/

# Data (never commit raw data)
data/
logs/

# Environment
.env
.env.*
!.env.example

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Jupyter
.ipynb_checkpoints/
*.ipynb_checkpoints

# Testing
.coverage
htmlcov/
.pytest_cache/

# Streamlit
.streamlit/

# Node (for Next.js)
node_modules/
.next/
out/
EOF
```

---

## 6. Environment Variables (.env)

### 6.1 Create `.env.example` (committed to git — no real values)

```bash
cat > .env.example << 'EOF'
# ── Data Sources ──────────────────────────────────────────────────────
NSE_BHAV_BASE_URL=https://archives.nseindia.com/content/historical/EQUITIES/
NEWSDATA_API_KEY=                 # free tier at newsdata.io

# ── LLM Providers (get at least one) ─────────────────────────────────
GROQ_API_KEY=                     # free at console.groq.com — recommended first
ANTHROPIC_API_KEY=                # paid — claude-haiku is cheapest
OPENAI_API_KEY=                   # paid
OPENROUTER_API_KEY=               # free models available at openrouter.ai
OLLAMA_API_KEY=                   # leave blank for local Ollama

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
EOF
```

### 6.2 Create your real `.env`

```bash
cp .env.example .env
```

Now edit `.env` with your actual values:

```bash
nano .env
```

**Minimum required to get started (Phase 1–2 needs nothing):**
- Nothing — Phase 1 and 2 work with zero API keys (yfinance is free, no key needed)

**Add when you reach that phase:**
- `GROQ_API_KEY` — get free at [console.groq.com](https://console.groq.com) (needed for LLM phase)
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — needed for alerts phase
- `API_READ_KEY` + `API_ADMIN_KEY` — needed for API phase (just make up a random string)

### 6.3 Generate secure random API keys

```bash
# Generate API_READ_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"

# Generate API_ADMIN_KEY (run again for a different value)
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Paste both outputs into `.env`.

---

## 7. Git Setup

### 7.1 Initialise the repo

```bash
cd /home/ubuntu/projects/minervini_ai
git init
git branch -M main
```

### 7.2 Configure git identity (if not done globally)

```bash
git config user.name "Your Name"
git config user.email "your@email.com"
```

### 7.3 First commit — skeleton only

```bash
git add pyproject.toml requirements.txt requirements-dev.txt \
        .gitignore .env.example \
        config/ \
        PROJECT_DESIGN.md DEV_SETUP.md

git commit -m "chore: initial project skeleton

- Directory structure from PROJECT_DESIGN.md v1.3.0
- pyproject.toml, requirements.txt, requirements-dev.txt
- config/settings.yaml, universe.yaml, logging.yaml
- .env.example (no secrets)
- .gitignore"
```

### 7.4 Connect to GitHub (optional but recommended)

```bash
# Create a new PRIVATE repo on github.com first, then:
git remote add origin git@github.com:YOUR_USERNAME/minervini_ai.git
git push -u origin main
```

---

## 8. Verify the Setup

Run these checks in order. Each one should pass before moving to the next.

### Check 1 — Python and venv

```bash
source /home/ubuntu/projects/minervini_ai/.venv/bin/activate
python --version          # Python 3.11.x
pip list | grep pandas    # pandas 2.x.x
```

### Check 2 — Project importable

```bash
cd /home/ubuntu/projects/minervini_ai
python -c "import ingestion; print('ingestion package OK')"
python -c "import features; print('features package OK')"
python -c "import rules; print('rules package OK')"
```

### Check 3 — Config loads

```bash
python -c "
import yaml
with open('config/settings.yaml') as f:
    cfg = yaml.safe_load(f)
print('settings.yaml OK — universe index:', cfg['universe']['index'])
"
```

### Check 4 — yfinance can fetch data

```bash
python -c "
import yfinance as yf
df = yf.download('RELIANCE.NS', period='5d', progress=False)
print('yfinance OK —', len(df), 'rows fetched for RELIANCE')
"
```

### Check 5 — SQLite works

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/test.db')
conn.execute('CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)')
conn.close()
import os; os.remove('data/test.db')
print('SQLite OK')
"
```

### Check 6 — Parquet works

```bash
python -c "
import pandas as pd
import pyarrow
df = pd.DataFrame({'a': [1,2,3], 'b': [4,5,6]})
df.to_parquet('data/test.parquet')
df2 = pd.read_parquet('data/test.parquet')
import os; os.remove('data/test.parquet')
print('Parquet OK —', len(df2), 'rows round-tripped')
"
```

### Check 7 — FastAPI importable

```bash
python -c "
from fastapi import FastAPI
app = FastAPI()
print('FastAPI OK')
"
```

### Check 8 — Streamlit importable

```bash
python -c "import streamlit; print('Streamlit OK — version', streamlit.__version__)"
```

### Check 9 — Ruff linting works

```bash
echo "x=1" > /tmp/test_ruff.py
ruff check /tmp/test_ruff.py
rm /tmp/test_ruff.py
echo "ruff OK"
```

### Check 10 — pytest runs (empty suite)

```bash
pytest tests/ -v 2>&1 | tail -5
# Expected: "no tests ran" or "0 passed" — that's fine for now
```

If all 10 checks pass, your environment is correctly set up.

---

## 9. Editor Setup (VS Code Remote)

The recommended way to develop on ShreeVault is VS Code with the Remote SSH extension — you edit files locally but they run on the server.

### 9.1 Install VS Code extensions (on your local machine)

Install these from the Extensions panel (`Ctrl+Shift+X`):

| Extension | ID |
|---|---|
| Remote - SSH | `ms-vscode-remote.remote-ssh` |
| Python | `ms-python.python` |
| Pylance | `ms-python.vscode-pylance` |
| Ruff | `charliermarsh.ruff` |
| YAML | `redhat.vscode-yaml` |
| GitLens | `eamodio.gitlens` |

### 9.2 Connect to ShreeVault via Remote SSH

1. Press `Ctrl+Shift+P` → `Remote-SSH: Connect to Host`
2. Enter `ubuntu@<your-server-ip>`
3. VS Code opens a new window connected to ShreeVault
4. Open the folder `/home/ubuntu/projects/minervini_ai`

### 9.3 Point VS Code to the project venv

Press `Ctrl+Shift+P` → `Python: Select Interpreter` → choose:
```
/home/ubuntu/projects/minervini_ai/.venv/bin/python
```

### 9.4 Create `.vscode/settings.json`

```bash
mkdir -p .vscode
cat > .vscode/settings.json << 'EOF'
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
    "editor.rulers": [100],
    "files.exclude": {
        "**/__pycache__": true,
        "**/*.pyc": true,
        ".venv": true
    }
}
EOF
```

### 9.5 Create `.vscode/launch.json` for debugging

```bash
cat > .vscode/launch.json << 'EOF'
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Run daily screen",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/scripts/run_daily.py",
            "args": ["--date", "today"],
            "cwd": "${workspaceFolder}",
            "envFile": "${workspaceFolder}/.env"
        },
        {
            "name": "Bootstrap universe",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/scripts/bootstrap.py",
            "args": ["--universe", "config"],
            "cwd": "${workspaceFolder}",
            "envFile": "${workspaceFolder}/.env"
        },
        {
            "name": "Run FastAPI (dev)",
            "type": "debugpy",
            "request": "launch",
            "module": "uvicorn",
            "args": ["api.main:app", "--reload", "--port", "8000"],
            "cwd": "${workspaceFolder}",
            "envFile": "${workspaceFolder}/.env"
        },
        {
            "name": "Pytest",
            "type": "debugpy",
            "request": "launch",
            "module": "pytest",
            "args": ["tests/", "-v"],
            "cwd": "${workspaceFolder}",
            "envFile": "${workspaceFolder}/.env"
        }
    ]
}
EOF
```

---

## 10. Node.js Setup (for Next.js — Phase 12 only)

**Skip this entirely for now.** Only do this when you reach Phase 12 (Next.js frontend). The pipeline, API, and Streamlit dashboard don't need Node.

When the time comes:

```bash
# Install Node.js 20 LTS via nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc
nvm install 20
nvm use 20
node --version   # v20.x.x
npm --version    # 10.x.x

# Create Next.js project inside the repo
cd /home/ubuntu/projects/minervini_ai
npx create-next-app@latest frontend \
    --typescript \
    --tailwind \
    --eslint \
    --app \
    --no-src-dir \
    --import-alias "@/*"
```

---

## 11. Quick Reference — Daily Commands

Save this section — you'll use these every session.

```bash
# ── Activate venv (do this first in every terminal session) ───────────
cd /home/ubuntu/projects/minervini_ai
source .venv/bin/activate

# ── Run the daily screen ───────────────────────────────────────────────
python scripts/run_daily.py --date today
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON"
python scripts/run_daily.py --watchlist mylist.csv
python scripts/run_daily.py --watchlist-only

# ── Bootstrap historical data ─────────────────────────────────────────
python scripts/bootstrap.py --universe config   # use config/universe.yaml
python scripts/bootstrap.py --symbols "RELIANCE,TCS"

# ── Run tests ─────────────────────────────────────────────────────────
pytest tests/ -v
pytest tests/unit/test_validator.py -v         # single file
pytest tests/ -v --cov=. --cov-report=term    # with coverage

# ── Lint and format ───────────────────────────────────────────────────
ruff check .
ruff format .

# ── Start FastAPI (dev) ───────────────────────────────────────────────
uvicorn api.main:app --reload --port 8000

# ── Start Streamlit dashboard ─────────────────────────────────────────
streamlit run dashboard/app.py --server.port 8501

# ── Git workflow ──────────────────────────────────────────────────────
git status
git add -p                        # interactive staging
git commit -m "feat: description"
git push

# ── Check logs ────────────────────────────────────────────────────────
tail -f logs/minervini.log
journalctl -u minervini-api.service -f     # after systemd setup

# ── SQLite quick queries ──────────────────────────────────────────────
sqlite3 data/results.db ".tables"
sqlite3 data/results.db "SELECT * FROM run_history ORDER BY created_at DESC LIMIT 5;"
sqlite3 data/results.db "SELECT symbol, setup_quality, score FROM sepa_results WHERE run_date = date('now') ORDER BY score DESC LIMIT 20;"
```

---

## What's Next

Once all 10 verification checks pass, you're ready to start Phase 1 development.

**First session:** Tell me:
> "Let's build `utils/exceptions.py`, `utils/logger.py`, and `utils/date_utils.py`"

I'll write all three files complete and ready to drop in. From there we build `ingestion/` module by module, test each one, and move forward phase by phase.

---

*This guide covers setup only. Refer to `PROJECT_DESIGN.md` for all architectural decisions.*

---

## 12. Production Deployment (ShreeVault)

This section covers running Minervini AI as persistent systemd services so everything
starts automatically on boot and the daily screen fires on schedule without manual
intervention.

All unit files live in `deploy/`. The install script symlinks them into
`/etc/systemd/system/` — the project files remain the single source of truth, so
editing a file in `deploy/` takes effect after one `sudo systemctl daemon-reload`.

---

### 12.1 Services overview

| Unit | Type | Purpose |
|---|---|---|
| `minervini-daily.timer` | Timer | Fires every Mon–Fri at 15:35 IST |
| `minervini-daily.service` | Oneshot | Runs `scripts/run_daily.py --date today` |
| `minervini-api.service` | Simple (always-on) | FastAPI backend on port 8000 |
| `minervini-dashboard.service` | Simple (always-on) | Streamlit dashboard on port 8501 |

---

### 12.2 One-time install

```bash
# From the project root on ShreeVault
cd /home/ubuntu/projects/minervini_ai

# Make scripts executable (only needed once)
chmod +x deploy/install.sh deploy/uninstall.sh

# Run the installer as root
sudo bash deploy/install.sh
```

The script will:
1. Symlink all `.service` and `.timer` files into `/etc/systemd/system/`
2. Run `systemctl daemon-reload`
3. Enable and start `minervini-daily.timer`, `minervini-api.service`, and `minervini-dashboard.service`
4. Print a live status summary for all three

It is **idempotent** — running it a second time is safe and will just re-create the
symlinks and print the current status.

**Prerequisite:** `.env` must exist and be populated before running the installer.
See [Section 6](#6-environment-variables-env) if you haven't done this yet.

---

### 12.3 Checking service status

```bash
# Quick one-liner for all three
systemctl status minervini-daily.timer minervini-api.service minervini-dashboard.service

# Timer specifically — shows next trigger time
systemctl list-timers --all | grep minervini

# Is the API actually responding?
curl -s http://localhost:8000/health | python3 -m json.tool
```

---

### 12.4 Viewing logs

```bash
# Last 50 lines of the daily screen (most recent run)
journalctl -u minervini-daily -n 50

# Follow the API log in real-time
journalctl -u minervini-api -f

# Follow the dashboard log in real-time
journalctl -u minervini-dashboard -f

# Logs from today only
journalctl -u minervini-daily --since today

# Logs for a specific date
journalctl -u minervini-daily --since "2025-06-01 00:00:00" --until "2025-06-01 23:59:59"
```

---

### 12.5 Stopping and restarting

```bash
# Restart the API (e.g. after a code change)
sudo systemctl restart minervini-api.service

# Restart the dashboard
sudo systemctl restart minervini-dashboard.service

# Stop everything
sudo systemctl stop minervini-api.service minervini-dashboard.service minervini-daily.timer

# Start everything again
sudo systemctl start minervini-daily.timer minervini-api.service minervini-dashboard.service

# Trigger the daily screen manually right now (outside the timer)
sudo systemctl start minervini-daily.service
journalctl -u minervini-daily -f     # watch it run
```

---

### 12.6 How the daily timer works

```
15:35 IST (Mon–Fri)
       │
       ▼
minervini-daily.timer  ──fires──▶  minervini-daily.service
                                         │
                                         ▼
                              .venv/bin/python scripts/run_daily.py --date today
                                         │
                                         ▼
                              Ingestion → Features → Scoring → Alerts → Report
```

`Persistent=true` means if ShreeVault is rebooted or powered off at 15:35, systemd
will run the missed job as soon as the server comes back online — you will never
silently skip a trading day.

`RandomizedDelaySec=30` adds up to 30 seconds of jitter so the timer does not
hammer external APIs at the exact same second every day.

---

### 12.7 Deploying code changes

After pushing new code and pulling on the server:

```bash
cd /home/ubuntu/projects/minervini_ai
git pull

# If you only changed Python files (no unit file changes):
sudo systemctl restart minervini-api.service minervini-dashboard.service

# If you edited a file in deploy/:
sudo systemctl daemon-reload
sudo systemctl restart minervini-api.service minervini-dashboard.service
```

The daily service will pick up new code automatically on its next run — no restart
needed for `minervini-daily` because it is a fresh process every time the timer fires.

---

### 12.8 Uninstalling

```bash
sudo bash deploy/uninstall.sh
```

This stops and disables all units, removes the symlinks from `/etc/systemd/system/`,
and reloads the daemon. The files in `deploy/` are left untouched so you can
reinstall at any time with `deploy/install.sh`.
