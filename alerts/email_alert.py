"""
alerts/email_alert.py
─────────────────────
SMTP email alert channel for the Minervini AI pipeline.

Sends a plain-text + HTML multipart email summarising the screening run.
One email per run (not one per symbol) to a configurable recipient list.

Design notes
────────────
  • Credentials come from constructor args → environment variables
    (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS) in that priority order.
  • Filtering mirrors TelegramAlert: A+/A setups by default; watchlist
    symbols with quality >= "B" and score >= min_score_alert are also
    included.
  • The message is multipart/alternative: plain text for simple clients,
    HTML table for rich clients (Gmail, Outlook).
  • STARTTLS is used when port == 587 (standard); SSL when port == 465.
    Plain (port 25) is supported but not recommended.
  • If alerts.email.enabled is False the method returns immediately with
    AlertResult(sent=0, failed=0, skipped=len(results)).
  • Any smtplib / socket error raises EmailAlertError.
    The caller (pipeline/runner.py) is responsible for catching it.

Config keys consumed (config["alerts"]["email"])
────────────────────────────────────────────────
    enabled          bool    (default False)
    to               list[str] | str   — recipient address(es)
    from_addr        str     (default: SMTP_USER env var)
    subject_prefix   str     (default "Minervini Screener")
    min_quality      str     (default "A")
    html             bool    (default True) — include HTML part

Environment variables (fallbacks)
──────────────────────────────────
    SMTP_HOST    (default smtp.gmail.com)
    SMTP_PORT    (default 587)
    SMTP_USER
    SMTP_PASS
"""

from __future__ import annotations

import os
import smtplib
import socket
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Union

from alerts.base import AlertResult, BaseAlert
from rules.scorer import SEPAResult
from utils.exceptions import EmailAlertError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Quality ordering (shared with Telegram — kept local to avoid coupling)
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_ORDER: dict[str, int] = {"A+": 0, "A": 1, "B": 2, "C": 3, "FAIL": 4}


def _quality_index(q: str) -> int:
    return _QUALITY_ORDER.get(q, 99)


def _meets_min_quality(result_quality: str, min_quality: str) -> bool:
    return _quality_index(result_quality) <= _quality_index(min_quality)


# ─────────────────────────────────────────────────────────────────────────────
# Plain-text body builder
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}"


def _build_plain(
    kept: list[tuple[SEPAResult, bool]],
    run_date: date,
) -> str:
    a_plus = sum(1 for r, _ in kept if r.setup_quality == "A+")
    a_cnt  = sum(1 for r, _ in kept if r.setup_quality == "A")

    lines: list[str] = [
        f"Minervini Screener — {run_date}",
        f"{a_plus} A+ setups | {a_cnt} A setups",
        "=" * 52,
        "",
    ]

    if not kept:
        lines.append("No A+/A setups today.")
        return "\n".join(lines)

    for result, is_watchlist in kept:
        star = "★ " if is_watchlist else "  "
        lines.append(
            f"{star}{result.symbol:<14} {result.setup_quality:<4}  "
            f"Score: {result.score:>3}  RS: {result.rs_rating:>2}"
        )
        lines.append(
            f"  Stage : {result.stage_label}"
        )
        lines.append(
            f"  Entry : ₹{_fmt(result.entry_price)}  "
            f"Stop: ₹{_fmt(result.stop_loss)}  "
            f"Risk: {_fmt(result.risk_pct)}%"
        )
        if result.rr_ratio is not None:
            lines.append(
                f"  R:R   : {_fmt(result.rr_ratio, 2)}  "
                f"Target: ₹{_fmt(result.target_price)}"
            )
        vcp_flag = "✓" if result.vcp_qualified      else "✗"
        brk_flag = "✓" if result.breakout_triggered else "✗"
        lines.append(f"  VCP: {vcp_flag}  Breakout: {brk_flag}")
        lines.append("-" * 52)

    lines.append("")
    lines.append("-- Minervini AI (automated message) --")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# HTML body builder
# ─────────────────────────────────────────────────────────────────────────────

