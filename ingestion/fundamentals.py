"""
ingestion/fundamentals.py
─────────────────────────
Screener.in scraper with 7-day JSON cache.

Design mandates (PROJECT_DESIGN.md §9.1–9.4):
  - Fundamentals are fetched from Screener.in via HTTP scraping.
  - Cached per symbol for 7 days (fundamentals change quarterly, not daily).
  - Runs AFTER the rule engine — only for Stage 2 + Trend Template candidates.
  - Returns None gracefully on ANY external failure — never crashes the pipeline.
  - All numeric values stored as float or None, never as strings.
  - Cache writes are atomic (write .tmp → os.replace to final path).

Public API:
    fetch_fundamentals(symbol, config, force_refresh=False) -> dict | None
    is_cache_valid(cache_path, cache_days) -> bool

Private:
    _fetch_from_screener(symbol) -> dict | None
    _parse_screener_html(html, symbol) -> dict
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from utils.exceptions import FundamentalsFetchError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_URL_CONSOLIDATED = "https://www.screener.in/company/{symbol}/consolidated/"
_URL_STANDALONE   = "https://www.screener.in/company/{symbol}/"

_HTTP_TIMEOUT    = 15        # seconds per request
_RETRY_ATTEMPTS  = 2         # total outer attempts (consolidated + standalone per attempt)
_RETRY_DELAY     = 3         # seconds between retry attempts

_IST = timezone(timedelta(hours=5, minutes=30))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Minervini-Fundamental-Fetcher/1.0; "
        "+https://github.com/minervini_ai)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Label → field name mapping for Screener.in key-ratios section.
# Keys are lowercased for case-insensitive matching.
_RATIO_LABEL_MAP: dict[str, str] = {
    "stock p/e":             "pe_ratio",
    "price to earning":      "pe_ratio",
    "p/e":                   "pe_ratio",
    "price to book value":   "pb_ratio",
    "price / book":          "pb_ratio",
    "p/b":                   "pb_ratio",
    "roe":                   "roe",
    "return on equity":      "roe",
    "roce":                  "roce",
    "return on capital":     "roce",
    "debt to equity":        "debt_to_equity",
    "d/e":                   "debt_to_equity",
}

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_float(text: str | None) -> float | None:
    """
    Parse a Screener.in display string to float.

    Strips: commas, %, 'Cr', 'cr', leading/trailing whitespace.
    Returns None on empty string, None input, or unparseable value.
    Never raises.
    """
    if text is None:
        return None
    cleaned = (
        text.strip()
        .replace(",", "")
        .replace("%", "")
        .replace("Cr", "")
        .replace("cr", "")
        .strip()
    )
    if not cleaned or cleaned in ("-", "—", "N/A", "NA"):
        return None
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _find_table_row(table, *label_fragments: str) -> list[str] | None:
    """
    Search a BeautifulSoup <table> for a row whose first cell text
    contains any of the label_fragments (case-insensitive).

    Returns the list of cell texts for that row, or None if not found.
    """
    if table is None:
        return None
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        first = cells[0].get_text(strip=True).lower()
        if any(frag.lower() in first for frag in label_fragments):
            return [c.get_text(strip=True) for c in cells]
    return None


def _numeric_cells(cells: list[str]) -> list[float]:
    """
    Given a list of cell text strings (first cell is the row label),
    return only the numeric values (skip label, skip non-numeric cells).
    """
    result: list[float] = []
    for cell in cells[1:]:          # skip label cell
        val = _to_float(cell)
        if val is not None:
            result.append(val)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_cache_valid(cache_path: Path, cache_days: int) -> bool:
    """
    Return True if *cache_path* exists and its 'fetched_at' timestamp is
    less than *cache_days* old.

    Args:
        cache_path:  Path to the cached {symbol}.json file.
        cache_days:  Maximum age in days before the cache is considered stale.

    Returns:
        True  — cache exists, is readable, and is within TTL.
        False — file missing, unreadable, corrupt, or expired.
    """
    if not cache_path.exists():
        return False
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        fetched_at_str = data.get("fetched_at", "")
        if not fetched_at_str:
            return False
        fetched_at = datetime.fromisoformat(fetched_at_str)
        now = datetime.now(tz=fetched_at.tzinfo or _IST)
        age_days = (now - fetched_at).total_seconds() / 86400.0
        return age_days < cache_days
    except Exception as exc:   # noqa: BLE001
        log.debug(
            "Cache validity check failed — treating as expired",
            path=str(cache_path),
            error=str(exc),
        )
        return False


def fetch_fundamentals(
    symbol: str,
    config: dict,
    force_refresh: bool = False,
) -> dict | None:
    """
    Fetch and cache fundamental data from Screener.in.

    Cache TTL is driven by config["fundamentals"]["cache_days"] (default 7).
    If fundamentals are disabled in config, returns None immediately without
    making any HTTP calls or reading the cache.

    Args:
        symbol:        NSE symbol string (e.g. "DIXON").
        config:        Parsed settings.yaml dict (must contain "fundamentals"
                       and "data" keys).
        force_refresh: If True, bypass cache and always re-fetch from web.

    Returns:
        dict with all fundamental fields on success.
        None on any external failure (network, parse error, disabled).
        Never raises to the caller.
    """
    # ── 1. Early exit if feature is disabled ─────────────────────────────────
    fund_cfg = config.get("fundamentals", {})
    if not fund_cfg.get("enabled", True):
        log.debug("Fundamentals disabled in config — skipping", symbol=symbol)
        return None

    cache_days: int = int(fund_cfg.get("cache_days", 7))

    # ── 2. Build cache path ───────────────────────────────────────────────────
    fundamentals_dir = Path(config["data"]["fundamentals_dir"])
    fundamentals_dir.mkdir(parents=True, exist_ok=True)
    cache_path = fundamentals_dir / f"{symbol}.json"

    # ── 3. Cache hit? ─────────────────────────────────────────────────────────
    if not force_refresh and is_cache_valid(cache_path, cache_days):
        log.debug("Fundamentals cache hit", symbol=symbol, path=str(cache_path))
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as exc:   # noqa: BLE001
            log.warning(
                "Cache read failed — will re-fetch",
                symbol=symbol,
                error=str(exc),
            )

    # ── 4. Fetch from Screener.in ─────────────────────────────────────────────
    data = _fetch_from_screener(symbol)
    if data is None:
        log.warning(
            "Fundamentals fetch returned None — pipeline continues without it",
            symbol=symbol,
        )
        return None

    # ── 5. Atomic cache write (tmp → replace) ─────────────────────────────────
    try:
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, cache_path)
        log.debug("Fundamentals cached", symbol=symbol, path=str(cache_path))
    except Exception as exc:   # noqa: BLE001
        log.warning(
            "Fundamentals cache write failed — data still returned in-memory",
            symbol=symbol,
            error=str(exc),
        )

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Private — HTTP layer
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_from_screener(symbol: str) -> dict | None:
    """
    Attempt to download the Screener.in company page for *symbol*.

    Strategy:
        outer loop: _RETRY_ATTEMPTS times total
        inner loop: consolidated URL first, standalone URL as fallback
        On first successful parse (200 + non-trivially-empty dict) → return.
        On 404 for consolidated → immediately try standalone (same attempt).
        On network error → retry after _RETRY_DELAY seconds.

    Returns:
        Parsed fundamentals dict (with 'symbol' and 'fetched_at' injected)
        on success.  None if all attempts/fallbacks fail.
    """
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        for url_template in (_URL_CONSOLIDATED, _URL_STANDALONE):
            url = url_template.format(symbol=symbol)
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
            except requests.RequestException as exc:
                log.warning(
                    "Screener.in network error",
                    symbol=symbol,
                    url=url,
                    attempt=attempt,
                    error=str(exc),
                )
                break   # network failure — skip inner loop, sleep, retry outer

            if resp.status_code == 404:
                log.debug("Screener.in 404 — trying standalone", symbol=symbol, url=url)
                continue   # try next URL template

            if resp.status_code != 200:
                log.warning(
                    "Screener.in unexpected HTTP status",
                    symbol=symbol,
                    url=url,
                    status=resp.status_code,
                )
                continue

            # ── Successful HTTP response — parse ──────────────────────────────
            try:
                data = _parse_screener_html(resp.text, symbol)
            except Exception as exc:   # noqa: BLE001
                log.warning(
                    "Screener.in HTML parse failed",
                    symbol=symbol,
                    url=url,
                    error=str(exc),
                )
                continue   # try fallback URL or next attempt

            data["symbol"]     = symbol
            data["fetched_at"] = datetime.now(_IST).isoformat()
            log.info(
                "Fundamentals fetched from Screener.in",
                symbol=symbol,
                url=url,
                attempt=attempt,
            )
            return data

        # If we still have retries left, wait before the next attempt.
        if attempt < _RETRY_ATTEMPTS:
            log.debug(
                "Screener.in retry pause",
                symbol=symbol,
                attempt=attempt,
                delay_sec=_RETRY_DELAY,
            )
            time.sleep(_RETRY_DELAY)

    log.warning(
        "All Screener.in fetch attempts exhausted",
        symbol=symbol,
        attempts=_RETRY_ATTEMPTS,
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Private — HTML parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_screener_html(html: str, symbol: str) -> dict:
    """
    Parse a Screener.in company page HTML into a fundamentals dict.

    Design rules:
        - Every sub-parse is wrapped in its own try/except.
        - A missing section or changed markup sets the affected field(s)
          to None — it never aborts the whole parse.
        - All numeric values are float or None.  Never strings.
        - eps_accelerating and fii_trend are derived fields computed
          from the raw series inside this function.

    Args:
        html:   Raw HTML text from the Screener.in company page.
        symbol: NSE symbol (used only for log context).

    Returns:
        dict with all 18 fundamental fields.  Any field that could not
        be extracted is None.
    """
    soup = BeautifulSoup(html, "html.parser")

    result: dict = {
        # Key ratios
        "pe_ratio":          None,
        "pb_ratio":          None,
        "roe":               None,
        "roce":              None,
        "debt_to_equity":    None,
        # Shareholding
        "promoter_holding":  None,
        "fii_holding_pct":   None,
        "fii_trend":         None,
        # EPS series
        "eps":               None,
        "eps_values":        [],
        "eps_growth_rates":  [],
        "eps_accelerating":  None,
        # Growth
        "sales_growth_yoy":  None,
        "profit_growth":     None,
        # Financials
        "latest_revenue":    None,
        "latest_profit":     None,
    }

    _parse_key_ratios(soup, symbol, result)
    _parse_quarterly_data(soup, symbol, result)
    _parse_annual_pl(soup, symbol, result)
    _parse_shareholding(soup, symbol, result)

    return result


# ── Sub-parsers (each is a no-raise void function that mutates result) ────────

def _parse_key_ratios(soup: BeautifulSoup, symbol: str, result: dict) -> None:
    """Parse the #top-ratios section for PE, PB, ROE, ROCE, D/E."""
    try:
        section = soup.find(id="top-ratios") or soup.find(
            "section", class_=lambda c: c and "top-ratios" in c
        )
        if section is None:
            log.debug("Key-ratios section not found", symbol=symbol)
            return

        for li in section.find_all("li"):
            try:
                name_el  = li.find("span", class_="name") or li.find("span", class_="field-name")
                value_el = (
                    li.find("span", class_="number")
                    or li.find("span", class_="value")
                    or li.find("span", class_="nowrap")
                )
                if name_el is None or value_el is None:
                    continue

                label = name_el.get_text(strip=True).lower()
                # The numeric span may be nested inside a nowrap span
                inner_num = value_el.find("span", class_="number")
                raw_val   = (inner_num or value_el).get_text(strip=True)

                for key_fragment, field in _RATIO_LABEL_MAP.items():
                    if key_fragment in label and result[field] is None:
                        result[field] = _to_float(raw_val)
                        break
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:   # noqa: BLE001
        log.debug("Key-ratios parse error", symbol=symbol, error=str(exc))


