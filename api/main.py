"""
api/main.py
───────────
FastAPI application entry point for the Minervini SEPA API.

Wires together:
  - CORS middleware (origins from CORS_ORIGINS env var)
  - Rate-limiting via slowapi (setup_rate_limiter)
  - Five routers: health, stocks, watchlist, portfolio, run
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

import contextlib
import os
from pathlib import Path

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import Counter, make_wsgi_app
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from api.deps import get_config, get_db_path
from api.rate_limit import setup_rate_limiter
from api.routers import health, portfolio, stocks, watchlist
from api.routers.backtest import router as backtest_router
from api.routers.run import router as run_router
from api.schemas.common import APIResponse
from storage.sqlite_store import init_db
from utils.logger import get_logger, setup_logging
from utils.env_check import warn_missing_env_vars
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

# NOTE: last_run_duration_sec and last_run_a_plus_count Gauges have been
# moved to api/routers/run.py where they are updated by the pipeline worker.

# ─────────────────────────────────────────────────────────────────────────────
# Lifespan context manager (replaces deprecated @app.on_event)
# ─────────────────────────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def _lifespan(application: FastAPI):  # noqa: ARG001
    """Initialise logging, database, and emit a startup log line."""
    setup_logging()

    db_path: Path = get_db_path()
    _db_existed = db_path.exists()  # capture BEFORE init_db creates the file
    init_db(db_path)

    cfg = get_config()
    for _w in warn_missing_env_vars(cfg):
        log.warning(_w)

    # ── Auth mode warning ─────────────────────────────────────────────────────
    read_key  = os.environ.get("API_READ_KEY",  "").strip()
    admin_key = os.environ.get("API_ADMIN_KEY", "").strip()
    if not read_key and not admin_key:
        log.warning(
            "API running in OPEN MODE — no API keys configured. "
            "All endpoints are publicly accessible. "
            "Set API_READ_KEY and API_ADMIN_KEY in .env for production."
        )
    elif not admin_key:
        log.warning("API_ADMIN_KEY is not set. POST /api/v1/run is unprotected.")

    # ── LLM API key check ──────────────────────────────────────────────────────
    _LLM_KEY_MAP = {
        "groq":       "GROQ_API_KEY",
        "anthropic":  "ANTHROPIC_API_KEY",
        "openai":     "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "gemini":     "GEMINI_API_KEY",
    }
    llm_cfg  = cfg.get("llm", {})
    if llm_cfg.get("enabled", False):
        _provider = llm_cfg.get("provider", "").lower()
        _key_var  = _LLM_KEY_MAP.get(_provider)
        if _key_var and not os.environ.get(_key_var, "").strip():
            log.warning(
                "LLM is enabled (provider=%s) but %s is not set. "
                "AI trade briefs will be skipped. "
                "Set %s in your .env file.",
                _provider, _key_var, _key_var,
            )

    # ── Database existence check ──────────────────────────────────────────────
    if not _db_existed:
        log.warning(
            "Database not found at %s. Run: python scripts/bootstrap.py "
            "to initialise the data store. API will return empty results.",
            str(db_path),
        )
    else:
        log.info(
            "Database found",
            path=str(db_path),
            size_mb=round(db_path.stat().st_size / 1e6, 2),
        )

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

# Run router — admin-only pipeline trigger; rate-limited via ADMIN_LIMIT
# inside the router itself (via @limiter.limit(ADMIN_LIMIT) on the endpoint).
app.include_router(run_router, prefix="/api/v1")

# Backtest router — read-only; surfaces run history, report blobs, equity curves.
app.include_router(backtest_router, prefix="/api/v1")

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
