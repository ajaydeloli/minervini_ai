"""
api/rate_limit.py
─────────────────
Per-IP rate limiting for the Minervini FastAPI layer, implemented with
``slowapi`` (a thin wrapper around the ``limits`` library).

Design
------
Two limit tiers are defined as module-level string constants (spec §12.5):

  READ_LIMIT  = "100/minute"   — applied to all GET endpoints
  ADMIN_LIMIT = "10/minute"    — applied to POST /api/v1/run

A single shared ``Limiter`` instance is created at import time and reused
across the entire application.  Routers import it directly and decorate
their endpoints with ``@limiter.limit(...)``:

    from api.rate_limit import limiter, READ_LIMIT

    @router.get("/stocks/top")
    @limiter.limit(READ_LIMIT)
    def top_stocks(request: Request, ...):
        ...

``setup_rate_limiter(app)`` must be called once in ``api/main.py`` during
application startup.  It wires three things onto the FastAPI app:

  1. ``app.state.limiter``    — so slowapi middleware can find the limiter.
  2. ``SlowAPIMiddleware``    — intercepts every request and enforces limits.
  3. Exception handler        — converts ``RateLimitExceeded`` into a JSON
                                response that matches the ``APIResponse``
                                envelope used everywhere in this API.

Rate-limit exceeded response (HTTP 429)
----------------------------------------
::

    {
        "success": false,
        "data":    null,
        "error":   "Rate limit exceeded"
    }

This shape is identical to the ``APIResponse`` envelope defined in
``api/schemas/common.py`` so clients can handle 429 errors with the same
code path as any other API error.

Import constraints
------------------
This module imports **only** from:
  - stdlib / third-party packages (``slowapi``, ``fastapi``, ``starlette``)
  - ``api/schemas/common.py``  (for the ``APIResponse`` type annotation)

It must *not* import from any other ``api/`` module to avoid circular
dependencies (``main.py`` imports this module during application startup,
before routers are loaded).

Verification
------------
``slowapi`` is listed in ``requirements.txt`` (``slowapi>=0.1.9``).
Confirm the installed version at any time with::

    pip show slowapi
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.requests import Request

# ``APIResponse`` is imported purely for the type annotation in the
# docstring / return-type hint below.  The actual JSON body is built
# from a plain dict so this module remains independent of the schema
# layer at runtime.
from api.schemas.common import APIResponse  # noqa: F401 (used in annotations)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level logger
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit constants  (spec §12.5)
# ─────────────────────────────────────────────────────────────────────────────

READ_LIMIT: str = "100/minute"
"""Applied to every GET endpoint via ``@limiter.limit(READ_LIMIT)``."""

ADMIN_LIMIT: str = "10/minute"
"""Applied to POST /api/v1/run and any other admin-only write endpoints."""

# ─────────────────────────────────────────────────────────────────────────────
# Shared Limiter instance
# ─────────────────────────────────────────────────────────────────────────────

limiter: Limiter = Limiter(key_func=get_remote_address)
"""
Singleton ``slowapi.Limiter`` keyed by the remote IP address of each client.

Routers import this instance directly and decorate endpoint functions::

    from api.rate_limit import limiter, READ_LIMIT, ADMIN_LIMIT

    @router.get("/stocks/top")
    @limiter.limit(READ_LIMIT)
    def top_stocks(request: Request, ...):
        ...

    @router.post("/run")
    @limiter.limit(ADMIN_LIMIT)
    def trigger_run(request: Request, ...):
        ...

Important: ``request: Request`` must appear as a parameter in every
rate-limited endpoint so slowapi can extract the client IP from it.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Rate-limit exceeded exception handler
# ─────────────────────────────────────────────────────────────────────────────

async def _rate_limit_exceeded_handler(
    request: Request,
    exc: RateLimitExceeded,
) -> JSONResponse:
    """
    Convert a ``slowapi.errors.RateLimitExceeded`` exception into a
    structured JSON response that matches the ``APIResponse`` envelope.

    HTTP status: 429 Too Many Requests

    Response body::

        {
            "success": false,
            "data":    null,
            "error":   "Rate limit exceeded"
        }

    Parameters
    ----------
    request:
        The incoming Starlette ``Request`` object (provided by FastAPI
        exception-handler machinery — not used here, kept for signature
        compatibility).
    exc:
        The ``RateLimitExceeded`` exception raised by slowapi.  Contains
        the limit string (e.g. ``"100 per 1 minute"``) in ``exc.detail``.

    Returns
    -------
    JSONResponse
        HTTP 429 with an ``APIResponse``-shaped JSON body.
    """
    log.warning(
        "Rate limit exceeded",
        extra={
            "client_ip": get_remote_address(request),
            "path": request.url.path,
            "limit_detail": str(exc.detail),
        },
    )
    body: dict[str, Any] = {
        "success": False,
        "data": None,
        "error": "Rate limit exceeded",
    }
    return JSONResponse(status_code=429, content=body)


# ─────────────────────────────────────────────────────────────────────────────
# Public setup function — called once in api/main.py
# ─────────────────────────────────────────────────────────────────────────────

def setup_rate_limiter(app: FastAPI) -> None:
    """
    Wire the shared ``Limiter`` instance and middleware into a FastAPI app.

    Call this exactly once from ``api/main.py`` during application startup,
    *before* any routers are registered::

        # api/main.py
        from fastapi import FastAPI
        from api.rate_limit import setup_rate_limiter

        app = FastAPI(...)
        setup_rate_limiter(app)

        app.include_router(stocks_router)
        ...

    What this function does
    -----------------------
    1. Attaches the shared ``limiter`` to ``app.state.limiter``.
       ``SlowAPIMiddleware`` looks for the limiter here; omitting this step
       causes every request to raise an ``AttributeError``.

    2. Adds ``SlowAPIMiddleware`` to the ASGI middleware stack.
       This middleware intercepts every incoming request and increments the
       per-IP counter.  When a counter exceeds its limit the middleware
       raises ``RateLimitExceeded`` before the endpoint function is called.

    3. Registers ``_rate_limit_exceeded_handler`` for ``RateLimitExceeded``.
       FastAPI will invoke this handler whenever slowapi raises the
       exception, returning a structured HTTP 429 response instead of an
       unformatted error page.

    Parameters
    ----------
    app:
        The FastAPI application instance to configure.

    Returns
    -------
    None
        All configuration is applied in-place; no new object is created.

    Raises
    ------
    Does not raise.  Import errors from ``slowapi`` will surface at module
    import time, not here.
    """
    # 1. Make the limiter discoverable by SlowAPIMiddleware.
    app.state.limiter = limiter
    log.debug("Rate limiter attached to app.state.limiter")

    # 2. Add the middleware that enforces limits on every request.
    app.add_middleware(SlowAPIMiddleware)
    log.debug("SlowAPIMiddleware added to application middleware stack")

    # 3. Register the structured-JSON exception handler for HTTP 429.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    log.debug(
        "RateLimitExceeded exception handler registered "
        "(READ_LIMIT=%s, ADMIN_LIMIT=%s)",
        READ_LIMIT,
        ADMIN_LIMIT,
    )
