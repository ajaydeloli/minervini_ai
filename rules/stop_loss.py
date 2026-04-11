"""
rules/stop_loss.py
──────────────────
Stop-loss computation rule for the Minervini AI SEPA rule engine.

Overview
────────
This module computes the stop-loss price for a SEPA / VCP setup using two
methods, applied in priority order:

    PRIMARY   — VCP base-low stop
        stop = last_pivot_low × (1 − vcp_buffer_pct / 100)
        Used when vcp_is_valid is True AND last_pivot_low is not NaN.
        This is Minervini's preferred method for VCP setups: place the
        stop just below the lowest point of the base.

    FALLBACK  — ATR stop
        stop = entry_price − (atr_multiplier × ATR_14)
        Used when the primary is unavailable (vcp_is_valid is False, or
        last_pivot_low is NaN).

Both methods are then subject to a max-risk cap:
    if risk_pct > max_risk_pct → stop = entry × (1 − max_risk_pct / 100)

Prerequisites
─────────────
The feature row must contain the following columns (produced by the
indicated feature modules):

    ATR_14          features/atr.py
    last_pivot_low  features/pivot.py
    vcp_is_valid    features/vcp.py

Public API
──────────
    compute_stop_loss(row, entry_price, config) → StopLossResult

Fail-loud contract  (PROJECT_DESIGN.md §19.1)
─────────────────────────────────────────────
    Missing vcp_is_valid or last_pivot_low column → RuleEngineError raised.
    Both last_pivot_low NaN AND ATR_14 NaN → RuleEngineError raised.
    Qualification failures are captured in stop_type, never raised.

Config keys consumed (config["stop_loss"])
──────────────────────────────────────────
    vcp_buffer_pct : float  (default 0.5)
        Stop is placed this many percent below last_pivot_low.
        stop = last_pivot_low × (1 − vcp_buffer_pct / 100)

    atr_multiplier : float  (default 2.0)
        Multiplier applied to ATR_14 for the fallback stop.
        stop = entry_price − atr_multiplier × ATR_14

    max_risk_pct : float  (default 8.0)
        Hard cap on risk.  If the computed stop implies risk% > this
        value, the stop is moved up to entry × (1 − max_risk_pct / 100).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from utils.exceptions import RuleEngineError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_VCP_BUFFER_PCT: float = 0.5
_DEFAULT_ATR_MULTIPLIER: float = 2.0
_DEFAULT_MAX_RISK_PCT: float = 8.0


# ─────────────────────────────────────────────────────────────────────────────
# StopLossResult dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StopLossResult:
    """
    Immutable result returned by compute_stop_loss().

    Fields
    ──────
    stop_price : float
        The computed (and possibly capped) stop-loss price.

    stop_type : str
        Which method produced the stop: "vcp_base" or "atr".

    risk_pct : float
        Distance from entry to stop as a percentage of entry:
            (entry_price − stop_price) / entry_price × 100

    capped : bool
        True when the raw stop implied risk_pct > max_risk_pct and the
        stop was moved up to the max-risk level.
    """

    stop_price: float
    stop_type: str   # "vcp_base" or "atr"
    risk_pct: float
    capped: bool


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_column(row: pd.Series, col_name: str) -> None:
    """
    Assert that *col_name* is present in *row*.

    Args:
        row:      Feature row as a pd.Series.
        col_name: Column name to check.

    Raises:
        RuleEngineError: If the column is absent from the index.
    """
    if col_name not in row.index:
        raise RuleEngineError(
            f"Required feature column '{col_name}' is missing from the feature row. "
            "Ensure the relevant feature module has been run for this symbol.",
            column=col_name,
        )


def _is_nan(value: object) -> bool:
    """Return True when *value* is None, pd.NA, or a NaN float."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _get_optional_float(row: pd.Series, col_name: str) -> float | None:
    """
    Return *col_name* from *row* as a float, or None if it is NaN / NA.

    Raises:
        RuleEngineError: If the column is absent from the index.
    """
    _require_column(row, col_name)
    val = row[col_name]
    if _is_nan(val):
        return None
    return float(val)


def _get_bool(row: pd.Series, col_name: str) -> bool:
    """
    Return *col_name* from *row* as a Python bool.

    pd.NA and NaN are treated as False.

    Raises:
        RuleEngineError: If the column is absent from the index.
    """
    _require_column(row, col_name)
    val = row[col_name]
    try:
        return bool(val)
    except (TypeError, ValueError):
        return False


