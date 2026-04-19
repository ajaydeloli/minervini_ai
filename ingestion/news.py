"""
ingestion/news.py
─────────────────
RSS feed fetcher + keyword scorer + LLM re-scorer.

Design mandates (PROJECT_DESIGN.md §10.1–10.4):
  - News is an OPTIONAL lightweight signal; it never gates a trade signal.
  - All failures are caught and logged — the module NEVER raises to the caller.
  - Market-wide articles are fetched once (parallel RSS), cached for 30 min.
  - Per-symbol filtering uses alias matching (config/symbol_aliases.yaml).
  - LLM re-scoring is optional and enabled via config; keyword scoring is always available.
  - Cache writes are atomic (write .tmp → os.replace to final path).

Public API:
    fetch_market_news(config, force_refresh=False) -> list[dict]
    fetch_symbol_news(symbol, config, all_news=None) -> list[dict]
    compute_news_score(articles) -> float   # -100.0 to +100.0

Private helpers:
    _load_aliases(config) -> dict[str, list[str]]
    _keyword_score(text) -> float           # fast heuristic, -1.0 to +1.0
    _llm_rescore(articles, symbol, config) -> list[dict]
    _load_news_cache(cache_path) -> list[dict] | None
    _save_news_cache(cache_path, articles) -> None
    _fetch_one_feed(url) -> list[dict]      # called inside ThreadPoolExecutor
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import yaml

from utils.exceptions import LLMError, NewsFetchError
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS: list[str] = [
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.moneycontrol.com/rss/business.xml",
]

BULLISH: list[str] = [
    "surge", "rally", "breakout", "upgrade", "order win", "buyback",
    "dividend", "beat", "record", "expansion", "acquisition", "growth",
    "profit rise", "revenue up", "strong quarter", "buy rating",
]

BEARISH: list[str] = [
    "probe", "fraud", "miss", "downgrade", "resignation", "sebi",
    "investigation", "loss", "decline", "regulatory", "penalty",
    "write-off", "recall", "default", "debt concern", "shortfall",
]

_IST = timezone(timedelta(hours=5, minutes=30))

# Module-level alias cache — loaded once, reused across calls.
# Key: resolved str path of symbol_aliases.yaml → Value: parsed dict.
_aliases_cache: dict[str, dict[str, list[str]]] = {}

# Path to symbol_aliases.yaml, resolved relative to this file's location
# so it works regardless of the caller's CWD.
_ALIASES_PATH: Path = Path(__file__).parent.parent / "config" / "symbol_aliases.yaml"


# ─────────────────────────────────────────────────────────────────────────────
# Private — cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_news_cache(cache_path: Path) -> list[dict] | None:
    """
    Load articles from the JSON cache file.

    Returns the article list if the file exists and is parseable.
    Returns None on any error (missing file, corrupt JSON, wrong schema).
    Does NOT check TTL — freshness is the caller's responsibility.
    """
    if not cache_path.exists():
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        articles = raw.get("articles")
        if not isinstance(articles, list):
            log.warning("News cache schema unexpected — ignoring", path=str(cache_path))
            return None
        return articles
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "News cache read failed — will re-fetch",
            path=str(cache_path),
            error=str(exc),
        )
        return None


def _is_cache_fresh(cache_path: Path, ttl_minutes: int) -> bool:
    """
    Return True if cache_path exists and its _cached_at timestamp
    is within ttl_minutes of now.
    """
    if not cache_path.exists():
        return False
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        cached_at_str = raw.get("_cached_at", "")
        if not cached_at_str:
            return False
        cached_at = datetime.fromisoformat(cached_at_str)
        now = datetime.now(tz=cached_at.tzinfo or _IST)
        age_minutes = (now - cached_at).total_seconds() / 60.0
        return age_minutes < ttl_minutes
    except Exception as exc:  # noqa: BLE001
        log.debug(
            "News cache freshness check failed — treating as stale",
            path=str(cache_path),
            error=str(exc),
        )
        return False


def _save_news_cache(cache_path: Path, articles: list[dict]) -> None:
    """
    Atomically write articles to the JSON cache file.

    Format written:
        { "_cached_at": "<ISO-8601>", "articles": [...] }

    Uses write-to-tmp → os.replace so the file is never left in a
    half-written state if the process is killed mid-write.
    Never raises — a failed cache write is logged and silently skipped.
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_cached_at": datetime.now(tz=_IST).isoformat(),
            "articles": articles,
        }
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, cache_path)
        log.debug("News cache saved", path=str(cache_path), count=len(articles))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "News cache write failed — data still returned in-memory",
            path=str(cache_path),
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Private — scoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def _keyword_score(text: str) -> float:
    """
    Fast keyword-based sentiment heuristic.

    Searches the combined title + summary text (lowercased) for bullish
    and bearish keywords.  Each bullish match contributes +1; each bearish
    match contributes -1.  The raw count is clamped to [-1.0, +1.0].

    Returns 0.0 when no keywords are found (neutral / insufficient signal).

    Args:
        text: Pre-joined "title + ' ' + summary" string.  May be empty.

    Returns:
        float in [-1.0, +1.0].
    """
    lowered = text.lower()
    score = 0
    for kw in BULLISH:
        if kw in lowered:
            score += 1
    for kw in BEARISH:
        if kw in lowered:
            score -= 1
    if score == 0:
        return 0.0
    return max(-1.0, min(1.0, float(score)))


