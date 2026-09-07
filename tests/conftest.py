from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digest.config import Settings  # noqa: E402


@pytest.fixture
def base_profile() -> dict:
    return {
        "name": "test profile",
        "audience": "test",
        "niches": [
            {"id": "ai-core", "label": "Core AI", "weight": 1.5, "keywords": ["AI", "LLM", "agents", "inference"]},
            {"id": "sports", "label": "Sports", "weight": 1.0, "keywords": ["49ers", "NFL", "soccer"]},
        ],
        "reddit": {"subreddits": ["LLMDevs"], "limit": 25},
        "x_search": {"window_hours": 48, "handles": [], "candidate_topics": 6},
        "portfolio": {"enabled": True, "watchlist": ["SpaceX", "neo4j"]},
        "linkedin": {"inbox": "inbox/linkedin"},
        "drafting": {"tone": "neutral, sharp", "voice_samples": [], "dos": [], "donts": []},
        "brightstack": {
            "name": "Brightstack",
            "one_liner": "AI-native workspace with a full team of agents, so work stays in one place with full context.",
            "audience": "PMs and operators using AI to do work that used to need engineers",
            "flows": ["Research", "Spec", "Implementation", "Review", "Iteration"],
            "voice": {
                "register": "senior practitioner talking to peers",
                "when_to_mention": "only when the post is genuinely about AI workflows or coding agents",
                "never": "no promo spam, no hype, no pitching into unrelated threads",
            },
        },
        "engagement": {"shortlist_size": 2},
        "post_ideas": {"per_channel": 3, "channels": ["x", "linkedin", "reddit"]},
        "rank": {"min_topics": 2, "max_topics": 8},
        "cost": {"per_run_budget_usd": 0.25},
        "delivery": {"email_to": "samislucid98@gmail.com", "email_subject_prefix": "[Feed Digest]"},
        "retention_days": 30,
    }


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings.from_env(env={})
    s.data_dir = tmp_path
    return s
