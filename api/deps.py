"""
api/deps.py
───────────
Shared FastAPI dependencies injected via Depends().

Every function here is designed to be used as a FastAPI dependency:

    @router.get("/stocks/top")
    def get_top(
        db_path: Path = Depends(get_db_path),
        config: dict = Depends(get_config),
        _key: str = Depends(require_read_key),
    ):
        ...

Design rules:
  - Zero circular imports: only imports from stdlib, fastapi, yaml,
    and storage.sqlite_store.
  - get_config() caches after first load — repeated Depends() calls
    within the same process never re-read the YAML file.
  - Auth functions are imported from api/auth.py (real validation with
    constant-time comparison and open-mode dev convenience).
  - All functions are synchronous (no async) — FastAPI runs sync
    dependencies in a thread pool automatically.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

# Auth dependencies — real implementations live in api/auth.py.
# Re-exported here so routers have a single canonical import path.
from api.auth import require_admin_key as require_admin_key  # noqa: F401
from api.auth import require_read_key as require_read_key    # noqa: F401

# ─────────────────────────────────────────────────────────────────────────────
# Internal cache — populated on first call to get_config(), never cleared.
# Declared at module level so the lifetime equals the process lifetime.
# ─────────────────────────────────────────────────────────────────────────────

_config_cache: Optional[dict] = None

# ─────────────────────────────────────────────────────────────────────────────
# Config / settings path (relative to project root, consistent with the rest
# of the codebase which uses CWD-relative paths for data / config files).
# ─────────────────────────────────────────────────────────────────────────────

_SETTINGS_PATH = Path("config/settings.yaml")

# ─────────────────────────────────────────────────────────────────────────────
# Default DB path mirrors storage/sqlite_store._DEFAULT_DB_PATH exactly.
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_DB_PATH = Path("data/minervini.db")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Database path
# ─────────────────────────────────────────────────────────────────────────────

def get_db_path() -> Path:
    """
    Return the SQLite database file path.

    Resolution order:
      1. ``MINERVINI_DB_PATH`` environment variable (if set and non-empty).
      2. Hard-coded default: ``data/minervini.db``  (same as
         ``storage.sqlite_store._DEFAULT_DB_PATH``).

    The path is returned as a :class:`pathlib.Path` object.  Callers are
    responsible for ensuring the parent directory exists before writing;
    the API layer is read-only so this is only relevant for init-time
    bootstrap checks.

    Returns
    -------
    Path
        Absolute or relative path to the SQLite DB file.

    Examples
    --------
    Default (no env var set):
        >>> get_db_path()
        PosixPath('data/minervini.db')

    Override via environment:
        $ MINERVINI_DB_PATH=/var/data/minervini.db uvicorn api.main:app
        >>> get_db_path()
        PosixPath('/var/data/minervini.db')
    """
    raw = os.environ.get("MINERVINI_DB_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_DB_PATH


# ─────────────────────────────────────────────────────────────────────────────
# 2. Config loader (cached after first load)
# ─────────────────────────────────────────────────────────────────────────────

def get_config() -> dict:
    """
    Load and return the application configuration from
    ``config/settings.yaml``.

    Caching behaviour:
        The YAML file is read at most **once** per process.  After the first
        successful (or failed) load the result is stored in the module-level
        ``_config_cache`` variable.  All subsequent calls return the cached
        value without touching the file system.

        This means hot-reloading config at runtime is *not* supported
        intentionally — restart the API process to pick up changes.

    Error handling:
        Any exception during file read or YAML parse is silently swallowed.
        The function returns an **empty dict** in that case so callers can
        safely do ``config.get("api", {})`` without extra null checks.
        A missing config file is therefore non-fatal for the API layer.

    Returns
    -------
    dict
        Parsed settings dict, or ``{}`` on any read/parse failure.

    Examples
    --------
        >>> cfg = get_config()
        >>> cfg.get("api", {}).get("port", 8000)
        8000
    """
    global _config_cache

    # Return immediately if already loaded (success or graceful empty dict).
    if _config_cache is not None:
        return _config_cache

    try:
        text = _SETTINGS_PATH.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
        # yaml.safe_load returns None for an empty file.
        _config_cache = loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001 — intentional broad catch; never raises
        _config_cache = {}

    return _config_cache