# ─────────────────────────────────────────────────────────────────────────────
# Private — alias loader
# ─────────────────────────────────────────────────────────────────────────────

def _load_aliases(config: dict) -> dict[str, list[str]]:  # noqa: ARG001
    """
    Load and memoize the symbol-alias mapping from config/symbol_aliases.yaml.

    The file is read at most once per process lifetime (memoized in the
    module-level _aliases_cache dict keyed by the resolved file path string).

    Args:
        config: Parsed settings.yaml dict.  Currently unused — the alias file
                path is resolved relative to this module's location, not from
                config.  The parameter is kept for API consistency and future
                extensibility (e.g. a config override for the aliases path).

    Returns:
        dict mapping NSE symbol strings (e.g. "RELIANCE") to lists of
        lowercase alias strings.  Returns an empty dict on any error;
        callers fall back to [symbol.lower()].
    """
    cache_key = str(_ALIASES_PATH.resolve())
    if cache_key in _aliases_cache:
        return _aliases_cache[cache_key]

    try:
        raw = yaml.safe_load(_ALIASES_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping, got {type(raw).__name__}")
        # Normalise: keys → str, values → list[str]
        parsed: dict[str, list[str]] = {
            str(k): [str(v) for v in (vs if isinstance(vs, list) else [vs])]
            for k, vs in raw.items()
        }
        _aliases_cache[cache_key] = parsed
        log.debug(
            "Symbol aliases loaded",
            path=str(_ALIASES_PATH),
            symbols=len(parsed),
        )
        return parsed
    except FileNotFoundError:
        log.warning(
            "symbol_aliases.yaml not found — alias matching will use symbol.lower()",
            path=str(_ALIASES_PATH),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Failed to load symbol_aliases.yaml — alias matching degraded",
            path=str(_ALIASES_PATH),
            error=str(exc),
        )
    # Cache the empty dict so we don't retry on every call
    _aliases_cache[cache_key] = {}
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Private — LLM re-scorer
# ─────────────────────────────────────────────────────────────────────────────

def _llm_rescore(
    articles: list[dict],
    symbol: str,
    config: dict,
) -> list[dict]:
    """
    Re-score article sentiment using an LLM for contextual accuracy.

    An LLM can understand nuance that keyword matching cannot — e.g.
    "SEBI probe on a competitor" is neutral for the target symbol even
    though "sebi" and "probe" are bearish keywords.

    Behaviour:
        - Returns the articles list unchanged (no LLM call) when
          ``config["llm"]["enabled"]`` is False or when the LLM client
          is unavailable.
        - For each article, builds a short prompt asking the model to rate
          sentiment for *symbol* as a float from -1.0 to +1.0, then updates
          ``article["sentiment"]`` and ``article["score"]`` in-place.
        - On any per-article failure (LLMError, malformed JSON, missing key)
          the original keyword-derived score is preserved and a WARNING is
          logged.  The function never raises to its caller.

    Args:
        articles: List of article dicts (each with "score", "sentiment" keys).
        symbol:   NSE symbol string used in the LLM prompt for context.
        config:   Parsed settings.yaml dict.

    Returns:
        The same articles list with LLM-updated scores where the call
        succeeded, or unchanged scores where it did not.  Never raises.
    """
    llm_enabled = config.get("llm", {}).get("enabled", False)
    if not llm_enabled:
        log.debug(
            "LLM re-scoring skipped — llm.enabled is false in config",
            symbol=symbol,
        )
        return articles

    from llm.llm_client import get_llm_client  # lazy import avoids circular deps

    client = get_llm_client(config)
    if client is None:
        log.debug("LLM client unavailable — skipping re-scoring", symbol=symbol)
        return articles

    count = 0
    for article in articles:
        prompt = (
            f"You are a financial news sentiment analyser for Indian equities.\n"
            f"Is the following news article positive, negative, or neutral for"
            f" the stock symbol {symbol}?\n"
            f"Reply ONLY with a valid JSON object, no explanation:\n"
            f'{{"sentiment": <float from -1.0 to 1.0>, "reason": "<one sentence max>"}}\n'
            f"Article: {article['title']}. {article.get('summary', '')}"
        )
        try:
            response = client.complete(prompt, max_tokens=120)
            # Strip markdown code fences that some models emit
            cleaned = response.strip().strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip()
            parsed = json.loads(cleaned)
            sentiment = float(parsed["sentiment"])
            article["sentiment"] = sentiment
            article["score"] = round(sentiment * 100)
            count += 1
        except (LLMError, json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning(
                "LLM rescore failed for article",
                symbol=symbol,
                reason=str(exc),
            )

    log.debug("LLM rescore complete", symbol=symbol, articles_rescored=count)
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# Private — single-feed fetcher (called inside ThreadPoolExecutor)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_one_feed(url: str) -> list[dict]:
    """
    Download and parse one RSS feed URL using feedparser.

    Designed to be called from a ThreadPoolExecutor.  All exceptions are
    caught internally — the function always returns a (possibly empty) list.

    feedparser.bozo handling:
        feedparser sets bozo=True when the feed is not well-formed XML but
        may still return partial entries.  We log a WARNING if a real
        exception was attached, but continue processing any entries that
        parsed successfully — maximising coverage from Indian news feeds
        that frequently emit slightly malformed XML.

    Args:
        url: RSS feed URL string.

    Returns:
        List of article dicts with keys:
            title, summary, url, source, published, sentiment, score.
        Empty list on complete failure.
    """
    try:
        parsed = feedparser.parse(url)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "feedparser raised an exception — feed skipped",
            feed=url,
            error=str(exc),
        )
        return []

    # Warn on bozo but keep any entries that did parse
    if getattr(parsed, "bozo", False):
        bozo_exc = getattr(parsed, "bozo_exception", None)
        if bozo_exc is not None:
            log.warning(
                "RSS feed is malformed — processing partial entries",
                feed=url,
                bozo_exception=str(bozo_exc),
            )

    entries = getattr(parsed, "entries", [])
    if not entries:
        log.debug("RSS feed returned 0 entries", feed=url)
        return []

    source_domain = urlparse(url).netloc or url
    articles: list[dict] = []

    for entry in entries:
        title   = (getattr(entry, "title",   None) or "").strip()
        summary = (getattr(entry, "summary", None) or "").strip()
        link    = (getattr(entry, "link",    None) or "").strip()

        # Prefer published_parsed (time.struct_time) for reliable ISO conversion;
        # fall back to the raw published/updated strings.
        published_str = ""
        published_parsed = getattr(entry, "published_parsed", None)
        if published_parsed:
            try:
                published_str = datetime(
                    *published_parsed[:6], tzinfo=timezone.utc
                ).isoformat()
            except Exception:  # noqa: BLE001
                pass
        if not published_str:
            published_str = (
                getattr(entry, "published", None)
                or getattr(entry, "updated", None)
                or ""
            )

        combined_text = title + " " + summary
        sentiment = _keyword_score(combined_text)

        articles.append({
            "title":     title,
            "summary":   summary,
            "url":       link,
            "source":    source_domain,
            "published": published_str,
            "sentiment": sentiment,
            "score":     sentiment * 100.0,
        })

    log.debug("RSS feed fetched", feed=url, articles=len(articles))
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# Public — market-wide news fetcher
# ─────────────────────────────────────────────────────────────────────────────

def fetch_market_news(
    config: dict,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Fetch market-wide news from all configured RSS feeds.

    Articles are keyword-scored on fetch and cached for
    config["news"]["cache_minutes"] minutes (default: 30).
    Feeds are fetched in parallel using a ThreadPoolExecutor with 4 workers.

    Args:
        config:        Parsed settings.yaml dict.
        force_refresh: If True, bypass the cache and always re-fetch.

    Returns:
        List of article dicts.  Each dict contains:
            title (str), summary (str), url (str), source (str),
            published (str ISO-8601), sentiment (float -1.0–+1.0),
            score (float -100.0–+100.0).
        Returns [] immediately if news.enabled is False in config.
        Returns [] (not raises) if all feeds fail.
    """
    news_cfg = config.get("news", {})

    # ── 1. Feature gate ───────────────────────────────────────────────────────
    if not news_cfg.get("enabled", True):
        log.debug("News disabled in config — skipping fetch")
        return []

    ttl_minutes: int = int(news_cfg.get("cache_minutes", 30))

    # ── 2. Cache check ────────────────────────────────────────────────────────
    news_dir = Path(config["data"]["news_dir"])
    cache_path = news_dir / "market_news.json"

    if not force_refresh and _is_cache_fresh(cache_path, ttl_minutes):
        cached = _load_news_cache(cache_path)
        if cached is not None:
            log.debug(
                "News cache hit",
                path=str(cache_path),
                articles=len(cached),
                ttl_minutes=ttl_minutes,
            )
            return cached

    # ── 3. Parallel RSS fetch ─────────────────────────────────────────────────
    feeds: list[str] = news_cfg.get("rss_feeds", RSS_FEEDS)
    log.debug("Fetching RSS feeds", count=len(feeds))

    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_url = {executor.submit(_fetch_one_feed, url): url for url in feeds}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                feed_articles = future.result()
            except Exception as exc:  # noqa: BLE001
                # _fetch_one_feed never raises, but guard anyway
                log.warning(
                    "Unexpected error from feed worker",
                    feed=url,
                    error=str(exc),
                )
                feed_articles = []

            # Deduplicate by URL
            for article in feed_articles:
                art_url = article.get("url", "")
                if art_url and art_url in seen_urls:
                    continue
                if art_url:
                    seen_urls.add(art_url)
                all_articles.append(article)

    log.info(
        "Market news fetched",
        feeds=len(feeds),
        articles=len(all_articles),
    )

    # ── 4. Keyword scoring is already applied inside _fetch_one_feed. ─────────
    #       (score = sentiment * 100 is set there.)

    # ── 5. Atomic cache save ──────────────────────────────────────────────────
    _save_news_cache(cache_path, all_articles)

    return all_articles


# ─────────────────────────────────────────────────────────────────────────────
# Public — per-symbol news fetcher
# ─────────────────────────────────────────────────────────────────────────────

def fetch_symbol_news(
    symbol: str,
    config: dict,
    all_news: list[dict] | None = None,
) -> list[dict]:
    """
    Filter market-wide news for a specific symbol using alias matching.

    If all_news is not provided, fetch_market_news() is called first.
    Optionally re-scores matched articles with an LLM when
    ``config["news"]["llm_rescore"]`` is True (requires ``config["llm"]["enabled"]``).

    Alias matching is case-insensitive substring search in
    title.lower() and summary.lower().  The alias list is loaded from
    config/symbol_aliases.yaml; if the symbol has no entry, [symbol.lower()]
    is used as the fallback alias.

    Args:
        symbol:   NSE symbol string (e.g. "RELIANCE").
        config:   Parsed settings.yaml dict.
        all_news: Pre-fetched market news list.  Pass this when calling
                  fetch_symbol_news() for multiple symbols in a single run
                  to avoid redundant RSS fetches.

    Returns:
        Filtered (and optionally LLM-rescored) list of article dicts.
        Returns [] if no articles match or if news is disabled.
        Never raises.
    """
    # ── 1. Ensure we have a market news pool ──────────────────────────────────
    if all_news is None:
        all_news = fetch_market_news(config)

    if not all_news:
        return []

    # ── 2. Resolve aliases ────────────────────────────────────────────────────
    aliases_map = _load_aliases(config)
    aliases: list[str] = aliases_map.get(symbol, [symbol.lower()])

    # ── 3. Filter articles ────────────────────────────────────────────────────
    filtered: list[dict] = []
    for article in all_news:
        title_lower   = article.get("title",   "").lower()
        summary_lower = article.get("summary", "").lower()
        if any(alias in title_lower or alias in summary_lower for alias in aliases):
            filtered.append(article)

    log.debug(
        "Symbol news filtered",
        symbol=symbol,
        aliases=aliases,
        matched=len(filtered),
        total=len(all_news),
    )

    if not filtered:
        return []

    # ── 4. Optional LLM re-scoring ────────────────────────────────────────────
    news_cfg = config.get("news", {})
    if news_cfg.get("llm_rescore", False):
        filtered = _llm_rescore(filtered, symbol, config)

    return filtered


# ─────────────────────────────────────────────────────────────────────────────
# Public — aggregate news score
# ─────────────────────────────────────────────────────────────────────────────

def _parse_published(published_str: str) -> datetime | None:
    """
    Parse a published timestamp string to a timezone-aware datetime.

    Tries three strategies in order:
        1. datetime.fromisoformat() — handles ISO-8601 (our own canonical format)
        2. email.utils.parsedate_to_datetime() — handles RFC 2822 / feedparser dates
        3. Returns None if both fail.

    Never raises.
    """
    if not published_str:
        return None
    # Strategy 1 — ISO-8601
    try:
        dt = datetime.fromisoformat(published_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    # Strategy 2 — RFC 2822
    try:
        return parsedate_to_datetime(published_str)
    except Exception:  # noqa: BLE001
        return None


def compute_news_score(articles: list[dict]) -> float:
    """
    Aggregate article-level scores into a single composite sentiment float.

    Weighting:
        Articles published within the last 24 hours receive 2× weight;
        older articles receive 1× weight.  This makes recent news dominate
        the signal without completely discarding older context.

    If a published date cannot be parsed for a given article, that article
    is treated as old (weight = 1.0) — a safe conservative assumption.

    Args:
        articles: List of article dicts, each containing a "score" key
                  (float in -100.0–+100.0) and a "published" key (str).

    Returns:
        float in [-100.0, +100.0].
        Returns 0.0 (neutral) if the articles list is empty.
    """
    if not articles:
        return 0.0

    now = datetime.now(tz=_IST)
    cutoff = now - timedelta(hours=24)

    total_weighted_score = 0.0
    total_weight = 0.0

    for article in articles:
        raw_score = float(article.get("score", 0.0))
        published_str = article.get("published", "")
        dt = _parse_published(published_str)

        # Determine recency weight
        if dt is not None:
            # Normalise to IST for comparison
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_ist = dt.astimezone(_IST)
            weight = 2.0 if dt_ist >= cutoff else 1.0
        else:
            weight = 1.0   # unknown date → treat as old

        total_weighted_score += raw_score * weight
        total_weight += weight

    if total_weight == 0.0:
        return 0.0

    raw_composite = total_weighted_score / total_weight
    return max(-100.0, min(100.0, raw_composite))
