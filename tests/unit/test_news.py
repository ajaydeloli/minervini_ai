"""
tests/unit/test_news.py
────────────────────────
Full unit-test suite for ingestion/news.py.

Test groups:
  GROUP 1  _keyword_score()                          5 tests
  GROUP 2  compute_news_score()                      5 tests
  GROUP 3  fetch_market_news() with mocking          4 tests
  GROUP 4  fetch_symbol_news()                       5 tests
  GROUP 5  _load_aliases()                           3 tests
  GROUP 6  News disabled                             1 test
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ingestion.news as news_module
from ingestion.news import (
    BULLISH,
    BEARISH,
    _keyword_score,
    _load_aliases,
    compute_news_score,
    fetch_market_news,
    fetch_symbol_news,
)

# ─────────────────────────────────────────────────────────────────────────────
# Timezone constant (matches news.py)
# ─────────────────────────────────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_config(news_dir: str = "/tmp/minervini_news_test", enabled: bool = True) -> dict:
    """Return a minimal config dict for news tests."""
    return {
        "news": {
            "enabled": enabled,
            "cache_minutes": 30,
            "rss_feeds": ["https://fake.feed/rss"],
            "llm_rescore": False,
        },
        "data": {
            "news_dir": news_dir,
        },
    }


def make_article(
    title: str = "Market rallies on strong earnings",
    summary: str = "Stocks surge as companies beat expectations.",
    score: float = 100.0,
    sentiment: float = 1.0,
    published: str = "",
) -> dict:
    """Return a minimal article dict matching the news.py schema."""
    return {
        "title": title,
        "summary": summary,
        "url": "https://example.com/news/1",
        "source": "example.com",
        "published": published,
        "sentiment": sentiment,
        "score": score,
    }


def make_mock_entry(
    title: str = "Generic headline",
    summary: str = "Generic body text.",
    link: str = "https://example.com/art/1",
) -> MagicMock:
    """Return a MagicMock that looks like a feedparser entry."""
    entry = MagicMock()
    entry.title = title
    entry.summary = summary
    entry.link = link
    entry.published_parsed = None
    entry.published = ""
    entry.updated = ""
    return entry


def make_mock_feed(entries=None, bozo: bool = False) -> MagicMock:
    """Return a MagicMock that looks like a feedparser result."""
    feed = MagicMock()
    feed.bozo = bozo
    feed.bozo_exception = None
    feed.entries = entries if entries is not None else [make_mock_entry()]
    return feed


def write_market_cache(news_dir: Path, articles: list[dict], age_minutes: float = 0) -> None:
    """Write a market_news.json cache file with controlled age."""
    news_dir.mkdir(parents=True, exist_ok=True)
    cached_at = datetime.now(tz=_IST) - timedelta(minutes=age_minutes)
    payload = {
        "_cached_at": cached_at.isoformat(),
        "articles": articles,
    }
    cache_path = news_dir / "market_news.json"
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: clear module-level alias cache between tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def clear_alias_cache():
    """Wipe the module-level _aliases_cache before and after each test."""
    news_module._aliases_cache.clear()
    yield
    news_module._aliases_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 1 — _keyword_score()
# ─────────────────────────────────────────────────────────────────────────────

class TestKeywordScore:
    """_keyword_score(text) -> float in [-1.0, +1.0]"""

    def test_pure_bullish_text_returns_positive(self):
        """Text containing only bullish keywords → score > 0."""
        text = "company shows surge in rally and dividend growth"
        result = _keyword_score(text)
        assert result > 0.0, f"Expected positive score, got {result}"

    def test_pure_bearish_text_returns_negative(self):
        """Text containing only bearish keywords → score < 0."""
        text = "sebi probe finds fraud and regulatory penalty for loss"
        result = _keyword_score(text)
        assert result < 0.0, f"Expected negative score, got {result}"

    def test_mixed_more_bullish_returns_positive(self):
        """Text with more bullish than bearish keywords → score > 0."""
        # Use 3 bullish keywords and 1 bearish keyword
        bullish_kws = BULLISH[:3]
        bearish_kws = BEARISH[:1]
        text = " ".join(bullish_kws + bearish_kws)
        result = _keyword_score(text)
        assert result > 0.0, f"Expected positive score for net-bullish text, got {result}"

    def test_no_matching_keywords_returns_zero(self):
        """Text with no bullish or bearish keywords → score == 0.0 exactly."""
        text = "the weather today is pleasant and clouds are forming"
        result = _keyword_score(text)
        assert result == 0.0

    def test_many_bullish_keywords_clamped_to_one(self):
        """Even with 10+ bullish keywords, result <= 1.0 (clamp enforced)."""
        # Use all BULLISH keywords — raw score would be len(BULLISH) without clamp
        text = " ".join(BULLISH)
        result = _keyword_score(text)
        assert result <= 1.0, f"Expected result clamped to 1.0, got {result}"
        assert result > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 2 — compute_news_score()
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeNewsScore:
    """compute_news_score(articles) -> float in [-100.0, +100.0]"""

    def test_empty_list_returns_zero(self):
        """No articles → 0.0 (neutral)."""
        assert compute_news_score([]) == 0.0

    def test_all_positive_articles_returns_positive(self):
        """All articles with positive scores → composite > 0."""
        articles = [
            make_article(score=80.0, sentiment=0.8),
            make_article(score=60.0, sentiment=0.6),
            make_article(score=100.0, sentiment=1.0),
        ]
        result = compute_news_score(articles)
        assert result > 0.0

    def test_all_negative_articles_returns_negative(self):
        """All articles with negative scores → composite < 0."""
        articles = [
            make_article(score=-80.0, sentiment=-0.8),
            make_article(score=-60.0, sentiment=-0.6),
            make_article(score=-100.0, sentiment=-1.0),
        ]
        result = compute_news_score(articles)
        assert result < 0.0

    def test_mixed_articles_result_between_bounds(self):
        """Mix of positive and negative articles → result in (-100, +100)."""
        articles = [
            make_article(score=100.0, sentiment=1.0),
            make_article(score=-100.0, sentiment=-1.0),
            make_article(score=50.0, sentiment=0.5),
            make_article(score=-30.0, sentiment=-0.3),
        ]
        result = compute_news_score(articles)
        assert -100.0 <= result <= 100.0

    def test_result_always_within_bounds(self):
        """Result must never exceed [-100.0, +100.0] regardless of inputs."""
        # Articles with extreme scores that could overflow without clamping
        articles = [make_article(score=100.0, sentiment=1.0)] * 50
        result_high = compute_news_score(articles)
        assert result_high <= 100.0

        articles_low = [make_article(score=-100.0, sentiment=-1.0)] * 50
        result_low = compute_news_score(articles_low)
        assert result_low >= -100.0


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 3 — fetch_market_news() with mocking
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchMarketNews:
    """fetch_market_news(config, force_refresh=False) -> list[dict]"""

    def test_news_disabled_returns_empty_without_http(self, tmp_path):
        """news.enabled=False → [] returned, feedparser never called."""
        config = make_config(news_dir=str(tmp_path), enabled=False)
        with patch("ingestion.news.feedparser.parse") as mock_parse:
            result = fetch_market_news(config)
        assert result == []
        mock_parse.assert_not_called()

    def test_fresh_cache_returns_cached_articles_without_http(self, tmp_path):
        """Cache within TTL → cached articles returned, feedparser NOT called."""
        articles = [make_article(title="Cached headline", score=50.0, sentiment=0.5)]
        write_market_cache(tmp_path, articles, age_minutes=5)  # 5 min old; TTL=30
        config = make_config(news_dir=str(tmp_path))

        with patch("ingestion.news.feedparser.parse") as mock_parse:
            result = fetch_market_news(config)

        mock_parse.assert_not_called()
        assert len(result) == 1
        assert result[0]["title"] == "Cached headline"

    def test_cache_miss_fetches_feeds_and_writes_cache(self, tmp_path):
        """No cache → feedparser called, articles returned, cache file written."""
        config = make_config(news_dir=str(tmp_path))
        entry = make_mock_entry(
            title="Reliance industries surge on new order win",
            summary="Strong quarter results beat estimates.",
            link="https://example.com/art/2",
        )
        mock_feed = make_mock_feed(entries=[entry])

        with patch("ingestion.news.feedparser.parse", return_value=mock_feed):
            result = fetch_market_news(config)

        # Articles returned
        assert len(result) == 1
        assert result[0]["title"] == "Reliance industries surge on new order win"

        # Cache written to disk
        cache_path = tmp_path / "market_news.json"
        assert cache_path.exists(), "Cache file should have been written after fetch"

    def test_all_feeds_fail_returns_empty_no_crash(self, tmp_path):
        """All feeds return empty entries → [] returned, no exception raised."""
        config = make_config(news_dir=str(tmp_path))
        empty_feed = make_mock_feed(entries=[])

        with patch("ingestion.news.feedparser.parse", return_value=empty_feed):
            result = fetch_market_news(config)

        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 4 — fetch_symbol_news()
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchSymbolNews:
    """fetch_symbol_news(symbol, config, all_news=None) -> list[dict]"""

    def _cfg(self, tmp_path: Path) -> dict:
        return make_config(news_dir=str(tmp_path))

    def test_matching_articles_returned(self, tmp_path, clear_alias_cache):
        """Articles mentioning symbol alias appear in the returned list."""
        config = self._cfg(tmp_path)
        # RELIANCE aliases include "reliance industries"
        matching = make_article(
            title="Reliance industries announces record profit",
            summary="RIL Q4 results beat all estimates.",
        )
        unrelated = make_article(
            title="TCS wins large deal from US bank",
            summary="Tata Consultancy Services scores big.",
        )
        result = fetch_symbol_news("RELIANCE", config, all_news=[matching, unrelated])
        assert len(result) == 1
        assert result[0]["title"] == matching["title"]

    def test_no_matching_articles_returns_empty(self, tmp_path, clear_alias_cache):
        """When no article mentions the symbol, [] is returned."""
        config = self._cfg(tmp_path)
        unrelated = make_article(
            title="Gold prices fall on strong dollar",
            summary="Commodity markets see a pullback.",
        )
        result = fetch_symbol_news("INFY", config, all_news=[unrelated])
        assert result == []

    def test_uses_provided_all_news_without_refetch(self, tmp_path, clear_alias_cache):
        """When all_news is supplied, fetch_market_news is NOT called."""
        config = self._cfg(tmp_path)
        pool = [
            make_article(
                title="Infosys revenue up sharply in Q3",
                summary="Infosys bpo records strong growth.",
            )
        ]
        with patch("ingestion.news.fetch_market_news") as mock_fetch:
            result = fetch_symbol_news("INFY", config, all_news=pool)
        mock_fetch.assert_not_called()
        assert len(result) == 1

    def test_alias_matching_is_case_insensitive(self, tmp_path, clear_alias_cache):
        """Article title with UPPERCASED alias text still matches."""
        config = self._cfg(tmp_path)
        # "HDFC BANK" uppercased — alias is "hdfc bank" (lowercase)
        article = make_article(
            title="HDFC BANK reports record quarterly profit",
            summary="Strong NII growth drives results.",
        )
        result = fetch_symbol_news("HDFCBANK", config, all_news=[article])
        assert len(result) == 1

    def test_unknown_symbol_falls_back_to_symbol_lower(self, tmp_path, clear_alias_cache):
        """Symbol absent from aliases.yaml → falls back to [symbol.lower()]."""
        config = self._cfg(tmp_path)
        # "NEWSTOCK" is not in the YAML; fallback is "newstock"
        article = make_article(
            title="newstock hits 52-week high on strong buying",
            summary="Retail investors accumulate newstock shares.",
        )
        result = fetch_symbol_news("NEWSTOCK", config, all_news=[article])
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 5 — _load_aliases()
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadAliases:
    """_load_aliases(config) -> dict[str, list[str]]"""

    def test_loads_aliases_from_yaml_correctly(self, clear_alias_cache):
        """Real YAML is parsed; known symbol is present with correct aliases."""
        aliases = _load_aliases({})
        # RELIANCE is in the YAML with known aliases
        assert "RELIANCE" in aliases
        alias_list = aliases["RELIANCE"]
        assert isinstance(alias_list, list)
        assert len(alias_list) > 0
        # At least one well-known alias exists
        assert any("reliance" in a for a in alias_list)

    def test_returns_empty_dict_when_yaml_missing(self, clear_alias_cache):
        """If aliases YAML file is not found, returns {} without raising."""
        fake_path = Path("/nonexistent/path/no_aliases.yaml")
        with patch("ingestion.news._ALIASES_PATH", fake_path):
            aliases = _load_aliases({})
        assert aliases == {}

    def test_all_alias_values_are_lowercase_strings(self, clear_alias_cache):
        """Every alias value in the loaded dict is a non-empty lowercase string."""
        aliases = _load_aliases({})
        for symbol, alias_list in aliases.items():
            for alias in alias_list:
                assert isinstance(alias, str), (
                    f"{symbol}: alias {alias!r} is not a str"
                )
                # Allow trailing space (intentional in YAML for short tickers)
                assert alias == alias.lower(), (
                    f"{symbol}: alias {alias!r} is not lowercase"
                )


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 6 — News disabled (integration path through fetch_symbol_news)
# ─────────────────────────────────────────────────────────────────────────────

class TestNewsDisabled:
    """When news.enabled=False, fetch_symbol_news should return []."""

    def test_fetch_symbol_news_disabled_returns_empty(self, tmp_path, clear_alias_cache):
        """fetch_symbol_news with news.enabled=False → []."""
        config = make_config(news_dir=str(tmp_path), enabled=False)

        # Even if we pass articles in, fetch_market_news would short-circuit
        # because all_news=None triggers an internal call that sees enabled=False.
        with patch("ingestion.news.feedparser.parse") as mock_parse:
            result = fetch_symbol_news("RELIANCE", config, all_news=None)

        mock_parse.assert_not_called()
        assert result == []
