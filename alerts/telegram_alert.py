"""
alerts/telegram_alert.py
─────────────────────────
Telegram alert channel for the Minervini AI pipeline.

Sends a single Markdown-v2 summary message per screening run via the
Telegram Bot API (plain HTTP, no python-telegram-bot library).

Design notes
────────────
  • Credentials come from constructor args → environment variables
    (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) in that priority order.
  • Filtering: only A+/A setups are included by default.
    Watchlist symbols with quality >= "B" are also included when their
    score meets config["watchlist"]["min_score_alert"].
  • Sort order: A+ tier first, A tier second; score descending within
    each tier.  Watchlist symbols get a "★ " prefix.
  • One message per run (not one per symbol) to avoid Telegram rate limits.
  • On any requests.RequestException the method raises TelegramAlertError.
    The caller (pipeline/runner.py) is responsible for catching it.
  • If alerts.telegram.enabled is False the method returns immediately
    with AlertResult(sent=0, failed=0, skipped=len(results)).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Union

import requests

from alerts.base import AlertResult, BaseAlert
from rules.scorer import SEPAResult
from storage.sqlite_store import get_watchlist_symbols
from utils.exceptions import TelegramAlertError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Quality ordering helpers
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_ORDER: dict[str, int] = {"A+": 0, "A": 1, "B": 2, "C": 3, "FAIL": 4}

_QUALITY_MIN_INDEX: dict[str, int] = {
    "A+": 0,
    "A":  1,
    "B":  2,
    "C":  3,
}


def _quality_index(q: str) -> int:
    return _QUALITY_ORDER.get(q, 99)


def _meets_min_quality(result_quality: str, min_quality: str) -> bool:
    """Return True when result_quality is at least as good as min_quality."""
    return _quality_index(result_quality) <= _quality_index(min_quality)


# ─────────────────────────────────────────────────────────────────────────────
# Markdown-v2 escaping
# ─────────────────────────────────────────────────────────────────────────────

# Characters that must be escaped in Telegram MarkdownV2 outside of bold/code
_MDV2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def _esc(text: str) -> str:
    """Escape a plain string for use inside a Markdown-v2 message."""
    for ch in _MDV2_SPECIAL:
        text = text.replace(ch, f"\\{ch}")
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Message builder
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _fmt_risk(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _build_message(
    filtered: list[tuple[SEPAResult, bool]],   # (result, is_watchlist)
    run_date: date,
) -> str:
    """
    Build a Telegram MarkdownV2 message for the screening run.

    Parameters
    ──────────
    filtered   Pre-sorted list of (SEPAResult, is_watchlist) tuples.
    run_date   Date of the screening run.
    """
    a_plus_count = sum(1 for r, _ in filtered if r.setup_quality == "A+")
    a_count      = sum(1 for r, _ in filtered if r.setup_quality == "A")

    lines: list[str] = []
    lines.append(
        f"🔔 *Minervini Screener — {_esc(str(run_date))}*"
    )
    lines.append(
        f"{_esc(str(a_plus_count))} A\\+ setups \\| "
        f"{_esc(str(a_count))} A setups"
    )
    lines.append("")

    if not filtered:
        lines.append(_esc("No A+/A setups today."))
        return "\n".join(lines)

    for result, is_watchlist in filtered:
        star = "★ " if is_watchlist else ""
        symbol_line = (
            f"{_esc(star)}*{_esc(result.symbol)}* — "
            f"{_esc(result.setup_quality)} \\| Score: {_esc(str(result.score))}"
        )
        lines.append(symbol_line)
        lines.append(
            f"Stage: {_esc(result.stage_label)} \\| RS: {_esc(str(result.rs_rating))}"
        )
        lines.append(
            f"Entry: ₹{_esc(_fmt_price(result.entry_price))} \\| "
            f"Stop: ₹{_esc(_fmt_price(result.stop_loss))} \\| "
            f"Risk: {_esc(_fmt_risk(result.risk_pct))}%"
        )
        vcp_icon       = "✅" if result.vcp_qualified    else "❌"
        breakout_icon  = "✅" if result.breakout_triggered else "❌"
        lines.append(f"VCP: {vcp_icon} \\| Breakout: {breakout_icon}")
        fd = result.fundamental_details or {}
        cm = fd.get("conditions_met", None)
        if cm is not None:
            fund_icon = "✅" if result.fundamental_pass else "❌"
            lines.append(
                f"Fundamentals: {fund_icon} \\({_esc(str(cm))}/7\\)"
            )
        else:
            lines.append("Fundamentals: N/A")
        lines.append("────────────────")
        lines.append("")

    return "\n".join(lines).rstrip()


# ─────────────────────────────────────────────────────────────────────────────
# TelegramAlert
# ─────────────────────────────────────────────────────────────────────────────

class TelegramAlert(BaseAlert):
    """
    Sends a single Telegram summary message per screening run.

    Parameters
    ──────────
    bot_token  Telegram Bot token.  Falls back to TELEGRAM_BOT_TOKEN env var.
    chat_id    Telegram chat/channel ID.  Falls back to TELEGRAM_CHAT_ID env var.
    """

    _API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self._token   = bot_token  or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id    or os.getenv("TELEGRAM_CHAT_ID", "")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _coerce(self, item: Union[SEPAResult, dict]) -> SEPAResult | None:
        """Accept SEPAResult or a plain dict; return SEPAResult or None."""
        if isinstance(item, SEPAResult):
            return item
        # dict path — reconstruct a minimal SEPAResult-like view via getattr
        # by building a real SEPAResult from the dict keys.
        try:
            return SEPAResult(**item)  # type: ignore[arg-type]
        except (TypeError, KeyError) as exc:
            log.warning("TelegramAlert: skipping unparseable result dict", error=str(exc))
            return None

    def _watchlist_symbols(self, config: dict) -> set[str]:  # noqa: ARG002
        """Return watchlist symbols from SQLite. config is unused (kept for call-site compat)."""
        return set(get_watchlist_symbols())

    # ── public API ────────────────────────────────────────────────────────────

    def send(
        self,
        results: list[Union[SEPAResult, dict]],
        run_date: date,
        config: dict,
    ) -> AlertResult:
        """
        Filter, sort, and dispatch the Telegram summary message.

        Returns AlertResult(sent=0, ...) silently when alerts.telegram.enabled
        is False.  Raises TelegramAlertError on HTTP/network failure.
        """
        tg_cfg: dict = config.get("alerts", {}).get("telegram", {})

        # ── early-exit when disabled ──────────────────────────────────────────
        if not tg_cfg.get("enabled", False):
            log.debug("TelegramAlert: disabled in config, skipping")
            return AlertResult(sent=0, failed=0, skipped=len(results))

        min_quality: str = tg_cfg.get("min_quality", "A")
        wl_min_score: int = (
            config.get("scoring", {}).get("min_score_alert", 70)
        )
        watchlist_syms = self._watchlist_symbols(config)

        # ── coerce & filter ───────────────────────────────────────────────────
        coerced: list[SEPAResult] = []
        for item in results:
            r = self._coerce(item)
            if r is not None:
                coerced.append(r)

        kept:    list[tuple[SEPAResult, bool]] = []
        skipped: int = 0

        for r in coerced:
            is_watchlist = r.symbol.upper() in watchlist_syms
            qualifies_main      = _meets_min_quality(r.setup_quality, min_quality)
            qualifies_watchlist = (
                is_watchlist
                and _meets_min_quality(r.setup_quality, "B")
                and r.score >= wl_min_score
            )
            if qualifies_main or qualifies_watchlist:
                kept.append((r, is_watchlist))
            else:
                skipped += 1

        # ── sort: A+ → A, then score descending ──────────────────────────────
        kept.sort(key=lambda t: (_quality_index(t[0].setup_quality), -t[0].score))

        # ── build message ─────────────────────────────────────────────────────
        text = _build_message(kept, run_date)

        # ── send ──────────────────────────────────────────────────────────────
        url = self._API_URL.format(token=self._token)
        payload = {
            "chat_id":    self._chat_id,
            "text":       text,
            "parse_mode": "MarkdownV2",
        }

        log.info(
            "TelegramAlert: sending message",
            run_date=str(run_date),
            kept=len(kept),
            skipped=skipped,
        )

        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            reason = str(exc)
            log.error("TelegramAlert: send failed", reason=reason)
            raise TelegramAlertError(
                f"Telegram send failed: {reason}", reason=reason
            ) from exc

        log.info("TelegramAlert: message sent successfully", run_date=str(run_date))
        return AlertResult(sent=1, failed=0, skipped=skipped)
