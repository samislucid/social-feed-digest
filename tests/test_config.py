from __future__ import annotations

import pytest

from digest.config import ConfigError, Settings, load_profile


def _write(tmp_path, text: str) -> str:
    p = tmp_path / "profile.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_valid_profile(tmp_path, base_profile):
    import yaml

    path = _write(tmp_path, yaml.safe_dump(base_profile))
    loaded = load_profile(path)
    assert loaded["name"] == "test profile"
    assert loaded["delivery"]["email_to"] == "samislucid98@gmail.com"


def test_missing_required_key_raises(tmp_path, base_profile):
    import yaml

    del base_profile["reddit"]
    path = _write(tmp_path, yaml.safe_dump(base_profile))
    with pytest.raises(ConfigError, match="reddit"):
        load_profile(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_profile(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path):
    path = _write(tmp_path, "niches: [unclosed")
    with pytest.raises(ConfigError, match="YAML"):
        load_profile(path)


def test_empty_subreddits_raises(tmp_path, base_profile):
    import yaml

    base_profile["reddit"]["subreddits"] = []
    path = _write(tmp_path, yaml.safe_dump(base_profile))
    with pytest.raises(ConfigError, match="subreddits"):
        load_profile(path)


def test_resend_key_used_as_smtp_password():
    s = Settings.from_env(env={"RESEND_API_KEY_HERMES_SOCIAL": "re_test_key"})
    assert s.smtp_password == "re_test_key"


def test_smtp_password_overrides_resend_key():
    s = Settings.from_env(env={"RESEND_API_KEY_HERMES_SOCIAL": "re_test_key", "SMTP_PASSWORD": "generic"})
    assert s.smtp_password == "generic"


def test_settings_from_env_defaults(settings):
    assert settings.xai_api_key is None
    assert settings.xai_model == "grok-4.6"
    assert settings.xai_base_url == "https://api.x.ai/v1"
    assert settings.smtp_port == 587
    assert settings.disable_claude is False


def test_settings_from_env_parses_values():
    s = Settings.from_env(
        env={
            "XAI_API_KEY": "k",
            "DIGEST_PAGE_TOKEN": "tok",
            "SMTP_PORT": "465",
            "DIGEST_EMAIL_TO": "someone@example.com",
            "DIGEST_DISABLE_CLAUDE": "1",
        }
    )
    assert s.xai_api_key == "k"
    assert s.page_token == "tok"
    assert s.smtp_port == 465
    assert s.email_to_override == "someone@example.com"
    assert s.disable_claude is True
