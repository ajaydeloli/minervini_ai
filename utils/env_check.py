"""
utils/env_check.py
──────────────────
Startup environment-variable validation.

Call warn_missing_env_vars(config) once at process start (inside the API
lifespan or the pipeline runner's Step 1) to surface missing credentials
before they cause cryptic mid-run failures.

Design contract
───────────────
- NEVER raises — only returns warning strings and logs at WARNING level.
- All checks are driven by the live config dict so the function can be
  unit-tested without touching real environment variables.
- Uses os.environ.get() for every check; never imports dotenv or any
  third-party helper.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # kept for future type-only imports

# ── Provider → required env var name ─────────────────────────────────────────

_PROVIDER_KEY_MAP: dict[str, str | None] = {
    "groq":        "GROQ_API_KEY",
    "anthropic":   "ANTHROPIC_API_KEY",
    "openai":      "OPENAI_API_KEY",
    "openrouter":  "OPENROUTER_API_KEY",
    "gemini":      "GEMINI_API_KEY",
    "ollama":      None,   # local — no key required
}


def warn_missing_env_vars(config: dict) -> list[str]:
    """
    Check that env vars required by enabled features are set.

    Returns a list of warning strings (never raises — warnings only).
    Logs each warning at WARNING level using utils.logger.get_logger().

    Args:
        config: The loaded application config dict (from get_config() or
                context.config).  Only *enabled* features are checked.

    Returns:
        List of human-readable warning strings.  Empty list = all clear.
    """
    # Deferred import avoids circular-import issues at module load time.
    from utils.logger import get_logger  # noqa: PLC0415
    log = get_logger(__name__)

    warnings: list[str] = []

    def _warn(msg: str) -> None:
        warnings.append(msg)
        log.warning(msg)

    # ── LLM provider key ─────────────────────────────────────────────────────
    llm_cfg = config.get("llm", {})
    if llm_cfg.get("enabled") is True:
        provider: str = llm_cfg.get("provider", "").lower().strip()
        if not provider:
            _warn(
                "LLM is enabled but no provider is configured "
                "(config.llm.provider is empty)."
            )
        else:
            required_var = _PROVIDER_KEY_MAP.get(provider)
            if required_var is None and provider not in _PROVIDER_KEY_MAP:
                # Unknown provider — we can't know which key it needs.
                _warn(
                    f"LLM is enabled with unknown provider '{provider}'; "
                    "cannot determine which API key env var is required."
                )
            elif required_var is not None:
                # A key IS required for this provider.
                if not os.environ.get(required_var, "").strip():
                    _warn(
                        f"LLM is enabled with provider='{provider}' but "
                        f"${required_var} is not set or empty."
                    )
            # else: provider == "ollama" → no key needed, skip silently.

    # ── Telegram alert ───────────────────────────────────────────────────────
    telegram_cfg = config.get("alerts", {}).get("telegram", {})
    if telegram_cfg.get("enabled") is True:
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            if not os.environ.get(var, "").strip():
                _warn(
                    f"Telegram alerts are enabled but ${var} is not set or empty."
                )

    # ── Email / SMTP alert ───────────────────────────────────────────────────
    email_cfg = config.get("alerts", {}).get("email", {})
    if email_cfg.get("enabled") is True:
        for var in ("SMTP_USER", "SMTP_PASS"):
            if not os.environ.get(var, "").strip():
                _warn(
                    f"Email alerts are enabled but ${var} is not set or empty."
                )

    # ── API auth — always check, regardless of other config ──────────────────
    read_key  = os.environ.get("API_READ_KEY",  "").strip()
    admin_key = os.environ.get("API_ADMIN_KEY", "").strip()
    if not read_key and not admin_key:
        _warn(
            "API is running in open/unauthenticated mode "
            "(both API_READ_KEY and API_ADMIN_KEY are unset or empty). "
            "Set these env vars to enable authentication."
        )

    return warnings
