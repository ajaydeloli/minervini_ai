"""
api/main.py
───────────
FastAPI application entry point for the Minervini SEPA API.

Wires together:
  - CORS middleware (origins from CORS_ORIGINS env var)
  - Rate-limiting via slowapi (setup_rate_limiter)
  - Four routers: health, stocks, watchlist, portfolio
  - POST /api/v1/run  — admin-only manual pipeline trigger (background thread)
  - GET  /metrics     — Prometheus WSGI endpoint (no auth)
  - GET  /            — 307 redirect to /api/v1/health
  - Global exception handler returning sanitised APIResponse envelopes
  - Startup event: logging + DB init

Running:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

Or via __main__:
    python -m api.main
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import os
import threading
from pathlib import Path

from a2wsgi import WSGIMiddleware
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import Counter, Gauge, make_wsgi_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from api.auth import require_admin_key
from api.deps import get_config, get_db_path
from api.rate_limit import setup_rate_limiter
from api.routers import health, portfolio, stocks, watchlist
from api.schemas.common import APIResponse, RunRequest
from storage.sqlite_store import init_db
from utils.logger import get_logger, setup_logging
from utils.run_meta import get_config_hash, get_git_sha

# ─────────────────────────────────────────────────────────────────────────────
# Module-level logger
# ─────────────────────────────────────────────────────────────────────────────

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus custom metrics  (module-level singletons)
# ─────────────────────────────────────────────────────────────────────────────

api_requests_total = Counter(
    "minervini_api_requests_total",
    "Total API requests",
    ["endpoint", "method", "status"],
)

last_run_duration_sec = Gauge(
    "minervini_last_run_duration_sec",
    "Last pipeline run duration in seconds",
)

last_run_a_plus_count = Gauge(
    "minervini_last_run_a_plus_count",
    "Last run A+ setup count",
)

# ─────────────────────────────────────────────────────────────────────────────
# Run-in-progress guard
#
# The Event is SET synchronously in trigger_run() (before spawning the task)
# and CLEARED in the finally block of the background worker.  This eliminates
# the race window between the is_set() check and the worker starting.
# ─────────────────────────────────────────────────────────────────────────────

_run_in_progress = threading.Event()

# ─────────────────────────────────────────────────────────────────────────────
# Lifespan context manager (replaces deprecated @app.on_event)
# ─────────────────────────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def _lifespan(application: FastAPI):  # noqa: ARG001
    """Initialise logging, database, and emit a startup log line."""
    setup_logging()

    db_path: Path = get_db_path()
    init_db(db_path)

    cfg = get_config()
    host: str = cfg.get("api", {}).get("host", "0.0.0.0")
    port: int = cfg.get("api", {}).get("port", 8000)
    git_sha: str = get_git_sha()
    config_hash: str = get_config_hash(Path("config/settings.yaml"))

    log.info(
        "Minervini SEPA API started",
        host=host,
        port=port,
        git_sha=git_sha,
        config_hash=config_hash,
        db=str(db_path),
        cors_origins=_cors_origins,
    )
    yield  # application runs here


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Minervini SEPA API",
    version="1.0.0",
    description="Minervini SEPA stock screening system API",
    lifespan=_lifespan,
)

# ─────────────────────────────────────────────────────────────────────────────
# CORS middleware
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://localhost:8501"]

_cors_raw = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins: list[str] = (
    [o.strip() for o in _cors_raw.split(",") if o.strip()]
    if _cors_raw
    else _DEFAULT_CORS_ORIGINS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────────────

setup_rate_limiter(app)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus request-counting middleware
# ─────────────────────────────────────────────────────────────────────────────

class _PrometheusMiddleware(BaseHTTPMiddleware):
    """Increment api_requests_total after every response (except /metrics)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/metrics"):
            api_requests_total.labels(
                endpoint=request.url.path,
                method=request.method,
                status=str(response.status_code),
            ).inc()
        return response


app.add_middleware(_PrometheusMiddleware)

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus /metrics endpoint (no auth; WSGI-mounted)
# ─────────────────────────────────────────────────────────────────────────────

app.mount("/metrics", WSGIMiddleware(make_wsgi_app()))

# ─────────────────────────────────────────────────────────────────────────────
# Router registration
# ─────────────────────────────────────────────────────────────────────────────

# Health router already carries prefix="/api/v1" and tags=["system"]
app.include_router(health.router)

