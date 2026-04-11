"""
alerts/base.py
──────────────
Abstract base class and shared data structures for all alert channels.

Design notes
────────────
  • BaseAlert defines a single mandatory method — send() — so every alert
    channel (Telegram, email, webhook) presents the same interface to the
    pipeline runner.
  • AlertResult is a lightweight dataclass that lets callers audit what
    happened without relying on exceptions for the "nothing to do" path.
  • Failures inside send() are signalled by raising the appropriate
    AlertError subclass (TelegramAlertError, EmailAlertError …).
    The pipeline runner — not the alert class — decides whether to swallow,
    log, or re-raise those errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Union

from rules.scorer import SEPAResult


# ─────────────────────────────────────────────────────────────────────────────
# AlertResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AlertResult:
    """
    Summary of one send() call.

    Attributes
    ──────────
    sent    Number of messages (or batches) successfully dispatched.
    failed  Number that failed after retries.
    skipped Number of results filtered out by min_quality / score threshold.
    error   Human-readable description of the last error, or None on success.
    """
    sent:    int
    failed:  int
    skipped: int
    error:   str | None = field(default=None)


# ─────────────────────────────────────────────────────────────────────────────
# BaseAlert
# ─────────────────────────────────────────────────────────────────────────────

class BaseAlert(ABC):
    """
    Abstract base for all alert channel implementations.

    Subclasses must implement send() and may override __init__ to
    accept channel-specific credentials (tokens, API keys, etc.).
    """

    @abstractmethod
    def send(
        self,
        results: list[Union[SEPAResult, dict]],
        run_date: date,
        config: dict,
    ) -> AlertResult:
        """
        Dispatch alert messages for the given screening results.

        Parameters
        ──────────
        results   List of SEPAResult objects (or plain dicts in legacy paths).
                  Implementations must handle both gracefully.
        run_date  The date this screening run covers (shown in the message).
        config    Full application config dict (from settings.yaml).
                  Each implementation reads its own sub-key, e.g.
                  config["alerts"]["telegram"].

        Returns
        ───────
        AlertResult summarising what was sent, skipped, or failed.

        Raises
        ──────
        AlertError (or a subclass) on unrecoverable delivery failure.
        The caller (pipeline/runner.py) is responsible for catching this.
        """
