from __future__ import annotations

from datetime import datetime, timezone

from digest.items import Item
from digest.rank import Topic, build_topics
from digest.render import Digest, build_email, render_html, render_markdown


def _digest(base_profile) -> Digest:
    items = [
        Item(channel="x", title="Open-weights model tops reasoning evals", url="https://x.com/u/status/1"),
        Item(channel="reddit", title="49ers lose starting QB to injury", url="https://reddit.com/r/nfl/1"),
    ]
    topics = build_topics(items, base_profile)
    topics[0].comment = "Sharp comment one."
    topics[1].comment = "Sharp comment two."
    return Digest(
        run_tag="2026-09-07_0930",
        generated_at=datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc),
        profile_name=base_profile["name"],
        topics=topics,
        post_ideas={"x": ["x1", "x2", "x3"], "linkedin": ["l1", "l2", "l3"], "reddit": ["r1", "r2", "r3"]},
        draft_source="template-fallback",
        collection={"reddit": 2, "x": 2},
        cost={"search_tool_calls": 4, "total_usd": 0.0312, "budget_usd": 0.25, "calls": []},
        email_to="samislucid98@gmail.com",
        subject_prefix="[Feed Digest]",
        warnings=["only 2 topics ranked (profile minimum 5)"],
    )


def test_markdown_contains_required_sections(base_profile):
    md = render_markdown(_digest(base_profile))
    assert "## Topics" in md
    assert "Quiet share:" in md
    assert "Suggested comment:" in md
    assert "## Post ideas" in md
    assert "### X" in md and "### LinkedIn" in md and "### Reddit" in md
    assert "https://x.com/u/status/1" in md
    assert "TEMPLATE DRAFT" in md  # provenance is visible in the artifact


def test_html_escapes_hostile_titles(base_profile):
    digest = _digest(base_profile)
    digest.topics[0].title = 'Open-weights model <script>alert(1)</script> tops evals'
    html = render_html(digest)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_email_is_multipart_with_correct_headers(base_profile):
    digest = _digest(base_profile)
    md = render_markdown(digest)
    html = render_html(digest)
    msg = build_email(digest, md, html, from_addr="digest@example.com")
    assert msg["To"] == "samislucid98@gmail.com"
    assert msg["From"] == "digest@example.com"
    assert "[Feed Digest]" in msg["Subject"]
    assert msg["X-Digest-Run"] == "2026-09-07_0930"
    parts = list(msg.walk())
    ctypes = {p.get_content_type() for p in parts}
    assert "text/plain" in ctypes and "text/html" in ctypes
