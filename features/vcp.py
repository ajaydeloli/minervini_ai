"""
features/vcp.py
───────────────
Volatility Contraction Pattern (VCP) detection module.

Public API
──────────
    compute(df, config) → pd.DataFrame

        Top-level entry point.  Runs the detector selected by
        config["vcp"]["detector"] and appends VCP metric columns to a
        *copy* of df, returning it.

        Output columns (meaningful only on the LAST row; NaN elsewhere):
            vcp_contraction_count   int      — number of contraction legs found
            vcp_max_depth_pct       float    — deepest correction in the base (%)
            vcp_final_depth_pct     float    — most-recent (shallowest) correction (%)
            vcp_vol_ratio           float    — last-leg avg vol / first-leg avg vol
            vcp_base_weeks          int      — base length in weeks
            vcp_is_valid            bool     — passes ALL qualification rules
            vcp_fail_reason         str|None — human-readable fail reason, or None

Design rules (PROJECT_DESIGN.md §19.1, §19.2)
─────────────────────────────────────────────
    • Pure functions only — no global state, no I/O.
    • No TA-Lib; pandas + numpy only.
    • All thresholds come from config["vcp"], not from hardcoded constants.
    • Fail loudly: missing pivot columns → FeatureComputeError.
    • Fewer than 2 confirmed swing highs → is_valid_vcp = False (not an error).

Config keys consumed (vcp section)
────────────────────────────────────
    detector               str   "rule_based" | "cnn"     (default "rule_based")
    min_contractions       int   minimum VCP legs          (default 2)
    require_declining_depth bool  each leg < previous      (default True)
    require_vol_contraction bool  last-leg vol < first-leg (default True)
    min_weeks              int   minimum base length       (default 3)
    max_weeks              int   maximum base length       (default 52)
    tightness_pct          float max final-leg depth (%)  (default 10.0)
    max_depth_pct          float max any-leg depth (%)    (default 50.0)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from utils.exceptions import FeatureComputeError

# ─────────────────────────────────────────────────────────────────────────────
# VCPMetrics dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VCPMetrics:
    """Structured output produced by every VCPDetector implementation."""
    contraction_count: int = 0
    max_depth_pct: float = 0.0       # deepest correction in the base (%)
    final_depth_pct: float = 0.0     # shallowest (most recent) correction (%)
    vol_contraction_ratio: float = 0.0  # avg vol last leg / avg vol first leg
    base_length_weeks: int = 0
    is_valid_vcp: bool = False
    fail_reason: Optional[str] = None  # human-readable reason if not valid


# ─────────────────────────────────────────────────────────────────────────────
# Abstract interface
# ─────────────────────────────────────────────────────────────────────────────

class VCPDetector(ABC):
    """
    Abstract VCP detector interface.
    All implementations must return VCPMetrics — the screener never
    knows or cares which detector is running underneath.
    """

    @abstractmethod
    def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics:
        """
        Analyse df and return a VCPMetrics instance.

        Parameters
        ──────────
        df     : pd.DataFrame with OHLCV columns plus is_swing_high /
                 is_swing_low boolean columns produced by features/pivot.py.
        config : full application config dict; implementors read config["vcp"].

        Returns
        ───────
        VCPMetrics (is_valid_vcp=False with fail_reason set when not a VCP).
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based detector (default)
# ─────────────────────────────────────────────────────────────────────────────

