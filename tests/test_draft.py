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
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, settings: json.dumps(payload))
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    assert result["source"] == "claude"
    assert topics[0].comment == "Numbers check out; worth a close read."
    assert topics[1].comment == "Depth chart implications are real."
    assert result["post_ideas"]["x"] == ["idea one", "idea two", "idea three"]


def test_malformed_claude_output_falls_back(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod, "_run_claude", lambda prompt, settings: "not json at all")
    topics = _topics(base_profile)
    result = draft_mod.draft_digest(topics, base_profile, settings)
    assert result["source"] == "template-fallback"
    assert "TEMPLATE DRAFT" in topics[0].comment


def test_missing_cli_falls_back(base_profile, settings, monkeypatch):
    settings.disable_claude = False
    monkeypatch.setattr(draft_mod.shutil, "which", lambda name: None)
    result = draft_mod.draft_digest(_topics(base_profile), base_profile, settings)
    assert result["source"] == "template-fallback"