_QUALITY_BG: dict[str, str] = {
    "A+":   "#2d4a1e",
    "A":    "#1a3a2a",
    "B":    "#1a2a3a",
    "C":    "#3a2a1a",
    "FAIL": "#3a1a1a",
}
_QUALITY_FG: dict[str, str] = {
    "A+":   "#ffd700",
    "A":    "#00e676",
    "B":    "#40c4ff",
    "C":    "orange",
    "FAIL": "#ef5350",
}


def _badge(quality: str) -> str:
    bg = _QUALITY_BG.get(quality, "#333")
    fg = _QUALITY_FG.get(quality, "#fff")
    return (
        f'<span style="background:{bg};color:{fg};'
        f'padding:2px 7px;border-radius:4px;font-weight:bold;">'
        f"{quality}</span>"
    )


def _build_html(
    kept: list[tuple[SEPAResult, bool]],
    run_date: date,
) -> str:
    a_plus = sum(1 for r, _ in kept if r.setup_quality == "A+")
    a_cnt  = sum(1 for r, _ in kept if r.setup_quality == "A")

    rows_html = ""
    if not kept:
        rows_html = (
            '<tr><td colspan="7" style="text-align:center;color:#aaa;">'
            "No A+/A setups today.</td></tr>"
        )
    else:
        for result, is_watchlist in kept:
            star  = "★ " if is_watchlist else ""
            sym   = f"{star}{result.symbol}"
            entry = f"₹{_fmt(result.entry_price)}"
            stop  = f"₹{_fmt(result.stop_loss)}"
            risk  = f"{_fmt(result.risk_pct)}%"
            rr    = _fmt(result.rr_ratio, 2) if result.rr_ratio is not None else "—"
            vcp   = "✓" if result.vcp_qualified      else "✗"
            brk   = "✓" if result.breakout_triggered else "✗"
            rows_html += (
                "<tr>"
                f'<td style="color:#e0e0e0;font-weight:bold;">{sym}</td>'
                f'<td style="text-align:center;">{_badge(result.setup_quality)}</td>'
                f'<td style="text-align:right;">{result.score}</td>'
                f'<td style="text-align:right;">{result.rs_rating}</td>'
                f'<td style="text-align:right;">{entry} / {stop} ({risk})</td>'
                f'<td style="text-align:right;">{rr}</td>'
                f'<td style="text-align:center;">{vcp} / {brk}</td>'
                "</tr>"
            )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#121212;color:#e0e0e0;font-family:monospace;padding:20px;">
<h2 style="color:#ffd700;">🔔 Minervini Screener — {run_date}</h2>
<p style="color:#aaa;">{a_plus} A+ setups &nbsp;|&nbsp; {a_cnt} A setups</p>
<table style="border-collapse:collapse;width:100%;font-size:13px;">
<thead>
<tr style="background:#1e1e1e;color:#90caf9;">
<th style="padding:8px;text-align:left;">Symbol</th>
<th style="padding:8px;">Quality</th>
<th style="padding:8px;text-align:right;">Score</th>
<th style="padding:8px;text-align:right;">RS</th>
<th style="padding:8px;text-align:right;">Entry / Stop (Risk)</th>
<th style="padding:8px;text-align:right;">R:R</th>
<th style="padding:8px;">VCP/BRK</th>
</tr>
</thead>
<tbody>{rows_html}</tbody>
</table>
<p style="color:#555;margin-top:20px;font-size:11px;">
  Minervini AI — automated daily screening report
</p>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# EmailAlert
# ─────────────────────────────────────────────────────────────────────────────