def _parse_quarterly_data(soup: BeautifulSoup, symbol: str, result: dict) -> None:
    """
    Parse the #quarters section for:
        EPS values (last 4 quarters) → eps, eps_values, eps_growth_rates, eps_accelerating
        Latest quarterly Revenue     → latest_revenue
        Latest quarterly Net Profit  → latest_profit
    """
    try:
        section = soup.find(id="quarters")
        if section is None:
            log.debug("Quarters section not found", symbol=symbol)
            return

        table = section.find("table")
        if table is None:
            return

        # ── EPS ───────────────────────────────────────────────────────────────
        try:
            eps_row = _find_table_row(table, "EPS in Rs", "Earnings per share", "EPS")
            if eps_row:
                all_vals = _numeric_cells(eps_row)
                # Take the last 4 available values (oldest → newest)
                eps_vals = all_vals[-4:] if len(all_vals) >= 4 else all_vals
                if eps_vals:
                    result["eps_values"] = eps_vals
                    result["eps"]        = eps_vals[-1]

                    # QoQ growth rates
                    rates: list[float] = []
                    for i in range(1, len(eps_vals)):
                        base = eps_vals[i - 1]
                        if base != 0:
                            rates.append(round((eps_vals[i] - base) / abs(base) * 100, 2))
                    result["eps_growth_rates"] = rates

                    if len(rates) >= 2:
                        result["eps_accelerating"] = rates[-1] > rates[-2]
                    elif len(rates) == 1:
                        result["eps_accelerating"] = None   # insufficient history
        except Exception as exc:   # noqa: BLE001
            log.debug("EPS quarterly parse error", symbol=symbol, error=str(exc))

        # ── Latest Revenue ────────────────────────────────────────────────────
        try:
            rev_row = _find_table_row(
                table, "Sales", "Revenue from Operations", "Net Sales", "Revenue"
            )
            if rev_row:
                vals = _numeric_cells(rev_row)
                result["latest_revenue"] = vals[-1] if vals else None
        except Exception as exc:   # noqa: BLE001
            log.debug("Revenue quarterly parse error", symbol=symbol, error=str(exc))

        # ── Latest Net Profit ─────────────────────────────────────────────────
        try:
            profit_row = _find_table_row(table, "Net Profit", "PAT", "Profit after tax")
            if profit_row:
                vals = _numeric_cells(profit_row)
                result["latest_profit"] = vals[-1] if vals else None
        except Exception as exc:   # noqa: BLE001
            log.debug("Net profit quarterly parse error", symbol=symbol, error=str(exc))

    except Exception as exc:   # noqa: BLE001
        log.debug("Quarterly section parse error", symbol=symbol, error=str(exc))


