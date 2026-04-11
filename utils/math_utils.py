"""
utils/math_utils.py
───────────────────
Pure numeric helpers — no pandas, no I/O, no side effects.

Every function here takes plain Python scalars or sequences (list /
tuple / numpy arrays) and returns scalars or lists.  Keeping these
free of DataFrame types means they can be used in rule engine code
that intentionally avoids pandas for speed.

All numpy imports are local-to-function to keep cold-import time low
when only simple helpers are needed.
"""

from __future__ import annotations

import math
from typing import Sequence


# ─────────────────────────────────────────────────────────────────────────────
# Type alias
# ─────────────────────────────────────────────────────────────────────────────

Numeric = int | float
NumericSeq = Sequence[Numeric]


# ─────────────────────────────────────────────────────────────────────────────
# Guard
# ─────────────────────────────────────────────────────────────────────────────

def _require_len(seq: NumericSeq, minimum: int, name: str = "sequence") -> None:
    if len(seq) < minimum:
        raise ValueError(
            f"'{name}' must have at least {minimum} elements, got {len(seq)}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Linear regression slope
# ─────────────────────────────────────────────────────────────────────────────

def linear_slope(values: NumericSeq) -> float:
    """
    Return the slope (β₁) of the ordinary-least-squares regression line
    fitted to *values* against an integer index [0, 1, …, n-1].

    Used in feature modules to compute MA trend direction:
        slope > 0  → trending up
        slope < 0  → trending down
        slope ≈ 0  → flat

    Args:
        values: Sequence of at least 2 numeric values (e.g. recent MA prices).

    Returns:
        Slope as a float.  Positive = uptrend, negative = downtrend.

    Raises:
        ValueError: If fewer than 2 values are provided.
    """
    _require_len(values, 2, "values")
    n = len(values)
    xs = list(range(n))
    x_mean = (n - 1) / 2.0               # mean of [0, 1, …, n-1]
    y_mean = sum(values) / n

    num = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))

    if den == 0:
        return 0.0
    return num / den


def normalised_slope(values: NumericSeq) -> float:
    """
    Slope normalised by the mean of *values*, expressed as a fraction
    per period.

    Example:
        normalised_slope([100, 101, 102])  ≈  0.01   (1% per period)

    Useful for comparing slopes across stocks with different price scales.
    """
    _require_len(values, 2, "values")
    mean_val = sum(values) / len(values)
    if mean_val == 0:
        return 0.0
    return linear_slope(values) / mean_val


# ─────────────────────────────────────────────────────────────────────────────
# Rolling statistics (pure Python, no pandas)
# ─────────────────────────────────────────────────────────────────────────────

