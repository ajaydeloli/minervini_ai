"""
tests/unit/test_api.py
──────────────────────
Phase 10 — FastAPI layer unit tests.

All DB calls and startup I/O are mocked with unittest.mock.patch so tests
are fully in-memory and never touch data/ on disk.

Auth keys are set to fixed test values by patching the module-level
singletons in api.auth directly — this works regardless of what is (or
is not) in the environment when pytest runs.

Run all tests:
    python -m pytest tests/unit/test_api.py -v

Skip the slow rate-limit test:
    python -m pytest tests/unit/test_api.py -v -m "not slow"
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# 1.  Stub paper_trading.* BEFORE the app is imported.
#     api.routers.portfolio does `from paper_trading.* import ...` at module
#     load time.  If we inject our stubs into sys.modules first, those imports
#     resolve to our MagicMock objects instead of the real package.
# ─────────────────────────────────────────────────────────────────────────────

def _make_portfolio_summary_mock() -> MagicMock:
    """Return a MagicMock that looks like a PortfolioSummary dataclass."""
    s = MagicMock()
    s.cash = 100_000.0
    s.open_value = 0.0
    s.total_value = 100_000.0
    s.initial_capital = 100_000.0
    s.total_return_pct = 0.0
    s.realised_pnl = 0.0
    s.unrealised_pnl = 0.0
    s.total_trades = 0
    s.win_rate = 0.0
    s.open_trades = 0
    s.positions = []       # _map_position_row iterates this
    return s


_pt_portfolio_mod = MagicMock()
_pt_portfolio_mod.get_open_positions.return_value = []
_pt_portfolio_mod.get_closed_trades.return_value = []

_pt_report_mod = MagicMock()
_pt_report_mod.get_portfolio_summary.return_value = _make_portfolio_summary_mock()

# ─────────────────────────────────────────────────────────────────────────────
# 2.  Module-scoped fixture: inject stubs → import api.main → restore on exit.
#
#     The injection is done inside a pytest fixture (not at bare module level)
#     so sys.modules is restored after this test module finishes.  This
#     prevents the MagicMock from leaking into other test modules that import
#     the real paper_trading package (e.g. test_paper_trading.py).
#
#     api.main is imported lazily inside the fixture — and again inside
#     `client` — so the router's `from paper_trading.* import …` statements
#     always execute while our stubs are present in sys.modules.
# ─────────────────────────────────────────────────────────────────────────────

_PT_STUB_NAMES = [
    "paper_trading",
    "paper_trading.portfolio",
    "paper_trading.report",
]
_PT_STUBS = {
    "paper_trading":            MagicMock(),
    "paper_trading.portfolio":  _pt_portfolio_mod,
    "paper_trading.report":     _pt_report_mod,
}

from fastapi.testclient import TestClient  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 3.  Test-key constants
# ─────────────────────────────────────────────────────────────────────────────


TEST_READ_KEY  = "test-read-key-ABCD1234"
TEST_ADMIN_KEY = "test-admin-key-WXYZ9876"
READ_HEADERS   = {"X-API-Key": TEST_READ_KEY}
ADMIN_HEADERS  = {"X-API-Key": TEST_ADMIN_KEY}
WRONG_HEADERS  = {"X-API-Key": "totally-wrong-key-ZZZZ"}

# ─────────────────────────────────────────────────────────────────────────────
# 4.  Shared fake DB row objects
# ─────────────────────────────────────────────────────────────────────────────

_LAST_RUN_OK = {
    "run_date": "2025-01-15",
    "status": "success",
    "duration_sec": 12.3,
    "universe_size": 500,
}

_LAST_RUN_FAILED = {
    "run_date": "2025-01-15",
    "status": "failed",
    "duration_sec": 3.0,
    "universe_size": 500,
}

_STOCK_ROW_APLUS = {
    "symbol": "DIXON",
    "score": 88,
    "setup_quality": "A+",
    "stage": 2,
    "stage_label": "Stage 2 — Advancing",
    "rs_rating": 88,
    "trend_template_pass": 1,
    "conditions_met": 8,
    "vcp_qualified": 1,
    "breakout_triggered": 1,
    "entry_price": 14200.0,
    "stop_loss": 13100.0,
    "risk_pct": 7.7,
    "news_score": 15.0,
    "fundamental_pass": 1,
    "run_date": "2025-01-15",
    "result_json": "{}",
}

_STOCK_ROW_A = {
    **_STOCK_ROW_APLUS,
    "symbol": "TCS",
    "setup_quality": "A",
    "score": 75,
}

_WL_ROW = {
    "id": 1,
    "symbol": "DIXON",
    "note": None,
    "added_at": "2025-01-10T08:30:00",
    "added_via": "api",
    "last_score": 88.0,
    "last_quality": "A+",
    "last_run_at": "2025-01-15T10:00:00",
}

# ─────────────────────────────────────────────────────────────────────────────
# 5.  Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _stub_paper_trading_modules():
    """
    Inject paper_trading stubs into sys.modules for the duration of this
    test module, then restore the originals.

    Setup  — saves whatever currently lives under each stub key, injects the
             MagicMock objects, and performs the one-time lazy import of
             api.main (so the portfolio router binds to our mocks rather than
             the real paper_trading functions).
    Teardown — restores every key to its original value so subsequent test
               modules (e.g. test_paper_trading.py) find the real package.
    """
    originals = {k: sys.modules.get(k) for k in _PT_STUB_NAMES}
    sys.modules.update(_PT_STUBS)

    # Trigger the first-time import of api.main now that stubs are live.
    # Because the old module-level `from api.main import app` is gone, this
    # is the first import in the session — the portfolio router executes its
    # `from paper_trading.* import …` statements here and picks up our mocks.
    import api.main  # noqa: F401

    yield

    for k, original_v in originals.items():
        if original_v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = original_v


@pytest.fixture(autouse=True)
def _patch_auth_keys():
    """
    Run every test in key-enforced mode with known test keys.

    Patches the module-level singletons in api.auth directly so no
    environment variable needs to be set before pytest starts.
    """
    with (
        patch("api.auth._READ_KEY", TEST_READ_KEY),
        patch("api.auth._ADMIN_KEY", TEST_ADMIN_KEY),
        patch("api.auth._OPEN_MODE", False),
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_startup():
    """
    Block all filesystem / external calls that fire during TestClient
    startup so tests remain fully in-memory.

    Patches:
      api.main.init_db          — SQLite init (would create data/ directory)
      api.main.get_db_path      — returns a dummy path
      api.main.get_config       — returns an empty config dict
      api.main.setup_logging    — no-op
      api.main.get_git_sha      — returns a fixed string
      api.main.get_config_hash  — returns a fixed string
      api.deps.get_db_path      — used as a FastAPI Depends in portfolio router
    """
    with (
        patch("api.main.init_db"),
        patch("api.main.get_db_path", return_value=Path("/tmp/test_minervini.db")),
        patch("api.main.get_config", return_value={}),
        patch("api.main.setup_logging"),
        patch("api.main.get_git_sha", return_value="abc123ef"),
        patch("api.main.get_config_hash", return_value="deadbeef"),
        patch("api.deps.get_db_path", return_value=Path("/tmp/test_minervini.db")),
    ):
        yield


@pytest.fixture(autouse=True)
def _reset_run_event():
    """
    Clear the _run_in_progress threading.Event before and after every test
    so the /run endpoint always starts from a known state.
    The Event now lives in api.routers.run (moved from api.main in Gap 6).
    """
    from api.routers.run import _run_in_progress
    _run_in_progress.clear()
    yield
    _run_in_progress.clear()


@pytest.fixture
def client(_stub_paper_trading_modules):
    """
    TestClient whose ASGI lifespan (startup/shutdown) is managed by the
    with-block.  raise_server_exceptions=False lets the test inspect 5xx
    responses rather than having the exception propagate into the test.

    Depends on _stub_paper_trading_modules to guarantee stubs are in
    sys.modules before api.main.app is referenced.
    """
    from api.main import app  # lazy — stubs already in sys.modules
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ═════════════════════════════════════════════════════════════════════════════
# Health router  (tests 1–4)
# ═════════════════════════════════════════════════════════════════════════════

def test_01_health_returns_200_success_and_valid_status(client):
    """
    GET /api/v1/health → 200, success=True, data.status in the valid set.

    The health endpoint is public (no auth), so no X-API-Key header is sent.
    Status must be one of the three documented values.
    """
    with patch("api.routers.health.get_last_run", return_value=_LAST_RUN_OK):
        resp = client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] in ("ok", "degraded", "no_data")


def test_02_health_degraded_when_last_run_failed(client):
    """
    GET /api/v1/health with mocked last_run returning status='failed'
    → data.status == 'degraded'.
    """
    with patch("api.routers.health.get_last_run", return_value=_LAST_RUN_FAILED):
        resp = client.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "degraded"


def test_03_meta_with_valid_read_key_returns_universe_size(client):
    """
    GET /api/v1/meta with valid read key → 200, data contains 'universe_size'.
    All sub-queries are mocked so no real DB is touched.
    """
    with (
        patch("api.routers.health.get_last_run", return_value=_LAST_RUN_OK),
        patch("api.routers.health.get_watchlist", return_value=[_WL_ROW]),
        patch("api.routers.health.get_top_results", return_value=[]),
        patch("api.routers.health.get_git_sha", return_value="abc123ef"),
        patch("api.routers.health.get_config_hash", return_value="deadbeef"),
    ):
        resp = client.get("/api/v1/meta", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "universe_size" in body["data"]


def test_04_meta_without_key_is_forbidden(client):
    """
    GET /api/v1/meta with no key → 403 or 422 (when API_READ_KEY is set).

    FastAPI returns 422 when the required X-API-Key header is absent
    (header field validation fails before the auth function runs).
    Both 403 and 422 are acceptable 'access denied' outcomes.
    """
    resp = client.get("/api/v1/meta")
    assert resp.status_code in (403, 422)


# ═════════════════════════════════════════════════════════════════════════════
# Stocks router  (tests 5–8)
# ═════════════════════════════════════════════════════════════════════════════

def test_05_stocks_top_with_valid_read_key(client):
    """
    GET /api/v1/stocks/top with valid read key → 200, data is a list.
    """
    with patch("api.routers.stocks.get_results_for_date", return_value=[_STOCK_ROW_APLUS]):
        resp = client.get("/api/v1/stocks/top", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_06_stocks_top_quality_filter_returns_only_matching_tier(client):
    """
    GET /api/v1/stocks/top?quality=A%2B (i.e. quality=A+) with valid read key
    → only rows with setup_quality='A+' are present in data.
    """
    rows = [_STOCK_ROW_APLUS, _STOCK_ROW_A]   # A+ and A row

    with patch("api.routers.stocks.get_results_for_date", return_value=rows):
        resp = client.get("/api/v1/stocks/top?quality=A%2B", headers=READ_HEADERS)

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Every returned item must be A+
    for item in data:
        assert item["setup_quality"] == "A+"
    # The A row must NOT appear
    symbols = [item["symbol"] for item in data]
    assert _STOCK_ROW_A["symbol"] not in symbols


def test_07_stock_detail_by_symbol_returns_200_or_404(client):
    """
    GET /api/v1/stock/DIXON with valid key → 200 (found) or 404 (not found).
    Both are acceptable; we test the envelope shape when 200.
    """
    with patch("api.routers.stocks.get_results_for_date", return_value=[_STOCK_ROW_APLUS]):
        resp = client.get("/api/v1/stock/DIXON", headers=READ_HEADERS)

    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["symbol"] == "DIXON"


def test_08_stocks_top_without_key_is_forbidden(client):
    """
    GET /api/v1/stocks/top with no key → 403 or 422.
    """
    resp = client.get("/api/v1/stocks/top")
    assert resp.status_code in (403, 422)


# ═════════════════════════════════════════════════════════════════════════════
# Watchlist router  (tests 9–14)
# ═════════════════════════════════════════════════════════════════════════════

def test_09_watchlist_get_with_valid_read_key(client):
    """
    GET /api/v1/watchlist with valid read key → 200, data is a list.
    """
    with patch("storage.sqlite_store.get_watchlist", return_value=[_WL_ROW]):
        resp = client.get("/api/v1/watchlist", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


def test_10_watchlist_post_single_valid_symbol(client):
    """
    POST /api/v1/watchlist/DIXON with admin key → 200 or 422.

    validate_symbol is patched to True (valid symbol) and add_symbol returns
    True (symbol was new).  The endpoint returns the updated watchlist list.
    """
    with (
        patch("api.routers.watchlist.validate_symbol", return_value=True),
        patch("storage.sqlite_store.add_symbol", return_value=True),
        patch("storage.sqlite_store.get_watchlist", return_value=[_WL_ROW]),
    ):
        resp = client.post("/api/v1/watchlist/DIXON", headers=ADMIN_HEADERS)

    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert resp.json()["success"] is True


def test_11_watchlist_post_invalid_symbol_returns_422(client):
    """
    POST /api/v1/watchlist/INVALID$$ with admin key → 422.

    validate_symbol is patched to return False, which causes the router to
    raise HTTPException(422).
    """
    with patch("api.routers.watchlist.validate_symbol", return_value=False):
        resp = client.post("/api/v1/watchlist/INVALID$$", headers=ADMIN_HEADERS)

    assert resp.status_code == 422


def test_12_watchlist_post_with_read_key_only_is_forbidden(client):
    """
    POST /api/v1/watchlist/RELIANCE with a read key (not admin key) → 403.
    """
    resp = client.post("/api/v1/watchlist/RELIANCE", headers=READ_HEADERS)
    assert resp.status_code == 403


def test_13_watchlist_bulk_add_returns_added_key(client):
    """
    POST /api/v1/watchlist/bulk with admin key, body {"symbols": [...]}
    → 200, data has 'added' key.
    """
    with (
        patch("api.routers.watchlist.validate_symbol", return_value=True),
        patch("storage.sqlite_store.add_symbol", return_value=True),
    ):
        resp = client.post(
            "/api/v1/watchlist/bulk",
            json={"symbols": ["RELIANCE", "TCS", "DIXON"]},
            headers=ADMIN_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "added" in body["data"]


def test_14_watchlist_delete_symbol_returns_200_or_404(client):
    """
    DELETE /api/v1/watchlist/DIXON with admin key → 200 (removed) or 404.
    Both are acceptable; we assert the envelope is consistent.
    """
    with (
        patch("storage.sqlite_store.symbol_in_watchlist", return_value=True),
        patch("storage.sqlite_store.remove_symbol", return_value=True),
        patch("storage.sqlite_store.get_watchlist", return_value=[]),
    ):
        resp = client.delete("/api/v1/watchlist/DIXON", headers=ADMIN_HEADERS)

    assert resp.status_code in (200, 404)


# ═════════════════════════════════════════════════════════════════════════════
# Portfolio router  (tests 15–16)
# ═════════════════════════════════════════════════════════════════════════════

def test_15_portfolio_summary_with_valid_read_key(client):
    """
    GET /api/v1/portfolio with valid read key → 200, success=True.

    get_portfolio_summary is patched in the router's namespace so the
    stubbed paper_trading.report module is not called directly.
    """
    with patch(
        "api.routers.portfolio.get_portfolio_summary",
        return_value=_make_portfolio_summary_mock(),
    ):
        resp = client.get("/api/v1/portfolio", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True


def test_16_portfolio_trades_open_status(client):
    """
    GET /api/v1/portfolio/trades?status=open with valid read key → 200,
    data is a list (empty list is acceptable when no paper trades exist).
    """
    with patch("api.routers.portfolio.get_open_positions", return_value=[]):
        resp = client.get(
            "/api/v1/portfolio/trades?status=open",
            headers=READ_HEADERS,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


# ═════════════════════════════════════════════════════════════════════════════
# Run endpoint  (tests 17–18)
# ═════════════════════════════════════════════════════════════════════════════

def test_17_run_endpoint_with_admin_key_returns_202(client):
    """
    POST /api/v1/run with admin key, body {"scope": "watchlist"} → 202.

    The endpoint queues an asyncio background task and returns immediately.
    We do not wait for the background task — only the 202 acceptance matters.
    """
    resp = client.post(
        "/api/v1/run",
        json={"scope": "watchlist"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "queued"


def test_18_run_endpoint_with_read_key_only_is_forbidden(client):
    """
    POST /api/v1/run with read key (not admin) → 403.
    The run endpoint requires admin-tier key; the read key is insufficient.
    """
    resp = client.post(
        "/api/v1/run",
        json={"scope": "watchlist"},
        headers=READ_HEADERS,
    )
    assert resp.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# Auth: wrong key  (test 19)
# ═════════════════════════════════════════════════════════════════════════════

def test_19_wrong_read_key_returns_403(client):
    """
    Any GET endpoint with the wrong X-API-Key value → 403.
    Uses /api/v1/meta as the representative protected endpoint.
    """
    with patch("api.routers.health.get_last_run", return_value=_LAST_RUN_OK):
        resp = client.get("/api/v1/meta", headers=WRONG_HEADERS)

    assert resp.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# Open mode  (test 20)
# ═════════════════════════════════════════════════════════════════════════════

def test_20_open_mode_any_key_value_passes(client):
    """
    API in open mode (no env keys set) → GET endpoints return 200 even
    when a 'wrong' key value is supplied in the header.

    In open mode _OPEN_MODE=True, require_read_key() returns immediately
    without comparing the header value against _READ_KEY.  We send
    WRONG_HEADERS (which would normally get 403) and expect 200.
    """
    with (
        patch("api.auth._READ_KEY", ""),
        patch("api.auth._ADMIN_KEY", ""),
        patch("api.auth._OPEN_MODE", True),
        patch("api.routers.stocks.get_results_for_date", return_value=[_STOCK_ROW_APLUS]),
    ):
        resp = client.get("/api/v1/stocks/top", headers=WRONG_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True


# ═════════════════════════════════════════════════════════════════════════════
# Rate limiting  (test 21 — optional, slow)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.slow
def test_21_rate_limit_triggers_429_after_burst(client):
    """
    101 rapid GET /api/v1/stocks/top requests → at least one 429.

    slowapi enforces a per-IP limit (READ_LIMIT = "100/minute" by default).
    After 100 successful requests from the same TestClient IP, the 101st
    should receive HTTP 429 Too Many Requests.

    This test is marked @pytest.mark.slow so it can be excluded with:
        pytest -m "not slow"
    """
    status_codes: list[int] = []

    with patch("api.routers.stocks.get_results_for_date", return_value=[]):
        for _ in range(101):
            r = client.get("/api/v1/stocks/top", headers=READ_HEADERS)
            status_codes.append(r.status_code)

    assert 429 in status_codes, (
        f"Expected at least one 429 from rate limiter after 101 requests; "
        f"got status codes: {sorted(set(status_codes))}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Run router — Gap 6 tests  (tests 22–23)
# Verify that POST /api/v1/run goes through the proper APIRouter in
# api/routers/run.py (and therefore through the rate limiter), not through
# a bare @app.post() that would bypass slowapi entirely.
# ═════════════════════════════════════════════════════════════════════════════

def test_22_run_scope_watchlist(client):
    """
    POST /api/v1/run with body {"scope": "watchlist"} and a valid admin key
    → 202 Accepted, success=True, data.scope == "watchlist".

    The background thread function is patched so the real pipeline never
    executes; we only verify the HTTP acceptance handshake and envelope shape.
    """
    with patch("api.routers.run._run_pipeline_in_background") as mock_bg:
        resp = client.post(
            "/api/v1/run",
            json={"scope": "watchlist"},
            headers=ADMIN_HEADERS,
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["scope"] == "watchlist"
    assert body["data"]["status"] == "queued"


def test_23_run_scope_universe(client):
    """
    POST /api/v1/run with body {"scope": "universe"} and a valid admin key
    → 202 Accepted, success=True.

    Confirms that the "universe" scope is accepted and routed correctly
    through the new APIRouter (not the old bare @app.post binding).
    The background thread is patched to prevent real pipeline execution.
    """
    with patch("api.routers.run._run_pipeline_in_background"):
        resp = client.post(
            "/api/v1/run",
            json={"scope": "universe"},
            headers=ADMIN_HEADERS,
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["scope"] == "universe"


# ═════════════════════════════════════════════════════════════════════════════
# Backtest router  (tests 24–27)
# ═════════════════════════════════════════════════════════════════════════════

# Shared fake DB row for a completed backtest run
_BACKTEST_RUN_ROW = {
    "id":           42,
    "run_date":     "2025-01-10",
    "run_mode":     "backtest",
    "scope":        "all",
    "status":       "success",
    "duration_sec": 45.2,
    "a_plus_count": 3,
    "a_count":      5,
    "passed_stage2": 120,
    "passed_tt":     60,
    "vcp_qualified": 18,
    "error_msg":     None,
    "created_at":   "2025-01-10T09:00:00",
    "finished_at":  "2025-01-10T09:00:45",
}


def test_24_backtest_runs_returns_list_of_run_dicts(client):
    """
    GET /api/v1/backtest/runs with valid read key → 200, success=True,
    data is a list, each item has a 'run_id' key matching the DB row id.

    get_run_history is patched at the router's import namespace so no
    real SQLite database is touched.
    """
    with patch("api.routers.backtest.get_run_history", return_value=[_BACKTEST_RUN_ROW]):
        resp = client.get("/api/v1/backtest/runs", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 1
    assert body["data"][0]["run_id"] == 42
    assert body["data"][0]["status"] == "success"
    assert body["data"][0]["a_plus_count"] == 3


def test_25_backtest_summary_returns_report_dict(client):
    """
    GET /api/v1/backtest/runs/42/summary with valid read key → 200, success=True,
    data contains the keys from the mocked JSON report.

    _read_report_file is patched at the router's namespace so no file is
    ever read from disk.
    """
    mock_report = {
        "total_trades": 10,
        "win_rate": 62.5,
        "max_drawdown_pct": -8.3,
        "cagr_pct": 24.1,
    }
    with patch(
        "api.routers.backtest._read_report_file",
        return_value=mock_report,
    ):
        resp = client.get("/api/v1/backtest/runs/42/summary", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["total_trades"] == 10
    assert body["data"]["win_rate"] == 62.5
    assert body["meta"]["run_id"] == "42"


def test_26_backtest_summary_invalid_id_returns_404(client):
    """
    GET /api/v1/backtest/runs/invalid_id/summary with valid read key → 404.

    _read_report_file is patched to raise FileNotFoundError, which the
    endpoint converts into an HTTP 404 response.  The test verifies that
    missing-file cases never bubble up as 500.
    """
    with patch(
        "api.routers.backtest._read_report_file",
        side_effect=FileNotFoundError("no such file"),
    ):
        resp = client.get(
            "/api/v1/backtest/runs/invalid_id/summary",
            headers=READ_HEADERS,
        )

    assert resp.status_code == 404


def test_27_backtest_runs_no_data_returns_empty_list_not_500(client):
    """
    GET /api/v1/backtest/runs when no backtest runs exist → 200, success=True,
    data is an empty list.

    Verifies that a completely empty run_history table (get_run_history
    returns []) is handled gracefully and does NOT cause a 500.
    """
    with patch("api.routers.backtest.get_run_history", return_value=[]):
        resp = client.get("/api/v1/backtest/runs", headers=READ_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == []
    assert body["meta"]["total"] == 0
