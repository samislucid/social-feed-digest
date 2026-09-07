"""Configuration: profile.yaml carries content settings; env vars carry secrets and infra.

Secrets (xAI key, Reddit credentials, SMTP, page token) never live in the repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised for unreadable or invalid configuration."""


REQUIRED_PROFILE_KEYS = ("niches", "reddit", "x_search", "delivery")


def load_profile(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate the topic profile. Any edit here changes the next run."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Profile file not found: {p.resolve()}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Profile is not valid YAML ({p}): {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Profile root must be a mapping")

    for key in REQUIRED_PROFILE_KEYS:
        if key not in data:
            raise ConfigError(f"Profile is missing required key: {key}")

    niches = data["niches"]
    if not isinstance(niches, list) or not niches:
        raise ConfigError("Profile 'niches' must be a non-empty list")
    for i, niche in enumerate(niches):
        if not isinstance(niche, dict) or "id" not in niche or "keywords" not in niche:
            raise ConfigError(f"niches[{i}] needs 'id' and 'keywords'")
        if not isinstance(niche["keywords"], list) or not niche["keywords"]:
            raise ConfigError(f"niches[{i}].keywords must be a non-empty list")

    reddit = data.get("reddit") or {}
    if not reddit.get("subreddits"):
        raise ConfigError("Profile 'reddit.subreddits' must be a non-empty list")

    delivery = data.get("delivery") or {}
    if not delivery.get("email_to"):
        raise ConfigError("Profile 'delivery.email_to' is required")

    return data


@dataclass
class Settings:
    """Runtime settings resolved from environment variables."""

    data_dir: Path
    xai_api_key: str | None
    xai_model: str
    xai_base_url: str
    page_token: str | None
    reddit_client_id: str | None
    reddit_client_secret: str | None
    reddit_user_agent: str
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    email_to_override: str | None
    disable_claude: bool = False  # DIGEST_DISABLE_CLAUDE=1 forces template drafts (tests, bare CI)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)

        def get(name: str, default: str | None = None) -> str | None:
            value = env.get(name)
            if value is None or value == "":
                return default
            return value

        return cls(
            data_dir=Path(get("DIGEST_DATA_DIR", "data")),
            xai_api_key=get("XAI_API_KEY"),
            xai_model=get("XAI_MODEL", "grok-4.6"),
            xai_base_url=get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
            page_token=get("DIGEST_PAGE_TOKEN"),
            reddit_client_id=get("REDDIT_CLIENT_ID"),
            reddit_client_secret=get("REDDIT_CLIENT_SECRET"),
            reddit_user_agent=get("REDDIT_USER_AGENT", "social-feed-digest/0.1 (personal digest)"),
            smtp_host=get("SMTP_HOST"),
            smtp_port=int(get("SMTP_PORT", "587")),
            smtp_user=get("SMTP_USER"),
            smtp_password=get("SMTP_PASSWORD"),
            smtp_from=get("SMTP_FROM") or get("SMTP_USER"),
            email_to_override=get("DIGEST_EMAIL_TO"),
            disable_claude=get("DIGEST_DISABLE_CLAUDE", "") in ("1", "true", "yes"),
        )
