"""Profile-driven X scout: high-priority niches are scanned and demanded every run.

Token costs / LLM economics is the high-priority core topic (Sam's X account is
"the token furnace"): profile.yaml marks the niche priority: high, the scout
prompt marks it and demands coverage when signal exists, and ranking carries
its keywords so cost posts surface reliably.
"""
from __future__ import annotations

from pathlib import Path

from digest.collect import xai_collector
from digest.config import load_profile

ROOT = Path(__file__).resolve().parents[1]

_TOKEN_NICHE = {
    "id": "token-costs",
    "label": "Token costs and LLM economics",
    "weight": 1.5,
    "priority": "high",
    "keywords": ["token cost", "API pricing", "LLM pricing", "cost comparison", "inference cost"],
}


def _with_token_niche(profile: dict) -> dict:
    profile["niches"].insert(0, dict(_TOKEN_NICHE))
    return profile


def test_niche_lines_mark_high_priority_niches(base_profile):
    lines = xai_collector._niche_lines(_with_token_niche(base_profile)).splitlines()
    assert lines[0].startswith("- token-costs (Token costs and LLM economics):")
    assert "[HIGH PRIORITY]" in lines[0]
    assert not any("[HIGH PRIORITY]" in ln for ln in lines[1:])  # only marked niches carry the marker


def test_high_priority_note_demands_coverage_when_signal_exists(base_profile):
    # Profiles without a high-priority niche are unchanged.
    assert xai_collector._high_priority_note(base_profile) == ""
    note = xai_collector._high_priority_note(_with_token_niche(base_profile))
    assert "token-costs" in note
    assert "at least one candidate topic" in note


def test_real_repo_profile_scans_token_costs_every_run():
    profile = load_profile(ROOT / "profile.yaml")
    lines = xai_collector._niche_lines(profile)
    assert "token-costs" in lines
    assert "[HIGH PRIORITY]" in lines
    assert "API pricing" in lines  # dedicated keywords reach the scout


def test_scout_prompt_carries_high_priority_rule(base_profile, settings, monkeypatch):
    settings.xai_api_key = "test-key"
    captured = {}

    def fake_run(_settings, prompt, *a, **k):
        captured["prompt"] = prompt
        text = (
            '{"topics": [{"title": "Frontier API pricing war: token cost down 40%", '
            '"why_hot": "price cut across two providers", "niche": "token-costs", '
            '"post_urls": ["https://x.com/u/status/9"]}]}'
        )
        return text, [], {"input_tokens": 100, "output_tokens": 50, "search_tool_calls": 1}

    monkeypatch.setattr(xai_collector, "_run_tool_search", fake_run)
    items, usage = xai_collector.collect_x_topics(settings, _with_token_niche(base_profile))
    assert "[HIGH PRIORITY]" in captured["prompt"]
    assert "at least one candidate topic" in captured["prompt"]
    assert usage["search_tool_calls"] == 1
    assert len(items) == 1
    assert items[0].channel == "x"
    assert items[0].niche_hint == "token-costs"
    assert items[0].url == "https://x.com/u/status/9"