def _parse_annual_pl(soup: BeautifulSoup, symbol: str, result: dict) -> None:
    """
    Parse the #profit-loss section for YoY sales growth and profit growth.

    Uses the last 2 available annual values to compute YoY % change.
    """
    try:
        section = soup.find(id="profit-loss")
        if section is None:
            log.debug("Profit-loss section not found", symbol=symbol)
            return

        table = section.find("table")
        if table is None:
            return

        # ── Sales growth YoY ─────────────────────────────────────────────────
        try:
            sales_row = _find_table_row(
                table, "Sales", "Revenue from Operations", "Net Sales", "Revenue"
            )
            if sales_row:
                vals = _numeric_cells(sales_row)
                if len(vals) >= 2 and vals[-2] != 0:
                    result["sales_growth_yoy"] = round(
                        (vals[-1] - vals[-2]) / abs(vals[-2]) * 100, 2
                    )
        except Exception as exc:   # noqa: BLE001
            log.debug("Sales growth parse error", symbol=symbol, error=str(exc))

        # ── Profit growth YoY ─────────────────────────────────────────────────
        try:
            profit_row = _find_table_row(table, "Net Profit", "PAT", "Profit after tax")
            if profit_row:
                vals = _numeric_cells(profit_row)
                if len(vals) >= 2 and vals[-2] != 0:
                    result["profit_growth"] = round(
                        (vals[-1] - vals[-2]) / abs(vals[-2]) * 100, 2
                    )
        except Exception as exc:   # noqa: BLE001
            log.debug("Profit growth parse error", symbol=symbol, error=str(exc))

    except Exception as exc:   # noqa: BLE001
        log.debug("Annual P&L section parse error", symbol=symbol, error=str(exc))


