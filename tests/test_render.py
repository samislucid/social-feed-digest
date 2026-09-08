from __future__ import annotations

from datetime import datetime, timezone

from digest.items import Item
from digest.rank import build_topics
from digest.render import DEGRADED_SUBJECT_TAG, Digest, build_email, render_email_html, render_html, render_markdown
from digest.shape import Engagement, Notable, shape_sections


def _digest(base_profile, run_tag: str = "2026-09-07_0930", **overrides) -> Digest:
    items = [
        Item(
            channel="x",
            title="Open-weights model tops reasoning evals",
            url="https://x.com/u/status/1",
            summary="A 70B open-weights model beat frontier closed models on three reasoning evals.",
            extra={"author": "@modelwatcher"},
        ),
        Item(
            channel="reddit",
            title="49ers lose starting QB to injury",
            url="https://reddit.com/r/nfl/1",
            summary="The 49ers placed their starting QB on injured reserve after Sunday's loss.",
            source_label="r/nfl",
        ),
    ]
    topics = build_topics(items, base_profile)
    sections = shape_sections(topics, base_profile)
    by = {s.channel: s for s in sections}
    by["x"].summary = "One eval story is running on X."
    by["x"].notable = [
        Notable(
            index=1,
            title="Open-weights model tops reasoning evals",
            url="https://x.com/u/status/1",
            author="@modelwatcher",
            why="First open-weights sweep of the new eval set.",
        )
    ]
    by["x"].drafts = ["Open weights just topped the reasoning evals.", "Second draft for your account."]
    by["x"].engagements = [
        Engagement(
            index=1,
            title=by["x"].notable[0].title,
            url=by["x"].notable[0].url,
            author="@modelwatcher",
            action="comment",
            comment="The 70B result holds up on our internal set.",
        )
    ]
    by["reddit"].engagements = [
        Engagement(
            index=1,
            title=by["reddit"].notable[0].title,
            url=by["reddit"].notable[0].url,
            author="r/nfl",
            action="comment",
            comment="The schedule is what kills them the next four weeks.",
        )
    ]
    digest = Digest(
        run_tag=run_tag,
        generated_at=datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc),
        profile_name=base_profile["name"],
        sections=sections,
        draft_source=overrides.pop("draft_source", "claude"),
        collection={"reddit": 2, "x": 2},
        cost={"search_tool_calls": 4, "total_usd": 0.0312, "budget_usd": 0.25, "calls": []},
        email_to="samislucid98@gmail.com",
        subject_prefix="[Feed Digest]",
        warnings=overrides.pop("warnings", ["only 2 topics ranked (profile minimum 5)"]),
        **overrides,
    )
    return digest


def test_markdown_is_channel_first_and_action_first(base_profile):
    md = render_markdown(_digest(base_profile))
    order = [md.index("## X"), md.index("## LinkedIn"), md.index("## Reddit (best")]
    assert order == sorted(order)
    assert "### What happened on X" in md
    assert "### Posts getting attention" in md
    assert "### Drafts for your account (post manually)" in md
    assert "### Comments and reposts worth making" in md
    assert "https://x.com/u/status/1" in md
    assert "@modelwatcher" in md
    assert "Why it matters:" in md
    assert "Suggested comment: The schedule is what kills them" in md
    assert "the worker never posts" in md
    assert "## Run footer" in md


