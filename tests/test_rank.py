from __future__ import annotations

from datetime import datetime, timedelta, timezone

from digest.items import Item
from digest.rank import build_topics


def _item(title: str, channel: str = "reddit", url: str | None = None, **kw) -> Item:
    return Item(
        channel=channel,
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        summary=kw.pop("summary", "some detail"),
        **kw,
    )


def test_dedupe_merges_same_story_across_channels(base_profile):
    items = [
        _item("Grok ships new coding agent for developers", channel="x"),
        _item("grok coding agent ships for developers!", channel="reddit"),
        _item("49ers injury report week 2", channel="reddit"),
    ]
    topics = build_topics(items, base_profile)
    assert len(topics) == 2
    merged = next(t for t in topics if "grok" in t.title.lower())
    assert {i.channel for i in merged.items} == {"x", "reddit"}
    assert set(merged.channel_labels) == {"x", "reddit"}


def test_ranking_prefers_profile_relevance(base_profile):
    items = [
        _item("Local LLM inference benchmark results", channel="reddit"),
        _item("NFL week two power rankings", channel="web"),
    ]
    topics = build_topics(items, base_profile)
    assert topics[0].niche == "ai-core"
    assert "LLM" in topics[0].title


def test_multi_source_boost_and_recency(base_profile):
    old = datetime.now(timezone.utc) - timedelta(days=2)
    fresh = datetime.now(timezone.utc)
    solo_old = _item("Bank of Korea cuts base rate as won slides", published=old)
    multi_fresh = _item("China semiconductor export policy shifts", published=fresh)
    multi_fresh_b = _item("China semiconductor export policy shifts", channel="x", published=fresh)
    topics = build_topics([solo_old, multi_fresh, multi_fresh_b], base_profile)
    china = next(t for t in topics if "China" in t.title)
    korea = next(t for t in topics if "Korea" in t.title)
    assert china.score > korea.score
    assert "x" in china.channel_labels


def test_max_topics_clamp(base_profile):
    items = [_item(f"Zebra{i} report", channel="web") for i in range(12)]
    base_profile["rank"]["max_topics"] = 3
    topics = build_topics(items, base_profile)
    assert len(topics) == 3


def test_quiet_share_prefers_x_link(base_profile):
    items = [
        _item("New open-weights model tops evals", channel="x", url="https://x.com/u/status/1"),
        _item("New open-weights model tops evals", channel="reddit", url="https://reddit.com/r/x/1"),
    ]
    topic = build_topics(items, base_profile)[0]
    assert topic.quiet_share_url == "https://x.com/u/status/1"
    assert topic.source_url == "https://x.com/u/status/1"


def test_fewer_than_min_topics_still_returns(base_profile):
    items = [_item("Single LLM topic", channel="reddit")]
    topics = build_topics(items, base_profile)
    assert len(topics) == 1
