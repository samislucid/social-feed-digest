from __future__ import annotations

import json

from digest import draft as draft_mod
from digest.items import Item
from digest.rank import build_topics


def _topics(base_profile):
    items = [
        Item(channel="x", title="Open-weights model tops reasoning evals", url="https://x.com/u/status/1"),
        Item(channel="reddit", title="49ers lose starting QB to injury", url="https://reddit.com/r/nfl/1"),
    ]
    return build_topics(items, base_profile)


def _payload(**extra):
    payload = {
        "comments": [
            {"index": 1, "comment": "Numbers check out; worth a close read."},
            {"index": 2, "comment": "Depth chart implications are real."},
        ],
        "post_ideas": {
            "x": ["idea one", "idea two", "idea three"],
            "linkedin": ["lesson one", "lesson two", "lesson three"],
            "reddit": ["prompt one", "prompt two", "prompt three"],
        },
    }
    payload.update(extra)
    return payload


def test_template_fallback_is_clearly_marked(base_profile, settings):
    settings.disable_claude = True
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    assert result["source"] == "template-fallback"
    assert all("TEMPLATE DRAFT" in t.comment for t in topics)
    assert set(result["post_ideas"]) == {"x", "linkedin", "reddit"}
    assert all(3 <= len(v) <= 5 for v in result["post_ideas"].values())


def test_claude_output_is_applied(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, settings: (json.dumps(_payload()), None))
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    assert result["source"] == "claude"
    assert topics[0].comment == "Numbers check out; worth a close read."
    assert topics[1].comment == "Depth chart implications are real."
    assert result["post_ideas"]["x"] == ["idea one", "idea two", "idea three"]


def test_malformed_claude_output_falls_back(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, settings: ("not json at all", None))
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    assert result["source"] == "template-fallback"
    assert "TEMPLATE DRAFT" in topics[0].comment
    assert result["claude_error"] == "claude output was not parseable JSON"


def test_missing_cli_reports_reason(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod.shutil, "which", lambda name: None)
    result = draft_mod.draft_digest(_topics(base_profile), base_profile, settings)
    assert result["source"] == "template-fallback"
    assert result["claude_error"] and "not found on PATH" in result["claude_error"]


def test_nonzero_exit_surfaces_stderr(base_profile, settings, monkeypatch):
    settings.disable_claude = False

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Invalid API key · Please run /login\n"

    monkeypatch.setattr(draft_mod.shutil, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setattr(draft_mod.subprocess, "run", lambda *a, **k: Proc())
    result = draft_mod.draft_digest(_topics(base_profile), base_profile, settings)
    assert result["source"] == "template-fallback"
    assert "claude exited 1" in result["claude_error"]
    assert "Invalid API key" in result["claude_error"]


def test_timeout_and_start_failure_are_named(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    import subprocess as subprocess_mod

    monkeypatch.setattr(draft_mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(draft_mod.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess_mod.TimeoutExpired(cmd="claude", timeout=240)))
    result = draft_mod.draft_digest(_topics(base_profile), base_profile, settings)
    assert "timed out" in result["claude_error"]

    def boom(*a, **k):
        raise OSError("[Errno 2] No such file or directory: '/gone/claude'")

    monkeypatch.setattr(draft_mod.subprocess, "run", boom)
    result = draft_mod.draft_digest(_topics(base_profile), base_profile, settings)
    assert "could not start" in result["claude_error"]


def test_configured_claude_bin_is_used(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd

        class Proc:
            returncode = 0
            stdout = json.dumps(_payload())
            stderr = ""

        return Proc()

    monkeypatch.setattr(draft_mod.shutil, "which", lambda name: name)  # echo the configured path through
    monkeypatch.setattr(draft_mod.subprocess, "run", fake_run)
    settings.claude_bin = "/home/sam/.local/bin/claude"
    result = draft_mod.draft_digest(_topics(base_profile), base_profile, settings)
    assert seen["cmd"][0] == "/home/sam/.local/bin/claude"
    assert result["source"] == "claude"


def test_prompt_grounded_in_post_text_with_guardrails(base_profile):
    items = [
        Item(
            channel="reddit",
            title="Agents ate my CI budget",
            url="https://reddit.com/r/LLMDevs/abc",
            summary="Our CI spend tripled after letting agents run unattended. Anyone else seeing runaway loop costs?",
        ),
        Item(channel="x", title="Eval harnesses beat vibes", url="https://x.com/u/status/2", summary="We cut regressions 40% with a 30-case eval."),
    ]
    topics = build_topics(items, base_profile)
    prompt = draft_mod._build_prompt(topics, base_profile, 4, ["x", "linkedin", "reddit"])
    # per-topic grounding: the post's own text is in the prompt
    assert "post text:" in prompt and "runaway loop costs" in prompt
    # comment guardrails
    assert "Ground it in the post's own text" in prompt
    assert "never generic praise" in prompt
    # synthesis guardrails for post ideas
    assert "at least two of the topics" in prompt
    assert "Never restate or retitle a single post" in prompt
    assert "never propose resharing a link" in prompt
    # shortlist is comment-fit, not heat
    assert "comment-fit, not raw heat" in prompt
    # brightstack voice block is consumed from the profile
    assert "AI-native workspace with a full team of agents" in prompt
    assert "no promo spam" in prompt
    # JSON contract includes the shortlist
    assert '"shortlist"' in prompt


def test_shortlist_from_claude_is_mapped_to_topics(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    payload = _payload(shortlist=[{"index": 1, "why": "practitioner reply lands", "comment": "Built on your eval point: ..."}])
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, settings: (json.dumps(payload), None))
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    assert result["source"] == "claude"
    entries = result["shortlist"]
    assert len(entries) == 1
    assert entries[0].index == 1
    assert entries[0].title == topics[0].title
    assert entries[0].url == topics[0].source_url
    assert entries[0].why == "practitioner reply lands"
    assert entries[0].comment == "Built on your eval point: ..."


def test_shortlist_falls_back_to_ai_fit_topics(base_profile, settings):
    settings.disable_claude = True
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    entries = result["shortlist"]
    assert entries, "fallback shortlist is never empty when topics exist"
    # the AI-niche topic is picked by comment-fit, not raw heat
    assert any("Open-weights" in e.title for e in entries)
    assert all("TEMPLATE DRAFT" in e.comment for e in entries)
