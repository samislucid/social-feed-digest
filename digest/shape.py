"""Channel-first shaping: split ranked topics into per-channel action sections.

The digest reads by channel, not by a cross-channel ranked topic list: X first
(activity summary, notable tweets, ready-to-post drafts, comments/reposts), then
LinkedIn mirroring that structure, then Reddit capped at its best few posts, then
a compact web/news context section.

Shaping is deterministic. It decides which posts exist in each section and
carries their real links and authors; drafting (draft.py) fills voice on top.
A claude failure changes the words, never the shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .items import Item
from .rank import Topic

SECTION_ORDER = ("x", "linkedin", "reddit", "web")

# Which section a topic lands in: the highest-priority channel among its items.
# A story with both an X post and a Reddit thread reads as X activity; its
# Reddit thread is background there, not a second headline, which keeps the
# Reddit surface equal to the Reddit section's cap.
_PRIMARY = {"x": 0, "linkedin": 1, "reddit": 2, "web": 3, "news": 3}

# Post ceilings per section. Reddit's cap is a user requirement: at most
# reddit.max_digest_posts (default 5) Reddit posts across the whole digest.
_CANDIDATE_CAPS = {"x": 6, "linkedin": 8, "web": 4}

_EMPTY_NOTES = {
    "x": "No X posts collected this run (xAI Live Search unavailable or skipped).",
    "linkedin": (
        "No LinkedIn posts this run. LinkedIn arrives through the watched inbox "
        "(Bright-assisted input); nothing reached it this run."
    ),
    "reddit": "No Reddit posts collected this run (RSS rate-limited or unreachable).",
}


@dataclass
class Notable:
    """One specific post: real link + author, with why it deserves attention."""

    index: int  # 1-based position within the section's candidate list
    title: str
    url: str
    author: str
    why: str
    engagement: int = 0  # shown on the card only when held (e.g. Reddit score)
    image: str = ""  # thumbnail URL held by the pipeline or attached at render time
    # 2026-09-08 mock redesign: the email's left rail shows the niche, the
    # source label (e.g. "r/LLMDevs"), and the post's local time. All three
    # default empty; the page renderer ignores them, so the page is unchanged.
    niche: str = ""
    source_label: str = ""
    posted_at: datetime | None = None


@dataclass
class Engagement:
    """One recommended manual action on a specific post (the worker never posts)."""

    index: int
    title: str
    url: str
    author: str
    action: str  # "comment" | "retweet" | "reshare"
    comment: str


@dataclass
class Section:
    """One channel's action-first slice of the digest."""

    channel: str
    candidates: list[Topic] = field(default_factory=list)
    summary: str = ""
    notable: list[Notable] = field(default_factory=list)
    drafts: list[str] = field(default_factory=list)
    engagements: list[Engagement] = field(default_factory=list)
    intro: str = ""  # deterministic context line, e.g. "best 5 of 17 Reddit posts"
    empty_note: str = ""


def primary_channel(topic: Topic) -> str:
    """The section a topic renders in: its highest-priority channel."""
    primary = min(topic.channels, key=lambda c: _PRIMARY.get(c, 9), default="web")
    return "web" if primary == "news" else primary  # news renders in the web context section


def _item_author(item: Item) -> str:
    return (str((item.extra or {}).get("author") or "").strip() or item.source_label.strip())


def topic_author(topic: Topic) -> str:
    """Author/source label of the topic's primary item ('@handle', 'r/sub', ...)."""
    for item in sorted(topic.items, key=lambda i: _PRIMARY.get(i.channel, 9)):
        author = _item_author(item)
        if author:
            return author
    return ""


def _primary_item(topic: Topic, channel: str | None = None) -> Item | None:
    """The topic's highest-priority item (optionally within one channel)."""
    items = [i for i in topic.items if channel is None or i.channel == channel]
    if not items:
        return None
    return sorted(items, key=lambda i: _PRIMARY.get(i.channel, 9))[0]


def notable_from_topic(topic: Topic, index: int, why: str = "") -> Notable:
    primary = primary_channel(topic)
    engagement = sum(item.engagement for item in topic.items if item.channel == primary)
    image = ""
    for item in sorted(topic.items, key=lambda i: _PRIMARY.get(i.channel, 9)):
        candidate = str((item.extra or {}).get("image") or "").strip()
        if candidate.startswith(("http://", "https://")):
            image = candidate
            break
    # 2026-09-08 email redesign: the left rail wants the niche id, the source
    # label of the primary item (e.g. "r/LLMDevs" next to REDDIT), and the
    # post's local time. topic.niche is the niche id ("ai-core"); the human
    # label lives in the profile and the email renders ids as-is (the mock
    # shows the id, e.g. "asian-economics").
    source_item = _primary_item(topic)
    source_label = source_item.source_label if source_item else ""
    posted_at = None
    for item in sorted(topic.items, key=lambda i: _PRIMARY.get(i.channel, 9)):
        if item.published:
            posted_at = item.published
            break
    return Notable(
        index=index,
        title=topic.title,
        url=topic.source_url,
        author=topic_author(topic),
        why=why or topic.why_hot,
        engagement=engagement,
        image=image,
        niche=topic.niche,
        source_label=source_label,
        posted_at=posted_at,
    )


def shape_sections(topics: list[Topic], profile: dict) -> list[Section]:
    """Assign ranked topics to channel sections, capping Reddit at its best posts."""
    reddit_cap = max(0, int((profile.get("reddit") or {}).get("max_digest_posts", 5)))

    buckets: dict[str, list[Topic]] = {c: [] for c in SECTION_ORDER}
    for topic in topics:  # topics arrive score-ranked
        buckets[primary_channel(topic)].append(topic)

    # Denominator for the honest "best k of n" line: every distinct Reddit URL
    # collected this run, including ones riding inside cross-channel topics.
    reddit_urls = {i.url for t in topics for i in t.items if i.channel == "reddit"}

    reddit_picked = buckets["reddit"][:reddit_cap]
    intro = (
        f"best {len(reddit_picked)} of {len(reddit_urls)} Reddit posts this run"
        if reddit_picked
        else ""
    )
    sections = [
        Section(
            channel="x",
            candidates=buckets["x"][: _CANDIDATE_CAPS["x"]],
            empty_note=_EMPTY_NOTES["x"],
        ),
        Section(
            channel="linkedin",
            candidates=buckets["linkedin"][: _CANDIDATE_CAPS["linkedin"]],
            empty_note=_EMPTY_NOTES["linkedin"],
        ),
        Section(
            channel="reddit",
            candidates=reddit_picked,
            intro=intro,
            empty_note=_EMPTY_NOTES["reddit"],
        ),
        Section(
            channel="web",
            candidates=buckets["web"][: _CANDIDATE_CAPS["web"]],
        ),
    ]
    # Deterministic post lists for the sections the reader scans directly.
    for section in sections:
        if section.channel in ("reddit", "web"):
            section.notable = [
                notable_from_topic(topic, i) for i, topic in enumerate(section.candidates, 1)
            ]
    # Web/news is context only; with nothing collected it is omitted rather
    # than shipping an empty section.
    return [s for s in sections if s.channel != "web" or s.candidates]