class EmailAlert(BaseAlert):
    """
    Sends a multipart plain-text + HTML email summary per screening run.

    Parameters
    ──────────
    smtp_host  SMTP server hostname.  Falls back to SMTP_HOST env var
               (default: smtp.gmail.com).
    smtp_port  SMTP port.  Falls back to SMTP_PORT env var (default: 587).
    smtp_user  SMTP login username.  Falls back to SMTP_USER env var.
    smtp_pass  SMTP login password.  Falls back to SMTP_PASS env var.
    """

    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_pass: str | None = None,
    ) -> None:
        self._host = smtp_host or os.getenv("SMTP_HOST", "smtp.gmail.com")
        self._port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self._user = smtp_user or os.getenv("SMTP_USER", "")
        self._pass = smtp_pass or os.getenv("SMTP_PASS", "")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _coerce(self, item: Union[SEPAResult, dict]) -> SEPAResult | None:
        if isinstance(item, SEPAResult):
            return item
        try:
            return SEPAResult(**item)  # type: ignore[arg-type]
        except (TypeError, KeyError) as exc:
            log.warning("EmailAlert: skipping unparseable result dict", error=str(exc))
            return None

    def _watchlist_symbols(self, config: dict) -> set[str]:
        wl = config.get("watchlist_symbols") or config.get("watchlist", {}).get("symbols")
        if isinstance(wl, (list, set, tuple)):
            return {str(s).upper() for s in wl}
        return set()

    def _build_message(
        self,
        kept: list[tuple[SEPAResult, bool]],
        run_date: date,
        from_addr: str,
        to_addrs: list[str],
        subject: str,
        include_html: bool,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_addr
        msg["To"]      = ", ".join(to_addrs)

        plain = _build_plain(kept, run_date)
        msg.attach(MIMEText(plain, "plain", "utf-8"))

        if include_html:
            html = _build_html(kept, run_date)
            msg.attach(MIMEText(html, "html", "utf-8"))

        return msg

    def _smtp_send(self, msg: MIMEMultipart, to_addrs: list[str]) -> None:
        """Open SMTP connection, authenticate, and send.  Raises EmailAlertError."""
        try:
            if self._port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(self._host, self._port, timeout=30)
            else:
                server = smtplib.SMTP(self._host, self._port, timeout=30)
                server.ehlo()
                server.starttls()
                server.ehlo()

            if self._user and self._pass:
                server.login(self._user, self._pass)

            server.sendmail(msg["From"], to_addrs, msg.as_string())
            server.quit()

        except (smtplib.SMTPException, socket.error, OSError) as exc:
            reason = str(exc)
            log.error("EmailAlert: SMTP send failed", reason=reason)
            raise EmailAlertError(
                f"Email send failed: {reason}", reason=reason
            ) from exc

    # ── public API ────────────────────────────────────────────────────────────

    def send(
        self,
        results: list[Union[SEPAResult, dict]],
        run_date: date,
        config: dict,
    ) -> AlertResult:
        """
        Filter, sort, and dispatch the email summary.

        Returns AlertResult(sent=0, ...) when alerts.email.enabled is False.
        Raises EmailAlertError on SMTP / network failure.
        """
        email_cfg: dict = config.get("alerts", {}).get("email", {})

        if not email_cfg.get("enabled", False):
            log.debug("EmailAlert: disabled in config, skipping")
            return AlertResult(sent=0, failed=0, skipped=len(results))

        # ── recipient list ────────────────────────────────────────────────────
        to_raw = email_cfg.get("to", [])
        to_addrs: list[str] = (
            [to_raw] if isinstance(to_raw, str) else list(to_raw)
        )
        if not to_addrs:
            log.warning("EmailAlert: no recipients configured — skipping")
            return AlertResult(sent=0, failed=0, skipped=len(results),
                               error="no recipients configured")

        from_addr: str    = email_cfg.get("from_addr", self._user)
        subject_prefix    = email_cfg.get("subject_prefix", "Minervini Screener")
        min_quality: str  = email_cfg.get("min_quality", "A")
        include_html: bool = email_cfg.get("html", True)
        wl_min_score: int  = config.get("scoring", {}).get("min_score_alert", 70)
        watchlist_syms     = self._watchlist_symbols(config)

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

        # ── build subject ─────────────────────────────────────────────────────
        a_plus = sum(1 for r, _ in kept if r.setup_quality == "A+")
        a_cnt  = sum(1 for r, _ in kept if r.setup_quality == "A")
        subject = f"{subject_prefix} — {run_date} | {a_plus} A+ | {a_cnt} A"

        # ── assemble & send ───────────────────────────────────────────────────
        msg = self._build_message(
            kept, run_date, from_addr, to_addrs, subject, include_html
        )

        log.info(
            "EmailAlert: sending",
            run_date=str(run_date),
            kept=len(kept),
            skipped=skipped,
            to=to_addrs,
        )

        self._smtp_send(msg, to_addrs)

        log.info("EmailAlert: sent successfully", run_date=str(run_date))
        return AlertResult(sent=1, failed=0, skipped=skipped)
