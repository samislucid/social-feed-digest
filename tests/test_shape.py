from __future__ import annotations

from digest.items import Item
from digest.rank import build_topics
from digest.shape import SECTION_ORDER, shape_sections

_REDDIT_THEMES = [
    "agents",
    "evals",
    "inference cost",
    "GPU supply",
    "open models",
    "RAG pipelines",
    "fine-tuning data",
    "quantization tricks",
]


def _item(channel: str, title: str, url: str, **kw) -> Item:
    return Item(channel=channel, title=title, url=url, **kw)


def _profile(**reddit_extra) -> dict:
    return {
        "name": "test profile",
        "audience": "test",
        "niches": [{"id": "ai-core", "label": "Core AI", "weight": 1.5, "keywords": ["AI", "LLM"]}],
        "reddit": {"subreddits": ["LLMDevs"], "limit": 25, **reddit_extra},
        "x_search": {"window_hours": 48, "handles": [], "candidate_topics": 6},
        "rank": {"min_topics": 2, "max_topics": 8},
    }


def _sections_for(items, profile=None):
    topics = build_topics(items, profile or _profile())
    return shape_sections(topics, profile or _profile())


def test_sections_are_channel_first_in_required_order():
    items = [
        _item("x", "Agent evals got practical", "https://x.com/u/status/1", extra={"author": "@a"}),
        _item("web", "Eval vendors publish new benchmarks", "https://example.com/evals", source_label="example.com"),
    ]
    sections = _sections_for(items)
    # X first, then LinkedIn, then Reddit (both keep their empty-note sections);
    # web/news context renders only when something was collected.
    assert [s.channel for s in sections] == ["x", "linkedin", "reddit", "web"]


def test_reddit_capped_at_best_five_posts_across_the_digest():
    profile = _profile(max_digest_posts=5)
    items = [
        _item(
            "reddit",
            f"LLM {theme} thread with bench numbers",
            f"https://reddit.com/r/LLMDevs/{i}",
            source_label="r/LLMDevs",
        )
        for i, theme in enumerate(_REDDIT_THEMES, 1)
    ]
    sections = _sections_for(items, profile)
    reddit = next(s for s in sections if s.channel == "reddit")
    assert len(reddit.candidates) == 5  # the cap, not the 8 collected
    assert len(reddit.notable) == 5
    assert reddit.intro == "best 5 of 8 Reddit posts this run"
    urls = {n.url for n in reddit.notable}
    assert len(urls) == 5


def test_reddit_cap_is_configurable_and_honest_when_sparse():
    profile = _profile(max_digest_posts=2)
    items = [
        _item("reddit", f"LLM {theme} thread", f"https://reddit.com/r/LLMDevs/{i}", source_label="r/LLMDevs")
        for i, theme in enumerate(_REDDIT_THEMES[:3], 1)
    ]
    sections = _sections_for(items, profile)
    reddit = next(s for s in sections if s.channel == "reddit")
    assert len(reddit.candidates) == 2
    assert reddit.intro == "best 2 of 3 Reddit posts this run"


def test_cross_channel_topic_renders_in_x_and_counts_toward_reddit_denominator():
    # One story on X + Reddit merges into one topic that renders in X; its
    # Reddit URL still counts toward the Reddit denominator but never headlines.
    items = [
        _item("x", "Agent evals got practical", "https://x.com/u/status/1", extra={"author": "@a"}),
        _item("reddit", "Agent evals got practical", "https://reddit.com/r/LLMDevs/99", source_label="r/LLMDevs"),
    ] + [
        _item("reddit", f"LLM {theme} thread", f"https://reddit.com/r/LLMDevs/{i}", source_label="r/LLMDevs")
        for i, theme in enumerate(_REDDIT_THEMES[:6], 1)
    ]
    sections = _sections_for(items)
    x = next(s for s in sections if s.channel == "x")
    reddit = next(s for s in sections if s.channel == "reddit")
    assert any(t.source_url == "https://x.com/u/status/1" for t in x.candidates)
    # The merged topic's Reddit URL is context, not a Reddit-section headline.
    assert all(n.url != "https://reddit.com/r/LLMDevs/99" for n in reddit.notable)
    assert reddit.intro == "best 5 of 7 Reddit posts this run"  # 6 threads + 1 in the merged topic


def test_linkedin_empty_section_keeps_visible_note():
    sections = _sections_for([_item("x", "Eval season", "https://x.com/u/status/1", extra={"author": "@a"})])
    linkedin = next(s for s in sections if s.channel == "linkedin")
    assert linkedin.candidates == []
    assert "watched inbox" in linkedin.empty_note


def test_web_news_context_section_omitted_when_empty():
    sections = _sections_for([_item("x", "Eval season", "https://x.com/u/status/1", extra={"author": "@a"})])
    assert all(s.channel != "web" for s in sections)


def test_web_news_section_renders_as_context():
    items = [
        _item("web", "Reuters: eval vendors merge", "https://reuters.com/evals", source_label="reuters.com"),
        _item("news", "FT: GPU supply tightens", "https://ft.com/gpu", source_label="ft.com"),
    ]
    sections = _sections_for(items)
    web = next(s for s in sections if s.channel == "web")
    assert len(web.notable) == 2
    assert {n.url for n in web.notable} == {"https://reuters.com/evals", "https://ft.com/gpu"}
