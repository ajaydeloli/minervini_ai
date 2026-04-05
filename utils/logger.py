"""
utils/logger.py
───────────────
Structured logging setup for the Minervini AI system.

Usage (in any module):
    from utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Feature computed", symbol="DIXON", rows=252)

Design:
  - One call to setup_logging() at process startup (pipeline/runner.py,
    scripts/run_daily.py, api/main.py).  Every other module just calls
    get_logger(__name__) — they never configure handlers themselves.
  - In production (LOG_FORMAT=json in .env), emits newline-delimited JSON
    so logs can be ingested by any log aggregator without parsing.
  - In development (default), emits coloured human-readable lines.
  - Log files rotate daily, keeping 30 days of history, so ShreeVault disk
    usage stays bounded.
  - Per-module log levels are loaded from config/logging.yaml so noisy
    third-party libraries (yfinance, urllib3) can be silenced without
    touching the code.
"""

from __future__ import annotations

import json
import logging
import logging.config
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # PyYAML — already in requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Internal state
# ─────────────────────────────────────────────────────────────────────────────

_configured: bool = False
_log_dir: Path = Path("logs")


# ─────────────────────────────────────────────────────────────────────────────
# JSON formatter (production)
# ─────────────────────────────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    """
    Emits one JSON object per log line.  Fields:
        ts        ISO-8601 UTC timestamp
        level     DEBUG / INFO / WARNING / ERROR / CRITICAL
        logger    dotted module name
        msg       the formatted message string
        **extra   any extra kwargs passed to the log call
    """

    def format(self, record: logging.LogRecord) -> str:
        # Base payload
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Extra structured fields attached via log.info("msg", extra={...})
        # or via the LoggerAdapter below
        skip = {
            "args", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread",
            "threadName", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in skip:
                payload[key] = value

        # Exception info
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Human-readable formatter (development)
# ─────────────────────────────────────────────────────────────────────────────

_LEVEL_COLOURS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
}
_RESET = "\033[0m"


class _DevFormatter(logging.Formatter):
    """
    Coloured, compact format for terminal output during development.
    Example line:
        2024-01-15 15:35:01 [INFO ] pipeline.runner  │ Daily run started  symbol=DIXON
    """
    _FMT = "{colour}{ts} [{level:<8}]{reset} {name:<28} │ {msg}{extra}"

    def format(self, record: logging.LogRecord) -> str:
        # Collect extra structured fields
        skip = {
            "args", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread",
            "threadName", "taskName",
        }
        extra_parts = [
            f"{k}={v!r}"
            for k, v in record.__dict__.items()
            if k not in skip
        ]
        extra_str = ("  " + "  ".join(extra_parts)) if extra_parts else ""

        use_colour = sys.stderr.isatty()
        colour = _LEVEL_COLOURS.get(record.levelname, "") if use_colour else ""
        reset = _RESET if use_colour else ""

        line = self._FMT.format(
            colour=colour,
            ts=datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            level=record.levelname,
            reset=reset,
            name=record.name[:28],
            msg=record.getMessage(),
            extra=extra_str,
        )

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(
    config_path: str | Path = "config/logging.yaml",
    log_dir: str | Path = "logs",
    force: bool = False,
) -> None:
    """
    Configure the root logger.  Call ONCE at process startup.

    Args:
        config_path:  Path to config/logging.yaml (per-module level overrides).
        log_dir:      Directory for rotating log files. Created if absent.
        force:        Re-configure even if already set up (useful in tests).

    Environment variables honoured:
        LOG_LEVEL   Override root log level (DEBUG / INFO / WARNING / ERROR).
                    Default: INFO.
        LOG_FORMAT  "json" → JSON formatter; anything else → dev formatter.
                    Default: dev formatter.
    """
    global _configured, _log_dir

    if _configured and not force:
        return

    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)

    root_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    root_level = getattr(logging, root_level_name, logging.INFO)
    use_json = os.getenv("LOG_FORMAT", "").lower() == "json"

    # ── Formatters ───────────────────────────────────────────────────────────
    formatter: logging.Formatter = _JSONFormatter() if use_json else _DevFormatter()

    # ── Handlers ─────────────────────────────────────────────────────────────
    # Console — stderr so it doesn't pollute stdout-captured output
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(root_level)

    # Rotating file — one file per day, 30-day retention
    log_file = _log_dir / "minervini.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setFormatter(_JSONFormatter())  # always JSON in files
    file_handler.setLevel(logging.DEBUG)         # files capture everything

    # ── Root logger ──────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)       # handlers filter to their own levels
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # ── Per-module overrides from logging.yaml ───────────────────────────────
    config_path = Path(config_path)
    if config_path.exists():
        try:
            with config_path.open() as fh:
                log_config = yaml.safe_load(fh) or {}
            for logger_name, level_str in log_config.get("loggers", {}).items():
                level = getattr(logging, str(level_str).upper(), None)
                if level is not None:
                    logging.getLogger(logger_name).setLevel(level)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Could not load logging.yaml: %s", exc
            )

    # ── Silence noisy third-party loggers ────────────────────────────────────
    for noisy in ("urllib3", "yfinance", "peewee", "httpx", "httpcore",
                  "apscheduler", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).debug(
        "Logging configured",
        log_dir=str(_log_dir),
        root_level=root_level_name,
        json_format=use_json,
    )


def get_logger(name: str) -> "StructuredLogger":
    """
    Return a StructuredLogger for the given module name.

    Usage:
        log = get_logger(__name__)
        log.info("Fetched data", symbol="DIXON", rows=252)
        log.warning("Skipping symbol", symbol="XYZ", reason="insufficient data")
        log.error("Pipeline failed", exc_info=True)
    """
    return StructuredLogger(logging.getLogger(name))


# ─────────────────────────────────────────────────────────────────────────────
# StructuredLogger  — thin wrapper that passes kwargs as LogRecord extras
# ─────────────────────────────────────────────────────────────────────────────

class StructuredLogger:
    """
    Wraps a standard Logger so callers can pass structured key=value
    arguments alongside the message string.

    log.info("Run complete", symbols=500, duration_sec=28.4)
    →  JSON: {"msg": "Run complete", "symbols": 500, "duration_sec": 28.4, ...}
    →  Dev:  2024-01-15 15:35:01 [INFO    ] pipeline.runner        │ Run complete  symbols=500  duration_sec=28.4
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @property
    def name(self) -> str:
        return self._logger.name

    def _log(self, level: int, msg: str, *args, exc_info=False, **kwargs) -> None:
        if self._logger.isEnabledFor(level):
            self._logger.log(level, msg, *args, exc_info=exc_info, extra=kwargs)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args, exc_info: bool = False, **kwargs) -> None:
        self._log(logging.ERROR, msg, *args, exc_info=exc_info, **kwargs)

    def critical(self, msg: str, *args, exc_info: bool = False, **kwargs) -> None:
        self._log(logging.CRITICAL, msg, *args, exc_info=exc_info, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        """Log ERROR with current exception info automatically attached."""
        self._log(logging.ERROR, msg, *args, exc_info=True, **kwargs)

    # Delegate everything else to the underlying logger
    def __getattr__(self, name: str):
        return getattr(self._logger, name)