def test_markdown_renders_linkedin_and_empty_notes(base_profile):
    md = render_markdown(_digest(base_profile))
    assert "## LinkedIn" in md
    assert "watched inbox" in md  # empty-channel note is visible
    # An actually-populated LinkedIn section mirrors the X structure.
    from digest.shape import Engagement, Notable

    items = [
        Item(
            channel="linkedin",
            title="Agent evals in procurement",
            url="https://linkedin.com/p/1",
            source_label="A. Practitioner",
        )
    ]
    topics = build_topics(items, base_profile)
    sections = shape_sections(topics, base_profile)
    li = next(s for s in sections if s.channel == "linkedin")
    li.summary = "One practitioner post."
    li.notable = [
        Notable(
            index=1,
            title="Agent evals in procurement",
            url="https://linkedin.com/p/1",
            author="A. Practitioner",
            why="Matches the eval theme from X.",
        )
    ]
    li.drafts = ["Evals are becoming procurement documents."]
    li.engagements = [
        Engagement(
            index=1,
            title="Agent evals in procurement",
            url="https://linkedin.com/p/1",
            author="A. Practitioner",
            action="reshare",
            comment="Worth amplifying for the operator audience.",
        )
    ]
    digest = Digest(
        run_tag="t",
        generated_at=datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc),
        profile_name=base_profile["name"],
        sections=sections,
        draft_source="claude",
        collection={"linkedin": 1},
        cost={},
        email_to="x@example.com",
    )
    md2 = render_markdown(digest)
    assert "### What happened on LinkedIn" in md2
    assert "### Posts getting attention" in md2
    assert "### Drafts for your account (post manually)" in md2
    assert "### Comments and reshares worth making" in md2
    assert 'Reshare "Agent evals in procurement"' in md2


def test_subject_counts_and_degraded_tag(base_profile):
    digest = _digest(base_profile)
    assert digest.subject == "[Feed Digest] 2026-09-07_0930 - 2 posts, 2 drafts"
    degraded = _digest(base_profile, draft_source="template-fallback", claude_error="claude binary not found on PATH")
    assert degraded.subject.endswith(DEGRADED_SUBJECT_TAG)
    md = render_markdown(degraded)
    assert "TEMPLATE DRAFT" in md
    assert "This run's drafting failed: claude binary not found on PATH." in md  # banner names the reason
    assert "drafting failed: claude binary not found on PATH" in render_email_html(degraded)
    assert "drafting failed: claude binary not found on PATH" in render_html(degraded)
    assert "DRAFTING DEGRADED: claude binary not found on PATH" in md


def test_html_mirrors_the_channel_shape(base_profile):
    html = render_html(_digest(base_profile))
    for marker in ("<h2>X</h2>", "<h2>LinkedIn</h2>", "<h2>Reddit (best 1 of 1 Reddit posts this run)</h2>"):
        assert marker in html
    assert "Drafts for your account (post manually)" in html
    assert 'href="https://x.com/u/status/1"' in html
    assert "the worker never posts" in html
    # Visual system: stat strip with per-channel bar, post cards, monogram
    # fallback tiles (nothing held an image), responsive CSS, and no JS.
    assert '<div class="statbar">' in html
    assert '<div class="post">' in html
    assert '<div class="tile">' in html
    assert "<script" not in html.lower()
    assert "@media" in html


def test_email_cards_show_thumbnail_and_engagement_when_held(base_profile):
    digest = _digest(base_profile)
    x = next(s for s in digest.sections if s.channel == "x")
    x.notable[0].image = "https://example.com/thumb.jpg"
    x.notable[0].engagement = 342
    html = render_email_html(digest)
    assert 'src="https://example.com/thumb.jpg"' in html
    assert 'alt=""' in html  # image-block-proof: empty alt, tinted tile behind
    assert "↑ 342" in html  # engagement chip only when held
    assert "RN" in html  # monogram fallback tile for the reddit card


def test_page_cards_show_thumbnail_engagement_and_monogram(base_profile):
    digest = _digest(base_profile)
    x = next(s for s in digest.sections if s.channel == "x")
    x.notable[0].image = "https://example.com/thumb.jpg"
    x.notable[0].engagement = 342
    html = render_html(digest)
    assert 'src="https://example.com/thumb.jpg"' in html
    assert "↑ 342" in html
    assert "Open post" in html
    assert '<div class="tile">' in html  # reddit card keeps the monogram fallback


def test_build_email_keeps_subject_and_both_parts(base_profile):
    digest = _digest(base_profile)
    message = build_email(digest, render_markdown(digest), render_email_html(digest), "digest@localhost")
    assert DEGRADED_SUBJECT_TAG not in message["Subject"]
    assert str(message["To"]) == "samislucid98@gmail.com"
    body = message.get_body("html").get_content()
    assert "<script" not in body.lower()
    assert 'href="https://x.com/u/status/1"' in body
    assert "the worker never posts" in body
    assert "Drafts for your account (post manually)" in body
    assert 'bgcolor="#f2f3f7"' in body  # Gmail-safe fixed-width container on light bg
