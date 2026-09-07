"""Reddit collector.

v1 ships the free RSS fallback (works before Reddit app-approval). A PRAW-based
source activates automatically when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are
set, giving real engagement scores and permalinks. Both implement RedditSource.
"""
from __future__ import annotations

import time
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import requests

from ..config import Settings
from ..items import Item, strip_html

RSS_TIMEOUT_S = 30
RSS_REQUEST_SPACING_S = 4.0  # Reddit rate-limits rapid unauthenticated .rss fetches
RSS_RETRY_WAIT_S = 15.0


class RedditError(RuntimeError):
    """Raised when a subreddit fetch fails hard (rate limit, outage)."""


class RedditSource:
    """Seam for Reddit backends. fetch() returns Items for one subreddit."""

    def fetch(self, subreddit: str, limit: int) -> list[Item]:  # pragma: no cover - interface
        raise NotImplementedError


class RssRedditSource(RedditSource):
    """Hot-listing via the public Atom feed: /r/<sub>/hot.rss."""

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent

    def fetch_url(self, subreddit: str, limit: int) -> str:
        url = f"https://www.reddit.com/r/{subreddit}/hot.rss?limit={limit}"
        resp = requests.get(url, headers={"User-Agent": self.user_agent}, timeout=RSS_TIMEOUT_S)
        if resp.status_code == 429:
            raise RedditError(f"r/{subreddit}: Reddit RSS rate-limited (429); back off and retry later")
        if resp.status_code >= 400:
            raise RedditError(f"r/{subreddit}: Reddit RSS returned {resp.status_code}")
        return resp.text

    def parse_feed(self, payload: str | bytes, subreddit: str, limit: int | None = None) -> list[Item]:
        feed = feedparser.parse(payload)
        items: list[Item] = []
        for entry in feed.entries[:limit]:
            link = entry.get("link") or ""
            title = strip_html(entry.get("title") or "")
            if not link or not title:
                continue
            summary = strip_html(entry.get("summary") or "")[:400]
            items.append(
                Item(
                    channel="reddit",
                    title=title,
                    url=link,
                    summary=summary,
                    source_label=f"r/{subreddit}",
                    published=_entry_datetime(entry),
                    extra={"author": strip_html(entry.get("author") or "")},
                )
            )
        return items

    def fetch(self, subreddit: str, limit: int) -> list[Item]:
        return self.parse_feed(self.fetch_url(subreddit, limit), subreddit, limit=limit)


class PrawRedditSource(RedditSource):
    """Read-only Data API backend; activates only with app credentials."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str) -> None:
        try:
            import praw  # optional dependency: pip install .[praw]
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise RedditError(
                "REDDIT_CLIENT_ID/SECRET are set but praw is not installed; run: pip install .[praw]"
            ) from exc
        self._reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            check_for_async=False,
        )

    def fetch(self, subreddit: str, limit: int) -> list[Item]:
        items: list[Item] = []
        for post in self._reddit.subreddit(subreddit).hot(limit=limit):
            title = post.title or ""
            if not title:
                continue
            items.append(
                Item(
                    channel="reddit",
                    title=title,
                    url=f"https://www.reddit.com{post.permalink}",
                    summary=(post.selftext or "")[:400],
                    source_label=f"r/{subreddit}",
                    published=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                    engagement=int(post.score or 0),
                    extra={"num_comments": int(post.num_comments or 0)},
                )
            )
        return items


def build_reddit_source(settings: Settings, log=print) -> RedditSource:
    """PRAW when credentials exist, RSS otherwise (works before API approval)."""
    if settings.reddit_client_id and settings.reddit_client_secret:
        try:
            return PrawRedditSource(
                settings.reddit_client_id, settings.reddit_client_secret, settings.reddit_user_agent
            )
        except RedditError as exc:
            log(f"WARN: PRAW unavailable ({exc}); falling back to RSS")
    return RssRedditSource(settings.reddit_user_agent)


def collect_reddit(settings: Settings, profile: dict, log=print) -> list[Item]:
    cfg = profile.get("reddit") or {}
    limit = int(cfg.get("limit", 25))
    source = build_reddit_source(settings, log=log)
    items: list[Item] = []
    for idx, subreddit in enumerate(cfg.get("subreddits") or []):
        if idx:
            time.sleep(RSS_REQUEST_SPACING_S)
        fetched = None
        for attempt in (1, 2, 3):
            try:
                fetched = source.fetch(subreddit, limit)
                break
            except (RedditError, requests.RequestException) as exc:
                # Reddit 429s bursty unauthenticated fetches; back off twice before giving up.
                if attempt < 3 and "429" in str(exc):
                    wait = RSS_RETRY_WAIT_S * (1 if attempt == 1 else 2.5)
                    log(f"r/{subreddit}: rate-limited, backing off {wait:.0f}s and retrying")
                    time.sleep(wait)
                    continue
                log(f"WARN: reddit fetch failed for r/{subreddit}: {exc}")
        if fetched is not None:
            log(f"collected {len(fetched)} items from r/{subreddit}")
            items.extend(fetched)
    return items


def _entry_datetime(entry) -> datetime | None:
    for key in ("updated_parsed", "published_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
    return None
