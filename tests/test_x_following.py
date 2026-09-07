from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from digest.collect import x_following
from digest.collect.x_following import (
    configured,
    ensure_following,
    is_due,
    load_state,
    save_state,
    sync,
)
from digest.config import Settings


def _settings(tmp_path, env_extra: dict | None = None) -> Settings:
    env = {
        "X_API_OAUTH1_CONSUMER_KEY": "ck",
        "X_API_OAUTH1_CONSUMER_SECRET": "cs",
        "X_API_ACCESS_TOKEN": "tok",
        "X_API_ACCESS_TOKEN_SECRET": "toksec",
        "X_API_USER_ID": "123",
    }
    env.update(env_extra or {})
    s = Settings.from_env(env=env)
    s.data_dir = tmp_path
    return s


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self.text = str(payload)
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _page(handles: list[str], next_token: str | None = None) -> dict:
    payload = {"data": [{"id": str(i), "username": h} for i, h in enumerate(handles)]}
    if next_token:
        payload["meta"] = {"next_token": next_token, "result_count": len(handles)}
    return payload


def test_unconfigured_seam_is_inert(tmp_path, monkeypatch):
    s = _settings(tmp_path, {"X_API_ACCESS_TOKEN": ""})
    assert not configured(s)
    calls = []

    def fail_get(*args, **kwargs):
        calls.append(1)
        raise AssertionError("no HTTP expected")

    monkeypatch.setattr(x_following.requests, "get", fail_get)
    assert ensure_following(s, 0.25) == ([], {})
    assert not (tmp_path / "state" / "x_following.json").exists()
    assert calls == []


def test_sync_paginates_merges_and_caches(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    seen_params = []

    def fake_get(url, auth=None, params=None, timeout=None):
        seen_params.append(dict(params or {}))
        if params.get("pagination_token") is None:
            return _Resp(_page(["Alice", "@bob"], next_token="t2"))
        return _Resp(_page(["carol"]))

    monkeypatch.setattr(x_following.requests, "get", fake_get)
    handles, usage = sync(s, 0.25)
    assert handles == ["alice", "bob", "carol"]
    assert usage["accounts_read"] == 3
    assert usage["cost_usd"] == pytest.approx(0.003)
    assert seen_params[0]["max_results"] == 250  # budget-capped: $0.25 / $0.001
    state = load_state(s)
    assert state["complete"] is True and state["next_token"] == ""
    assert is_due(s) is False


def test_sync_resumes_cursor_within_weekly_budget(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    seen_params = []

    def fake_get(url, auth=None, params=None, timeout=None):
        seen_params.append(dict(params or {}))
        n = int(params["max_results"])
        token = f"t{len(seen_params) + 1}"
        return _Resp(_page([f"h{len(seen_params)}-{i}" for i in range(n)], next_token=token))

    monkeypatch.setattr(x_following.requests, "get", fake_get)
    handles, usage = sync(s, 0.002)  # affords 2 accounts this window
    assert len(handles) == 2 and usage["cost_usd"] == pytest.approx(0.002)
    assert load_state(s)["complete"] is False

    handles, usage = sync(s, 0.002)  # next weekly window resumes the cursor
    assert seen_params[1]["pagination_token"] == "t2"
    assert len(handles) == 4 and usage["cost_usd"] == pytest.approx(0.002)


def test_sync_skips_when_budget_affords_nothing(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    save_state(s, {"synced_utc": "2026-08-01T00:00:00+00:00", "handles": ["cached"]})
    calls = []

    def fail_get(*args, **kwargs):
        calls.append(1)
        raise AssertionError("no request should fire")

    monkeypatch.setattr(x_following.requests, "get", fail_get)
    handles, usage = sync(s, 0.0005)
    assert handles == ["cached"] and usage == {}
    assert calls == []
    assert load_state(s)["synced_utc"] == "2026-08-01T00:00:00+00:00"  # weekly gate preserved


def test_first_page_failure_raises_and_keeps_cache(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    save_state(s, {"synced_utc": "2026-08-01T00:00:00+00:00", "handles": ["cached"]})

    def fail_get(*args, **kwargs):
        return _Resp({"title": "Unauthorized"}, status_code=401)

    monkeypatch.setattr(x_following.requests, "get", fail_get)
    with pytest.raises(x_following.XFollowingError):
        sync(s, 0.25)
    assert load_state(s)["handles"] == ["cached"]  # cache untouched, gate preserved


def test_mid_pagination_failure_keeps_partial_result(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    calls = []

    def fake_get(url, auth=None, params=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _Resp(_page(["a", "b"], next_token="t2"))
        return _Resp({"title": "Too Many Requests"}, status_code=429)

    monkeypatch.setattr(x_following.requests, "get", fake_get)
    handles, usage = sync(s, 0.25)
    assert handles == ["a", "b"]
    state = load_state(s)
    assert state["next_token"] == "t2" and state["complete"] is False
    assert is_due(s) is False  # failed attempt still respects the weekly window


def test_ensure_following_serves_cache_between_windows(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    save_state(s, {"synced_utc": datetime.now(timezone.utc).isoformat(), "handles": ["a"]})
    calls = []

    def fail_get(*args, **kwargs):
        calls.append(1)
        raise AssertionError("cache day must not call the API")

    monkeypatch.setattr(x_following.requests, "get", fail_get)
    assert ensure_following(s, 0.25) == (["a"], {})
    assert calls == []


def test_ensure_following_reserves_xai_headroom(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    s.xai_api_key = "xai-test"
    seen = {}

    def fake_get(url, auth=None, params=None, timeout=None):
        seen.update(params or {})
        return _Resp(_page(["a"]))

    monkeypatch.setattr(x_following.requests, "get", fake_get)
    ensure_following(s, 0.25)
    # 0.25 budget - 0.10 xAI reserve = 0.15 affordable -> 150 accounts max
    assert seen["max_results"] == 150


def test_is_due_gates_weekly(tmp_path):
    s = _settings(tmp_path)
    assert is_due(s) is True
    save_state(s, {"synced_utc": datetime.now(timezone.utc).isoformat(), "handles": []})
    assert is_due(s) is False
    old = datetime.now(timezone.utc) - timedelta(days=8)
    save_state(s, {"synced_utc": old.isoformat(), "handles": []})
    assert is_due(s) is True


def test_missing_optional_dependency_raises(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    monkeypatch.setitem(sys.modules, "requests_oauthlib", None)
    with pytest.raises(x_following.XFollowingError, match="requests-oauthlib"):
        sync(s, 0.25)