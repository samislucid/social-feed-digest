from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage

from digest.deliver import prune, run_tag_for, store_digest
from digest.items import Item
from digest.rank import build_topics
from digest.render import Digest, build_email, render_html, render_markdown


def _digest(base_profile, run_tag: str = "2026-09-07_0930") -> Digest:
    items = [Item(channel="x", title="Open-weights model tops evals", url="https://x.com/u/status/1")]
    topics = build_topics(items, base_profile)
    topics[0].comment = "c"
    return Digest(
        run_tag=run_tag,
        generated_at=datetime(2026, 9, 7, 16, 30, tzinfo=timezone.utc),
        profile_name=base_profile["name"],
        topics=topics,
        post_ideas={"x": ["a", "b", "c"], "linkedin": ["d", "e", "f"], "reddit": ["g", "h", "i"]},
        draft_source="template-fallback",
        collection={"x": 1},
        cost={"search_tool_calls": 2, "total_usd": 0.01, "budget_usd": 0.25, "calls": []},
        email_to="samjookim@gmail.com",
    )


def test_run_tag_is_pacific_local():
    # 2026-09-07 02:30 UTC == 2026-09-06 19:30 Pacific (PDT, UTC-7)
    assert run_tag_for(datetime(2026, 9, 7, 2, 30, tzinfo=timezone.utc)) == "2026-09-06_1930"


def test_store_digest_writes_all_artifacts(base_profile, settings):
    digest = _digest(base_profile)
    md = render_markdown(digest)
    html = render_html(digest)
    msg = build_email(digest, md, html, "digest@example.com")
    run_dir = store_digest(digest, md, html, msg, settings)

    names = {p.name for p in run_dir.iterdir()}
    assert {"digest.md", "digest.html", "digest.json", "digest.eml"} <= names

    pages = settings.data_dir / "pages"
    assert (pages / "latest.html").read_text(encoding="utf-8") == html
    assert (pages / f"{digest.run_tag}.html").exists()
    assert digest.run_tag in (pages / "index.html").read_text(encoding="utf-8")


def test_prune_removes_old_runs_but_keeps_recent(base_profile, settings):
    old = _digest(base_profile, run_tag="2020-01-01_0000")
    md = render_markdown(old)
    html = render_html(old)
    msg: EmailMessage = build_email(old, md, html, "digest@example.com")
    store_digest(old, md, html, msg, settings)
    new = _digest(base_profile)
    store_digest(new, md, html, msg, settings)

    removed = prune(settings, retention_days=30)

    digests = settings.data_dir / "digests"
    assert not (digests / "2020-01-01_0000").exists()
    assert (digests / new.run_tag).exists()
    assert removed >= 1
