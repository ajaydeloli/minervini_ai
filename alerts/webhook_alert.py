"""
alerts/webhook_alert.py
────────────────────────
Generic HTTP webhook alert channel for the Minervini AI pipeline.

Dispatches a JSON POST to one or more webhook URLs (Slack incoming
webhooks, Discord webhooks, or any custom HTTP endpoint).

Design notes
────────────
  • One POST per URL per run.  Each POST contains a compact JSON summary
    of the screening results.
  • Slack-compatible format: {"text": "...", "blocks": [...]} when
    config["alerts"]["webhook"]["format"] == "slack" (default).
  • Plain format: {"run_date": ..., "summary": {...}, "setups": [...]}
    when format == "plain".  Useful for custom consumers.
  • Each URL is attempted independently; failures are counted but do NOT
    abort the remaining URLs.
  • If alerts.webhook.enabled is False, returns immediately with
    AlertResult(sent=0, ...).
  • A per-URL failure raises WebhookAlertError only when ALL URLs fail.
    Partial failures are logged as warnings and reflected in AlertResult.

Config keys consumed (config["alerts"]["webhook"])
───────────────────────────────────────────────────
    enabled      bool      (default False)
    url          str | list[str]   — one or more webhook endpoint URLs
    format       "slack" | "plain"  (default "slack")
    min_quality  str       (default "A")
    timeout_sec  int       (default 10)

Slack block-kit layout
──────────────────────
    Header section  : "🔔 Minervini Screener — {date}"
    Summary section : "N A+ | N A setups"
    One section per setup (symbol, quality, score, entry/stop, R:R, VCP)
    Dividers between setups
"""

from __future__ import annotations

import json
from datetime import date
from typing import Union

import requests

from alerts.base import AlertResult, BaseAlert
from rules.scorer import SEPAResult
from utils.exceptions import WebhookAlertError
from utils.logger import get_logger

log = get_logger(__name__)

_QUALITY_ORDER: dict[str, int] = {"A+": 0, "A": 1, "B": 2, "C": 3, "FAIL": 4}


def _quality_index(q: str) -> int:
    return _QUALITY_ORDER.get(q, 99)


def _meets_min_quality(result_quality: str, min_quality: str) -> bool:
    return _quality_index(result_quality) <= _quality_index(min_quality)


def _fmt(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}"


# ─────────────────────────────────────────────────────────────────────────────
# Slack block-kit payload builder
# ─────────────────────────────────────────────────────────────────────────────

def _slack_payload(
    kept: list[tuple[SEPAResult, bool]],
    run_date: date,
) -> dict:
    """Build a Slack block-kit compatible payload."""
    a_plus = sum(1 for r, _ in kept if r.setup_quality == "A+")
    a_cnt  = sum(1 for r, _ in kept if r.setup_quality == "A")

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"🔔 Minervini Screener — {run_date}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{a_plus} A+* setups  |  *{a_cnt} A* setups"},
        },
    ]

    if not kept:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No A+/A setups today._"},
        })
    else:
        for result, is_watchlist in kept:
            star  = "★ " if is_watchlist else ""
            vcp   = "✅" if result.vcp_qualified      else "❌"
            brk   = "✅" if result.breakout_triggered else "❌"
            rr_str = (
                f"  R\\:R `{_fmt(result.rr_ratio, 2)}`  "
                f"Target ₹`{_fmt(result.target_price)}`"
                if result.rr_ratio is not None else ""
            )
            text = (
                f"*{star}{result.symbol}*  `{result.setup_quality}`  "
                f"Score: *{result.score}*  RS: {result.rs_rating}\n"
                f"{result.stage_label}\n"
                f"Entry ₹`{_fmt(result.entry_price)}`  "
                f"Stop ₹`{_fmt(result.stop_loss)}`  "
                f"Risk `{_fmt(result.risk_pct)}%`"
                f"{rr_str}\n"
                f"VCP {vcp}  Breakout {brk}"
            )
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            })

    # Fallback `text` field for notifications / plain clients
    summary_text = (
        f"Minervini Screener {run_date}: {a_plus} A+ | {a_cnt} A setups"
    )
    return {"text": summary_text, "blocks": blocks}


# ─────────────────────────────────────────────────────────────────────────────
# Plain JSON payload builder
# ─────────────────────────────────────────────────────────────────────────────

