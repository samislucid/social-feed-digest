"""Drafting: headless `claude -p` when available, clearly-marked template fallback otherwise.

On the VPS the Claude Code CLI is installed and covered by Sam's Max plan
automation credit; drafts then match the voice in profile.yaml. In bare
environments (CI, first boot) the fallback produces usable placeholder drafts
marked TEMPLATE DRAFT so nobody mistakes them for final copy.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from .config import Settings
from .rank import Topic

CLAUDE_TIMEOUT_S = 240
TEMPLATE_MARKER = "TEMPLATE DRAFT (claude CLI unavailable in this environment)"

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


def _build_prompt(topics: list[Topic], profile: dict, per_channel: int, channels: list[str]) -> str:
    d = profile.get("drafting") or {}
    topic_lines = []
    for i, topic in enumerate(topics, 1):
        topic_lines.append(
            f"{i}. {topic.title} | why hot: {topic.why_hot} | source: {topic.source_url} | "
            f"niches: {topic.niche} | channels: {', '.join(topic.channel_labels)}"
        )
    samples = "\n".join(f"- {s}" for s in (d.get("voice_samples") or [])) or "- (none yet; neutral sharp register)"
    ideas_example = ", ".join(f'"{c}": ["..."]' for c in channels)
    return (
        "You ghostwrite social media engagement for Sam.\n"
        f"Tone: {d.get('tone', 'neutral, sharp, specific')}.\n"
        f"Audience: {profile.get('audience', 'AI builders, investors, sports and markets watchers')}.\n"
        "Do: " + "; ".join(d.get("dos") or []) + "\n"
        "Don't: " + "; ".join(d.get("donts") or []) + "\n"
        "Voice samples:\n" + samples + "\n\n"
        "Topics (ranked):\n" + "\n".join(topic_lines) + "\n\n"
        f"For each topic write a ready-to-paste suggested comment: 1-2 sentences, max 280 chars, "
        "no hashtags, no emoji, specific to the source.\n"
        f"Also write {per_channel} fresh, original post ideas per channel ({', '.join(channels)}): "
        "short hooks or takes, not replies to the topics above.\n"
        'Return STRICT JSON only, no prose:\n'
        '{"comments": [{"index": 1, "comment": "..."}], '
        '"post_ideas": {' + ideas_example + '}}'
    )


def _run_claude(prompt: str, settings: Settings | None = None) -> str | None:
    """Run claude -p headless. Returns stdout text or None when unusable."""
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def _template_comment(topic: Topic) -> str:
    return f"[{TEMPLATE_MARKER}] Worth engaging: {topic.title.strip()} - {topic.why_hot.strip()}"


def _template_post_ideas(topics: list[Topic], per_channel: int, channels: list[str]) -> dict[str, list[str]]:
    ideas: dict[str, list[str]] = {}
    angles = {"x": _X_ANGLES, "linkedin": _LINKEDIN_ANGLES, "reddit": _REDDIT_ANGLES}
    for channel in channels:
        channel_angles = angles.get(channel, angles["x"])
        out: list[str] = []
        for i in range(per_channel):
            topic = topics[i % max(len(topics), 1)] if topics else None
            angle = channel_angles[i % len(channel_angles)]
            if topic:
                out.append(f"[{TEMPLATE_MARKER}] {angle}: {topic.title.strip()}")
            else:  # pragma: no cover - runner guarantees at least one topic or skips drafting
                out.append(f"[{TEMPLATE_MARKER}] {angle}")
        ideas[channel] = out
    return ideas


def draft_digest(topics: list[Topic], profile: dict, settings: Settings, log=print) -> dict:
    """Attach suggested comments to topics and produce per-channel post ideas.

    Returns {"source": "claude" | "template-fallback", "post_ideas": {...}} and
    fills topic.comment in place.
    """
    post_cfg = profile.get("post_ideas") or {}
    per_channel = int(post_cfg.get("per_channel", 4))
    channels = list(post_cfg.get("channels") or ["x", "linkedin", "reddit"])

    result: dict = {"source": "claude", "post_ideas": {}}
    parsed = None
    if topics and not settings.disable_claude:
        out = _run_claude(_build_prompt(topics, profile, per_channel, channels), settings)
        parsed = _extract_json(out) if out else None

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
        fallback = _template_post_ideas(topics, per_channel, missing_channels)
        ideas.update(fallback)

    result["post_ideas"] = ideas
    if used_fallback:
        result["source"] = "template-fallback"
        log("WARN: using template drafts (claude -p unavailable or unparsable); drafts are marked TEMPLATE DRAFT")
    else:
        log(f"drafts via claude -p: {len(comments_by_index)} comments, post ideas for {len(ideas)} channels")
    return result
