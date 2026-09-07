"""xAI Live Search collector.

Uses the Responses API with server-side `x_search` and `web_search` tools.
Cost model (per docs.x.ai/developers/pricing, checked 2026-09-07):
  - search tool invocations: $5 per 1,000 calls ($0.005 each)
  - grok-4.6 tokens: $2.00/1M input, $6.00/1M output (short context)
Two focused calls per run (X trends, web/news sweep) with low reasoning effort
stay well inside the $0.25 per-run budget.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import requests

from ..config import Settings
from ..items import Item

TOOL_CALL_COST_USD = 0.005  # $5 per 1k search tool invocations
INPUT_TOKEN_COST_PER_M = 2.00
OUTPUT_TOKEN_COST_PER_M = 6.00
REQUEST_TIMEOUT_S = 180


class XaiError(RuntimeError):
    """Raised when the xAI API fails in a way the run should report, not swallow."""


def _usage_from_response(data: dict) -> dict:
    """Extract a cost report for one Responses API call."""
    usage = data.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    search_calls = 0
    for part in data.get("output") or []:
        ptype = str(part.get("type", "")) if isinstance(part, dict) else ""
        # Real Responses API returns {"type": "custom_tool_call", "name": "x_keyword_search"};
        # count both that shape and any explicit search-tool item.
        if "search" in ptype or ptype == "custom_tool_call":
            search_calls += 1
    cost = (
        input_tokens / 1_000_000 * INPUT_TOKEN_COST_PER_M
        + output_tokens / 1_000_000 * OUTPUT_TOKEN_COST_PER_M
        + search_calls * TOOL_CALL_COST_USD
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "search_tool_calls": search_calls,
        "cost_usd": round(cost, 6),
    }


def _post_responses(settings: Settings, payload: dict) -> dict:
    resp = requests.post(
        f"{settings.xai_base_url}/responses",
        json=payload,
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        timeout=REQUEST_TIMEOUT_S,
    )
    if resp.status_code in (401, 403):
        raise XaiError(f"xAI auth failed ({resp.status_code}); check XAI_API_KEY")
    if resp.status_code == 402:
        raise XaiError("xAI billing error (402); check console.x.ai credits")
    if resp.status_code >= 400:
        raise XaiError(f"xAI API error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _output_text(data: dict) -> str:
    chunks: list[str] = []
    for part in data.get("output") or []:
        if not isinstance(part, dict) or part.get("type") != "message":
            continue
        for piece in part.get("content") or []:
            if isinstance(piece, dict) and piece.get("type") == "output_text":
                chunks.append(piece.get("text") or "")
    return "\n".join(chunks)


def _extract_json(text: str) -> dict:
    """Parse the first balanced JSON object found in the model output."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise XaiError("Model output contained no parseable JSON object")


def _window(profile: dict) -> tuple[str, str]:
    hours = int((profile.get("x_search") or {}).get("window_hours", 48))
    now = datetime.now(timezone.utc)
    return (
        (now - timedelta(hours=hours)).date().isoformat(),
        (now + timedelta(days=1)).date().isoformat(),
    )


def _niche_lines(profile: dict) -> str:
    # Compact on purpose: the prompt is re-sent on every agent search turn, so
    # verbose keyword lists multiply the input-token cost of a run.
    lines = []
    for niche in profile["niches"]:
        kws = ", ".join(niche["keywords"][:8])
        lines.append(f"- {niche['id']} ({niche.get('label', niche['id'])}): {kws}")
    return "\n".join(lines)


def _run_tool_search(
    settings: Settings, prompt: str, tools: list[dict], max_output_tokens: int = 3500
) -> tuple[str, list[str], dict]:
    payload = {
        "model": settings.xai_model,
        "input": [{"role": "user", "content": prompt}],
        "tools": tools,
        "include": ["no_inline_citations"],
        "reasoning": {"effort": "low"},
        "max_output_tokens": max_output_tokens,
    }
    data = _post_responses(settings, payload)
    text = _output_text(data)
    citations = [c for c in (data.get("citations") or []) if isinstance(c, str)]
    return text, citations, _usage_from_response(data)


_X_POST_URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,20})/status/")


