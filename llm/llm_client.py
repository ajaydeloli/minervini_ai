"""
llm/llm_client.py
─────────────────
LLM client abstraction for the Minervini AI system.

Public API
──────────
  LLMClient           Abstract base class.  All concrete providers implement it.
  GroqClient          Groq API (default: llama-3.3-70b-versatile).
  AnthropicClient     Anthropic API (default: claude-haiku-4-5).
  OpenAIClient        OpenAI API (default: gpt-4o-mini).
  OpenRouterClient    OpenRouter API (default: deepseek/deepseek-r1:free).
  OllamaClient        Local Ollama server (default: llama3.2).
  GeminiClient        Google Gemini API (default: gemini-2.0-flash).
  get_llm_client()    Factory: reads config dict → returns LLMClient | None.

Design mandates
───────────────
  - The LLM is a NARRATOR only.  It receives finished rule outputs and
    generates plain-English commentary.  It NEVER scores, filters, or
    modifies any SEPAResult.
  - Every LLM error must be caught and returned as None by the factory.
    LLM failures must NEVER crash the pipeline.
  - Provider is selected via config["llm"]["provider"].  Default: "groq".
  - All provider libraries are imported lazily to avoid ImportError when
    a library is not installed.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from utils.exceptions import LLMProviderError, LLMResponseError
from utils.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base class
# ─────────────────────────────────────────────────────────────────────────────

class LLMClient(ABC):
    """
    Abstract base for all LLM provider clients.

    Subclasses must implement:
      - complete(prompt, max_tokens) → str
      - provider_name (property) → str
    """

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """
        Send *prompt* to the LLM and return the response text.

        Args:
            prompt:     The full prompt string to send.
            max_tokens: Upper bound on tokens in the completion.

        Returns:
            The response content as a plain string.

        Raises:
            LLMProviderError:  Any API/network/auth/rate-limit failure.
            LLMResponseError:  Response is empty or cannot be parsed.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable identifier for the provider, e.g. 'groq'."""


# ─────────────────────────────────────────────────────────────────────────────
# Groq
# ─────────────────────────────────────────────────────────────────────────────

class GroqClient(LLMClient):
    """
    LLM client backed by the Groq API.

    Reads GROQ_API_KEY from the environment.
    Default model: llama-3.3-70b-versatile.
    """

    def __init__(self, model: str = "llama-3.3-70b-versatile") -> None:
        """
        Args:
            model: Groq model identifier to use for completions.
        """
        self._model = model

    @property
    def provider_name(self) -> str:
        return "groq"

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """Call the Groq chat-completions endpoint and return the text."""
        try:
            import groq  # lazy import
        except ImportError as exc:
            raise LLMProviderError(self.provider_name, f"groq package not installed: {exc}") from exc

        try:
            client = groq.Groq(api_key=os.environ.get("GROQ_API_KEY"))
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMProviderError(self.provider_name, str(exc)) from exc

        try:
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage:
                log.debug(
                    "LLM token usage",
                    provider=self.provider_name,
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                )
        except (IndexError, AttributeError) as exc:
            raise LLMResponseError(f"Could not parse Groq response: {exc}") from exc

        if not text.strip():
            raise LLMResponseError("Groq returned an empty response")

        return text


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic
# ─────────────────────────────────────────────────────────────────────────────

class AnthropicClient(LLMClient):
    """
    LLM client backed by the Anthropic Messages API.

    Reads ANTHROPIC_API_KEY from the environment.
    Default model: claude-haiku-4-5.
    """

    def __init__(self, model: str = "claude-haiku-4-5") -> None:
        """
        Args:
            model: Anthropic model identifier to use for completions.
        """
        self._model = model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """Call the Anthropic Messages endpoint and return the text."""
        try:
            import anthropic  # lazy import
        except ImportError as exc:
            raise LLMProviderError(self.provider_name, f"anthropic package not installed: {exc}") from exc

        try:
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            message = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise LLMProviderError(self.provider_name, str(exc)) from exc

        try:
            text = message.content[0].text if message.content else ""
            usage = getattr(message, "usage", None)
            if usage:
                log.debug(
                    "LLM token usage",
                    provider=self.provider_name,
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                )
        except (IndexError, AttributeError) as exc:
            raise LLMResponseError(f"Could not parse Anthropic response: {exc}") from exc

        if not text.strip():
            raise LLMResponseError("Anthropic returned an empty response")

        return text


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIClient(LLMClient):
    """
    LLM client backed by the OpenAI Chat Completions API.

    Reads OPENAI_API_KEY from the environment.
    Default model: gpt-4o-mini.
    """

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        """
        Args:
            model: OpenAI model identifier to use for completions.
        """
        self._model = model

    @property
    def provider_name(self) -> str:
        return "openai"

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """Call the OpenAI chat-completions endpoint and return the text."""
        try:
            import openai  # lazy import
        except ImportError as exc:
            raise LLMProviderError(self.provider_name, f"openai package not installed: {exc}") from exc

        try:
            client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMProviderError(self.provider_name, str(exc)) from exc

        try:
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage:
                log.debug(
                    "LLM token usage",
                    provider=self.provider_name,
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                )
        except (IndexError, AttributeError) as exc:
            raise LLMResponseError(f"Could not parse OpenAI response: {exc}") from exc

        if not text.strip():
            raise LLMResponseError("OpenAI returned an empty response")

        return text


# ─────────────────────────────────────────────────────────────────────────────
# OpenRouter
# ─────────────────────────────────────────────────────────────────────────────

class OpenRouterClient(LLMClient):
    """
    LLM client backed by OpenRouter, using the OpenAI-compatible API.

    Reads OPENROUTER_API_KEY from the environment.
    Default model: deepseek/deepseek-r1:free.
    """

    _BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str = "deepseek/deepseek-r1:free") -> None:
        """
        Args:
            model: OpenRouter model identifier (e.g. 'deepseek/deepseek-r1:free').
        """
        self._model = model

    @property
    def provider_name(self) -> str:
        return "openrouter"

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """Call the OpenRouter chat-completions endpoint and return the text."""
        try:
            import openai  # lazy import
        except ImportError as exc:
            raise LLMProviderError(self.provider_name, f"openai package not installed: {exc}") from exc

        try:
            client = openai.OpenAI(
                api_key=os.environ.get("OPENROUTER_API_KEY"),
                base_url=self._BASE_URL,
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMProviderError(self.provider_name, str(exc)) from exc

        try:
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage:
                log.debug(
                    "LLM token usage",
                    provider=self.provider_name,
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                )
        except (IndexError, AttributeError) as exc:
            raise LLMResponseError(f"Could not parse OpenRouter response: {exc}") from exc

        if not text.strip():
            raise LLMResponseError("OpenRouter returned an empty response")

        return text


# ─────────────────────────────────────────────────────────────────────────────
# Ollama  (local)
# ─────────────────────────────────────────────────────────────────────────────

class OllamaClient(LLMClient):
    """
    LLM client backed by a locally running Ollama server, accessed via
    its OpenAI-compatible API endpoint.

    Base URL is read from OLLAMA_BASE_URL (default: http://localhost:11434/v1).
    Default model: llama3.2.
    """

    _DEFAULT_BASE_URL = "http://localhost:11434/v1"

    def __init__(self, model: str = "llama3.2") -> None:
        """
        Args:
            model: Ollama model tag to use (must be pulled locally first).
        """
        self._model = model
        self._base_url = os.environ.get("OLLAMA_BASE_URL", self._DEFAULT_BASE_URL)

    @property
    def provider_name(self) -> str:
        return "ollama"

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """Call the local Ollama chat-completions endpoint and return the text."""
        try:
            import openai  # lazy import
        except ImportError as exc:
            raise LLMProviderError(self.provider_name, f"openai package not installed: {exc}") from exc

        try:
            client = openai.OpenAI(
                api_key="ollama",
                base_url=self._base_url,
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise LLMProviderError(self.provider_name, str(exc)) from exc

        try:
            text = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            if usage:
                log.debug(
                    "LLM token usage",
                    provider=self.provider_name,
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                )
        except (IndexError, AttributeError) as exc:
            raise LLMResponseError(f"Could not parse Ollama response: {exc}") from exc

        if not text.strip():
            raise LLMResponseError("Ollama returned an empty response")

        return text


# ─────────────────────────────────────────────────────────────────────────────
# Google Gemini
# ─────────────────────────────────────────────────────────────────────────────

class GeminiClient(LLMClient):
    """
    LLM client backed by the Google Gemini API (google-generativeai SDK).

    Reads GEMINI_API_KEY from the environment.
    Default model: gemini-2.0-flash.

    Install:
        pip install google-generativeai>=0.7.0

    Token-usage notes:
        The Gemini SDK exposes usage metadata on the response object as
        ``response.usage_metadata`` with fields ``prompt_token_count`` and
        ``candidates_token_count``.  Both are logged at DEBUG level when
        available; their absence is silently tolerated (older SDK versions
        and some model variants may omit them).
    """

    def __init__(self, model: str = "gemini-2.0-flash") -> None:
        """
        Args:
            model: Gemini model identifier, e.g. 'gemini-2.0-flash' or
                   'gemini-1.5-pro'.  Passed directly to
                   ``genai.GenerativeModel``.
        """
        self._model = model

    @property
    def provider_name(self) -> str:
        return "gemini"

    def complete(self, prompt: str, max_tokens: int = 350) -> str:
        """
        Call the Gemini GenerateContent endpoint and return the response text.

        The SDK's ``generate_content`` method is used with
        ``GenerationConfig(max_output_tokens=max_tokens)`` so the token
        ceiling from config is honoured consistently with other providers.

        Args:
            prompt:     Full prompt string.
            max_tokens: Maximum output tokens.

        Returns:
            Plain-text response from the model.

        Raises:
            LLMProviderError:  google-generativeai not installed, or any
                               API / network / auth / quota failure.
            LLMResponseError:  Response is empty or the SDK object cannot
                               be parsed.
        """
        try:
            import google.generativeai as genai  # lazy import
        except ImportError as exc:
            raise LLMProviderError(
                self.provider_name,
                f"google-generativeai package not installed: {exc}",
            ) from exc

        try:
            api_key = os.environ.get("GEMINI_API_KEY")
            genai.configure(api_key=api_key)

            generation_config = genai.GenerationConfig(max_output_tokens=max_tokens)
            model_client = genai.GenerativeModel(
                model_name=self._model,
                generation_config=generation_config,
            )
            response = model_client.generate_content(prompt)
        except Exception as exc:
            raise LLMProviderError(self.provider_name, str(exc)) from exc

        try:
            text: str = response.text or ""
            usage = getattr(response, "usage_metadata", None)
            if usage:
                log.debug(
                    "LLM token usage",
                    provider=self.provider_name,
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                )
        except (AttributeError, ValueError) as exc:
            # response.text raises ValueError when the response was blocked
            raise LLMResponseError(
                f"Could not parse Gemini response: {exc}"
            ) from exc

        if not text.strip():
            raise LLMResponseError("Gemini returned an empty response")

        return text


# ─────────────────────────────────────────────────────────────────────────────
# Provider registry
# ─────────────────────────────────────────────────────────────────────────────

_PROVIDER_MAP: dict[str, type[LLMClient]] = {
    "groq":        GroqClient,
    "anthropic":   AnthropicClient,
    "openai":      OpenAIClient,
    "openrouter":  OpenRouterClient,
    "ollama":      OllamaClient,
    "gemini":      GeminiClient,
}


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def get_llm_client(config: dict) -> LLMClient | None:
    """
    Factory function: read *config* and return a ready-to-use LLMClient,
    or None if the LLM layer is disabled / misconfigured.

    Args:
        config: The full settings dict (loaded from settings.yaml).
                Relevant keys under config["llm"]:
                  enabled  (bool)  – if False, return None immediately.
                  provider (str)   – one of groq | anthropic | openai |
                                     openrouter | ollama | gemini.
                  model    (str)   – optional model override.

    Returns:
        An instantiated LLMClient, or None.

    Notes:
        - Unknown provider → log warning, return None.
        - Any exception during instantiation → log warning, return None.
        - This function NEVER raises — LLM failures must not crash the pipeline.
    """
    llm_cfg: dict = config.get("llm", {})

    if not llm_cfg.get("enabled", False):
        log.info("LLM layer disabled via config", enabled=False)
        return None

    provider: str = llm_cfg.get("provider", "groq").lower().strip()
    model: str | None = llm_cfg.get("model")

    client_class = _PROVIDER_MAP.get(provider)
    if client_class is None:
        log.warning(
            "Unknown LLM provider — skipping LLM layer",
            provider=provider,
            known_providers=list(_PROVIDER_MAP.keys()),
        )
        return None

    try:
        client: LLMClient = client_class(model) if model else client_class()
        log.info(
            "LLM client initialised",
            provider=provider,
            model=model or "(default)",
        )
        return client
    except Exception as exc:
        log.warning(
            "Failed to initialise LLM client — skipping LLM layer",
            provider=provider,
            reason=str(exc),
        )
        return None
