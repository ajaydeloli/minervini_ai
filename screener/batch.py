"""
screener/batch.py
─────────────────
Parallel batch execution wrapper for the SEPA screener.

The core parallel execution logic lives in screener/pipeline.py
(run_screen uses ProcessPoolExecutor internally). This module re-exports
the public batch interface so external code can import from screener.batch
as documented in the project design spec.

Usage:
    from screener.batch import run_screen_batch
    results = run_screen_batch(universe, run_date, config, n_workers=8)
"""

from screener.pipeline import run_screen as run_screen_batch

__all__ = ["run_screen_batch"]