def _parse_shareholding(soup: BeautifulSoup, symbol: str, result: dict) -> None:
    """
    Parse the #shareholding section for:
        promoter_holding  — latest quarter promoter % (float)
        fii_holding_pct   — latest quarter FII/FPI % (float)
        fii_trend         — "rising" | "flat" | "falling"

    FII trend classification:
        rising  — latest > previous by > 0.5 percentage points
        falling — latest < previous by > 0.5 percentage points
        flat    — change within ±0.5 pp
    """
    try:
        section = soup.find(id="shareholding")
        if section is None:
            log.debug("Shareholding section not found", symbol=symbol)
            return

        # Screener.in renders multiple tables in the shareholding section
        # (quarterly + yearly).  We use the first table found.
        table = section.find("table")
        if table is None:
            return

        # ── Promoter holding ──────────────────────────────────────────────────
        try:
            prom_row = _find_table_row(
                table, "Promoter", "Promoters", "Promoter & Promoter Group"
            )
            if prom_row:
                vals = _numeric_cells(prom_row)
                result["promoter_holding"] = vals[-1] if vals else None
        except Exception as exc:   # noqa: BLE001
            log.debug("Promoter holding parse error", symbol=symbol, error=str(exc))

        # ── FII / FPI holding ─────────────────────────────────────────────────
        try:
            fii_row = _find_table_row(
                table,
                "FII", "FPI", "Foreign Institutions", "Foreign Institutional",
                "FII / FPI", "FIIs",
            )
            if fii_row:
                vals = _numeric_cells(fii_row)
                if vals:
                    result["fii_holding_pct"] = vals[-1]

                    if len(vals) >= 2:
                        delta = vals[-1] - vals[-2]
                        if delta > 0.5:
                            result["fii_trend"] = "rising"
                        elif delta < -0.5:
                            result["fii_trend"] = "falling"
                        else:
                            result["fii_trend"] = "flat"
        except Exception as exc:   # noqa: BLE001
            log.debug("FII holding parse error", symbol=symbol, error=str(exc))

    except Exception as exc:   # noqa: BLE001
        log.debug("Shareholding section parse error", symbol=symbol, error=str(exc))
