from __future__ import annotations

from pathlib import Path

from digest.collect.reddit_collector import RssRedditSource

FIXTURE = Path(__file__).parent / "fixtures" / "reddit_hot.rss"


def test_parse_payload_extracts_entries():
    source = RssRedditSource(user_agent="test-ua")
    items = source.parse_feed(FIXTURE.read_bytes(), "LLMDevs")
    assert len(items) == 2
    first = items[0]
    assert first.channel == "reddit"
    assert first.source_label == "r/LLMDevs"
    assert first.title == "Local LLM inference benchmarks - September 2026"
    assert first.url == "https://www.reddit.com/r/LLMDevs/comments/1abc/local_llm_inference/"
    assert first.published is not None


def test_parse_payload_strips_html_from_summary():
    source = RssRedditSource(user_agent="test-ua")
    items = source.parse_feed(FIXTURE.read_bytes(), "LLMDevs")
    assert "<b>" not in items[0].summary
    assert "benchmarks" in items[0].summary


def test_parse_payload_respects_limit():
    source = RssRedditSource(user_agent="test-ua")
    items = source.parse_feed(FIXTURE.read_bytes(), "LLMDevs", limit=1)
    assert len(items) == 1