def _compute_risk_pct(entry_price: float, stop_price: float) -> float:
    """Return (entry − stop) / entry × 100, rounded to 4 d.p."""
    return round((entry_price - stop_price) / entry_price * 100, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_stop_loss(
    row: pd.Series,
    entry_price: float,
    config: dict,
) -> StopLossResult:
    """
    Compute the stop-loss price for a SEPA / VCP setup.

    Parameters
    ──────────
    row : pd.Series
        Most recent row from the feature DataFrame (iloc[-1]).
        Required columns:
            vcp_is_valid    — bool; selects primary vs fallback method
            last_pivot_low  — float | NaN; VCP base low
            ATR_14          — float | NaN; 14-period ATR (fallback)

    entry_price : float
        The price at which the position would be entered (e.g. the
        breakout close from check_entry_trigger()).

    config : dict
        Full application configuration dict.
        Keys consumed from config["stop_loss"]:
            vcp_buffer_pct  float  (default 0.5)
            atr_multiplier  float  (default 2.0)
            max_risk_pct    float  (default 8.0)

    Returns
    ───────
    StopLossResult

    Raises
    ──────
    RuleEngineError
        • If vcp_is_valid or last_pivot_low is absent from the row.
        • If both last_pivot_low is NaN/unusable AND ATR_14 is NaN —
          i.e. there is no basis on which to compute any stop.
    """
    sl_cfg: dict = config.get("stop_loss", {})
    vcp_buffer_pct: float = float(sl_cfg.get("vcp_buffer_pct", _DEFAULT_VCP_BUFFER_PCT))
    atr_multiplier: float = float(sl_cfg.get("atr_multiplier", _DEFAULT_ATR_MULTIPLIER))
    max_risk_pct: float   = float(sl_cfg.get("max_risk_pct",   _DEFAULT_MAX_RISK_PCT))

    log.debug(
        "compute_stop_loss called",
        entry_price=entry_price,
        vcp_buffer_pct=vcp_buffer_pct,
        atr_multiplier=atr_multiplier,
        max_risk_pct=max_risk_pct,
    )

    # ── Read feature columns ─────────────────────────────────────────────────
    vcp_is_valid: bool        = _get_bool(row, "vcp_is_valid")
    pivot_low: float | None   = _get_optional_float(row, "last_pivot_low")
    atr_14: float | None      = _get_optional_float(row, "ATR_14")

    # ── Select stop method ───────────────────────────────────────────────────
    raw_stop: float
    stop_type: str

    use_vcp_primary = vcp_is_valid and pivot_low is not None

    if use_vcp_primary:
        # PRIMARY: place stop just below the VCP base low
        raw_stop = pivot_low * (1.0 - vcp_buffer_pct / 100.0)
        stop_type = "vcp_base"
        log.debug(
            "Using VCP base-low stop",
            pivot_low=pivot_low,
            raw_stop=raw_stop,
        )
    else:
        # FALLBACK: ATR stop
        if atr_14 is None:
            raise RuleEngineError(
                "cannot compute stop: no VCP base and no ATR",
                vcp_is_valid=vcp_is_valid,
                pivot_low=pivot_low,
            )
        raw_stop = entry_price - atr_multiplier * atr_14
        stop_type = "atr"
        log.debug(
            "Using ATR fallback stop",
            atr_14=atr_14,
            atr_multiplier=atr_multiplier,
            raw_stop=raw_stop,
        )

    # ── Apply max-risk cap ───────────────────────────────────────────────────
    raw_risk_pct = _compute_risk_pct(entry_price, raw_stop)
    capped = False

    if raw_risk_pct > max_risk_pct:
        stop_price = entry_price * (1.0 - max_risk_pct / 100.0)
        capped = True
        log.debug(
            "Stop capped by max_risk_pct",
            raw_risk_pct=raw_risk_pct,
            max_risk_pct=max_risk_pct,
            capped_stop=stop_price,
        )
    else:
        stop_price = raw_stop

    risk_pct = _compute_risk_pct(entry_price, stop_price)

    result = StopLossResult(
        stop_price=stop_price,
        stop_type=stop_type,
        risk_pct=risk_pct,
        capped=capped,
    )

    log.info(
        "Stop-loss computed",
        stop_type=stop_type,
        stop_price=round(stop_price, 4),
        risk_pct=risk_pct,
        capped=capped,
    )

    return result
