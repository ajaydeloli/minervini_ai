"""
ingestion/universe_loader.py
────────────────────────────
Unified symbol resolver — the single place where all symbol sources
(universe YAML, SQLite watchlist, CLI file, CLI inline) are merged
and deduplicated before a pipeline run.

Design mandate (PROJECT_DESIGN.md §6.5 — The Two-List Model):
  - Watchlist (SQLite) and Universe (universe.yaml) are NEVER conflated.
  - They are kept as separate lists and merged at call time.
  - Watchlist symbols always appear FIRST in the 'all' list.
  - cli_symbols completely overrides all other sources when provided.
  - cli_watchlist_file symbols are persisted into SQLite before the merge.
  - All returned lists are deduplicated and uppercase.

Anti-pattern explicitly avoided:
  "Watchlist = universe" — conflating the two causes confusing UX and
  scope bugs. (PROJECT_DESIGN.md §19.2)

Public API:
    RunSymbols              — dataclass: watchlist, universe, all, scope
    resolve_symbols()       — main entry point for the pipeline runner
    load_watchlist_file()   — parse .csv / .json / .xlsx / .txt → list[str]
    validate_symbol()       — NSE symbol format check → bool
    load_universe_yaml()    — parse universe.yaml → list[str]
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from utils.exceptions import InvalidSymbolError, UniverseLoadError, WatchlistParseError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Symbol validation
# ─────────────────────────────────────────────────────────────────────────────

_MIN_SYMBOL_LEN = 1
_MAX_SYMBOL_LEN = 20


def validate_symbol(symbol: str) -> bool:
    """
    Return True if *symbol* is a valid NSE equity symbol.

    Rules (PROJECT_DESIGN.md §6.5):
        - Length: 1–20 characters
        - Characters: uppercase A-Z and digits 0-9 only
        - No spaces, dots, hyphens, or any other special characters

    Benchmark index tickers (e.g. "^CRSLDX") are intentionally excluded
    because they are not tradeable equity symbols — the benchmark tickers
    are handled directly in ingestion/base.py.

    Args:
        symbol: String to validate (already stripped / uppercased by caller).

    Returns:
        True if valid, False otherwise.
    """
    if not symbol:
        return False
    if not (_MIN_SYMBOL_LEN <= len(symbol) <= _MAX_SYMBOL_LEN):
        return False
    return all(c.isalpha() and c.isupper() or c.isdigit() for c in symbol)


# ─────────────────────────────────────────────────────────────────────────────
# Universe YAML loader
# ─────────────────────────────────────────────────────────────────────────────

# Placeholder top-20 for modes that require an external API/scrape
# (nifty500, nse_all) — replaced by real downloader in Phase 1 completion.
_NIFTY500_PLACEHOLDER: list[str] = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "SBIN", "BHARTIARTL", "BAJFINANCE", "HINDUNILVR", "ITC",
    "KOTAKBANK", "LT", "AXISBANK", "WIPRO", "ONGC",
    "NTPC", "POWERGRID", "MARUTI", "TATAMOTORS", "SUNPHARMA",
]


def load_universe_yaml(
    path: str | Path = "config/universe.yaml",
) -> list[str]:
    """
    Parse universe.yaml and return a sorted, deduplicated list of
    uppercase NSE symbols.

    Mode handling:
        "list"     → returns the explicit 'symbols' list from the file
        "nifty500" → placeholder top-20 (real scraper added in a later phase)
        "nse_all"  → same placeholder (real scraper added in a later phase)

    Args:
        path: Path to the universe YAML file.

    Returns:
        Sorted list of validated uppercase symbols.

    Raises:
        UniverseLoadError: If the file is missing, unreadable, YAML is
                           malformed, mode is unrecognised, or the
                           resulting symbol list is empty.
    """
    path = Path(path)

    if not path.exists():
        raise UniverseLoadError(
            f"Universe file not found: '{path}'. "
            "Create it or pass a different path to load_universe_yaml()."
        )

    try:
        with path.open(encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise UniverseLoadError(
            f"Cannot parse universe YAML '{path}': {exc}"
        ) from exc
    except OSError as exc:
        raise UniverseLoadError(
            f"Cannot read universe file '{path}': {exc}"
        ) from exc

    mode = str(config.get("mode", "list")).strip().lower()
    log.debug("Universe YAML loaded", path=str(path), mode=mode)

    if mode == "list":
        raw: list = config.get("symbols", []) or []
        symbols = _clean_symbol_list(raw, source=str(path))

    elif mode in ("nifty500", "nse_all"):
        log.warning(
            f"Universe mode '{mode}' is a placeholder — returning top-20 hardcoded list. "
            "Implement a real scraper to expand this.",
            mode=mode,
        )
        symbols = sorted(_NIFTY500_PLACEHOLDER)

    else:
        raise UniverseLoadError(
            f"Unrecognised universe mode '{mode}' in '{path}'. "
            "Valid modes: 'list', 'nifty500', 'nse_all'."
        )

    if not symbols:
        raise UniverseLoadError(
            f"Universe file '{path}' (mode='{mode}') produced an empty symbol list. "
            "Add at least one symbol before running."
        )

    log.debug("Universe symbols loaded", count=len(symbols), mode=mode)
    return symbols


def _clean_symbol_list(raw: list, source: str = "") -> list[str]:
    """
    Uppercase, strip, deduplicate, sort, and validate a raw list of
    symbol candidates.  Invalid entries are logged and skipped.

    Returns a sorted list of valid symbols.
    """
    seen: set[str] = set()
    valid: list[str] = []

    for entry in raw:
        if not entry:
            continue
        sym = str(entry).strip().upper()
        if not sym:
            continue
        if sym in seen:
            continue
        if not validate_symbol(sym):
            log.warning(
                "Invalid symbol skipped in universe list",
                symbol=sym,
                source=source,
            )
            continue
        seen.add(sym)
        valid.append(sym)

    return sorted(valid)


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist file parser
# ─────────────────────────────────────────────────────────────────────────────

def load_watchlist_file(path: Path) -> list[str]:
    """
    Parse a watchlist file and return a list of valid NSE symbols.

    Supported formats:
        .csv   — column named 'symbol' (case-insensitive), or first column
        .json  — JSON array of strings: ["RELIANCE", "TCS", ...]
        .xlsx  — first sheet; column named 'symbol' or column A
        .txt   — one symbol per line (blank lines and # comments ignored)

    Behaviour:
        - Each parsed symbol is uppercased and stripped.
        - Invalid symbols are logged with a WARNING and SKIPPED.
        - Valid symbols are deduplicated (order preserved, first occurrence wins).
        - Raises WatchlistParseError if the file is unreadable OR produces
          zero valid symbols after filtering.

    Args:
        path: Path to the watchlist file.

    Returns:
        Deduplicated list of valid uppercase symbols (order from file preserved).

    Raises:
        WatchlistParseError: If the file cannot be read, has an unsupported
                             extension, or contains zero valid symbols.
    """
    path = Path(path)

    if not path.exists():
        raise WatchlistParseError(
            path=str(path),
            reason=f"File not found: '{path}'",
        )

    suffix = path.suffix.lower()

    try:
        if suffix == ".csv":
            raw_symbols = _parse_csv(path)
        elif suffix == ".json":
            raw_symbols = _parse_json(path)
        elif suffix in (".xlsx", ".xls"):
            raw_symbols = _parse_xlsx(path)
        elif suffix == ".txt":
            raw_symbols = _parse_txt(path)
        else:
            raise WatchlistParseError(
                path=str(path),
                reason=(
                    f"Unsupported file extension '{suffix}'. "
                    "Supported: .csv, .json, .xlsx, .txt"
                ),
            )
    except WatchlistParseError:
        raise
    except Exception as exc:
        raise WatchlistParseError(
            path=str(path),
            reason=f"Unexpected error reading file: {exc}",
        ) from exc

    # Validate, deduplicate, and collect
    valid: list[str] = []
    seen: set[str] = set()
    invalid_count = 0

    for raw in raw_symbols:
        if not raw:
            continue
        sym = str(raw).strip().upper()
        if not sym:
            continue
        if sym in seen:
            continue
        if not validate_symbol(sym):
            log.warning(
                "Invalid symbol skipped in watchlist file",
                symbol=sym,
                file=str(path),
            )
            invalid_count += 1
            continue
        seen.add(sym)
        valid.append(sym)

    log.debug(
        "Watchlist file parsed",
        file=str(path),
        valid=len(valid),
        invalid=invalid_count,
    )

    if not valid:
        raise WatchlistParseError(
            path=str(path),
            reason=(
                f"File contains no valid NSE symbols after filtering "
                f"({invalid_count} invalid entries skipped). "
                "Symbols must be 1–20 uppercase letters/digits (e.g. RELIANCE, TCS)."
            ),
        )

    return valid


# ── Format-specific parsers ───────────────────────────────────────────────────

def _parse_csv(path: Path) -> list[str]:
    """
    Parse a CSV file.  Looks for a column named 'symbol' (case-insensitive).
    Falls back to the first column if 'symbol' column is absent.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")  # strip BOM if present
    except OSError as exc:
        raise WatchlistParseError(path=str(path), reason=f"Cannot read file: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))

    # Find the symbol column (case-insensitive)
    fieldnames = reader.fieldnames or []
    symbol_col: str | None = None
    for fn in fieldnames:
        if fn.strip().lower() == "symbol":
            symbol_col = fn
            break

    symbols: list[str] = []

    if symbol_col is not None:
        for row in reader:
            val = row.get(symbol_col, "")
            if val:
                symbols.append(val.strip())
    else:
        # No 'symbol' header — use first column value from every row
        # Re-parse without DictReader to get positional access
        reader2 = csv.reader(io.StringIO(text))
        for i, row in enumerate(reader2):
            if i == 0 and row and row[0].strip().lower() in ("symbol", "ticker", "scrip"):
                # It's a header row — skip it
                continue
            if row:
                symbols.append(row[0].strip())

    return symbols


def _parse_json(path: Path) -> list[str]:
    """
    Parse a JSON file.  Expected format: a top-level array of strings.
    Also accepts {"symbols": [...]} as a convenience wrapper.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WatchlistParseError(path=str(path), reason=f"Cannot read file: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WatchlistParseError(
            path=str(path),
            reason=f"Invalid JSON: {exc}",
        ) from exc

    if isinstance(data, list):
        return [str(item) for item in data if item]

    if isinstance(data, dict):
        # Accept {"symbols": [...]} wrapper
        items = data.get("symbols") or data.get("watchlist") or []
        if isinstance(items, list):
            return [str(item) for item in items if item]

    raise WatchlistParseError(
        path=str(path),
        reason=(
            "JSON must be an array of strings (e.g. [\"RELIANCE\", \"TCS\"]) "
            "or an object with a 'symbols' key."
        ),
    )


def _parse_xlsx(path: Path) -> list[str]:
    """
    Parse the first sheet of an Excel file.
    Looks for a column named 'symbol' (case-insensitive); falls back to
    column A (index 0).

    Requires openpyxl (already in requirements.txt).
    """
    try:
        import openpyxl
    except ImportError as exc:
        raise WatchlistParseError(
            path=str(path),
            reason="openpyxl is required to read .xlsx files. Install it with: pip install openpyxl",
        ) from exc

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise WatchlistParseError(
            path=str(path),
            reason=f"Cannot open Excel file: {exc}",
        ) from exc

    ws = wb.active  # first sheet
    symbols: list[str] = []

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return symbols

    # Detect header row — check if first row contains a 'symbol' column
    header_row = [str(cell).strip().lower() if cell is not None else "" for cell in rows[0]]
    symbol_col_idx: int | None = None
    for idx, h in enumerate(header_row):
        if h == "symbol":
            symbol_col_idx = idx
            break

    start_row = 0
    if symbol_col_idx is not None:
        # First row is a header
        start_row = 1
    else:
        # No header — use column A (index 0)
        symbol_col_idx = 0
        # Check if the very first cell looks like a header (non-symbol string)
        first_cell = str(rows[0][0]).strip().upper() if rows[0] else ""
        if first_cell.lower() in ("symbol", "ticker", "scrip", "name"):
            start_row = 1

    for row in rows[start_row:]:
        if symbol_col_idx < len(row) and row[symbol_col_idx] is not None:
            val = str(row[symbol_col_idx]).strip()
            if val:
                symbols.append(val)

    wb.close()
    return symbols


def _parse_txt(path: Path) -> list[str]:
    """
    Parse a plain text file.  One symbol per line.
    Lines starting with '#' are treated as comments and ignored.
    Blank lines are ignored.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise WatchlistParseError(path=str(path), reason=f"Cannot read file: {exc}") from exc

    symbols: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Handle comma-separated on a single line (common user mistake)
        parts = stripped.split(",")
        for part in parts:
            val = part.strip()
            if val:
                symbols.append(val)

    return symbols


# ─────────────────────────────────────────────────────────────────────────────
# RunSymbols dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RunSymbols:
    """
    The resolved symbol lists for a single pipeline run.

    Attributes:
        watchlist: Symbols from the SQLite watchlist table + any CLI file input.
                   These are the user's personally curated symbols.
                   Scanned at priority — shown first in reports and alerts.

        universe:  Symbols from config/universe.yaml (or the Nifty 500 / NSE All
                   equivalent for non-'list' modes).
                   The broad scan population.

        all:       Deduplicated union of watchlist + universe.
                   Watchlist symbols appear FIRST (design mandate §6.5).
                   This is the list the pipeline iterates when scope='all'.

        scope:     The scope requested by the caller:
                   'all'        — scan universe + watchlist (default)
                   'universe'   — scan universe only (skip watchlist)
                   'watchlist'  — scan watchlist only (skip full universe scan)
    """
    watchlist: list[str] = field(default_factory=list)
    universe:  list[str] = field(default_factory=list)
    all:       list[str] = field(default_factory=list)
    scope:     str        = "all"

    def __post_init__(self) -> None:
        # Defensive: ensure all lists contain only uppercase strings
        self.watchlist = [s.upper() for s in self.watchlist]
        self.universe  = [s.upper() for s in self.universe]
        self.all       = [s.upper() for s in self.all]

    @property
    def symbols_to_scan(self) -> list[str]:
        """
        Convenience property: returns the correct symbol list for the current scope.
        Identical to self.all for scope='all', self.universe for scope='universe',
        self.watchlist for scope='watchlist'.
        """
        if self.scope == "universe":
            return self.universe
        if self.scope == "watchlist":
            return self.watchlist
        return self.all

    def __repr__(self) -> str:
        return (
            f"RunSymbols(scope={self.scope!r}, "
            f"watchlist={len(self.watchlist)}, "
            f"universe={len(self.universe)}, "
            f"all={len(self.all)})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main resolver
# ─────────────────────────────────────────────────────────────────────────────

def resolve_symbols(
    config_path: str | Path = "config/universe.yaml",
    cli_watchlist_file: Path | None = None,
    cli_symbols: list[str] | None = None,
    scope: Literal["all", "universe", "watchlist"] = "all",
) -> RunSymbols:
    """
    Resolve the final symbol lists for a pipeline run.

    Priority and merge logic (PROJECT_DESIGN.md §6.5):

        1. cli_symbols (--symbols flag) — highest priority.
           When provided, ALL other sources are ignored.  The symbols
           are returned as both watchlist and universe with scope='all'.
           This is the "quick ad-hoc run" path.

        2. cli_watchlist_file (--watchlist flag).
           Parsed via load_watchlist_file() and PERSISTED into the SQLite
           watchlist table (added_via='file_upload') before the merge.
           The file symbols then appear in the watchlist list.

        3. SQLite watchlist table.
           Always included in the watchlist list unless scope='universe'.

        4. config/universe.yaml.
           Always included in the universe list unless scope='watchlist'.

    Deduplication:
        Watchlist symbols appear FIRST in the 'all' list.  Universe symbols
        that are already in the watchlist are not duplicated.

    Args:
        config_path:         Path to universe.yaml.
        cli_watchlist_file:  Optional path to a watchlist file (.csv / .json /
                             .xlsx / .txt) passed via --watchlist flag.
        cli_symbols:         Optional list of inline symbols passed via --symbols
                             flag (e.g. ["RELIANCE", "TCS"]).  Overrides all.
        scope:               "all" | "universe" | "watchlist"

    Returns:
        RunSymbols dataclass with .watchlist, .universe, .all, .scope.

    Raises:
        UniverseLoadError:  If universe.yaml cannot be loaded and scope != 'watchlist'.
        WatchlistParseError: If cli_watchlist_file is given but unparseable.
        InvalidSymbolError:  If a cli_symbols entry fails validation.
    """
    # ── Fast path: cli_symbols overrides everything ──────────────────────────
    if cli_symbols:
        validated: list[str] = []
        for raw in cli_symbols:
            sym = raw.strip().upper()
            if not validate_symbol(sym):
                raise InvalidSymbolError(
                    symbol=sym,
                    reason=(
                        "Symbol must be 1–20 uppercase letters and/or digits "
                        "(e.g. RELIANCE, TCS, DIXON). "
                        f"Got: '{sym}'"
                    ),
                )
            validated.append(sym)

        # Deduplicate, preserve order
        seen: set[str] = set()
        deduped: list[str] = []
        for s in validated:
            if s not in seen:
                seen.add(s)
                deduped.append(s)

        log.info(
            "resolve_symbols: cli_symbols override active",
            symbols=deduped,
            scope="all",
        )
        return RunSymbols(
            watchlist=deduped,
            universe=deduped,
            all=deduped,
            scope="all",
        )

    # ── Step 1: Parse + persist cli_watchlist_file ───────────────────────────
    file_symbols: list[str] = []
    if cli_watchlist_file is not None:
        file_symbols = load_watchlist_file(Path(cli_watchlist_file))
        log.info(
            "Watchlist file parsed — persisting to SQLite",
            file=str(cli_watchlist_file),
            symbols=len(file_symbols),
        )
        _persist_to_watchlist(file_symbols, added_via="file_upload")

    # ── Step 2: Load SQLite watchlist ─────────────────────────────────────────
    sqlite_symbols: list[str] = []
    if scope != "universe":
        sqlite_symbols = _load_sqlite_watchlist()
        log.debug("SQLite watchlist loaded", count=len(sqlite_symbols))

    # Merge file_symbols + sqlite_symbols into a single ordered watchlist.
    # file_symbols go FIRST (most recently added intent), then any SQLite
    # symbols not already in that list.
    watchlist = _ordered_union(file_symbols, sqlite_symbols)

    # ── Step 3: Load universe.yaml ────────────────────────────────────────────
    universe: list[str] = []
    if scope != "watchlist":
        universe = load_universe_yaml(config_path)
        log.debug("Universe loaded", count=len(universe))

    # ── Step 4: Build the 'all' list — watchlist first, no duplicates ─────────
    all_symbols = _ordered_union(watchlist, universe)

    result = RunSymbols(
        watchlist=watchlist,
        universe=universe,
        all=all_symbols,
        scope=scope,
    )

    log.info(
        "resolve_symbols complete",
        scope=scope,
        watchlist=len(result.watchlist),
        universe=len(result.universe),
        all=len(result.all),
        has_file=cli_watchlist_file is not None,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ordered_union(first: list[str], second: list[str]) -> list[str]:
    """
    Return a deduplicated list containing all elements of *first* followed
    by elements of *second* that are not already in *first*.

    Preserves the order within each source.

    Examples:
        _ordered_union(["TCS", "RELIANCE"], ["RELIANCE", "INFY"])
        → ["TCS", "RELIANCE", "INFY"]
    """
    seen: set[str] = set()
    result: list[str] = []
    for sym in first:
        if sym not in seen:
            seen.add(sym)
            result.append(sym)
    for sym in second:
        if sym not in seen:
            seen.add(sym)
            result.append(sym)
    return result


def _load_sqlite_watchlist() -> list[str]:
    """
    Load symbols from the SQLite watchlist table.

    Returns an empty list (not an error) if the database does not exist
    yet or the watchlist table is empty — the system must work on first
    run before any watchlist has been populated.
    """
    try:
        from storage.sqlite_store import get_watchlist_symbols
        return get_watchlist_symbols(sort_by="added_at")
    except Exception as exc:
        # Database may not exist yet on the very first run — not a fatal error.
        log.warning(
            "Could not load SQLite watchlist — using empty list",
            reason=str(exc),
        )
        return []


def _persist_to_watchlist(symbols: list[str], added_via: str = "file_upload") -> None:
    """
    Persist *symbols* into the SQLite watchlist table.

    Symbols that already exist are skipped silently (bulk_add_symbols uses
    ON CONFLICT DO NOTHING).  Failures are logged as warnings — a broken DB
    write must not abort the pipeline run.
    """
    if not symbols:
        return
    try:
        from storage.sqlite_store import bulk_add_symbols
        result = bulk_add_symbols(symbols, added_via=added_via)  # type: ignore[arg-type]
        log.debug(
            "Watchlist file symbols persisted",
            added=result.get("added", 0),
            skipped=result.get("skipped", 0),
            via=added_via,
        )
    except Exception as exc:
        log.warning(
            "Failed to persist watchlist file symbols to SQLite — continuing without persistence",
            reason=str(exc),
            symbol_count=len(symbols),
        )