def rolling_mean(values: NumericSeq, window: int) -> list[float]:
    """
    Return a list of rolling means with the same length as *values*.
    Leading positions that don't have a full window are filled with NaN.

    Args:
        values: Input sequence.
        window: Rolling window size (>= 1).

    Returns:
        List of floats, len == len(values).
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    result: list[float] = [math.nan] * len(values)
    for i in range(window - 1, len(values)):
        result[i] = sum(values[i - window + 1 : i + 1]) / window
    return result


def rolling_max(values: NumericSeq, window: int) -> list[float]:
    """Rolling maximum over *window* periods."""
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    result: list[float] = [math.nan] * len(values)
    for i in range(window - 1, len(values)):
        result[i] = max(values[i - window + 1 : i + 1])
    return result


def rolling_min(values: NumericSeq, window: int) -> list[float]:
    """Rolling minimum over *window* periods."""
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    result: list[float] = [math.nan] * len(values)
    for i in range(window - 1, len(values)):
        result[i] = min(values[i - window + 1 : i + 1])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Percentile rank
# ─────────────────────────────────────────────────────────────────────────────

def percentile_rank(value: Numeric, population: NumericSeq) -> float:
    """
    Return the percentile rank of *value* within *population* (0–100).

    Used in features/relative_strength.py to convert raw RS into a
    0–99 rating comparable to IBD's Relative Strength Rating.

    Formula:
        rank = (number of values in population strictly less than value)
               / len(population) × 100

    Edge cases:
        If value is the minimum  → 0.0
        If value is the maximum  → approaches 100.0 (never exactly 100)
        Empty population         → raises ValueError

    Args:
        value:      The value to rank (e.g. a symbol's RS raw score).
        population: The full comparison set (e.g. RS raw scores for all symbols).
    """
    if not population:
        raise ValueError("population must be non-empty")
    count_below = sum(1 for v in population if v < value)
    return round(count_below / len(population) * 100, 2)


def clamp(value: Numeric, lo: Numeric, hi: Numeric) -> Numeric:
    """Clamp *value* to the closed interval [lo, hi]."""
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────────────────────────────────────
# Return calculations
# ─────────────────────────────────────────────────────────────────────────────

def pct_change(start: Numeric, end: Numeric) -> float:
    """
    Return percentage change from *start* to *end*.

        pct_change(100, 125) → 25.0   (25%)
        pct_change(100, 80)  → -20.0

    Raises:
        ZeroDivisionError: If start == 0.
    """
    if start == 0:
        raise ZeroDivisionError(f"start value is 0; cannot compute pct_change.")
    return (end - start) / abs(start) * 100.0


def pct_above(price: Numeric, reference: Numeric) -> float:
    """
    How many percent is *price* above *reference*?

    pct_above(130, 100) → 30.0
    pct_above(80, 100)  → -20.0
    """
    return pct_change(reference, price)


def pct_below_high(price: Numeric, high: Numeric) -> float:
    """
    How many percent is *price* below *high*?
    Positive value means price is below the high.

        pct_below_high(90, 100) → 10.0  (10% below)
        pct_below_high(105, 100) → -5.0  (5% above high)
    """
    if high == 0:
        raise ZeroDivisionError("high is 0; cannot compute pct_below_high.")
    return (high - price) / high * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# ATR helpers
# ─────────────────────────────────────────────────────────────────────────────

def true_range(
    high: Numeric,
    low: Numeric,
    prev_close: Numeric,
) -> float:
    """
    True Range = max(high - low, |high - prev_close|, |low - prev_close|).
    Used in features/atr.py.
    """
    return float(max(
        high - low,
        abs(high - prev_close),
        abs(low  - prev_close),
    ))


def average_true_range(
    highs: NumericSeq,
    lows: NumericSeq,
    closes: NumericSeq,
    period: int = 14,
) -> list[float]:
    """
    Compute Wilder's smoothed ATR for each bar.

    Returns a list of the same length as inputs.  The first *period*
    values are filled with NaN (insufficient history).

    Args:
        highs:   Daily high prices.
        lows:    Daily low prices.
        closes:  Daily close prices (must be 1 longer than other lists OR
                 same length when first close is used as its own prev_close).
        period:  ATR smoothing period (default 14).
    """
    n = len(highs)
    if not (n == len(lows) == len(closes)):
        raise ValueError("highs, lows, closes must be the same length.")
    _require_len(highs, period + 1, "price series")

    # True ranges
    trs: list[float] = [math.nan]
    for i in range(1, n):
        trs.append(true_range(highs[i], lows[i], closes[i - 1]))

    # Wilder's smoothing: seed with simple mean of first *period* TRs
    result: list[float] = [math.nan] * n
    seed_trs = [trs[i] for i in range(1, period + 1)]
    result[period] = sum(seed_trs) / period

    for i in range(period + 1, n):
        result[i] = (result[i - 1] * (period - 1) + trs[i]) / period

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Contraction / VCP helpers
# ─────────────────────────────────────────────────────────────────────────────

def depth_pct(high: Numeric, low: Numeric) -> float:
    """
    Percentage depth of a price contraction:
        depth_pct(100, 80) → 20.0  (20% correction)

    Used in VCP contraction analysis.
    """
    if high == 0:
        raise ZeroDivisionError("high is 0; cannot compute depth_pct.")
    return (high - low) / high * 100.0


def is_contracting(depths: Sequence[float]) -> bool:
    """
    Return True if every successive depth in *depths* is strictly
    smaller than the one before it (monotonically declining corrections).

        is_contracting([20.0, 12.0, 6.0]) → True
        is_contracting([20.0, 25.0, 6.0]) → False

    Args:
        depths: Contraction depths in chronological order (oldest first).
    """
    if len(depths) < 2:
        return False
    return all(depths[i] < depths[i - 1] for i in range(1, len(depths)))


# ─────────────────────────────────────────────────────────────────────────────
# Score / weight helpers
# ─────────────────────────────────────────────────────────────────────────────

def weighted_score(components: dict[str, tuple[float, float]]) -> float:
    """
    Compute a weighted composite score.

    Args:
        components: mapping of
            name → (component_score_0_to_100, weight_0_to_1)

    Returns:
        Weighted sum as a float in [0, 100], rounded to 2 decimal places.

    Example:
        weighted_score({
            "rs_rating": (88.0, 0.30),
            "trend":     (100.0, 0.25),
            "vcp":       (75.0, 0.25),
            "volume":    (60.0, 0.10),
            "fundamental": (50.0, 0.07),
            "news":      (55.0, 0.03),
        })
        → 82.35
    """
    total_weight = sum(w for _, (_, w) in components.items())
    if not math.isclose(total_weight, 1.0, abs_tol=1e-6):
        raise ValueError(
            f"Component weights must sum to 1.0, got {total_weight:.6f}. "
            f"Components: {list(components.keys())}"
        )
    score = sum(s * w for _, (s, w) in components.items())
    return round(clamp(score, 0.0, 100.0), 2)


def safe_divide(numerator: Numeric, denominator: Numeric, default: float = 0.0) -> float:
    """
    Division that returns *default* instead of raising on zero denominator.
    """
    if denominator == 0:
        return default
    return numerator / denominator


def round2(value: Numeric) -> float:
    """Round to 2 decimal places (common for INR prices and percentages)."""
    return round(float(value), 2)


def is_finite(value: object) -> bool:
    """Return True if *value* is a finite float (not NaN, not ±inf)."""
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