# Stocks router already carries prefix="/api/v1"; add tag for OpenAPI grouping
app.include_router(stocks.router, tags=["Screener"])

# Watchlist router has no prefix; mount under /api/v1/watchlist
app.include_router(
    watchlist.router,
    prefix="/api/v1/watchlist",
    tags=["Watchlist"],
)

# Portfolio router carries prefix="/portfolio"; mount under /api/v1
app.include_router(
    portfolio.router,
    prefix="/api/v1",
    tags=["Portfolio"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Global exception handler — never exposes tracebacks to clients
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch any unhandled exception, log it with full traceback, and return
    a sanitised APIResponse envelope (success=False) to the client.
    HTTP 4xx exceptions raised deliberately by endpoints are NOT caught here
    because FastAPI handles those before this handler fires.
    """
    log.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        exc_info=True,
    )

    # Increment Prometheus counter so error spikes are visible in dashboards
    api_requests_total.labels(
        endpoint=request.url.path,
        method=request.method,
        status="500",
    ).inc()

    body = APIResponse(
        success=False,
        data=None,
        error="An internal server error occurred. Please try again later.",
    ).model_dump()

    return JSONResponse(status_code=500, content=body)


# ─────────────────────────────────────────────────────────────────────────────
# GET / — redirect to health check
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def _root_redirect():
    """307 redirect from / to /api/v1/health."""
    return RedirectResponse(url="/api/v1/health", status_code=307)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/run — admin-only manual pipeline trigger
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_in_background(
    scope: str,
    symbols: list[str] | None,
    db_path: Path,
    config: dict,
) -> None:
    """
    Execute the Minervini pipeline in a background thread.

    Imports are deferred to avoid circular imports at module load time and
    to ensure the pipeline is only imported when a /run request is made.

    _run_in_progress is set by the caller (trigger_run) BEFORE this function
    is scheduled, so a second /run request arriving before the thread starts
    will still see the flag and receive a 409.  This function is responsible
    for clearing the flag in its finally block regardless of outcome.
    """
    from pipeline.context import RunContext
    from pipeline import runner  # deferred — heavy import

    try:
        context = RunContext(
            run_date=datetime.date.today(),
            mode="manual",
            scope=scope,        # type: ignore[arg-type]
            config=config,
            db_path=db_path,
        )
        if symbols:
            context.cli_symbols = symbols

        result = runner.run(context)

        # Mirror key metrics into Prometheus gauges
        last_run_duration_sec.set(result.duration_sec)
        last_run_a_plus_count.set(result.a_plus_count)

        log.info(
            "Manual pipeline run complete",
            scope=scope,
            status=result.status,
            duration_sec=result.duration_sec,
            a_plus=result.a_plus_count,
        )
    except Exception:
        log.error("Background pipeline run failed", exc_info=True)
    finally:
        _run_in_progress.clear()


@app.post(
    "/api/v1/run",
    response_model=APIResponse[dict],
    status_code=202,
    summary="Trigger a manual pipeline run",
    description=(
        "Admin-only. Queues a background pipeline run and returns 202 "
        "immediately. Returns 409 if a run is already in progress."
    ),
    tags=["Admin"],
)
async def trigger_run(
    body: RunRequest,
    _key: str = Depends(require_admin_key),
) -> JSONResponse:
    """POST /api/v1/run — trigger a manual pipeline run (non-blocking)."""

    if _run_in_progress.is_set():
        conflict_body = APIResponse(
            success=False,
            data=None,
            error="Run already in progress",
        ).model_dump()
        return JSONResponse(status_code=409, content=conflict_body)

    # Set the flag NOW (before spawning) to close the race window.
    _run_in_progress.set()

    db_path: Path = get_db_path()
    config: dict = get_config()
    scope: str = body.scope
    symbols: list[str] | None = body.symbols

    # asyncio.to_thread runs the blocking pipeline in a thread-pool thread
    # without blocking the event loop; create_task ensures the response is
    # sent before the pipeline starts executing.
    asyncio.create_task(
        asyncio.to_thread(
            _run_pipeline_in_background,
            scope,
            symbols,
            db_path,
            config,
        )
    )

    log.info(
        "Pipeline run queued",
        scope=scope,
        symbols=symbols,
    )

    accepted_body = APIResponse(
        success=True,
        data={"status": "queued", "scope": scope},
    ).model_dump()

    return JSONResponse(status_code=202, content=accepted_body)


# ─────────────────────────────────────────────────────────────────────────────
# __main__ entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        workers=2,
    )
