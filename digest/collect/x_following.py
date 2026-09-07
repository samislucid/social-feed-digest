"""Optional X API v2 seam: sync Sam's following list, boost followed authors.

Modeled on the Reddit PRAW seam: without credentials the worker behaves exactly
as before (no requests, no state writes). With X_API_* user-context credentials
set, GET /2/users/{id}/following is fetched at most once a week, cached in
data/state/x_following.json (portfolio-sweep state-marker pattern), and used by
rank.build_topics to boost digest items authored by followed accounts.

Cost model (docs.x.com, checked 2026-09-07):
  - Owned Reads: reading the authenticated user's own following list costs
    $0.001 per returned account (per the official pricing docs).
  - The sync is hard-capped by the run's remaining per_run_budget_usd: it
    requests only pages it can afford and resumes from the cursor on the next
    weekly window. On sync days a reserve keeps the standing xAI calls inside
    the same per-run budget.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import Settings

COST_PER_ACCOUNT_USD = 0.001  # Owned Reads, docs.x.com pricing
MAX_PAGE_RESULTS = 1000  # endpoint max_results cap (docs.x.com/x-api/users/get-following)
FOLLOWING_REFRESH_DAYS = 7  # refresh no more than weekly; never per-run
REQUEST_TIMEOUT_S = 30
# Room for the standing xAI calls (X trends + web/news sweep) on sync days so
# the combined run stays inside cost.per_run_budget_usd.
XAI_STANDING_RESERVE_USD = 0.10


class XFollowingError(RuntimeError):
    """Raised when the X API fails before any page could be fetched."""


def configured(settings: Settings) -> bool:
    """The seam activates only with full OAuth 1.0a user-context credentials."""
    return bool(
        settings.x_api_access_token
        and settings.x_api_access_token_secret
        and settings.x_api_oauth1_consumer_key
        and settings.x_api_oauth1_consumer_secret
        and settings.x_api_user_id
    )


def state_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "state" / "x_following.json"


def load_state(settings: Settings) -> dict:
    try:
        data = json.loads(state_path(settings).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(settings: Settings, state: dict) -> None:
    path = state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def cached_handles(settings: Settings) -> list[str]:
    return [h for h in load_state(settings).get("handles") or [] if h]


def is_due(settings: Settings, now: datetime | None = None) -> bool:
    """Due when never synced, or the last successful sync is older than a week."""
    state = load_state(settings)
    raw = str(state.get("synced_utc") or "")
    if not raw:
        return True
    try:
        synced = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - synced >= timedelta(days=FOLLOWING_REFRESH_DAYS)


def _oauth1(settings: Settings):
    """OAuth 1.0a user-context signing (user access tokens never expire)."""
    from requests_oauthlib import OAuth1

    return OAuth1(
        client_key=settings.x_api_oauth1_consumer_key,
        client_secret=settings.x_api_oauth1_consumer_secret,
        resource_owner_key=settings.x_api_access_token,
        resource_owner_secret=settings.x_api_access_token_secret,
    )


def sync(
    settings: Settings, remaining_budget_usd: float, log=print
) -> tuple[list[str], dict]:
    """Fetch following pages while inside the budget; merge into the cache.

    Returns (handles, usage). usage is {} when no page was affordable. Raises
    XFollowingError only when the first page fails (nothing fetched, nothing
    spent, cache untouched); a mid-pagination failure keeps the partial result
    and its cursor for the next weekly window.
    """
    try:
        auth = _oauth1(settings)
    except ImportError as exc:
        raise XFollowingError(
            "requests-oauthlib is not installed; run: pip install -e '.[xapi]'"
        ) from exc

    url = f"{settings.x_api_base_url.rstrip('/')}/2/users/{settings.x_api_user_id}/following"
    state = load_state(settings)
    handles = [h for h in state.get("handles") or [] if h]
    token = str(state.get("next_token") or "") or None
    spent = 0.0
    pages = 0
    accounts = 0

    while True:
        page_size = min(
            MAX_PAGE_RESULTS, int((remaining_budget_usd - spent) / COST_PER_ACCOUNT_USD)
        )
        if page_size < 1:
            if pages == 0:
                log(
                    f"X following sync skipped: ${spent:.3f} spent leaves less than "
                    f"${COST_PER_ACCOUNT_USD} of the run budget for one account"
                )
                return handles, {}
            break

        params: dict = {"max_results": page_size, "user.fields": "username"}
        if token:
            params["pagination_token"] = token
        try:
            resp = requests.get(url, auth=auth, params=params, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as exc:
            if pages == 0:
                raise XFollowingError(f"X API request failed: {exc}") from exc
            log(f"WARN: X following sync interrupted after {pages} page(s): {exc}")
            break
        if resp.status_code != 200:
            detail = (resp.text or "")[:200]
            if pages == 0:
                raise XFollowingError(f"X API returned HTTP {resp.status_code}: {detail}")
            log(
                f"WARN: X following sync interrupted after {pages} page(s): "
                f"HTTP {resp.status_code}"
            )
            break

        data = resp.json() or {}
        users = [u for u in data.get("data") or [] if str(u.get("username") or "").strip()]
        handles.extend(str(u["username"]).strip().lstrip("@").lower() for u in users)
        accounts += len(users)
        spent += COST_PER_ACCOUNT_USD * len(users)
        pages += 1
        token = ((data.get("meta") or {}).get("next_token")) or None
        if not token or not users:
            break

    state = {
        "synced_utc": datetime.now(timezone.utc).isoformat(),
        "handles": sorted(set(handles)),
        "accounts_read_this_sync": accounts,
        "cost_usd": round(spent, 4),
        "next_token": token or "",
        "complete": not bool(token),
    }
    save_state(settings, state)
    log(
        f"X following sync: {accounts} accounts read ({pages} page(s)), "
        f"{len(state['handles'])} cached handles, ${spent:.3f} spent"
        + ("" if not token else "; resumes next weekly window")
    )
    return state["handles"], {
        "cost_usd": round(spent, 4),
        "accounts_read": accounts,
        "search_tool_calls": 0,
    }


def ensure_following(
    settings: Settings, budget_usd: float, log=print
) -> tuple[list[str], dict]:
    """Scheduled-run entry point: sync when configured and due, else serve cache.

    Returns (handles, usage); usage is {} on cache days. Unconfigured callers
    get ([], {}) with no requests and no state writes - exactly the old behavior.
    """
    if not configured(settings):
        return [], {}
    if not is_due(settings):
        return cached_handles(settings), {}
    reserve = XAI_STANDING_RESERVE_USD if settings.xai_api_key else 0.0
    return sync(settings, budget_usd - reserve, log=log)