"""Drafting: headless `claude -p` when available, clearly-marked template fallback otherwise.

On the VPS the Claude Code CLI is installed and covered by Sam's Max plan
automation credit; drafts then match the voice in profile.yaml. In bare
environments (CI, first boot) the fallback produces usable placeholder drafts
marked TEMPLATE DRAFT so nobody mistakes them for final copy.

A claude failure is never silent: `_run_claude` returns the real reason (binary
missing from the service PATH, non-zero exit with claude's own stderr, timeout,
empty or unparsable output) and `draft_digest` carries it out as `claude_error`
so the email subject and run footer can explain a degraded digest.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from .config import Settings
from .rank import Topic

CLAUDE_TIMEOUT_S = 240
TEMPLATE_MARKER = "TEMPLATE DRAFT (claude CLI unavailable in this environment)"
SOURCE_TEXT_LIMIT = 420  # chars of the post's own text given to the drafter
REASON_LIMIT = 300  # chars of failure detail kept for the run footer

_X_ANGLES = [
    "Contrarian take worth posting",
    "Lead with the number",
    "Ask the sharper question",
    "State the prediction plainly",
    "Name the second-order effect",
]
_LINKEDIN_ANGLES = [
    "What this changes for operators",
    "The practitioner lesson here",
    "How teams are actually applying this",
    "The tradeoff nobody mentions",
    "What to do this quarter about it",
]
_REDDIT_ANGLES = [
    "Discussion starter: what does this change for your setup",
    "Who here has hit this in practice",
    "Worth testing this week",
    "The detail most comments will miss",
    "Ask the subreddit what they would do",
]


@dataclass
class ShortlistEntry:
    """One specific post worth commenting on, with a ready-to-post comment."""

    index: int  # 1-based topic index the entry refers to
    title: str
    url: str
    why: str  # comment-fit reason, not raw heat
    comment: str


def _extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of model output."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _source_text(topic: Topic) -> str:
    """The post's own text: the best summary any of the topic's items carries."""
    candidates = [item for item in topic.items if item.summary and item.summary.strip()]
    if not candidates:
        return ""
    chosen = next((i for i in candidates if i.url == topic.source_url), None)
    chosen = chosen or max(candidates, key=lambda i: len(i.summary))
    text = " ".join(chosen.summary.split())
    if len(text) > SOURCE_TEXT_LIMIT:
        text = text[: SOURCE_TEXT_LIMIT - 3].rstrip() + "..."
    return text


def _shortlist_size(profile: dict) -> int:
    return int((profile.get("engagement") or {}).get("shortlist_size", 3))


def _build_prompt(topics: list[Topic], profile: dict, per_channel: int, channels: list[str]) -> str:
    d = profile.get("drafting") or {}
    bs = profile.get("brightstack") or {}
    topic_lines = []
    for i, topic in enumerate(topics, 1):
        src = _source_text(topic)
        text_part = f' | post text: "{src}"' if src else ""
        topic_lines.append(
            f"{i}. {topic.title} | why hot: {topic.why_hot} | source: {topic.source_url} | "
            f"niches: {topic.niche} | channels: {', '.join(topic.channel_labels)}{text_part}"
        )
    samples = "\n".join(f"- {s}" for s in (d.get("voice_samples") or [])) or "- (none yet; neutral sharp register)"
    ideas_example = ", ".join(f'"{c}": ["..."]' for c in channels)
    shortlist_size = _shortlist_size(profile)

    brightstack = ""
    if bs:
        voice = bs.get("voice") or {}
        block = []
        if bs.get("one_liner"):
            block.append(f"- What it is: {bs['one_liner']}")
        if bs.get("audience"):
            block.append(f"- Who it is for: {bs['audience']}")
        if bs.get("flows"):
            block.append(f"- Core flows: {', '.join(str(f) for f in bs['flows'])}")
        if voice.get("when_to_mention"):
            block.append(f"- Mention only when: {voice['when_to_mention']}")
        if voice.get("register"):
            block.append(f"- Voice when mentioning it: {voice['register']}")
        if voice.get("never"):
            block.append(f"- Never: {voice['never']}")
        if block:
            brightstack = (
                "\nBrightstack context (Sam's product; use sparingly, only where it genuinely fits):\n"
                + "\n".join(block)
                + "\n"
            )

    return (
        "You ghostwrite social media engagement for Sam.\n"
        f"Tone: {d.get('tone', 'neutral, sharp, specific')}.\n"
        f"Audience: {profile.get('audience', 'AI builders, investors, sports and markets watchers')}.\n"
        "Do: " + "; ".join(d.get("dos") or []) + "\n"
        "Don't: " + "; ".join(d.get("donts") or []) + "\n"
        "Voice samples:\n" + samples + "\n"
        + brightstack
        + "\n"
        "Topics (ranked):\n" + "\n".join(topic_lines) + "\n\n"
        "1) Suggested comment per topic: the actual comment Sam could post as a reply on\n"
        "   that specific thread. Ground it in the post's own text: build on or answer a\n"
        "   concrete claim from it, and add the practitioner angle it misses. 1-3\n"
        "   sentences, max 300 chars, no hashtags, no emoji, never generic praise.\n"
        f"2) Post ideas: {per_channel} per channel ({', '.join(channels)}). Every idea must be\n"
        "   original synthesis of a theme that shows up across at least two of the topics\n"
        "   above - a fresh take Sam could write. Never restate or retitle a single post,\n"
        "   never propose resharing a link, never 'thoughts on <title>'.\n"
        f"3) Engagement shortlist: the {shortlist_size} topics where a comment from Sam has the\n"
        "   best fit - AI topics where a practitioner reply with light, relevant Brightstack\n"
        "   context lands. Pick by comment-fit, not raw heat. For each: a one-line 'why' and\n"
        "   the ready-to-post comment.\n"
        "Return STRICT JSON only, no prose:\n"
        '{"comments": [{"index": 1, "comment": "..."}], '
        '"post_ideas": {' + ideas_example + "}, "
        '"shortlist": [{"index": 1, "why": "...", "comment": "..."}]}'
    )


def _run_claude(prompt: str, settings: Settings | None = None) -> tuple[str | None, str | None]:
    """Run claude -p headless. Returns (stdout, None), or (None, the real failure reason)."""
    configured = ((settings.claude_bin if settings else "") or "").strip()
    exe = shutil.which(configured) if configured else shutil.which("claude")
    if not exe:
        if configured:
            return None, f"claude binary not found: DIGEST_CLAUDE_BIN={configured} is missing or not executable"
        return None, f"claude binary not found on PATH (PATH={os.environ.get('PATH', '')[:200]})"
    try:
        proc = subprocess.run(
            [exe, "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, f"claude -p timed out after {CLAUDE_TIMEOUT_S}s"
    except OSError as exc:
        return None, f"claude could not start: {exc}"
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())
        if len(detail) > REASON_LIMIT:
            detail = detail[: REASON_LIMIT - 3] + "..."
        return None, f"claude exited {proc.returncode}: {detail or '(no stderr/stdout output)'}"
    if not proc.stdout.strip():
        return None, "claude produced empty output"
    return proc.stdout, None


def _template_comment(topic: Topic) -> str:
    return f"[{TEMPLATE_MARKER}] Worth engaging: {topic.title.strip()} - {topic.why_hot.strip()}"


def _template_post_ideas(topics: list[Topic], per_channel: int, channels: list[str]) -> dict[str, list[str]]:
    """Placeholder ideas shaped like the real thing: cross-topic synthesis angles.

    They are still TEMPLATE-marked placeholders, but they point at a theme shared
    by two topics rather than retitling one post, so the digest shape stays honest.
    """
    ideas: dict[str, list[str]] = {}
    angles = {"x": _X_ANGLES, "linkedin": _LINKEDIN_ANGLES, "reddit": _REDDIT_ANGLES}
    for channel in channels:
        channel_angles = angles.get(channel, angles["x"])
        out: list[str] = []
        for i in range(per_channel):
            angle = channel_angles[i % len(channel_angles)]
            a = topics[i % len(topics)] if topics else None
            b = topics[(i + 1) % len(topics)] if len(topics) > 1 else None
            if a and b:
                out.append(
                    f"[{TEMPLATE_MARKER}] {angle}: synthesize the theme shared by "
                    f'"{a.title.strip()}" and "{b.title.strip()}"'
                )
            elif a:  # single topic: name the angle, never fake a synthesis
                out.append(f'[{TEMPLATE_MARKER}] {angle}: build on "{a.title.strip()}" across this run\'s sources')
            else:  # pragma: no cover - runner guarantees at least one topic or skips drafting
                out.append(f"[{TEMPLATE_MARKER}] {angle}")
        ideas[channel] = out
    return ideas


def _fallback_shortlist(topics: list[Topic], size: int) -> list[ShortlistEntry]:
    """Deterministic comment-fit pick for template runs: AI-fit topics first, then rank order."""
    def fit(pair: tuple[int, Topic]) -> tuple[int, int]:
        idx, topic = pair
        return (0 if (topic.niche or "").startswith("ai") else 1, idx)

    picked = sorted(sorted(enumerate(topics, 1), key=fit)[: max(size, 0)])
    return [
        ShortlistEntry(
            index=idx,
            title=topic.title,
            url=topic.source_url,
            why="AI topic with direct Brightstack fit (template run; not model-scored)",
            comment=topic.comment,
        )
        for idx, topic in picked
    ]


def draft_digest(topics: list[Topic], profile: dict, settings: Settings, log=print) -> dict:
    """Attach suggested comments to topics and produce post ideas + the shortlist.

    Returns {"source": "claude" | "template-fallback", "post_ideas": {...},
    "claude_error": str | None, "shortlist": [ShortlistEntry]} and fills
    topic.comment in place.
    """
    post_cfg = profile.get("post_ideas") or {}
    per_channel = int(post_cfg.get("per_channel", 4))
    channels = list(post_cfg.get("channels") or ["x", "linkedin", "reddit"])
    shortlist_size = _shortlist_size(profile)

    result: dict = {"source": "claude", "post_ideas": {}, "claude_error": None, "shortlist": []}
    parsed = None
    if topics and not settings.disable_claude:
        out, reason = _run_claude(_build_prompt(topics, profile, per_channel, channels), settings)
        if reason:
            result["claude_error"] = reason
            log(f"WARN: claude -p unavailable: {reason}")
        parsed = _extract_json(out) if out else None
        if out and parsed is None:
            result["claude_error"] = "claude output was not parseable JSON"
    elif settings.disable_claude:
        result["claude_error"] = "drafting disabled (DIGEST_DISABLE_CLAUDE=1)"

    comments_by_index: dict[int, str] = {}
    ideas: dict[str, list[str]] = {}
    if parsed:
        for entry in parsed.get("comments") or []:
            try:
                idx = int(entry.get("index"))
                comment = str(entry.get("comment") or "").strip()
            except (TypeError, ValueError):
                continue
            if comment:
                comments_by_index[idx] = comment
        raw_ideas = parsed.get("post_ideas") or {}
        for channel in channels:
            got = [str(s).strip() for s in raw_ideas.get(channel) or [] if str(s).strip()]
            if got:
                ideas[channel] = got[:5]

    used_fallback = False
    for i, topic in enumerate(topics, 1):
        comment = comments_by_index.get(i)
        if not comment:
            used_fallback = True
            comment = _template_comment(topic)
        topic.comment = comment

    missing_channels = [c for c in channels if c not in ideas or len(ideas[c]) < 3]
    if missing_channels:
        used_fallback = True
        ideas.update(_template_post_ideas(topics, per_channel, missing_channels))

    # Engagement shortlist: model entries mapped back to their topics, with a
    # deterministic AI-fit fallback when claude gave none.
    by_index = {i: t for i, t in enumerate(topics, 1)}
    shortlist: list[ShortlistEntry] = []
    for entry in (parsed or {}).get("shortlist") or []:
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        topic = by_index.get(idx)
        comment = str(entry.get("comment") or "").strip()
        if topic and comment:
            shortlist.append(
                ShortlistEntry(
                    index=idx,
                    title=topic.title,
                    url=topic.source_url,
                    why=str(entry.get("why") or "").strip() or "high comment-fit",
                    comment=comment,
                )
            )
    if not shortlist:
        shortlist = _fallback_shortlist(topics, shortlist_size)
    if shortlist_size > 0:
        shortlist = shortlist[:shortlist_size]
    result["shortlist"] = shortlist

    result["post_ideas"] = ideas
    if used_fallback:
        result["source"] = "template-fallback"
        log("WARN: template drafts in this run; the run footer and RUNBOOK.md carry the claude failure reason")
    else:
        log(
            f"drafts via claude -p: {len(comments_by_index)} comments, post ideas for "
            f"{len(ideas)} channels, shortlist {len(shortlist)}"
        )
    return result