def _plain_payload(
    kept: list[tuple[SEPAResult, bool]],
    run_date: date,
) -> dict:
    """Build a plain JSON payload suitable for any HTTP consumer."""
    a_plus = sum(1 for r, _ in kept if r.setup_quality == "A+")
    a_cnt  = sum(1 for r, _ in kept if r.setup_quality == "A")

    setups = []
    for result, is_watchlist in kept:
        setups.append({
            "symbol":            result.symbol,
            "is_watchlist":      is_watchlist,
            "setup_quality":     result.setup_quality,
            "score":             result.score,
            "stage":             result.stage,
            "stage_label":       result.stage_label,
            "rs_rating":         result.rs_rating,
            "entry_price":       result.entry_price,
            "stop_loss":         result.stop_loss,
            "risk_pct":          result.risk_pct,
            "rr_ratio":          result.rr_ratio,
            "target_price":      result.target_price,
            "vcp_qualified":     result.vcp_qualified,
            "breakout_triggered": result.breakout_triggered,
        })

    return {
        "run_date": str(run_date),
        "summary": {"a_plus": a_plus, "a": a_cnt, "total": len(kept)},
        "setups": setups,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebhookAlert
# ─────────────────────────────────────────────────────────────────────────────

class WebhookAlert(BaseAlert):
    """
    Posts a JSON screening summary to one or more webhook URLs.

    Supports Slack incoming webhooks, Discord webhooks (use format="plain"
    for Discord since it does not use Slack block-kit), and any custom
    HTTP endpoint that accepts a JSON POST body.

    Parameters
    ──────────
    urls        List of webhook URLs.  Overrides config["alerts"]["webhook"]["url"].
    timeout_sec HTTP request timeout in seconds (default 10).
    """

    def __init__(
        self,
        urls: list[str] | None = None,
        timeout_sec: int = 10,
    ) -> None:
        self._urls        = urls or []
        self._timeout_sec = timeout_sec

    # ── helpers ───────────────────────────────────────────────────────────────

    def _coerce(self, item: Union[SEPAResult, dict]) -> SEPAResult | None:
        if isinstance(item, SEPAResult):
            return item
        try:
            return SEPAResult(**item)  # type: ignore[arg-type]
        except (TypeError, KeyError) as exc:
            log.warning("WebhookAlert: skipping unparseable result dict", error=str(exc))
            return None

    def _watchlist_symbols(self, config: dict) -> set[str]:
        wl = config.get("watchlist_symbols") or config.get("watchlist", {}).get("symbols")
        if isinstance(wl, (list, set, tuple)):
            return {str(s).upper() for s in wl}
        return set()

    def _resolve_urls(self, config: dict) -> list[str]:
        """Merge constructor-injected URLs with config URLs (deduplicated)."""
        cfg_raw = config.get("alerts", {}).get("webhook", {}).get("url", [])
        cfg_urls: list[str] = (
            [cfg_raw] if isinstance(cfg_raw, str) else list(cfg_raw)
        )
        seen: set[str] = set()
        merged: list[str] = []
        for u in (self._urls + cfg_urls):
            if u and u not in seen:
                seen.add(u)
                merged.append(u)
        return merged

    # ── public API ────────────────────────────────────────────────────────────

    def send(
        self,
        results: list[Union[SEPAResult, dict]],
        run_date: date,
        config: dict,
    ) -> AlertResult:
        """
        Filter, sort, and POST the webhook payload to all configured URLs.

        Returns AlertResult(sent=0, ...) when alerts.webhook.enabled is False.
        Raises WebhookAlertError when EVERY URL fails.
        Partial failures (some URLs succeed) are logged as warnings and
        reflected in AlertResult.failed without raising.
        """
        wh_cfg: dict = config.get("alerts", {}).get("webhook", {})

        if not wh_cfg.get("enabled", False):
            log.debug("WebhookAlert: disabled in config, skipping")
            return AlertResult(sent=0, failed=0, skipped=len(results))

        urls = self._resolve_urls(config)
        if not urls:
            log.warning("WebhookAlert: no URLs configured — skipping")
            return AlertResult(sent=0, failed=0, skipped=len(results),
                               error="no webhook URLs configured")

        fmt: str          = wh_cfg.get("format", "slack")
        min_quality: str  = wh_cfg.get("min_quality", "A")
        timeout: int      = int(wh_cfg.get("timeout_sec", self._timeout_sec))
        wl_min_score: int = config.get("scoring", {}).get("min_score_alert", 70)
        watchlist_syms    = self._watchlist_symbols(config)

        # ── coerce & filter ───────────────────────────────────────────────────
        coerced: list[SEPAResult] = [
            r for item in results
            if (r := self._coerce(item)) is not None
        ]

        kept:    list[tuple[SEPAResult, bool]] = []
        skipped: int = 0

        for r in coerced:
            is_wl = r.symbol.upper() in watchlist_syms
            qualifies_main = _meets_min_quality(r.setup_quality, min_quality)
            qualifies_wl   = (
                is_wl
                and _meets_min_quality(r.setup_quality, "B")
                and r.score >= wl_min_score
            )
            if qualifies_main or qualifies_wl:
                kept.append((r, is_wl))
            else:
                skipped += 1

        kept.sort(key=lambda t: (_quality_index(t[0].setup_quality), -t[0].score))

        # ── build payload ─────────────────────────────────────────────────────
        if fmt == "plain":
            payload = _plain_payload(kept, run_date)
        else:
            payload = _slack_payload(kept, run_date)

        # ── dispatch to each URL ──────────────────────────────────────────────
        sent   = 0
        failed = 0
        last_error: str | None = None

        for url in urls:
            try:
                resp = requests.post(
                    url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                )
                resp.raise_for_status()
                sent += 1
                log.info("WebhookAlert: POST succeeded", url=url)
            except requests.RequestException as exc:
                reason = str(exc)
                log.warning("WebhookAlert: POST failed", url=url, reason=reason)
                last_error = reason
                failed += 1

        if sent == 0 and failed > 0:
            raise WebhookAlertError(
                f"All {failed} webhook URL(s) failed. Last error: {last_error}",
                reason=last_error,
            )

        log.info(
            "WebhookAlert: dispatch complete",
            sent=sent, failed=failed, skipped=skipped,
        )
        return AlertResult(sent=sent, failed=failed, skipped=skipped,
                           error=last_error if failed else None)
