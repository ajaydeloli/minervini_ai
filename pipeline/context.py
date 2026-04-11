"""
pipeline/context.py
───────────────────
RunContext dataclass — immutable configuration bundle passed through every
stage of the Minervini AI pipeline.

RunContext captures everything that defines one pipeline run: the date,
operational mode, symbol scope, loaded config, database path, and a dry-run
flag.  Passing it as a single argument keeps every pipeline step stateless
and testable — no global state, no argparse objects leaking into business
logic.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


@dataclass
class RunContext:
    """
    Immutable configuration bundle for a single pipeline run.

    Attributes
    ──────────
    run_date : The trading date being evaluated (IST calendar date).
    mode     : Operational mode — "daily" for the scheduled run, "backtest"
               for historical replay, "manual" for ad-hoc CLI invocations.
    scope    : Symbol scope — "all" (watchlist + universe), "universe" only,
               or "watchlist" only.
    config   : Full application config dict loaded from settings.yaml.
    db_path  : Path to the SQLite database file.
    dry_run  : When True, symbol resolution and logging are performed but
               feature computation, screening, report generation, and alerts
               are all skipped.  No side-effects outside of logging.
    """

    run_date: datetime.date
    mode: Literal["daily", "backtest", "manual"]
    scope: Literal["all", "universe", "watchlist"]
    config: dict
    db_path: Path
    dry_run: bool = False
    # Path to the YAML config file that produced *config*.
    # Used by get_config_hash() to fingerprint the live file on disk.
    config_path: Path = Path("config/settings.yaml")

    # ── Optional CLI overrides (set by run_daily.py only) ────────────────────
    # When cli_symbols is provided, symbol resolution uses these directly
    # instead of reading the universe YAML.
    # When cli_watchlist_file is provided, it is forwarded to resolve_symbols().
    cli_symbols: Optional[list[str]] = field(default=None, compare=False)
    cli_watchlist_file: Optional[Path] = field(default=None, compare=False)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def config_hash(self) -> str:
        """
        Return a SHA-256 hex digest of the config dict serialised as
        sorted, compact JSON.

        Useful for detecting config drift between runs when stored in
        run_history.config_hash.
        """
        serialised = json.dumps(
            self.config,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialised.encode()).hexdigest()

    def git_sha(self) -> str | None:
        """
        Return the HEAD git commit SHA of the current repository, or None
        when the working directory is not inside a git repo (or git is not
        installed).

        Uses subprocess with a short timeout so it never blocks the pipeline.
        """
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                sha = proc.stdout.strip()
                return sha if sha else None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None
