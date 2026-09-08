"""Best-effort thumbnails: pipeline-held first, bounded og:image fetch, safe fallback."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest import images
from digest.items import Item
from digest.rank import build_topics
from digest.render import Digest
from digest.shape import shape_sections


def _digest(base_profile, urls: list[str]) -> Digest:
    # Distinct titles: the ranker merges same-story items, so shared tokens would
    # collapse these URLs into one topic.
    titles = ["Chip export rules tighten", "GPU prices climb again", "Browser engine update ships"]
    items = [
        Item(channel="web", title=titles[i], url=u, source_label="reuters.com")
        for i, u in enumerate(urls)
    ]
    topics = build_topics(items, base_profile)
    sections = shape_sections(topics, base_profile)
    return Digest(
        run_tag="t",
        generated_at=datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc),
        profile_name=base_profile["name"],
        sections=sections,
        draft_source="claude",
        collection={"web": len(urls)},
        cost={},
        email_to="x@example.com",
    )


def test_og_image_parses_absolute_and_relative_meta():
    html = '<html><head><meta property="og:image" content="https://cdn.example.com/pic.jpg"></head></html>'
    assert images.og_image("https://example.com/a", fetch=lambda u, t: html.encode()) == "https://cdn.example.com/pic.jpg"
    html_rel = "<meta content='/img/x.jpg' property='og:image:secure_url'>"
    resolved = images.og_image("https://example.com/a", fetch=lambda u, t: html_rel.encode())
    # relative URLs resolve against the page url
    assert resolved == "https://example.com/img/x.jpg"


def test_attach_fills_unwalled_posts_and_keeps_held_images(base_profile):
    digest = _digest(
        base_profile,
        ["https://example.com/a", "https://x.com/u/status/1", "https://example.com/b"],
    )
    web = next(s for s in digest.sections if s.channel == "web")
    by_url = {n.url: n for n in web.notable}  # rank order varies; index by url
    held, walled, fetched = (
        by_url["https://example.com/a"],
        by_url["https://x.com/u/status/1"],
        by_url["https://example.com/b"],
    )
    held.image = "https://cdn.example.com/held.jpg"  # pipeline-held: never fetched

    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: float) -> bytes:
        assert "x.com" not in url, "login-walled host must not be fetched"
        calls.append(url)
        return b'<meta property="og:image" content="https://cdn.example.com/pic.jpg">'

    logs: list[str] = []
    images.attach_post_images(digest, fetch=fake_fetch, log=logs.append)
    assert held.image == "https://cdn.example.com/held.jpg"
    assert walled.image == ""  # x.com is walled: monogram fallback stays
    assert fetched.image == "https://cdn.example.com/pic.jpg"
    assert calls == ["https://example.com/b"]
    assert logs == []


def test_attach_never_raises_on_fetch_failure(base_profile):
    digest = _digest(base_profile, ["https://example.com/offline"])

    def failing_fetch(url: str, timeout_s: float) -> bytes:
        raise ConnectionError("no network")

    logs: list[str] = []
    images.attach_post_images(digest, fetch=failing_fetch, log=logs.append)
    web = next(s for s in digest.sections if s.channel == "web")
    assert web.notable[0].image == ""  # graceful fallback intact
    assert logs and "thumbnail unavailable" in logs[0]


def test_og_image_requires_http():
    with pytest.raises(images.FetchError):
        images.og_image("ftp://example.com/a", fetch=lambda u, t: b"")
