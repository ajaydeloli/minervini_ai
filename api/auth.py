"""
api/auth.py
───────────
X-API-Key authentication for the Minervini FastAPI layer.

Two independent key tiers are supported, both loaded from environment
variables at import time:

  API_READ_KEY   — required for all GET endpoints
  API_ADMIN_KEY  — required for POST /api/v1/run and DELETE /api/v1/watchlist

Dev / open mode
───────────────
If *both* API_READ_KEY and API_ADMIN_KEY are absent or empty in the
environment, the module enters "open mode": every request passes without
any key check.  A WARNING is logged exactly once at module import time so
the operator knows the API is unprotected:

    WARNING  api.auth: API running in OPEN mode — set API_READ_KEY and
             API_ADMIN_KEY in .env

This allows the API to work out-of-the-box on a fresh local dev machine
with no .env configuration required.

Security design
───────────────
- Keys are compared with ``hmac.compare_digest`` to prevent timing-oracle
  attacks (an attacker cannot determine key length by measuring response
  latency).
- HTTPException is raised (not a custom MinerviniError) so FastAPI handles
  JSON serialisation automatically:

      HTTP 401  →  key environment variable is not configured on the server
                   (the *server* is misconfigured; client should escalate)
      HTTP 403  →  key is set but the provided value does not match
                   (the *client* supplied the wrong credential)

Usage in routers
────────────────
    from fastapi import APIRouter, Depends
    from api.auth import require_read_key, require_admin_key

    router = APIRouter()

    @router.get("/stocks/top")
    def top_stocks(_key: str = Depends(require_read_key)):
        ...

    @router.post("/run")
    def trigger_run(_key: str = Depends(require_admin_key)):
        ...

deps.py re-exports both functions, so routers can import from either
location — the canonical source of truth is this module.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Header, HTTPException

# ─────────────────────────────────────────────────────────────────────────────
# Module-level logger
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Load keys from environment once at import time.
# Strip surrounding whitespace to tolerate common .env editing mistakes.
# ─────────────────────────────────────────────────────────────────────────────

_READ_KEY: str = os.environ.get("API_READ_KEY", "").strip()
_ADMIN_KEY: str = os.environ.get("API_ADMIN_KEY", "").strip()

# ─────────────────────────────────────────────────────────────────────────────
# Open mode: both keys absent → dev convenience, one-time warning.
# ─────────────────────────────────────────────────────────────────────────────

_OPEN_MODE: bool = not _READ_KEY and not _ADMIN_KEY

if _OPEN_MODE:
    log.warning(
        "API running in OPEN mode — set API_READ_KEY and API_ADMIN_KEY in .env"
    )

# ─────────────────────────────────────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────────────────────────────────────

def _safe_compare(a: str, b: str) -> bool:
    """
    Constant-time string comparison using ``hmac.compare_digest``.

    ``hmac.compare_digest`` requires both arguments to be of the same type
    (both ``str`` or both ``bytes``).  Using ``str`` directly is safe here
    because our keys are ASCII printable values from environment variables.

    Returns True iff *a* and *b* are identical.
    """
    return hmac.compare_digest(a, b)


# ─────────────────────────────────────────────────────────────────────────────
# Public dependency functions
# ─────────────────────────────────────────────────────────────────────────────

def require_read_key(x_api_key: str = Header(...)) -> str:
    """
    FastAPI dependency that enforces read-tier authentication.

    Inject with ``Depends(require_read_key)`` on any GET endpoint.

    Behaviour
    ---------
    Open mode (both keys unset)
        Passes through immediately — no header check.

    API_READ_KEY set
        Compares the ``X-API-Key`` header value against ``API_READ_KEY``
        using constant-time comparison.

    Raises
    ------
    HTTPException 401
        ``API_READ_KEY`` is not configured in the environment.
        This indicates a *server-side* misconfiguration, not a wrong key.

    HTTPException 403
        The provided ``X-API-Key`` header value does not match
        ``API_READ_KEY``.

    Returns
    -------
    str
        The validated key string on success (useful for logging in callers).

    Examples
    --------
    Router usage::

        @router.get("/stocks/top")
        def top(_key: str = Depends(require_read_key)):
            ...
    """
    if _OPEN_MODE:
        return x_api_key

    if not _READ_KEY:
        raise HTTPException(
            status_code=401,
            detail="API_READ_KEY is not configured on this server.",
        )

    if not _safe_compare(x_api_key, _READ_KEY):
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return x_api_key


def require_admin_key(x_api_key: str = Header(...)) -> str:
    """
    FastAPI dependency that enforces admin-tier authentication.

    Inject with ``Depends(require_admin_key)`` on POST /api/v1/run and
    DELETE /api/v1/watchlist (and any other write/destructive endpoints).

    Admin keys are checked against the ``API_ADMIN_KEY`` environment
    variable, which is *independent* from ``API_READ_KEY``.  A client that
    holds only the read key cannot access admin endpoints.

    Behaviour
    ---------
    Open mode (both keys unset)
        Passes through immediately — no header check.

    API_ADMIN_KEY set
        Compares the ``X-API-Key`` header value against ``API_ADMIN_KEY``
        using constant-time comparison.

    Raises
    ------
    HTTPException 401
        ``API_ADMIN_KEY`` is not configured in the environment.

    HTTPException 403
        The provided ``X-API-Key`` header value does not match
        ``API_ADMIN_KEY``.

    Returns
    -------
    str
        The validated key string on success.

    Examples
    --------
    Router usage::

        @router.post("/run")
        def trigger_run(_key: str = Depends(require_admin_key)):
            ...

        @router.delete("/watchlist")
        def clear_watchlist(_key: str = Depends(require_admin_key)):
            ...
    """
    if _OPEN_MODE:
        return x_api_key

    if not _ADMIN_KEY:
        raise HTTPException(
            status_code=401,
            detail="API_ADMIN_KEY is not configured on this server.",
        )

    if not _safe_compare(x_api_key, _ADMIN_KEY):
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return x_api_key
