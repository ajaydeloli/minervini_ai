# Makefile for Minervini AI — Minervini SEPA stock analysis system
# Python 3.11 | venv at .venv | Ubuntu/bash only

VENV   = .venv
PYTHON = $(VENV)/bin/python
PIP    = $(VENV)/bin/pip

.PHONY: install test test-fast lint format format-check \
        daily backtest rebuild paper-reset api dashboard clean help

# ── Default target ────────────────────────────────────────────────────────────
.DEFAULT_GOAL := help

# ── install ───────────────────────────────────────────────────────────────────
# Install the project and all dev dependencies into the virtual environment.
install:
	$(PIP) install -e ".[dev]"

# ── test ──────────────────────────────────────────────────────────────────────
# Run the full test suite with coverage report (verbose, short tracebacks).
test:
	$(PYTHON) -m pytest tests/ -v --tb=short --cov=. --cov-report=term-missing

# ── test-fast ─────────────────────────────────────────────────────────────────
# Run tests stopping on the first failure, with minimal output.
test-fast:
	$(PYTHON) -m pytest tests/ -x -q

# ── lint ──────────────────────────────────────────────────────────────────────
# Lint the codebase with ruff (no auto-fix).
lint:
	$(PYTHON) -m ruff check .

# ── format ────────────────────────────────────────────────────────────────────
# Auto-format all Python files with ruff.
format:
	$(PYTHON) -m ruff format .

# ── format-check ──────────────────────────────────────────────────────────────
# Check formatting without making changes (useful in CI).
format-check:
	$(PYTHON) -m ruff format --check .

# ── daily ─────────────────────────────────────────────────────────────────────
# Run the daily pipeline for today's date.
daily:
	$(PYTHON) scripts/run_daily.py --date today

# ── backtest ──────────────────────────────────────────────────────────────────
# Run the backtester over a date range.
# Usage: make backtest START=2020-01-01 END=2024-01-01
backtest:
	$(PYTHON) scripts/backtest_runner.py --start $(START) --end $(END)

# ── rebuild ───────────────────────────────────────────────────────────────────
# Rebuild feature store for the Nifty 500 universe from scratch.
rebuild:
	$(PYTHON) scripts/rebuild_features.py --universe nifty500

# ── paper-reset ───────────────────────────────────────────────────────────────
# Reset the paper-trading portfolio to a clean state.
paper-reset:
	$(PYTHON) -c "from paper_trading.simulator import reset_portfolio; reset_portfolio(confirm=True)"

# ── api ───────────────────────────────────────────────────────────────────────
# Start the FastAPI server with hot-reload on port 8000.
api:
	$(PYTHON) -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# ── dashboard ─────────────────────────────────────────────────────────────────
# Launch the Streamlit dashboard on port 8501.
dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py --server.port 8501

# ── clean ─────────────────────────────────────────────────────────────────────
# Remove all build artefacts, caches, and coverage data.
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name .coverage -delete
	find . -type d -name htmlcov -exec rm -rf {} +

# ── help ──────────────────────────────────────────────────────────────────────
# Print a formatted list of available targets with descriptions.
help:
	@echo ""
	@echo "  Minervini AI — available make targets"
	@echo "  ────────────────────────────────────────────────────────────"
	@echo "  install       Install project + dev deps into .venv"
	@echo "  test          Full pytest suite with coverage report"
	@echo "  test-fast     Pytest: stop on first failure, quiet output"
	@echo "  lint          Ruff linter (no auto-fix)"
	@echo "  format        Ruff auto-formatter"
	@echo "  format-check  Ruff format check only (CI-safe)"
	@echo "  daily         Run daily pipeline for today"
	@echo "  backtest      Backtest (make backtest START=YYYY-MM-DD END=YYYY-MM-DD)"
	@echo "  rebuild       Rebuild feature store for Nifty 500 universe"
	@echo "  paper-reset   Reset paper-trading portfolio"
	@echo "  api           Start FastAPI server on :8000 (--reload)"
	@echo "  dashboard     Launch Streamlit dashboard on :8501"
	@echo "  clean         Remove __pycache__, .pytest_cache, .coverage, htmlcov/"
	@echo "  help          Show this help message"
	@echo ""
