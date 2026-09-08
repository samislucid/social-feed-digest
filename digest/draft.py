"""Drafting: headless `claude -p` when available, clearly-marked template fallback otherwise.

On the VPS the Claude Code CLI is installed and covered by Sam's Max plan
automation credit; drafts then match the voice in profile.yaml. In bare
environments (CI, first boot) the fallback produces usable placeholder drafts
marked TEMPLATE DRAFT so nobody mistakes them for final copy.

A claude failure is never silent: `_run_claude` returns the real reason (binary
missing from the service PATH, non-zero exit with claude's own stderr, timeout,
empty or unparsable output) and `draft_digest` carries it out as `claude_error`
so the email subject and run footer can explain a degraded digest. An incomplete
response - a missing channel or field for a channel that has posts - is retried
once before any gap-patching; remaining gaps are patched with TEMPLATE content
and logged as WARN with the real reason. `draft_source` distinguishes a partial
patch ("claude-partial") from a full fallback ("template-fallback"). Web/news is
deterministic context the prompt never contracts, so it is never counted as a
gap - that prompt/validator mismatch silently degraded the 2026-09-07 17:30 run.

The drafting target is the channel-first shape (shape.py): per channel an
activity summary, notable posts, ready-to-post drafts for Sam's own account,
and engagement suggestions. Sam posts everything manually; the worker never
posts anywhere.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .config import Settings
from .shape import Engagement, Notable, Section, topic_author
from .rank import Topic

CLAUDE_TIMEOUT_S = 240
TEMPLATE_MARKER = "TEMPLATE DRAFT (claude CLI unavailable in this environment)"
SOURCE_TEXT_LIMIT = 420  # chars of the post's own text given to the drafter
REASON_LIMIT = 300  # chars of failure detail kept for the run footer

_NOTABLE_MAX = 4  # "a few specific tweets/posts getting attention"
_DRAFT_MAX = 3  # 2-3 ready-to-post drafts per channel
_DRAFT_COUNT = 2  # template fallback produces this many drafts per channel
_ENGAGEMENT_MAX = 3

# Actions the worker may suggest per channel; Sam executes them manually.
_ACTIONS = {"x": ("comment", "retweet"), "linkedin": ("comment", "reshare"), "reddit": ("comment",)}

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


def _guidance_strings(entries: object) -> list[str]:
    """Flatten free-form profile guidance (dos/donts/voice_samples) to prompt strings.

    profile.yaml is user-editable state ("Edit freely" per the file header), so
    entries may be plain strings or structured mappings such as a pasted voice
    block. Render any shape readably; never raise inside _build_prompt.
    """
    out: list[str] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            rendered = "; ".join(f"{k}: {v}" for k, v in entry.items() if str(v or "").strip())
        elif entry is None:
            rendered = ""
        else:
            rendered = str(entry)
        rendered = " ".join(str(rendered).split())
        if rendered:
            out.append(rendered)
    return out


def _brightstack_block(profile: dict) -> str:
    """The Brightstack positioning/voice block, verbatim from profile.yaml.

    Must keep reaching the prompt: it is what keeps product mentions relevant
    and rare instead of promo.
    """
    bs = profile.get("brightstack") or {}
    if not bs:
        return ""
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
    if not block:
        return ""
    return (
        "\nBrightstack context (Sam's product; use sparingly, only where it genuinely fits):\n"
        + "\n".join(block)
        + "\n"
    )


_CHANNEL_LABELS = {"x": "X posts", "linkedin": "LinkedIn posts", "reddit": "Reddit threads"}


def _candidate_line(i: int, topic: Topic) -> str:
    src = _source_text(topic)
    text_part = f' | post text: "{src}"' if src else ""
    who = topic_author(topic)
    who_part = f"author: {who} | " if who else ""
    return (
        f"{i}. {topic.title} | {who_part}url: {topic.source_url} | "
        f"niches: {topic.niche} | why hot: {topic.why_hot}{text_part}"
    )


def _build_prompt(sections: list[Section], profile: dict) -> str:
    d = profile.get("drafting") or {}
    samples = "\n".join(f"- {s}" for s in _guidance_strings(d.get("voice_samples"))) or (
        "- (none yet; neutral sharp register)"
    )
    by_channel = {s.channel: s for s in sections}

    blocks: list[str] = []
    for channel in ("x", "linkedin", "reddit"):
        section = by_channel.get(channel)
        candidates = section.candidates if section else []
        label = _CHANNEL_LABELS[channel]
        if candidates:
            lines = "\n".join(_candidate_line(i, t) for i, t in enumerate(candidates, 1))
            blocks.append(f"{label}:\n{lines}")
        else:
            blocks.append(f"{label}: (none this run)")
    web = by_channel.get("web")
    if web and web.candidates:
        wlines = "\n".join(
            f"{i}. {t.title} | url: {t.source_url} | why hot: {t.why_hot}"
            for i, t in enumerate(web.candidates, 1)
        )
        blocks.append(
            "Background from web/news (context for drafts; not a posting surface):\n" + wlines
        )
    inputs = "\n\n".join(blocks)

    return (
        "You ghostwrite Sam's private social feed digest. The digest is channel-first:\n"
        "per channel it ships an activity summary, the posts getting attention,\n"
        "ready-to-post drafts for Sam's own account, and recommended engagement.\n"
        f"Tone: {d.get('tone', 'neutral, sharp, specific')}.\n"
        f"Audience: {profile.get('audience', 'AI builders, investors, sports and markets watchers')}.\n"
        "Do: " + "; ".join(_guidance_strings(d.get("dos"))) + "\n"
        "Don't: " + "; ".join(_guidance_strings(d.get("donts"))) + "\n"
        "Voice samples:\n" + samples + "\n"
        + _brightstack_block(profile)
        + "\n"
        f"{inputs}\n\n"
        "TASK\n"
        "Return JSON only (no prose, no code fence) exactly in this schema, with\n"
        "every key and field present:\n"
        '{"x": {"summary": "...", "notable": [{"index": 1, "why": "..."}], '
        '"drafts": ["..."], "engagements": [{"index": 1, "action": "comment", "comment": "..."}]}, '
        '"linkedin": {"summary": "...", "notable": [{"index": 1, "why": "..."}], '
        '"drafts": ["..."], "engagements": [{"index": 1, "action": "comment", "comment": "..."}]}, '
        '"reddit": {"summary": "...", "engagements": [{"index": 1, "comment": "..."}]}}\n\n'
        "Per channel:\n"
        "- summary: 1-3 sentences on what happened in this channel across the listed posts.\n"
        "- notable (X and LinkedIn only): 2-4 specific posts from the list that are getting\n"
        "  attention, each with a concrete 'why it matters' grounded in the post's own text.\n"
        "- drafts: 2-3 ready-to-post posts for Sam's own account. Original synthesis drawing\n"
        "  on two or more of this run's posts; never a retitle of one article and never a\n"
        "  link share. X drafts <= 280 characters each; LinkedIn drafts 60-120 words.\n"
        "- engagements: 2-3 recommended manual actions on specific listed posts. Allowed\n"
        "  actions: X 'comment' or 'retweet'; LinkedIn 'comment' or 'reshare'; Reddit\n"
        "  'comment' only. Comments must be real, postable replies to that specific thread,\n"
        "  grounded in the post's own text. For 'retweet'/'reshare', the comment is one\n"
        "  sentence on why the repost is worth Sam's name.\n"
        "- 'index' must reference the numbered list of that channel; links and authors are\n"
        "  attached from source data and never invented.\n"
        "- Completeness is a hard requirement: every channel key (x, linkedin, reddit)\n"
        "  and every field in the schema MUST appear in your response. Never omit a key\n"
        "  or a field. If a channel list says (none this run), still return all of its\n"
        "  fields with explicit empty values (summary \"\" and empty arrays). For a\n"
        "  channel that has posts, an empty summary or empty arrays are a defect that\n"
        "  forces a retry. The web/news background needs no JSON entry; context only.\n"
        "\nSam posts everything manually. You never post; you only draft."
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


def _template_summary(section: Section) -> str:
    label = {"x": "X", "linkedin": "LinkedIn", "reddit": "Reddit"}.get(section.channel, section.channel)
    if not section.candidates:
        return ""
    lead = section.candidates[0].title.strip()
    unit = "thread" if section.channel == "reddit" else "post"
    n = len(section.candidates)
    return f"{n} {label} {unit}{'s' if n != 1 else ''} on your topics this run; loudest: \"{lead}\"."


def _template_drafts(section: Section) -> list[str]:
    angles = _X_ANGLES if section.channel == "x" else _LINKEDIN_ANGLES
    cands = section.candidates
    out: list[str] = []
    for i in range(min(_DRAFT_COUNT, len(cands)) if cands else 0):
        angle = angles[i % len(angles)]
        a = cands[i % len(cands)]
        b = cands[(i + 1) % len(cands)] if len(cands) > 1 else None
        if b and b is not a:
            hint = (
                f"[{TEMPLATE_MARKER}] {angle}: connect \"{a.title.strip()}\" and "
                f"\"{b.title.strip()}\" into one post"
            )
        else:  # single candidate: name the angle, never fake a synthesis
            hint = f"[{TEMPLATE_MARKER}] {angle}: build on \"{a.title.strip()}\" across this run's sources"
        out.append(hint)
    return out


def _candidate_at(section: Section, index: object) -> Topic | None:
    try:
        idx = int(index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if 1 <= idx <= len(section.candidates):
        return section.candidates[idx - 1]
    return None


def _eval_claude(sections: list[Section], parsed: dict, apply: bool) -> list[str]:
    """Validate (and optionally apply) parsed claude JSON; return the gaps.

    The contract is exactly what the prompt demands: for each of x/linkedin/
    reddit with candidates, every field must be present and non-empty. Channels
    without candidates render a visible note and are never gaps; web/news is
    shaped deterministically and the prompt never contracts it, so it is never
    a gap either. With apply=False this is the read-only validator that decides
    whether a response is complete before it touches the sections.
    """
    gaps: list[str] = []
    for section in sections:
        channel = section.channel
        if channel == "web" or not section.candidates:
            continue  # web is deterministic background; an empty channel shows its note
        data = parsed.get(channel)
        data = data if isinstance(data, dict) else {}

        summary = str(data.get("summary") or "").strip()
        if summary:
            if apply:
                section.summary = summary
        else:
            gaps.append(f"{channel}: no summary")

        if channel in ("x", "linkedin"):
            notable: list[Notable] = []
            for entry in (data.get("notable") or [])[:_NOTABLE_MAX]:
                if not isinstance(entry, dict):
                    continue
                topic = _candidate_at(section, entry.get("index"))
                if topic is None:
                    continue
                notable.append(
                    Notable(
                        index=int(entry["index"]),
                        title=topic.title,
                        url=topic.source_url,
                        author=topic_author(topic),
                        why=str(entry.get("why") or "").strip(),
                    )
                )
            if notable:
                if apply:
                    section.notable = notable
            else:
                gaps.append(f"{channel}: no notable picks")

            drafts = [
                str(x).strip()
                for x in (data.get("drafts") or [])
                if isinstance(x, str) and str(x).strip()
            ][:_DRAFT_MAX]
            if drafts:
                if apply:
                    section.drafts = drafts
            else:
                gaps.append(f"{channel}: no drafts")

        engagements: list[Engagement] = []
        allowed = _ACTIONS.get(channel, ("comment",))
        for entry in (data.get("engagements") or []):
            if not isinstance(entry, dict):
                continue
            topic = _candidate_at(section, entry.get("index"))
            if topic is None:
                continue
            action = str(entry.get("action") or "comment").strip().lower()
            if action not in allowed:
                continue
            comment = str(entry.get("comment") or "").strip()
            if not comment:
                continue
            engagements.append(
                Engagement(
                    index=int(entry["index"]),
                    title=topic.title,
                    url=topic.source_url,
                    author=topic_author(topic),
                    action=action,
                    comment=comment,
                )
            )
            if len(engagements) >= _ENGAGEMENT_MAX:
                break
        if engagements:
            if apply:
                section.engagements = engagements
        else:
            gaps.append(f"{channel}: no engagement suggestions")
    return gaps


def _claude_gaps(sections: list[Section], parsed: dict) -> list[str]:
    """Read-only validation: which demanded fields did this response miss?"""
    return _eval_claude(sections, parsed, apply=False)


def _fill_from_claude(sections: list[Section], parsed: dict) -> list[str]:
    """Apply parsed claude JSON to the sections; return human-readable gaps."""
    return _eval_claude(sections, parsed, apply=True)


def _fill_template(sections: list[Section]) -> None:
    """Deterministic fallback content for every piece claude did not provide."""
    for section in sections:
        if section.channel == "web" or not section.candidates:
            continue  # web post lists are shaped deterministically already
        if not section.summary.strip():
            section.summary = _template_summary(section)
        if section.channel in ("x", "linkedin") and not section.notable:
            section.notable = [
                Notable(
                    index=i,
                    title=t.title,
                    url=t.source_url,
                    author=topic_author(t),
                    why=t.why_hot,
                )
                for i, t in enumerate(section.candidates[:3], 1)
            ]
        if section.channel in ("x", "linkedin") and not section.drafts:
            section.drafts = _template_drafts(section)
        if not section.engagements:
            top = section.candidates[0]
            section.engagements = [
                Engagement(
                    index=1,
                    title=top.title,
                    url=top.source_url,
                    author=topic_author(top),
                    action="comment",
                    comment=_template_comment(top),
                )
            ]


def draft_digest(sections: list[Section], profile: dict, settings: Settings, log=print) -> dict:
    """Fill channel sections with summaries, drafts, and engagement suggestions.

    Returns {"source": "claude" | "claude-partial" | "template-fallback",
    "claude_error": str | None}. Sections are filled in place. claude -p is the
    voice; an incomplete or unparsable response is retried once before any
    patching, so a clean run costs exactly one claude call and a hard failure
    (binary missing, non-zero exit, timeout) is never retried. Any remaining
    failure or gap is patched with clearly-marked template content so the run
    degrades instead of dying; patches WARN with the real reason, and
    draft_source distinguishes a partial patch from a full fallback.
    """
    result: dict = {"source": "claude", "claude_error": None}
    parsed = None
    claude_ran = False
    retry_note = ""
    if any(s.candidates for s in sections) and not settings.disable_claude:
        prompt = None
        try:
            prompt = _build_prompt(sections, profile)
        except Exception as exc:  # noqa: BLE001 - a profile edit that breaks the
            # prompt must degrade the run, never kill it: the digest still ships
            # with DRAFTING DEGRADED and the real reason in the email footer.
            result["claude_error"] = f"prompt build failed from profile: {exc}"
            log(f"WARN: claude -p unavailable: {result['claude_error']}")
        if prompt is not None:
            out, err = _run_claude(prompt, settings)
            if err:
                result["claude_error"] = err
                log(f"WARN: claude -p unavailable: {err}")
            else:
                claude_ran = True
                parsed = _extract_json(out or "")
                if parsed is None or _claude_gaps(sections, parsed):
                    # Incomplete or unparsable: retry claude once before any
                    # patching, then keep the attempt with fewer gaps (a tie
                    # keeps the first attempt; unparsable counts as worst).
                    retry_out, retry_err = _run_claude(prompt, settings)
                    retry_parsed = None if retry_err else _extract_json(retry_out or "")
                    first_gaps = _claude_gaps(sections, parsed) if parsed is not None else None
                    retry_gaps = (
                        _claude_gaps(sections, retry_parsed) if retry_parsed is not None else None
                    )
                    if retry_gaps is not None and (
                        first_gaps is None or len(retry_gaps) < len(first_gaps)
                    ):
                        parsed = retry_parsed
                        first_gaps = retry_gaps
                    if retry_err:
                        retry_note = f" (claude retry failed: {retry_err})"
                    elif retry_parsed is None:
                        retry_note = " (claude retry was also not parsable)"
                    elif first_gaps == []:
                        log("claude retry succeeded after an incomplete first response")
                    else:
                        retry_note = " (after 1 retry)"

    gaps: list[str] = []
    if parsed is None:
        _fill_template(sections)
        if claude_ran and not result["claude_error"]:
            # Hard claude failures already WARNed above; unparsable output did not.
            result["claude_error"] = f"claude output was not parsable JSON{retry_note}"
            log(f"WARN: claude -p unusable, template drafts applied: {result['claude_error']}")
    else:
        gaps = _fill_from_claude(sections, parsed)
        if gaps:
            _fill_template(sections)
            result["claude_error"] = (
                "incomplete claude response, template patches applied: "
                + "; ".join(gaps)
                + retry_note
            )
            result["source"] = "claude-partial"
            log(f"WARN: {result['claude_error']}")
    if parsed is None:
        result["source"] = "template-fallback"
    return result
