"""
tests/unit/test_universe_loader.py
───────────────────────────────────
Unit tests for ingestion/universe_loader.py.

Coverage:
    TestValidateSymbol       — validate_symbol()
    TestLoadUniverseYaml     — load_universe_yaml()
    TestLoadWatchlistFile    — load_watchlist_file() (CSV / JSON / TXT / XLSX)
    TestResolveSymbols       — resolve_symbols()
    TestRunSymbolsToScan     — RunSymbols.symbols_to_scan property
"""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest
import yaml

import storage.sqlite_store as ss
from ingestion.universe_loader import (
    RunSymbols,
    load_universe_yaml,
    load_watchlist_file,
    resolve_symbols,
    validate_symbol,
)
from utils.exceptions import InvalidSymbolError, UniverseLoadError, WatchlistParseError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_yaml(tmp_path: Path, data: dict, filename: str = "universe.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _write_txt(tmp_path: Path, content: str, filename: str = "watchlist.txt") -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def _write_csv(tmp_path: Path, content: str, filename: str = "watchlist.csv") -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def _write_json(tmp_path: Path, data, filename: str = "watchlist.json") -> Path:
    p = tmp_path / filename
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_xlsx(tmp_path: Path, rows: list[list], filename: str = "watchlist.xlsx") -> Path:
    p = tmp_path / filename
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(str(p))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# TestValidateSymbol
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateSymbol:
    # ── valid symbols ────────────────────────────────────────────────────────

    def test_valid_reliance(self):
        assert validate_symbol("RELIANCE") is True

    def test_valid_tcs(self):
        assert validate_symbol("TCS") is True

    def test_valid_dixon(self):
        assert validate_symbol("DIXON") is True

    def test_valid_single_char(self):
        assert validate_symbol("A") is True

    def test_valid_alphanumeric(self):
        assert validate_symbol("ABC123") is True

    def test_valid_max_length(self):
        assert validate_symbol("A" * 20) is True

    def test_valid_digits_only(self):
        # Implementation: (c.isalpha() and c.isupper()) or c.isdigit()
        # Each digit satisfies c.isdigit() → digits-only is VALID per implementation
        assert validate_symbol("123") is True

    # ── invalid symbols ──────────────────────────────────────────────────────

    def test_invalid_empty_string(self):
        assert validate_symbol("") is False

    def test_invalid_whitespace_only(self):
        assert validate_symbol(" ") is False

    def test_invalid_lowercase(self):
        assert validate_symbol("reliance") is False

    def test_invalid_space_within(self):
        assert validate_symbol("RELI ANCE") is False

    def test_invalid_hyphen(self):
        assert validate_symbol("RELI-ANCE") is False

    def test_invalid_caret_prefix(self):
        assert validate_symbol("^CRSLDX") is False

    def test_invalid_too_long(self):
        assert validate_symbol("A" * 21) is False

    def test_invalid_mixed_case(self):
        assert validate_symbol("Reliance") is False


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadUniverseYaml
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadUniverseYaml:
    def test_valid_list_mode_sorted(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "list", "symbols": ["TCS", "RELIANCE", "INFY"]})
        result = load_universe_yaml(p)
        assert result == sorted(["TCS", "RELIANCE", "INFY"])

    def test_list_mode_deduplication(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "list", "symbols": ["TCS", "TCS", "RELIANCE"]})
        result = load_universe_yaml(p)
        assert result.count("TCS") == 1
        assert len(result) == 2

    def test_list_mode_invalid_symbols_skipped(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "list", "symbols": ["RELIANCE", "bad-sym", "TCS"]})
        result = load_universe_yaml(p)
        assert "RELIANCE" in result
        assert "TCS" in result
        assert "BAD-SYM" not in result

    def test_list_mode_returns_uppercase(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "list", "symbols": ["reliance", "tcs"]})
        result = load_universe_yaml(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_nifty500_mode_returns_nonempty(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "nifty500"})
        result = load_universe_yaml(p)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_nse_all_mode_returns_nonempty(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "nse_all"})
        result = load_universe_yaml(p)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_unknown_mode_raises(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "foobar"})
        with pytest.raises(UniverseLoadError):
            load_universe_yaml(p)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(UniverseLoadError):
            load_universe_yaml(tmp_path / "nonexistent.yaml")

    def test_empty_symbols_list_raises(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "list", "symbols": []})
        with pytest.raises(UniverseLoadError):
            load_universe_yaml(p)

    def test_malformed_yaml_raises(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("mode: list\nsymbols: [RELIANCE\n  - TCS", encoding="utf-8")
        with pytest.raises(UniverseLoadError):
            load_universe_yaml(p)

    def test_all_invalid_symbols_in_list_raises(self, tmp_path: Path):
        p = _write_yaml(tmp_path, {"mode": "list", "symbols": ["bad-sym", "123!!"]})
        with pytest.raises(UniverseLoadError):
            load_universe_yaml(p)


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadWatchlistFile — CSV
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWatchlistFileCsv:
    def test_symbol_header_column(self, tmp_path: Path):
        p = _write_csv(tmp_path, "symbol\nRELIANCE\nTCS\n")
        result = load_watchlist_file(p)
        assert result == ["RELIANCE", "TCS"]

    def test_no_header_first_column_used(self, tmp_path: Path):
        # No 'symbol'/'ticker'/'scrip' header → every row treated as data
        p = _write_csv(tmp_path, "RELIANCE,somedata\nTCS,moredata\n")
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_mixed_case_uppercased(self, tmp_path: Path):
        p = _write_csv(tmp_path, "symbol\nreliance\ntcs\n")
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_blank_rows_ignored(self, tmp_path: Path):
        p = _write_csv(tmp_path, "symbol\nRELIANCE\n\nTCS\n")
        result = load_watchlist_file(p)
        assert len(result) == 2

    def test_invalid_symbols_skipped(self, tmp_path: Path):
        p = _write_csv(tmp_path, "symbol\nRELIANCE\nbad-sym\nTCS\n")
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result
        assert len(result) == 2

    def test_all_invalid_symbols_raises(self, tmp_path: Path):
        p = _write_csv(tmp_path, "symbol\nbad-sym\nanother!!\n")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(p)

    def test_deduplication_order_preserved(self, tmp_path: Path):
        p = _write_csv(tmp_path, "symbol\nRELIANCE\nTCS\nRELIANCE\n")
        result = load_watchlist_file(p)
        assert result.count("RELIANCE") == 1
        assert result[0] == "RELIANCE"


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadWatchlistFile — JSON
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWatchlistFileJson:
    def test_json_array(self, tmp_path: Path):
        p = _write_json(tmp_path, ["RELIANCE", "TCS"])
        result = load_watchlist_file(p)
        assert result == ["RELIANCE", "TCS"]

    def test_json_object_symbols_key(self, tmp_path: Path):
        p = _write_json(tmp_path, {"symbols": ["RELIANCE", "TCS"]})
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_json_object_watchlist_key(self, tmp_path: Path):
        p = _write_json(tmp_path, {"watchlist": ["RELIANCE"]})
        result = load_watchlist_file(p)
        assert "RELIANCE" in result

    def test_invalid_json_raises(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(p)

    def test_json_array_of_integers_handled(self, tmp_path: Path):
        # Single-digit integers convert to "1", "2", "3" → each passes validate_symbol
        # (digits satisfy c.isdigit()).  Result is a non-empty list of digit strings.
        p = _write_json(tmp_path, [1, 2, 3])
        result = load_watchlist_file(p)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_empty_json_array_raises(self, tmp_path: Path):
        p = _write_json(tmp_path, [])
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(p)

    def test_json_array_mixed_case_uppercased(self, tmp_path: Path):
        p = _write_json(tmp_path, ["reliance", "tcs"])
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_deduplication_json(self, tmp_path: Path):
        p = _write_json(tmp_path, ["RELIANCE", "TCS", "RELIANCE"])
        result = load_watchlist_file(p)
        assert result.count("RELIANCE") == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadWatchlistFile — TXT
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWatchlistFileTxt:
    def test_one_symbol_per_line(self, tmp_path: Path):
        p = _write_txt(tmp_path, "RELIANCE\nTCS\nINFY\n")
        result = load_watchlist_file(p)
        assert set(result) == {"RELIANCE", "TCS", "INFY"}

    def test_comment_lines_ignored(self, tmp_path: Path):
        p = _write_txt(tmp_path, "# this is a comment\nRELIANCE\nTCS\n")
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result
        assert len(result) == 2

    def test_blank_lines_ignored(self, tmp_path: Path):
        p = _write_txt(tmp_path, "RELIANCE\n\nTCS\n\n")
        result = load_watchlist_file(p)
        assert len(result) == 2

    def test_comma_separated_on_one_line(self, tmp_path: Path):
        p = _write_txt(tmp_path, "RELIANCE,TCS,INFY\n")
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result
        assert "INFY" in result

    def test_all_invalid_raises(self, tmp_path: Path):
        p = _write_txt(tmp_path, "bad-sym\nanother!!\n")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(p)

    def test_deduplication_txt(self, tmp_path: Path):
        p = _write_txt(tmp_path, "RELIANCE\nTCS\nRELIANCE\n")
        result = load_watchlist_file(p)
        assert result.count("RELIANCE") == 1
        assert result[0] == "RELIANCE"


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadWatchlistFile — XLSX
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWatchlistFileXlsx:
    def test_symbol_header_column(self, tmp_path: Path):
        p = _write_xlsx(tmp_path, [["symbol"], ["RELIANCE"], ["TCS"]])
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_no_header_column_a(self, tmp_path: Path):
        p = _write_xlsx(tmp_path, [["RELIANCE"], ["TCS"]])
        result = load_watchlist_file(p)
        assert "RELIANCE" in result
        assert "TCS" in result

    def test_empty_sheet_raises(self, tmp_path: Path):
        p = _write_xlsx(tmp_path, [])
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(p)

    def test_deduplication_xlsx(self, tmp_path: Path):
        p = _write_xlsx(tmp_path, [["symbol"], ["RELIANCE"], ["TCS"], ["RELIANCE"]])
        result = load_watchlist_file(p)
        assert result.count("RELIANCE") == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestLoadWatchlistFile — common (all formats)
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadWatchlistFileCommon:
    def test_nonexistent_file_raises(self, tmp_path: Path):
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(tmp_path / "ghost.csv")

    def test_unsupported_extension_raises(self, tmp_path: Path):
        p = tmp_path / "watchlist.pdf"
        p.write_text("RELIANCE\n", encoding="utf-8")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(p)


# ─────────────────────────────────────────────────────────────────────────────
# TestResolveSymbols
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveSymbols:
    """Tests for resolve_symbols().  Uses tmp_db_path fixture + monkeypatch."""

    def _universe_yaml(self, tmp_path: Path, symbols: list[str] | None = None) -> Path:
        syms = symbols if symbols is not None else ["HDFCBANK", "ICICIBANK", "SBIN"]
        return _write_yaml(tmp_path, {"mode": "list", "symbols": syms})

    # ── cli_symbols override ──────────────────────────────────────────────────

    def test_cli_symbols_overrides_all(self, tmp_path: Path, tmp_db_path: Path, monkeypatch):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        cfg = self._universe_yaml(tmp_path)
        result = resolve_symbols(cfg, cli_symbols=["RELIANCE", "TCS"])
        assert result.all == ["RELIANCE", "TCS"]

    def test_cli_symbols_invalid_raises(self, tmp_path: Path, tmp_db_path: Path, monkeypatch):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        cfg = self._universe_yaml(tmp_path)
        with pytest.raises(InvalidSymbolError):
            resolve_symbols(cfg, cli_symbols=["RELI-ANCE"])

    def test_cli_symbols_deduplication(self, tmp_path: Path, tmp_db_path: Path, monkeypatch):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        cfg = self._universe_yaml(tmp_path)
        result = resolve_symbols(cfg, cli_symbols=["RELIANCE", "TCS", "RELIANCE"])
        assert result.all.count("RELIANCE") == 1
        assert len(result.all) == 2

    # ── scope handling ────────────────────────────────────────────────────────

    def test_scope_watchlist_returns_only_watchlist(self, tmp_path: Path, tmp_db_path: Path, monkeypatch):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        ss.add_symbol("RELIANCE", added_via="cli")
        cfg = self._universe_yaml(tmp_path)
        result = resolve_symbols(cfg, scope="watchlist")
        assert "RELIANCE" in result.watchlist
        assert result.universe == []

    def test_scope_universe_returns_only_universe(self, tmp_path: Path, tmp_db_path: Path, monkeypatch):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        ss.add_symbol("RELIANCE", added_via="cli")
        cfg = self._universe_yaml(tmp_path, symbols=["HDFCBANK", "SBIN"])
        result = resolve_symbols(cfg, scope="universe")
        assert result.watchlist == []
        assert "HDFCBANK" in result.universe
        assert "SBIN" in result.universe

    def test_scope_all_watchlist_first(self, tmp_path: Path, tmp_db_path: Path, monkeypatch):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        ss.add_symbol("WIPRO", added_via="cli")
        cfg = self._universe_yaml(tmp_path, symbols=["INFY", "TCS"])
        result = resolve_symbols(cfg, scope="all")
        # Watchlist symbol must appear before universe-only symbols
        wipro_idx = result.all.index("WIPRO")
        infy_idx = result.all.index("INFY")
        assert wipro_idx < infy_idx

    # ── cli_watchlist_file ────────────────────────────────────────────────────

    def test_cli_watchlist_file_persisted_and_in_watchlist(
        self, tmp_path: Path, tmp_db_path: Path, monkeypatch
    ):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        wl_file = _write_csv(tmp_path, "symbol\nDIXON\nVOLTAS\n", filename="wl.csv")
        cfg = self._universe_yaml(tmp_path)
        result = resolve_symbols(cfg, cli_watchlist_file=wl_file)
        assert "DIXON" in result.watchlist
        assert "VOLTAS" in result.watchlist
        # Symbols must also be persisted in SQLite
        assert ss.symbol_in_watchlist("DIXON")
        assert ss.symbol_in_watchlist("VOLTAS")

    # ── empty watchlist + scope="watchlist" ───────────────────────────────────

    def test_empty_watchlist_scope_watchlist_returns_empty(
        self, tmp_path: Path, tmp_db_path: Path, monkeypatch
    ):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        cfg = self._universe_yaml(tmp_path)
        # No symbols added to watchlist — resolve should return empty watchlist
        result = resolve_symbols(cfg, scope="watchlist")
        assert result.watchlist == []

    # ── overlap: same symbol in watchlist and universe ────────────────────────

    def test_overlap_symbol_appears_once_in_all(
        self, tmp_path: Path, tmp_db_path: Path, monkeypatch
    ):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        ss.add_symbol("HDFCBANK", added_via="cli")
        # Universe also contains HDFCBANK
        cfg = self._universe_yaml(tmp_path, symbols=["HDFCBANK", "SBIN"])
        result = resolve_symbols(cfg, scope="all")
        assert result.all.count("HDFCBANK") == 1

    def test_overlap_symbol_at_watchlist_position(
        self, tmp_path: Path, tmp_db_path: Path, monkeypatch
    ):
        monkeypatch.setattr(ss, "_db_path", tmp_db_path)
        ss.add_symbol("HDFCBANK", added_via="cli")
        cfg = self._universe_yaml(tmp_path, symbols=["SBIN", "HDFCBANK"])
        result = resolve_symbols(cfg, scope="all")
        # HDFCBANK is in watchlist, so it comes before SBIN (universe-only)
        assert result.all.index("HDFCBANK") < result.all.index("SBIN")


# ─────────────────────────────────────────────────────────────────────────────
# TestRunSymbolsToScan
# ─────────────────────────────────────────────────────────────────────────────

class TestRunSymbolsToScan:
    def _make(self, watchlist, universe, scope):
        all_syms = list(dict.fromkeys(watchlist + [s for s in universe if s not in watchlist]))
        return RunSymbols(watchlist=watchlist, universe=universe, all=all_syms, scope=scope)

    def test_scope_all_returns_all(self):
        rs = self._make(["RELIANCE"], ["TCS"], scope="all")
        assert rs.symbols_to_scan == rs.all

    def test_scope_universe_returns_universe(self):
        rs = self._make(["RELIANCE"], ["TCS", "INFY"], scope="universe")
        assert rs.symbols_to_scan == rs.universe

    def test_scope_watchlist_returns_watchlist(self):
        rs = self._make(["RELIANCE", "DIXON"], ["TCS"], scope="watchlist")
        assert rs.symbols_to_scan == rs.watchlist

    def test_scope_all_includes_both_sources(self):
        rs = self._make(["RELIANCE"], ["TCS", "INFY"], scope="all")
        assert "RELIANCE" in rs.symbols_to_scan
        assert "TCS" in rs.symbols_to_scan
        assert "INFY" in rs.symbols_to_scan