class RuleBasedVCPDetector(VCPDetector):
    """
    Deterministic, auditable VCP detector using pivot math + volume ratios.

    Algorithm
    ─────────
    a. Extract confirmed swing highs and swing lows from is_swing_high /
       is_swing_low columns (produced by features/pivot.py).
    b. Build contraction legs — alternating swing-high → swing-low sequences.
       Each leg depth = (sh_price - sl_price) / sh_price * 100.
    c. contraction_count  = number of valid legs found.
    d. max_depth_pct      = deepest leg depth.
    e. final_depth_pct    = most-recent (shallowest) leg depth.
    f. vol_contraction_ratio = avg volume in last leg / avg volume in first leg.
    g. base_length_weeks  = (date of last swing high - date of first swing high) / 7.
    h. is_valid_vcp       = all rules from config["vcp"] pass.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_confirmed_pivots(
        df: pd.DataFrame,
    ) -> tuple[list[tuple], list[tuple]]:
        """
        Return (swing_highs, swing_lows) as lists of (index_position, date, price).
        Only rows where is_swing_high / is_swing_low is True (not False, not NA).
        """
        swing_highs: list[tuple] = []
        swing_lows: list[tuple] = []

        for i, (idx, row) in enumerate(df.iterrows()):
            sh = row["is_swing_high"]
            sl = row["is_swing_low"]

            # pd.NA evaluates to NA-truthy; guard with pd.notna
            if pd.notna(sh) and bool(sh):
                swing_highs.append((i, idx, float(row["high"])))

            if pd.notna(sl) and bool(sl):
                swing_lows.append((i, idx, float(row["low"])))

        return swing_highs, swing_lows

    @staticmethod
    def _build_contraction_legs(
        swing_highs: list[tuple],
        swing_lows: list[tuple],
    ) -> list[dict]:
        """
        Pair each swing high with the nearest subsequent swing low to form
        a contraction leg.  Returns a list of dicts (ordered chronologically):
            {
              "sh_pos": int, "sh_date": timestamp, "sh_price": float,
              "sl_pos": int, "sl_date": timestamp, "sl_price": float,
              "depth_pct": float,
            }
        """
        legs: list[dict] = []

        for sh_pos, sh_date, sh_price in swing_highs:
            # Find the first swing low that comes AFTER this swing high
            matching_sl = None
            for sl_pos, sl_date, sl_price in swing_lows:
                if sl_pos > sh_pos:
                    matching_sl = (sl_pos, sl_date, sl_price)
                    break

            if matching_sl is None:
                continue

            sl_pos, sl_date, sl_price = matching_sl
            if sh_price <= 0:
                continue

            depth_pct = (sh_price - sl_price) / sh_price * 100.0
            if depth_pct < 0:
                depth_pct = 0.0

            legs.append(
                {
                    "sh_pos": sh_pos,
                    "sh_date": sh_date,
                    "sh_price": sh_price,
                    "sl_pos": sl_pos,
                    "sl_date": sl_date,
                    "sl_price": sl_price,
                    "depth_pct": depth_pct,
                }
            )

        # Deduplicate: if two legs share the same swing-low, keep the one
        # with the most recent (highest-pos) swing-high — that is the
        # "tightest" contraction for the same low.
        seen_sl: dict[int, dict] = {}
        for leg in legs:
            sl_pos = leg["sl_pos"]
            if sl_pos not in seen_sl or leg["sh_pos"] > seen_sl[sl_pos]["sh_pos"]:
                seen_sl[sl_pos] = leg

        # Re-sort by swing-high position (chronological order)
        unique_legs = sorted(seen_sl.values(), key=lambda l: l["sh_pos"])
        return unique_legs

    @staticmethod
    def _avg_volume_in_range(df: pd.DataFrame, start_pos: int, end_pos: int) -> float:
        """Average volume between start_pos and end_pos (inclusive)."""
        subset = df.iloc[start_pos : end_pos + 1]
        if subset.empty:
            return 0.0
        return float(subset["volume"].mean())

    # ------------------------------------------------------------------
    # Main detect implementation
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics:  # noqa: C901
        vcp_cfg = config.get("vcp", {})

        min_contractions: int = int(vcp_cfg.get("min_contractions", 2))
        require_declining: bool = bool(vcp_cfg.get("require_declining_depth", True))
        require_vol_contraction: bool = bool(vcp_cfg.get("require_vol_contraction", True))
        min_weeks: int = int(vcp_cfg.get("min_weeks", 3))
        max_weeks: int = int(vcp_cfg.get("max_weeks", 52))
        tightness_pct: float = float(vcp_cfg.get("tightness_pct", 10.0))
        cfg_max_depth: float = float(vcp_cfg.get("max_depth_pct", 50.0))

        # ── Step a: extract confirmed pivots ──────────────────────────
        swing_highs, swing_lows = self._extract_confirmed_pivots(df)

        if len(swing_highs) < 2:
            return VCPMetrics(
                is_valid_vcp=False,
                fail_reason="insufficient pivots",
            )

        # ── Step b: build contraction legs ────────────────────────────
        legs = self._build_contraction_legs(swing_highs, swing_lows)

        if not legs:
            return VCPMetrics(
                is_valid_vcp=False,
                fail_reason="insufficient pivots",
            )

        # ── Steps c–e: contraction metrics ───────────────────────────
        contraction_count = len(legs)
        depths = [leg["depth_pct"] for leg in legs]
        max_depth_pct = float(max(depths))
        final_depth_pct = float(depths[-1])  # most recent leg

        # ── Step f: volume contraction ratio ──────────────────────────
        first_leg = legs[0]
        last_leg = legs[-1]

        first_leg_vol = self._avg_volume_in_range(
            df, first_leg["sh_pos"], first_leg["sl_pos"]
        )
        last_leg_vol = self._avg_volume_in_range(
            df, last_leg["sh_pos"], last_leg["sl_pos"]
        )

        if first_leg_vol > 0:
            vol_ratio = last_leg_vol / first_leg_vol
        else:
            vol_ratio = 1.0  # cannot determine; assume no contraction

        # ── Step g: base length in weeks ──────────────────────────────
        first_sh_date = swing_highs[0][1]
        last_sh_date = swing_highs[-1][1]
        try:
            delta_days = (last_sh_date - first_sh_date).days
        except Exception:
            delta_days = 0
        base_length_weeks = int(delta_days / 7)

        # ── Step h: apply qualification rules ────────────────────────
        # Evaluate each rule in order; first failure short-circuits.
        fail_reason: Optional[str] = None

        if contraction_count < min_contractions:
            fail_reason = (
                f"contraction_count {contraction_count} < "
                f"min_contractions {min_contractions}"
            )
        elif require_declining and not _is_declining(depths):
            fail_reason = "contractions not declining in depth"
        elif require_vol_contraction and vol_ratio >= 1.0:
            fail_reason = (
                f"vol_contraction_ratio {vol_ratio:.3f} >= 1.0 "
                "(no volume dry-up)"
            )
        elif not (min_weeks <= base_length_weeks <= max_weeks):
            fail_reason = (
                f"base_length_weeks {base_length_weeks} not in "
                f"[{min_weeks}, {max_weeks}]"
            )
        elif final_depth_pct >= tightness_pct:
            fail_reason = (
                f"final_depth_pct {final_depth_pct:.2f}% >= "
                f"tightness_pct {tightness_pct}%"
            )
        elif max_depth_pct > cfg_max_depth:
            fail_reason = (
                f"max_depth_pct {max_depth_pct:.2f}% > "
                f"config max_depth_pct {cfg_max_depth}%"
            )

        is_valid = fail_reason is None

        return VCPMetrics(
            contraction_count=contraction_count,
            max_depth_pct=max_depth_pct,
            final_depth_pct=final_depth_pct,
            vol_contraction_ratio=vol_ratio,
            base_length_weeks=base_length_weeks,
            is_valid_vcp=is_valid,
            fail_reason=fail_reason,
        )


# ─────────────────────────────────────────────────────────────────────────────
# CNN stub (Phase 12+)
# ─────────────────────────────────────────────────────────────────────────────

class CNNVCPDetector(VCPDetector):
    """
    Future upgrade — Phase 12+.
    Loads a trained CNN model and runs inference on a rendered chart image.
    Requires: labeled training data (paper trading results), PyTorch.
    Same VCPMetrics output — zero changes to screener or pipeline.
    DO NOT implement until 6+ months of paper trading labels are available.
    """

    def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics:
        raise NotImplementedError(
            "CNNVCPDetector is reserved for Phase 12+. "
            "Set config['vcp']['detector'] = 'rule_based'."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

DETECTORS: dict[str, Optional[type]] = {
    "rule_based": RuleBasedVCPDetector,
    "cnn": None,  # placeholder — CNNVCPDetector available once trained
}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_declining(depths: list[float]) -> bool:
    """Return True if each successive depth value is strictly less than the previous."""
    for i in range(1, len(depths)):
        if depths[i] >= depths[i - 1]:
            return False
    return True


def _validate_pivot_columns(df: pd.DataFrame) -> None:
    """Raise FeatureComputeError if pivot columns are absent."""
    missing = [c for c in ("is_swing_high", "is_swing_low") if c not in df.columns]
    if missing:
        raise FeatureComputeError(
            symbol=str(getattr(df, "name", "unknown")),
            feature="vcp",
            reason=(
                f"Required pivot columns missing from DataFrame: {missing}. "
                "Run features/pivot.py compute() first."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Top-level entry point consumed by features/feature_store.py.

    Selects the detector specified by config["vcp"]["detector"], runs it,
    and appends VCP metric columns to a *copy* of df.

    Columns appended (meaningful only on the LAST row; NaN/None elsewhere):
        vcp_contraction_count   int
        vcp_max_depth_pct       float
        vcp_final_depth_pct     float
        vcp_vol_ratio           float
        vcp_base_weeks          int
        vcp_is_valid            bool
        vcp_fail_reason         object (str or None)

    Parameters
    ──────────
    df     : pd.DataFrame with OHLCV columns + is_swing_high + is_swing_low.
    config : full application config dict.

    Returns
    ───────
    pd.DataFrame — new DataFrame with VCP columns appended.

    Raises
    ──────
    FeatureComputeError
        If is_swing_high or is_swing_low columns are missing from df.
    ValueError
        If config["vcp"]["detector"] names an unknown or unimplemented detector.
    """
    _validate_pivot_columns(df)

    vcp_cfg = config.get("vcp", {})
    detector_key: str = str(vcp_cfg.get("detector", "rule_based"))

    if detector_key not in DETECTORS:
        raise ValueError(
            f"Unknown VCP detector '{detector_key}'. "
            f"Valid options: {list(DETECTORS.keys())}"
        )

    detector_cls = DETECTORS[detector_key]
    if detector_cls is None:
        raise ValueError(
            f"VCP detector '{detector_key}' is registered but not yet implemented. "
            "Use 'rule_based' instead."
        )

    detector: VCPDetector = detector_cls()
    metrics: VCPMetrics = detector.detect(df, config)

    # ── Build output DataFrame ────────────────────────────────────────────────
    out = df.copy()
    n = len(out)

    # All rows default to NaN / None; only the last row carries real values.
    # vcp_is_valid uses a nullable boolean dtype (pd.NA default) so that a
    # Python bool can be assigned without a lossy-cast error on pandas ≥ 2.0.
    out["vcp_contraction_count"] = np.nan
    out["vcp_max_depth_pct"] = np.nan
    out["vcp_final_depth_pct"] = np.nan
    out["vcp_vol_ratio"] = np.nan
    out["vcp_base_weeks"] = np.nan
    out["vcp_is_valid"] = pd.array([pd.NA] * n, dtype="boolean")
    out["vcp_fail_reason"] = None

    last = n - 1
    # Use .at for all scalar assignments on the last row — safer than .iloc
    # for mixed-dtype columns because it goes through the Index directly.
    out.at[out.index[last], "vcp_contraction_count"] = float(metrics.contraction_count)
    out.at[out.index[last], "vcp_max_depth_pct"]     = float(metrics.max_depth_pct)
    out.at[out.index[last], "vcp_final_depth_pct"]   = float(metrics.final_depth_pct)
    out.at[out.index[last], "vcp_vol_ratio"]          = float(metrics.vol_contraction_ratio)
    out.at[out.index[last], "vcp_base_weeks"]         = float(metrics.base_length_weeks)
    out.at[out.index[last], "vcp_is_valid"]           = bool(metrics.is_valid_vcp)
    out.at[out.index[last], "vcp_fail_reason"]        = metrics.fail_reason

    return out
