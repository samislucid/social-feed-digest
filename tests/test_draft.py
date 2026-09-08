from __future__ import annotations

import json
from pathlib import Path

import pytest

from digest import draft as draft_mod
from digest.config import load_profile
from digest.items import Item
from digest.rank import build_topics
from digest.shape import shape_sections

ROOT = Path(__file__).resolve().parents[1]


def _items():
    return [
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


def _sections(profile):
    topics = build_topics(_items(), profile)
    return shape_sections(topics, profile)


def _claude_payload(**extra):
    payload = {
        "x": {
            "summary": "Two eval-focused posts; open-weights beat closed models on reasoning.",
            "notable": [{"index": 1, "why": "First open-weights sweep of the new eval set; practitioners are re-running it."}],
            "drafts": ["Open weights just topped the reasoning evals. The gap is a release cycle now.", "Benchmarks moved; deployment math did not. Open models are the default assumption again."],
            "engagements": [{"index": 1, "action": "comment", "comment": "The 70B result holds up on our internal set; the interesting part is cost per solved task, not the leaderboard."}],
        },
        "linkedin": {
            "summary": "Quiet on LinkedIn; one practitioner post on agent evals.",
            "notable": [],
            "drafts": ["Evals are becoming procurement documents. Teams now ask for reasoning numbers before they ask for a demo."],
            "engagements": [],
        },
        "reddit": {
            "summary": "One injury thread for the sports niche.",
            "engagements": [{"index": 1, "comment": "Depth chart aside, the schedule is what kills them the next four weeks."}],
        },
    }
    payload.update(extra)
    return json.dumps(payload)


def test_template_fallback_fills_sections_with_marked_placeholders(base_profile, settings):
    settings.disable_claude = True
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result["source"] == "template-fallback"
    assert not result["claude_error"] or isinstance(result["claude_error"], str)
    by = {s.channel: s for s in sections}
    for channel in ("x", "linkedin"):
        section = by[channel]
        if not section.candidates:
            # Empty channel: visible note, no fake drafts (LinkedIn has no input here).
            assert section.empty_note
            assert not section.drafts
            continue
        assert section.summary
        assert section.notable, f"{channel} needs fallback notable posts"
        assert section.drafts and all(d.startswith("[TEMPLATE DRAFT") for d in section.drafts)
        assert section.engagements and all(e.comment.startswith("[TEMPLATE DRAFT") for e in section.engagements)
    reddit = by["reddit"]
    assert reddit.notable and reddit.notable[0].url == "https://reddit.com/r/nfl/1"
    assert reddit.engagements[0].comment.startswith("[TEMPLATE DRAFT")


def test_claude_drafts_fill_channel_sections(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, s=None: (_claude_payload(), None))
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result == {"source": "claude", "claude_error": None}
    by = {s.channel: s for s in sections}
    x = by["x"]
    assert x.summary.startswith("Two eval-focused posts")
    # Notable carries the REAL link and author from source data, never model text.
    assert x.notable[0].url == "https://x.com/u/status/1"
    assert x.notable[0].author == "@modelwatcher"
    assert "open-weights" in x.notable[0].why
    assert len(x.drafts) == 2 and "TEMPLATE" not in x.drafts[0]
    assert x.engagements[0].action == "comment"
    assert by["reddit"].engagements[0].comment.startswith("Depth chart")


def test_claude_invalid_indexes_are_dropped_and_degrade(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    payload = _claude_payload(
        x={
            "summary": "Summary text.",
            "notable": [{"index": 99, "why": "out of range"}, {"index": "abc", "why": "not an index"}],
            "drafts": ["A real draft."],
            "engagements": [{"index": 7, "action": "comment", "comment": "wrong index"}],
        }
    )
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, s=None: (payload, None))
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result["source"] == "claude-partial"
    assert "incomplete claude response" in (result["claude_error"] or "")
    by = {s.channel: s for s in sections}
    # No hallucinated links: nothing at index 99 exists, so the notable pick and
    # engagement were patched from template (marked) rather than trusting model
    # output. Claude's free-text drafts survive; the run still degrades visibly.
    assert by["x"].notable[0].why == by["x"].candidates[0].why_hot
    assert all(e.comment.startswith("[TEMPLATE DRAFT") for e in by["x"].engagements)
    assert "incomplete claude response" in (result["claude_error"] or "")


def test_partial_claude_response_patches_only_the_gaps(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    payload = _claude_payload(linkedin={"summary": "", "notable": [], "drafts": [], "engagements": []})
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, s=None: (payload, None))
    items = _items() + [
        Item(channel="linkedin", title="Agent evals in procurement", url="https://linkedin.com/p/1", source_label="A. Practitioner"),
    ]
    sections = shape_sections(build_topics(items, base_profile), base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result["source"] == "claude-partial"
    by = {s.channel: s for s in sections}
    # X content survived; only the missing LinkedIn pieces were patched.
    assert "TEMPLATE" not in by["x"].drafts[0]
    assert by["linkedin"].drafts and all(d.startswith("[TEMPLATE DRAFT") for d in by["linkedin"].drafts)
    assert "linkedin: no drafts" in (result["claude_error"] or "")


def test_empty_linkedin_run_does_not_degrade(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, s=None: (_claude_payload(), None))
    items = [
        Item(channel="x", title="Only X activity this run", url="https://x.com/u/status/2", extra={"author": "@a"}),
    ]
    topics = build_topics(items, base_profile)
    sections = shape_sections(topics, base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result == {"source": "claude", "claude_error": None}


def test_claude_failure_keeps_the_real_reason(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    reason = "claude binary not found: DIGEST_CLAUDE_BIN=/nonexistent/claude is missing or not executable"
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, s=None: (None, reason))
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result["source"] == "template-fallback"
    assert result["claude_error"] == reason
    by = {s.channel: s for s in sections}
    assert by["x"].drafts and all(d.startswith("[TEMPLATE DRAFT") for d in by["x"].drafts)


def test_prompt_keeps_brightstack_voice_and_channel_lists(base_profile):
    sections = _sections(base_profile)
    prompt = draft_mod._build_prompt(sections, base_profile)
    assert "Brightstack context" in prompt
    assert "AI-native workspace with a full team of agents" in prompt
    assert "Mention only when" in prompt
    assert "Do:" in prompt and "Don't:" in prompt
    # Channel-first inputs and schema.
    assert "https://x.com/u/status/1" in prompt
    assert "@modelwatcher" in prompt
    assert "https://reddit.com/r/nfl/1" in prompt
    for key in ('"notable"', '"drafts"', '"engagements"', "retweet", "reshare", '"summary"'):
        assert key in prompt
    assert "never post" in prompt.lower()


def test_prompt_built_from_real_repo_profile_keeps_voice_block():
    profile = load_profile(ROOT / "profile.yaml")
    sections = _sections(profile)
    prompt = draft_mod._build_prompt(sections, profile)
    bs = profile["brightstack"]
    assert bs["one_liner"].splitlines()[0].strip()[:40] in prompt
    assert bs["voice"]["register"] in prompt
    dos = draft_mod._guidance_strings(profile["drafting"]["dos"])
    assert dos[0] in prompt
    assert "max_digest_posts" not in prompt  # config noise never reaches the model


def test_prompt_carries_sams_writing_focus_from_real_repo_profile():
    profile = load_profile(ROOT / "profile.yaml")
    prompt = draft_mod._build_prompt(_sections(profile), profile)
    assert "Writing focus" in prompt
    assert "token furnace" in prompt
    assert "substantive and accurate" in prompt
    assert "LLMs, agents, harnesses, and applications" in prompt


def test_writing_focus_block_absent_when_profile_defines_none(base_profile):
    prompt = draft_mod._build_prompt(_sections(base_profile), base_profile)
    assert "Writing focus" not in prompt


def test_template_drafts_synthesize_across_topics(base_profile, settings):
    settings.disable_claude = True
    sections = _sections(base_profile)
    draft_mod.draft_digest(sections, base_profile, settings)
    x = next(s for s in sections if s.channel == "x")
    # Two items only produce one X candidate here, so the fallback must not
    # pretend a cross-topic synthesis exists.
    assert all("build on" in d or "connect" in d for d in x.drafts)


def _fake_claude(responses):
    """_run_claude stand-in returning canned (out, err) pairs in call order."""
    calls: list[str] = []

    def fake(prompt, s=None):
        calls.append(prompt)
        return responses[len(calls) - 1] if len(calls) <= len(responses) else responses[-1]

    return fake, calls


def test_incomplete_response_retries_once_then_succeeds(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    partial = json.loads(_claude_payload())
    del partial["x"]  # claude omitted the whole channel on the first try
    fake, calls = _fake_claude([(json.dumps(partial), None), (_claude_payload(), None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    logs: list[str] = []
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings, log=logs.append)
    assert result == {"source": "claude", "claude_error": None}
    assert len(calls) == 2  # one retry, fired only because the first pass was incomplete
    by = {s.channel: s for s in sections}
    assert by["x"].summary.startswith("Two eval-focused posts")
    assert "TEMPLATE" not in by["x"].drafts[0]
    assert any("retry succeeded" in line for line in logs)


def test_retry_fails_into_partial_patches_with_warn(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    partial = _claude_payload(
        x={"summary": "X summary from claude.", "notable": [], "drafts": [], "engagements": []}
    )
    fake, calls = _fake_claude([(partial, None), (partial, None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    logs: list[str] = []
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings, log=logs.append)
    assert result["source"] == "claude-partial"  # partial, not a full fallback
    assert result["claude_error"].startswith(
        "incomplete claude response, template patches applied: x: no notable picks"
    )
    assert "(after 1 retry)" in (result["claude_error"] or "")
    assert len(calls) == 2
    by = {s.channel: s for s in sections}
    assert by["x"].summary == "X summary from claude."  # claude's real content survives
    assert by["x"].drafts and all(d.startswith("[TEMPLATE DRAFT") for d in by["x"].drafts)
    warns = [line for line in logs if line.startswith("WARN: incomplete claude response")]
    assert warns and "x: no drafts" in warns[0]


def test_retry_prefers_the_attempt_with_fewer_gaps(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    worse = json.loads(_claude_payload())
    worse["x"] = {"summary": "S.", "notable": [], "drafts": [], "engagements": []}  # 3 gaps
    fake, calls = _fake_claude([(json.dumps(worse), None), (_claude_payload(), None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result == {"source": "claude", "claude_error": None}
    assert len(calls) == 2
    by = {s.channel: s for s in sections}
    assert by["x"].summary.startswith("Two eval-focused posts")  # the complete retry won


def test_retry_does_not_regress_to_a_worse_attempt(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    first = json.loads(_claude_payload())
    first["reddit"] = {"summary": "One injury thread for the sports niche.", "engagements": []}
    worse = json.loads(_claude_payload())
    worse["x"]["drafts"] = []
    worse["x"]["engagements"] = []
    fake, calls = _fake_claude([(json.dumps(first), None), (json.dumps(worse), None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result["source"] == "claude-partial"
    assert "reddit: no engagement suggestions" in (result["claude_error"] or "")
    assert "x: no drafts" not in (result["claude_error"] or "")  # the better attempt won
    by = {s.channel: s for s in sections}
    assert by["x"].drafts[0].startswith("Open weights")  # first attempt's drafts kept
    assert by["reddit"].engagements[0].comment.startswith("[TEMPLATE DRAFT")


def test_unparsable_then_success_recovers(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    fake, calls = _fake_claude([("here is your digest: {oops", None), (_claude_payload(), None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result == {"source": "claude", "claude_error": None}
    assert len(calls) == 2


def test_unparsable_after_retry_degrades_fully_with_warn(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    fake, calls = _fake_claude([("{oops", None), ("still not json", None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    logs: list[str] = []
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings, log=logs.append)
    assert result["source"] == "template-fallback"
    assert "not parsable" in (result["claude_error"] or "")
    assert len(calls) == 2
    by = {s.channel: s for s in sections}
    assert by["x"].drafts and all(d.startswith("[TEMPLATE DRAFT") for d in by["x"].drafts)
    assert any(line.startswith("WARN:") and "template drafts" in line for line in logs)


def test_hard_failure_is_not_retried(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    reason = "claude binary not found on PATH"
    fake, calls = _fake_claude([(None, reason)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    sections = _sections(base_profile)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert len(calls) == 1  # deterministic hard failures are not retried
    assert result["source"] == "template-fallback"
    assert result["claude_error"] == reason


def test_web_section_is_never_a_gap(base_profile, settings, monkeypatch):
    """Regression for the 2026-09-07 17:30 production run.

    The prompt schema has no web entry (web/news is deterministic context), so
    claude omitting web is compliant, not a defect: it must not set claude_error
    or flip draft_source - exactly the silent degradation that run showed.
    """
    settings.disable_claude = False
    fake, calls = _fake_claude([(_claude_payload(), None)])
    monkeypatch.setattr(draft_mod, "_run_claude", fake)
    items = _items() + [
        Item(
            channel="web",
            title="Enterprises pilot coding agents at scale",
            url="https://example.com/evals",
            source_label="example.com",
        ),
    ]
    sections = shape_sections(build_topics(items, base_profile), base_profile)
    assert any(s.channel == "web" and s.candidates for s in sections)
    result = draft_mod.draft_digest(sections, base_profile, settings)
    assert result == {"source": "claude", "claude_error": None}
    assert len(calls) == 1  # no retry: the response was complete under the real contract
