"""End-to-end sandbox run: seeded X/Reddit/LinkedIn inputs, drafting enabled, no claude binary.

This is the acceptance test for the channel-first reshape: the whole pipeline
(collection stubs, deterministic shaping, degraded drafting, rendering, artifact
storage) runs and lands in the template-fallback path cleanly, with the new
channel-first shape in the artifacts and the real claude failure reason surfaced.
"""
from __future__ import annotations

import json
import os
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from digest import __main__ as cli
from digest.items import Item

ROOT = Path(__file__).resolve().parents[1]

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
_REDDIT = [
    Item(
        channel="reddit",
        title=f"LLM {theme} thread with bench numbers",
        url=f"https://reddit.com/r/LLMDevs/{i}",
        source_label="r/LLMDevs",
    )
    for i, theme in enumerate(_REDDIT_THEMES, 1)  # 8 threads: the cap keeps only the best 5
]
_X = [
    Item(
        channel="x",
        title="Open-weights model tops reasoning evals",
        url="https://x.com/u/status/1",
        summary="A 70B open-weights model beat frontier closed models on three reasoning evals.",
        extra={"author": "@modelwatcher"},
    ),
    Item(
        channel="x",
        title="Agent frameworks are consolidating",
        url="https://x.com/u/status/2",
        summary="Three agent frameworks merged their runtimes this week; teams are re-evaluating.",
        extra={"author": "@buildinpublic"},
    ),
    Item(
        channel="x",
        title="Frontier API pricing war: per-token price cut 40%",
        url="https://x.com/u/status/3",
        summary="Two providers cut API pricing; token cost comparisons are now the default eval.",
        extra={"author": "@tokenfurnace"},
    ),
]
_WEB = [
    Item(channel="web", title="Enterprises pilot coding agents at scale", url="https://example.com/news/1", summary="Two more enterprises moved pilots to production.")
]
_LINKEDIN = [
    Item(channel="linkedin", title="Procurement now asks for eval scores", url="https://linkedin.com/p/1", summary="Buyers want agent eval scores in RFPs.", source_label="A. Practitioner")
]


@pytest.fixture
def e2e_env(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "collect_reddit", lambda *a, **k: [Item(channel="reddit", title=t.title, url=t.url, source_label=t.source_label) for t in _REDDIT])
    monkeypatch.setattr(cli, "collect_x_topics", lambda *a, **k: ([Item(channel="x", title=t.title, url=t.url, summary=t.summary, extra=dict(t.extra)) for t in _X], {"search_tool_calls": 2, "cost_usd": 0.02}))
    monkeypatch.setattr(cli, "collect_web_sweep", lambda *a, **k: ([Item(channel="web", title=t.title, url=t.url, summary=t.summary) for t in _WEB], {"search_tool_calls": 1, "cost_usd": 0.01}))
    monkeypatch.setattr(cli, "collect_linkedin", lambda *a, **k: [Item(channel="linkedin", title=t.title, url=t.url, summary=t.summary, source_label=t.source_label) for t in _LINKEDIN])
    # Thumbnails are a render-time nicety: stub the network attach for determinism.
    monkeypatch.setattr(cli, "attach_post_images", lambda *a, **k: None)

    env = {
        "XAI_API_KEY": "k",  # drafting enabled; collectors stubbed above
        "DIGEST_CLAUDE_BIN": "/nonexistent/claude",  # no claude binary -> degraded path
        "PATH": "/usr/bin:/bin",  # nothing that could satisfy the claude lookup
        "HOME": str(tmp_path),
        "DIGEST_DATA_DIR": str(tmp_path / "data"),
    }
    monkeypatch.setattr(os, "environ", {**os.environ, **env})
    return tmp_path / "data"


def test_e2e_channel_first_degraded_run(e2e_env, tmp_path, capsys):
    rc = cli.main(
        [
            "run",
            "--dry-run",
            "--skip-portfolio",
            "--profile",
            str(ROOT / "profile.yaml"),
            "--data-dir",
            str(e2e_env),
            "--json-summary",
            str(tmp_path / "summary.json"),
        ]
    )
    assert rc == 0
    # The done line names the claude outcome even on a degraded run, so the
    # ops grep for claude|warn|degraded reflects real run health.
    out = capsys.readouterr().out
    assert "claude degraded (claude binary not found" in out

    runs = sorted((e2e_env / "digests").iterdir())
    assert runs, "no run directory written"
    run_dir = runs[-1]
    md = (run_dir / "digest.md").read_text(encoding="utf-8")

    # Channel-first, action-first shape in the required order.
    assert md.index("## X") < md.index("## LinkedIn") < md.index("## Reddit (best")
    assert "### What happened on X" in md
    assert "### Posts getting attention" in md
    assert "https://x.com/u/status/1" in md
    assert "@modelwatcher" in md
    # Token costs / LLM economics is the high-priority core topic: the seeded
    # token-cost post surfaces in the digest output.
    assert "https://x.com/u/status/3" in md
    assert "API pricing" in md
    assert "### Drafts for your account (post manually)" in md
    assert "### Comments and reposts worth making" in md
    # LinkedIn mirrors the X structure.
    assert "### Drafts for your account (post manually)" in md
    assert "### Comments and reshares worth making" in md
    # Reddit capped: best 5 of the 8 seeded threads.
    assert "## Reddit (best 5 of 8 Reddit posts this run)" in md
    assert "quantization tricks" not in md  # ranks 6-8 stay out of the digest
    # Web/news context section, and the manual-posting promise.
    assert "## Also spotted (web and news)" in md
    assert "the worker never posts" in md

    # Degraded path: template drafts, visible in subject and footer with the
    # real failure reason (no claude binary).
    assert "TEMPLATE DRAFT" in md
    assert "DRAFTING DEGRADED: claude binary not found: DIGEST_CLAUDE_BIN=/nonexistent/claude" in md
    msg = BytesParser(policy=email_policy.default).parsebytes((run_dir / "digest.eml").read_bytes())
    assert "[DEGRADED: template drafts]" in str(msg["Subject"])

    digest_json = json.loads((run_dir / "digest.json").read_text(encoding="utf-8"))
    assert digest_json["draft_source"] == "template-fallback"
    assert {s["channel"] for s in digest_json["sections"]} == {"x", "linkedin", "reddit", "web"}
    reddit = next(s for s in digest_json["sections"] if s["channel"] == "reddit")
    assert len(reddit["notable"]) == 5
    assert "engagement" in reddit["notable"][0] and "image" in reddit["notable"][0]
    assert reddit["notable"][0]["image"] == ""  # seeded inputs hold no thumbnails
    linkedin = next(s for s in digest_json["sections"] if s["channel"] == "linkedin")
    assert linkedin["drafts"]  # seeded LinkedIn input produced drafts (template)

    # Visual upgrade: page carries the new card/stat-strip system; the email
    # part is Gmail-safe (tables, no JS) and looks complete with no images.
    page_html = (run_dir / "digest.html").read_text(encoding="utf-8")
    assert '<div class="statbar">' in page_html
    assert '<div class="post">' in page_html
    assert '<div class="tile">' in page_html  # monogram fallback tiles
    assert "<script" not in page_html.lower()
    email_part = msg.get_body(("html",))
    assert email_part is not None
    email_html = email_part.get_content()
    assert "<script" not in email_html.lower()
    assert 'width="600"' in email_html
    assert "src=" not in email_html  # nothing held -> no remote images, tiles only

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["draft_source"] == "template-fallback"
    assert summary["sections"]["reddit"]["posts"] == 5
    assert summary["sections"]["x"]["drafts"] >= 1
