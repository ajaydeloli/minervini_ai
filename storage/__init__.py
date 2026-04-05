"""
storage/__init__.py
───────────────────
Re-exports the public surface of the storage package so callers can write:

    from storage import read, append_row, init_db, add_symbol, log_run
"""

from storage.parquet_store import (
    read,
    read_tail,
    read_tail_at_least,
    read_date_range,
    write,
    append_row,
    append_dataframe,
    exists,
    row_count,
    last_date,
    deduplicate,
    is_corrupt,
    copy_safe,
    DuplicateDateError,
    INDEX_COL,
)

from storage.sqlite_store import (
    init_db,
    create_tables,
    db_path,
    # watchlist
    add_symbol,
    remove_symbol,
    symbol_in_watchlist,
    get_watchlist,
    get_watchlist_symbols,
    get_watchlist_symbol,
    bulk_add_symbols,
    clear_watchlist,
    update_symbol_score,
    # run history
    log_run,
    finish_run,
    get_last_run,
    get_run_history,
    # screener results
    save_results,
    get_results_for_date,
    get_top_results,
    get_symbol_history,
    get_latest_result,
    # meta
    get_meta,
)

__all__ = [
    # parquet
    "read", "read_tail", "read_tail_at_least", "read_date_range",
    "write", "append_row", "append_dataframe",
    "exists", "row_count", "last_date",
    "deduplicate", "is_corrupt", "copy_safe",
    "DuplicateDateError", "INDEX_COL",
    # sqlite
    "init_db", "create_tables", "db_path",
    "add_symbol", "remove_symbol", "symbol_in_watchlist",
    "get_watchlist", "get_watchlist_symbols", "get_watchlist_symbol",
    "bulk_add_symbols", "clear_watchlist", "update_symbol_score",
    "log_run", "finish_run", "get_last_run", "get_run_history",
    "save_results", "get_results_for_date", "get_top_results",
    "get_symbol_history", "get_latest_result",
    "get_meta",
]
