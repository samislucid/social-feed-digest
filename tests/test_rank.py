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


def test_followed_author_boost_breaks_score_tie(base_profile):
    # Both topics score 3.0 (one ai-core keyword each); the boost decides order.
    a = _item("Inference costs drop again", channel="x", url="https://x.com/devA/status/1")
    a.extra = {"author": "devA"}
    b = _item("Agents adoption doubles", channel="x", url="https://x.com/devB/status/2")
    b.extra = {"author": "devB"}
    unboosted = build_topics([a, b], base_profile)
    assert unboosted[0].items[0].extra["author"] == "devB"
    boosted = build_topics([a, b], base_profile, followed_handles=["@DevA"])
    assert boosted[0].items[0].extra["author"] == "devA"


def test_followed_author_ignored_without_seam(base_profile):
    a = _item("Inference cost drops 90% overnight", channel="x", url="https://x.com/devA/status/1")
    a.extra = {"author": "devA"}
    plain = build_topics([a], base_profile)
    assert build_topics([a], base_profile, followed_handles=[])[0].score == plain[0].score


def test_fewer_than_min_topics_still_returns(base_profile):
    items = [_item("Single LLM topic", channel="reddit")]
    topics = build_topics(items, base_profile)
    assert len(topics) == 1


def test_token_cost_post_assigns_to_the_token_costs_niche(base_profile):
    from pathlib import Path

    from digest.config import load_profile

    profile = load_profile(Path(__file__).resolve().parents[1] / "profile.yaml")
    cost = _item(
        "Frontier API pricing war: per-token price cut 40%",
        channel="x",
        url="https://x.com/u/status/9",
        summary="Cost comparison thread: token cost down across two providers.",
    )
    topics = build_topics([cost], profile)
    assert topics[0].niche == "token-costs"


def test_token_cost_post_outranks_generic_ai_for_cost_keywords(base_profile):
    from pathlib import Path

    from digest.config import load_profile

    profile = load_profile(Path(__file__).resolve().parents[1] / "profile.yaml")
    cost = _item(
        "Frontier API pricing war: per-token price cut 40%",
        channel="x",
        url="https://x.com/u/status/9",
        summary="Cost comparison thread: token cost down across two providers.",
    )
    generic = _item("Open-weights model tops reasoning evals", channel="x", url="https://x.com/u/status/10")
    topics = build_topics([cost, generic], profile)
    assert topics[0].niche == "token-costs"
    assert topics[0].score > topics[1].score