def collect_x_topics(
    settings: Settings, profile: dict, followed_handles: list[str] | None = None
) -> tuple[list[Item], dict]:
    """Hot topics on X within the profile's niches, each with source post URLs.

    followed_handles (optional, from the X API v2 seam) are folded into the
    scout prompt at no extra cost: same single call, same tool budget.
    """
    if not settings.xai_api_key:
        raise XaiError("XAI_API_KEY is not set; X trend collection is unavailable")

    cfg = profile.get("x_search") or {}
    handles = [h for h in (cfg.get("handles") or []) if h]
    for h in followed_handles or []:
        if h and h.lower() not in {x.lower() for x in handles}:
            handles.append(h)
    candidates = int(cfg.get("candidate_topics", 12))
    from_date, to_date = _window(profile)
    handle_note = (
        " Pay special attention to recent posts from these voices: "
        + ", ".join(handles[:40])
        + (f" (+{len(handles) - 40} more)." if len(handles) > 40 else ".")
        if handles
        else ""
    )

    prompt = (
        "You are a trend scout. Use x_search to find what is genuinely hot on X in the last "
        f"{from_date} to {to_date} window for the audience of an AI builder and investor.\n"
        f"Niche clusters and keywords (weight in parentheses):\n{_niche_lines(profile)}\n"
        f"{handle_note}\n"
        "Rank candidates by momentum (reposts, replies, velocity) and niche fit. "
        f"Return {candidates} candidate topics as STRICT JSON only, no prose:\n"
        '{"topics": [{"title": "...", "why_hot": "one line with the concrete signal", '
        '"niche": "<niche id>", "post_urls": ["https://x.com/<user>/status/<id>", ...]}]}\n'
        "Rules: every topic needs at least one real post URL you saw in results; "
        "titles are specific, not generic; no duplicates. Report only topics you verified in "
        "posts; never speculate about claims you did not see. "
        "Cost budget: use AT MOST 3 x_search invocations for this entire call, then answer."
    )
    tools = [{"type": "x_search", "from_date": from_date, "to_date": to_date}]
    text, _citations, usage = _run_tool_search(settings, prompt, tools, max_output_tokens=2800)
    payload = _extract_json(text)

    items: list[Item] = []
    for topic in payload.get("topics") or []:
        title = str(topic.get("title") or "").strip()
        if not title:
            continue
        why = str(topic.get("why_hot") or "").strip()
        niche = str(topic.get("niche") or "").strip()
        urls = [u for u in (topic.get("post_urls") or []) if isinstance(u, str) and u.startswith("http")]
        if not urls:
            continue
        for url in urls:
            author = ""
            match = _X_POST_URL_RE.match(url)
            if match:
                author = match.group(1).lower()
            items.append(
                Item(
                    channel="x",
                    title=title,
                    url=url,
                    summary=why,
                    source_label="X (via Live Search)",
                    niche_hint=niche,
                    extra={"author": author} if author else {},
                )
            )
    return items, usage


def collect_web_sweep(settings: Settings, profile: dict, include_portfolio: bool) -> tuple[list[Item], dict]:
    """Web/news sweep over the niches; optionally one batched portfolio-watchlist scan."""
    if not settings.xai_api_key:
        raise XaiError("XAI_API_KEY is not set; web/news sweep is unavailable")

    from_date, to_date = _window(profile)
    portfolio = profile.get("portfolio") or {}
    watchlist = [w for w in (portfolio.get("watchlist") or []) if w] if include_portfolio else []

    portfolio_block = ""
    if watchlist:
        names = "\n".join(f"- {name}" for name in watchlist)
        portfolio_block = (
            "Also scan this private-markets watchlist as ONE batched sweep (1-3 searches "
            f"covering the whole list, not per-entity): \n{names}\n"
            "Only report entities with real news in the window.\n"
        )

    if watchlist:
        json_example = (
            '{"news": [{"title": "...", "why_hot": "one line with the concrete signal", '
            '"url": "https://...", "niche": "<niche id>"}], '
            '"portfolio": [{"entity": "...", "title": "...", "url": "https://...", '
            '"why_hot": "one line"}]}'
        )
    else:
        json_example = (
            '{"news": [{"title": "...", "why_hot": "one line with the concrete signal", '
            '"url": "https://...", "niche": "<niche id>"}]}'
        )

    prompt = (
        "Use web_search (and news results) for what is newly notable in the last "
        f"{from_date} to {to_date} window for these niche clusters:\n{_niche_lines(profile)}\n"
        f"{portfolio_block}"
        "Prefer primary or high-signal sources. Report only items you verified in results. "
        "Cost budget: use AT MOST 2 web_search invocations for this entire call"
        + ("; batch many watchlist entities into each query" if watchlist else "")
        + ", then answer. Return STRICT JSON only, no prose:\n"
        + json_example
    )
    tools = [{"type": "web_search"}]
    text, _citations, usage = _run_tool_search(settings, prompt, tools, max_output_tokens=1800)
    payload = _extract_json(text)

    items: list[Item] = []
    for entry in payload.get("news") or []:
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not title or not url.startswith("http"):
            continue
        items.append(
            Item(
                channel="web",
                title=title,
                url=url,
                summary=str(entry.get("why_hot") or "").strip(),
                source_label=url.split("/")[2] if "://" in url else "",
                niche_hint=str(entry.get("niche") or "").strip(),
            )
        )
    for entry in payload.get("portfolio") or []:
        title = str(entry.get("title") or "").strip()
        url = str(entry.get("url") or "").strip()
        entity = str(entry.get("entity") or "").strip()
        if not title or not url.startswith("http"):
            continue
        items.append(
            Item(
                channel="news",
                title=title,
                url=url,
                summary=str(entry.get("why_hot") or "").strip(),
                source_label=f"watchlist: {entity}" if entity else "watchlist",
                niche_hint="markets",
                extra={"portfolio_entity": entity},
            )
        )
    return items, usage


