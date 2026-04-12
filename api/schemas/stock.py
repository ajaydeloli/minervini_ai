"""
api/schemas/stock.py
────────────────────
Pydantic v2 response models for stock / screener endpoints.

Hierarchy
─────────
  StockSummary   — compact row used in list responses
                   (/stocks/top, /stocks/trend, /stocks/vcp)
  StockDetail    — extends StockSummary with nested detail dicts + narrative
                   (used in GET /stock/{symbol})
  StockHistory   — symbol + list of StockSummary used in
                   GET /stock/{symbol}/history
  WatchlistEntry — single row returned in GET /watchlist

Field names mirror:
  - screener_results columns   → storage/sqlite_store.py
  - SEPAResult dataclass fields → rules/scorer.py
  - watchlist columns          → storage/sqlite_store.py

Conventions (project-wide):
  - Python 3.11+ native generics / union syntax  (X | Y, list[T])
  - Pydantic v2 — no deprecated validators
  - All monetary fields are float (rupees); percentages are float
  - Nullable fields default to None — never use sentinel 0/-1
  - run_date / added_at are ISO date/datetime strings, not datetime objects
    (avoids tz serialisation edge cases in the API layer)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# StockSummary — compact list row
# ─────────────────────────────────────────────────────────────────────────────

class StockSummary(BaseModel):
    """
    Compact representation of one SEPA evaluation result.

    Used in list responses: GET /stocks/top, /stocks/trend, /stocks/vcp.
    Each field maps directly to a column in the screener_results SQLite table
    or a field on the SEPAResult dataclass in rules/scorer.py.

    Fields
    ──────
    symbol              : NSE ticker, e.g. "DIXON".
    score               : Composite SEPA score 0–100.
    setup_quality       : Quality tag — one of A+, A, B, C, FAIL.
    stage               : Weinstein stage (1–4).
    stage_label         : Human-readable stage description.
    rs_rating           : Relative Strength rating 0–99.
    trend_template_pass : True when all 8 Minervini TT conditions are met.
    conditions_met      : Count of TT conditions passed (0–8).
    vcp_qualified       : True when a valid VCP pattern was detected.
    breakout_triggered  : True when today's price crossed the pivot entry.
    entry_price         : Suggested entry price; None if no breakout triggered.
    stop_loss           : Calculated stop-loss price; None if not applicable.
    risk_pct            : (entry − stop) / entry × 100; None if not applicable.
    rr_ratio            : Reward:Risk ratio; None if no resistance pivot found.
    fundamental_pass    : True when fundamental template passed (Phase 5+).
                          None when fundamentals were not evaluated.
    news_score          : Sentiment score −100..+100; None when not evaluated.
    run_date            : ISO date string ("YYYY-MM-DD") of the screen run.
    """

    symbol:               str
    score:                int
    setup_quality:        str           # A+, A, B, C, FAIL
    stage:                int
    stage_label:          str
    rs_rating:            int
    trend_template_pass:  bool
    conditions_met:       int
    vcp_qualified:        bool
    breakout_triggered:   bool
    entry_price:          float | None
    stop_loss:            float | None
    risk_pct:             float | None
    rr_ratio:             float | None
    fundamental_pass:     bool  | None
    news_score:           float | None
    run_date:             str           # ISO date string, e.g. "2024-01-15"


# ─────────────────────────────────────────────────────────────────────────────
# StockDetail — full single-symbol response
# ─────────────────────────────────────────────────────────────────────────────

class StockDetail(StockSummary):
    """
    Full SEPA detail for a single symbol.

    Extends StockSummary with nested detail dicts and an LLM narrative.
    Used by GET /api/v1/stock/{symbol}.

    Additional Fields
    ─────────────────
    trend_template_details : dict mapping each of the 8 TT condition names
                             to a bool.  None when not available in storage.
                             Example: {"above_150_200_ma": true, "rs_52w_high": true, ...}
    fundamental_details    : dict mapping each of the 7 fundamental condition
                             names to a bool.  None when Phase 5 data is absent.
    vcp_details            : dict with VCP metrics: contraction_count,
                             max_depth_pct, final_depth_pct, vol_ratio,
                             base_weeks, fail_reason, quality_grade.
                             None when no VCP data is stored.
    narrative              : LLM-generated plain-text analysis (Phase 6).
                             None when LLM is disabled or result not cached.
    """

    trend_template_details: dict[str, bool] | None = None
    fundamental_details:    dict[str, bool] | None = None
    vcp_details:            dict            | None = None
    narrative:              str             | None = None


# ─────────────────────────────────────────────────────────────────────────────
# StockHistory — per-symbol historical scoring
# ─────────────────────────────────────────────────────────────────────────────

class StockHistory(BaseModel):
    """
    Historical SEPA scores for a single symbol across multiple screen runs.

    Used by GET /api/v1/stock/{symbol}/history.

    Fields
    ──────
    symbol  : NSE ticker.
    history : Ordered list of StockSummary rows, most recent first.
              Each entry corresponds to one pipeline run date.
              Empty list when the symbol has never been screened.
    """

    symbol:  str
    history: list[StockSummary]


# ─────────────────────────────────────────────────────────────────────────────
# WatchlistEntry — single row in GET /watchlist
# ─────────────────────────────────────────────────────────────────────────────

class WatchlistEntry(BaseModel):
    """
    One row in the GET /api/v1/watchlist response.

    Mirrors the watchlist table in SQLite (storage/sqlite_store.py).

    Fields
    ──────
    symbol       : NSE ticker.
    note         : Optional free-text annotation; None when not set.
    added_at     : ISO datetime string when the symbol was added,
                   e.g. "2024-01-10T08:30:00".
    added_via    : Source of the add: cli | api | dashboard | file_upload | test.
    last_score   : Most recent composite SEPA score (0–100); None before first run.
    last_quality : Most recent setup quality tag; None before first run.
    last_run_at  : ISO datetime string of the last screen run that updated this
                   watchlist entry; None before first run.
    """

    symbol:       str
    note:         str   | None
    added_at:     str
    added_via:    str
    last_score:   float | None
    last_quality: str   | None
    last_run_at:  str   | None
