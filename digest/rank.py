"""Deterministic ranking: keyword priors, multi-source momentum, recency, dedupe.

Profile keywords and niche weights drive relevance; cross-source and
cross-channel agreement drive momentum. Same story across channels is merged
once and keeps each channel's share target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .items import Item

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the this to was were will with you your".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")
_BOUNDARY_KW_MAX = 4  # keywords this short match on word boundaries only


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1)


def _keyword_hits(text_lower: str, keyword: str) -> int:
    kw = keyword.lower().strip()
    if not kw:
        return 0
    if len(kw) <= _BOUNDARY_KW_MAX:
        return len(re.findall(rf"\b{re.escape(kw)}\b", text_lower))
    return text_lower.count(kw)


def niche_scores(text: str, niches: list[dict]) -> dict[str, float]:
    """Weighted keyword score per niche id for a piece of text."""
    text_lower = text.lower()
    scores: dict[str, float] = {}
    for niche in niches:
        weight = float(niche.get("weight", 1.0))
        hits = sum(_keyword_hits(text_lower, kw) for kw in niche.get("keywords") or [])
        if hits:
            scores[niche["id"]] = hits * weight
    return scores


@dataclass
class Topic:
    """A merged, ranked topic assembled from one or more items."""

    title: str
    items: list[Item]
    score: float = 0.0
    niche: str = ""
    channels: list[str] = field(default_factory=list)
    source_url: str = ""
    quiet_share_url: str = ""
    why_hot: str = ""
    comment: str = ""

    @property
    def channel_labels(self) -> list[str]:
        return sorted(set(self.channels))


_CHANNEL_PRIORITY = {"x": 0, "reddit": 1, "news": 2, "web": 3, "linkedin": 4}


def _pick_share_urls(group: list[Item]) -> tuple[str, str]:
    """(source_url, quiet_share_url): prefer an X post, then Reddit thread, then web."""
    def key(item: Item) -> tuple[int, int]:
        return (_CHANNEL_PRIORITY.get(item.channel, 9), -item.engagement)

    ordered = sorted(group, key=key)
    source = ordered[0].url if ordered else ""
    quiet = ordered[0].url if ordered else ""
    return source, quiet


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _item_author(item: Item) -> str:
    return str((item.extra or {}).get("author") or "")


def _group_items(items: list[Item], threshold: float = 0.55) -> list[list[Item]]:
    """Greedy clustering: identical URL, high title similarity, or the same
    author restating their own story (common on hot feeds) with weaker similarity."""
    groups: list[tuple[frozenset[str], list[Item]]] = []
    for item in sorted(items, key=lambda i: i.title):
        tokens = _tokens(item.title)
        author = _item_author(item)
        target = None
        for group in groups:
            members = group[1]
            if item.url in {i.url for i in members}:
                target = group
                break
            if _jaccard(tokens, group[0]) >= threshold:
                target = group
                break
            if author and _jaccard(tokens, group[0]) >= 0.25 and any(
                _item_author(i) == author and i.channel == item.channel for i in members
            ):
                target = group
                break
        if target is None:
            groups.append((tokens, [item]))
        else:
            target[1].append(item)
    return [g[1] for g in groups]


FOLLOWED_AUTHOR_BONUS = 2.5  # topic authored by an account Sam follows


def _followed_bonus(group: list[Item], followed: frozenset[str] | None) -> float:
    """Boost when any item in the group is authored by a followed X account."""
    if not followed:
        return 0.0
    for item in group:
        if _item_author(item).lstrip("@").lower() in followed:
            return FOLLOWED_AUTHOR_BONUS
    return 0.0


def _score(
    group: list[Item], niches: list[dict], now: datetime, followed: frozenset[str] | None = None
) -> tuple[float, str]:
    text = " ".join(i.title + " " + i.summary for i in group)
    scores = niche_scores(text, niches)
    niche, niche_score = max(scores.items(), key=lambda kv: kv[1]) if scores else ("", 0.0)
    channels = {i.channel for i in group}
    recency_hours = None
    published = [i.published for i in group if i.published]
    if published:
        recency_hours = max((now - p).total_seconds() for p in published) / 3600.0
    recency_bonus = 0.0
    if recency_hours is not None:
        recency_bonus = max(0.0, 3.0 - recency_hours / 12.0)  # 3 pts fresh, decays over 36h
    engagement = sum(i.engagement for i in group)
    engagement_bonus = min(3.0, engagement / 100.0) if engagement else 0.0
    score = (
        niche_score * 2.0
        + (len(group) - 1) * 1.5  # multi-source agreement
        + (2.0 if len(channels) > 1 else 0.0)  # cross-channel momentum
        + recency_bonus
        + engagement_bonus
        + _followed_bonus(group, followed)
    )
    return score, niche


def _why_hot(group: list[Item]) -> str:
    summaries = [i.summary.strip() for i in group if i.summary.strip()]
    if summaries:
        first = summaries[0]
        return first if len(first) <= 220 else first[:217].rstrip() + "..."
    channels = sorted({i.channel for i in group})
    labels = sorted({i.source_label for i in group if i.source_label})
    if labels:
        return f"Active on {', '.join(labels[:3])}"
    return f"Active across {', '.join(channels)}"


def build_topics(
    items: list[Item],
    profile: dict,
    now: datetime | None = None,
    followed_handles: list[str] | None = None,
) -> list[Topic]:
    """Merge, score, rank, and clamp to the profile's topic bounds.

    followed_handles (optional, from the X API v2 seam) boost topics whose
    items are authored by accounts Sam follows.
    """
    now = now or datetime.now(timezone.utc)
    followed = frozenset(h.lstrip("@").lower() for h in followed_handles or [])
    niches = profile["niches"]
    rank_cfg = profile.get("rank") or {}
    min_topics = int(rank_cfg.get("min_topics", 5))
    max_topics = int(rank_cfg.get("max_topics", 8))

    topics: list[Topic] = []
    for group in _group_items(items):
        best = max(
            group,
            key=lambda i: (i.channel == "x", i.engagement, i.published or datetime.min.replace(tzinfo=timezone.utc)),
        )
        score, niche = _score(group, niches, now, followed)
        source_url, quiet_share_url = _pick_share_urls(group)
        topics.append(
            Topic(
                title=best.title,
                items=group,
                score=score,
                niche=niche,
                channels=[i.channel for i in group],
                source_url=source_url,
                quiet_share_url=quiet_share_url,
                why_hot=_why_hot(group),
            )
        )

    topics.sort(key=lambda t: (-t.score, t.title))
    # min_topics is a quality gate the runner warns about; we never pad with filler.
    return topics[:max_topics]
