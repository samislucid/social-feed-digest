"""Rendering: markdown artifact, standalone HTML page, and MIME email message.

Channel-first layout, per Sam's 2026-09-07 review: X and LinkedIn sections are
action-first (activity summary, posts getting attention, ready-to-post drafts,
recommended comments/reposts/reshares); Reddit is capped at its best few posts;
web/news is compact context. Every draft and comment is a suggestion Sam posts
manually - the worker never posts anywhere.

2026-09-08 visual upgrade: the email HTML and the token page render separately
from one shared visual system (one indigo accent family, consistent type
scale, a subtle per-channel color hint). Post preview cards carry link,
author/handle, engagement when held, one-line why-it-matters, and a thumbnail
when the pipeline holds or cheaply fetched one (digest/images.py). The email is
table-based with inline styles: Gmail-safe, mobile-friendly single column,
no JavaScript, and complete-looking cards even with images blocked.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
from html import escape

from .shape import Engagement, Notable, Section

CHANNEL_NAMES = {"x": "X", "reddit": "Reddit", "linkedin": "LinkedIn", "web": "Web", "news": "News"}
# Subject tag when drafts fell back, so a degraded run is visible in the inbox
# before it is opened. The real failure reason lives in the run footer.
DEGRADED_SUBJECT_TAG = "[DEGRADED: template drafts]"
PARTIAL_SUBJECT_TAG = "[DEGRADED: partial drafts]"
# Draft sources that read as degraded on every surface (subject, banner, footer).
# claude-partial runs kept most claude drafting but template-patched the gaps.
DEGRADED_SOURCES = ("template-fallback", "claude-partial")
TEMPLATE_BANNER = (
    "TEMPLATE DRAFT (claude CLI unavailable in this environment): placeholder content below"
    " until the claude CLI is installed and authenticated (see RUNBOOK.md, section 5)."
)
MANUAL_POSTING_NOTE = "All drafts and comments are suggestions to post manually; the worker never posts."

# --- shared visual system ---------------------------------------------------
# One accent family (indigo) for actions and structure; channels only get a
# subtle hint (left bar, tile tint, legend dot), never competing palettes.
ACCENT = "#4f46e5"
ACCENT_INK = "#3730a3"
ACCENT_SOFT = "#eef0fe"
INK = "#16181d"
BODY_INK = "#3d4350"
MUTED = "#5f6470"
FAINT = "#8a8f9a"
LINE = "#e4e6ee"
BG = "#f2f3f7"
AMBER_INK = "#9a6700"
AMBER_BG = "#fff8e6"
AMBER_LINE = "#f0db9f"
CHANNEL_HINT = {"x": "#1d9bf0", "linkedin": "#0a66c2", "reddit": "#ff4500", "web": "#64748b", "news": "#64748b"}
CHANNEL_TINT = {"x": "#eaf5fe", "linkedin": "#e9f1f9", "reddit": "#fdeee7", "web": "#edf0f4", "news": "#edf0f4"}
_FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


@dataclass
class Digest:
    """Everything one run produced, ready to render."""

    run_tag: str
    generated_at: datetime
    profile_name: str
    sections: list[Section]
    draft_source: str
    collection: dict[str, int]
    cost: dict
    email_to: str
    subject_prefix: str = "[Feed Digest]"
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    claude_error: str = ""

    @property
    def subject(self) -> str:
        posts = sum(len(s.notable) for s in self.sections)
        drafts = sum(len(s.drafts) for s in self.sections)
        s = f"{self.subject_prefix} {self.run_tag} - {posts} posts, {drafts} drafts"
        if self.draft_source == "template-fallback":
            s += f" {DEGRADED_SUBJECT_TAG}"
        elif self.draft_source == "claude-partial":
            s += f" {PARTIAL_SUBJECT_TAG}"
        return s


def _degraded_banner_text(digest: Digest) -> str:
    """Top banner for degraded runs: names this run's real failure reason."""
    if digest.claude_error:
        return f"{TEMPLATE_BANNER} This run's drafting failed: {digest.claude_error}."
    return TEMPLATE_BANNER


def channel_label(channel: str) -> str:
    return CHANNEL_NAMES.get(channel, channel)


def _section_title(section: Section) -> str:
    if section.channel == "web":
        return "Also spotted (web and news)"
    if section.channel == "reddit" and section.intro:
        return f"Reddit ({section.intro})"
    return channel_label(section.channel)


def _who(author: str) -> str:
    return f" - {author}" if author.strip() else ""


def _engagement_line(entry: Engagement) -> str:
    target = f'"{entry.title}" ({entry.url})'
    if entry.action == "retweet":
        return f"- Repost {target}: {entry.comment}"
    if entry.action == "reshare":
        return f"- Reshare {target}: {entry.comment}"
    return f"- Comment on {target}: {entry.comment}"


def _render_section_md(lines: list[str], section: Section) -> None:
    lines.append("")
    lines.append(f"## {_section_title(section)}")
    if not section.candidates:
        lines.append("")
        lines.append(
            section.empty_note
            or f"Nothing collected for this channel this run."
        )
        return

    if section.channel in ("x", "linkedin"):
        lines.append("")
        lines.append(f"### What happened on {channel_label(section.channel)}")
        lines.append("")
        lines.append(section.summary or _section_title(section))
        if section.notable:
            lines.append("")
            lines.append("### Posts getting attention")
            for n in section.notable:
                lines.append("")
                lines.append(f"{n.index}. {n.title}{_who(n.author)}")
                lines.append(f"   Post: {n.url}")
                if n.why:
                    lines.append(f"   Why it matters: {n.why}")
        if section.drafts:
            lines.append("")
            lines.append("### Drafts for your account (post manually)")
            for i, draft in enumerate(section.drafts, 1):
                lines.append("")
                lines.append(f"{i}. {draft}")
        if section.engagements:
            verb = "reposts" if section.channel == "x" else "reshares"
            lines.append("")
            lines.append(f"### Comments and {verb} worth making")
            for entry in section.engagements:
                lines.append(_engagement_line(entry))
        return

    # Reddit and web/news: a capped, ranked post list with suggested comments.
    if section.summary:
        lines.append("")
        lines.append(section.summary)
    for n in section.notable:
        lines.append("")
        lines.append(f"{n.index}. {n.title}{_who(n.author)}")
        lines.append(f"   Post: {n.url}")
        if n.why:
            lines.append(f"   Why hot: {n.why}")
        if section.channel == "reddit":
            match = next((e for e in section.engagements if e.index == n.index), None)
            if match:
                lines.append(f"   Suggested comment: {match.comment}")


def render_markdown(digest: Digest) -> str:
    lines: list[str] = []
    gen = digest.generated_at.strftime("%Y-%m-%d %H:%M %Z").strip()
    lines.append(f"# Social Feed Digest - {digest.run_tag}")
    lines.append("")
    lines.append(
        f"_{gen} · profile: {digest.profile_name} · drafting: {digest.draft_source} · "
        f"est. external cost: ${digest.cost.get('total_usd', 0.0):.4f}_"
    )
    lines.append("")
    lines.append(f"_{MANUAL_POSTING_NOTE}_")
    if digest.draft_source in DEGRADED_SOURCES:
        lines.append("")
        lines.append(f"> {_degraded_banner_text(digest)}")
    for section in digest.sections:
        _render_section_md(lines, section)
    lines.append("")
    lines.append("## Run footer")
    searches = digest.cost.get("search_tool_calls", 0)
    lines.append(
        "- Collection: " + ", ".join(f"{channel_label(k)}: {v}" for k, v in sorted(digest.collection.items()))
    )
    lines.append(f"- xAI Live Search tool calls: {searches} · est. cost: ${digest.cost.get('total_usd', 0.0):.4f} "
                 f"(budget ${digest.cost.get('budget_usd', 0.25):.2f})")
    lines.append(f"- Run duration: {digest.duration_s:.1f}s")
    if digest.claude_error:
        lines.append(f"- DRAFTING DEGRADED: {digest.claude_error}")
    for warning in digest.warnings:
        lines.append(f"- WARNING: {warning}")
    lines.append("")
    return "\n".join(lines)


def _comment_html(text: str) -> str:
    if text.startswith("[TEMPLATE DRAFT"):
        return f'<span class="template">{escape("[TEMPLATE DRAFT]")}</span>' + escape(text.split("]", 1)[-1])
    return escape(text)


def _template_marked(text: str) -> bool:
    return text.startswith("[TEMPLATE DRAFT")


def _totals(digest: Digest) -> tuple[int, int]:
    return (
        sum(len(s.notable) for s in digest.sections),
        sum(len(s.drafts) for s in digest.sections),
    )


def _per_channel(digest: Digest) -> list[tuple[str, int, int]]:
    return [(s.channel, len(s.notable), len(s.drafts)) for s in digest.sections]


def _initials(author: str) -> str:
    """Short monogram for the no-image fallback tile ('@handle', 'r/sub', ...)."""
    words = [w for w in re.split(r"[/@\s]+", (author or "").strip()) if w]
    if len(words) >= 2:
        letters = "".join(w[0] for w in words[:2]).upper()
    else:
        letters = (words[0][:2].upper() if words else "·")
    return letters or "·"


def _section_counts_label(section: Section) -> str:
    n, d = len(section.notable), len(section.drafts)
    return f"{n} post{'s' if n != 1 else ''} · {d} draft{'s' if d != 1 else ''}"


# --- page (token-protected static page; stylesheet-driven, tiny footprint) ---

_PAGE_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f2f3f7;color:#3d4350;font:15px/1.55 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:28px 20px 48px}
.masthead{background:#fff;border:1px solid #e4e6ee;border-radius:14px;padding:22px 24px 18px;margin-bottom:16px}
.eyebrow{color:#4f46e5;font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
h1{margin:6px 0 2px;font-size:22px;font-weight:800;color:#16181d;letter-spacing:-.01em}
.tagline{color:#5f6470;font-size:13px;margin:4px 0 0}
.banner{margin-top:12px;background:#fff8e6;border:1px solid #f0db9f;color:#9a6700;border-radius:10px;padding:10px 12px;font-size:13px;font-weight:600}
.stats{display:flex;gap:22px;margin-top:14px}
.big{font-size:24px;font-weight:800;color:#16181d;line-height:1}
.big small{font-size:12.5px;font-weight:600;color:#5f6470;margin-left:4px}
.statbar{display:flex;height:8px;border-radius:999px;overflow:hidden;background:#e4e6ee;margin-top:12px}
.statbar span{display:block;height:100%}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:9px;font-size:12.5px;color:#5f6470}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:1px}
.note{color:#5f6470;font-size:13px;margin:12px 0 0}
.card{background:#fff;border:1px solid #e4e6ee;border-left-width:4px;border-radius:14px;padding:18px 22px 16px;margin-bottom:16px}
.card.x{border-left-color:#1d9bf0}.card.linkedin{border-left-color:#0a66c2}.card.reddit{border-left-color:#ff4500}.card.web{border-left-color:#64748b}.card.news{border-left-color:#64748b}
.chead{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
h2{margin:0;font-size:17px;font-weight:800;color:#16181d;letter-spacing:-.01em}
.ccount{font-size:12px;font-weight:600;color:#5f6470;background:#f2f3f7;border-radius:999px;padding:2px 10px;white-space:nowrap}
.csummary{margin:8px 0 2px;font-size:14.5px}
.subhead{margin:16px 0 2px;font-size:13px;font-weight:700;color:#5f6470}
.post{display:flex;gap:14px;padding:14px 2px;border-top:1px solid #e4e6ee}
.subhead+.post,.posts+.post{border-top-color:#e4e6ee}
.thumb{flex:0 0 72px;width:72px;height:72px;border-radius:10px;overflow:hidden;flex-shrink:0}
.tile{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:22px}
.card.x .tile{background:#eaf5fe;color:#1d9bf0}.card.linkedin .tile{background:#e9f1f9;color:#0a66c2}.card.reddit .tile{background:#fdeee7;color:#ff4500}.card.web .tile,.card.news .tile{background:#edf0f4;color:#64748b}
.thumb img{width:72px;height:72px;object-fit:cover;display:block}
.ptitle{font-weight:700;font-size:16px;line-height:1.4;color:#16181d;text-decoration:none}
.ptitle:hover{color:#4f46e5}
.pmeta{margin-top:4px;font-size:13px;color:#5f6470}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;background:#eef0fe;color:#3730a3;font-size:11.5px;font-weight:700;margin-left:6px}
.why{margin-top:7px;font-size:14px;color:#3d4350}
.popen{margin-top:7px}
.popen a{color:#4f46e5;font-weight:600;font-size:13px;text-decoration:none}
.popen a:hover{text-decoration:underline}
.comment{background:#f6f7f9;border-left:3px solid #dfe3ec;border-radius:0 8px 8px 0;padding:8px 11px;margin-top:8px;font-size:13.5px;color:#3d4350}
.draft{background:#eef0fe;border-left:3px solid #4f46e5;border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:14.5px;color:#16181d}
.dnum{color:#3730a3;font-weight:800;margin-right:4px}
.template{color:#9a6700;font-weight:700}
.act{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.04em;margin-right:8px}
.act.comment{background:#eef0fe;color:#3730a3}.act.retweet{background:#eaf5fe;color:#0a66c2}.act.reshare{background:#e8f0f8;color:#0a66c2}.act.comment.reddit{background:#fdeee7;color:#c2410c}
.eng{padding:10px 2px;border-top:1px solid #e4e6ee;font-size:14px}
.eng .etitle{font-weight:600;color:#16181d;text-decoration:none}
.eng .etitle:hover{color:#4f46e5}
.ewho{color:#5f6470;font-size:13px}
footer.run{background:#fff;border:1px solid #e4e6ee;border-radius:14px;padding:14px 22px;color:#5f6470;font-size:12.5px;line-height:1.7}
footer.run .warn{color:#9a6700;font-weight:700}
footer.run b{color:#3d4350}
@media(max-width:600px){.wrap{padding:16px 10px 32px}.card,.masthead,footer.run{padding:14px}.post{gap:10px}.thumb{flex:0 0 56px;width:56px;height:56px}.thumb img{width:56px;height:56px}.stats{gap:16px}}
"""


def render_html(digest: Digest) -> str:
    """The token-protected page: same content and visual system as the email,
    rendered as a responsive stylesheet-driven page (no JavaScript needed)."""
    e = escape
    posts, drafts = _totals(digest)
    per_channel = _per_channel(digest)
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light">',
        f"<title>{e(digest.subject)}</title>",
        f"<style>{_PAGE_CSS}</style></head><body>",
        '<div class="wrap">',
        '<div class="masthead">',
        f'<div class="eyebrow">Social Feed Digest</div>',
        f"<h1>{e(digest.run_tag)}</h1>",
        f'<p class="tagline">{e(digest.generated_at.strftime("%Y-%m-%d %H:%M %Z"))} · profile: {e(digest.profile_name)} · '
        f"drafting: {e(digest.draft_source)} · est. external cost: ${digest.cost.get('total_usd', 0.0):.4f}</p>",
        f'<p class="note">{e(MANUAL_POSTING_NOTE)}</p>',
    ]
    if digest.draft_source in DEGRADED_SOURCES:
        parts.append(f'<div class="banner">{e(_degraded_banner_text(digest))}</div>')
    parts.append(
        f'<div class="stats"><div class="big">{posts}<small>post{"s" if posts != 1 else ""}</small></div>'
        f'<div class="big">{drafts}<small>draft{"s" if drafts != 1 else ""}</small></div></div>'
    )
    if posts:
        segments = "".join(
            f'<span style="width:{round(n / posts * 100)}%;background:{CHANNEL_HINT.get(c, ACCENT)}"></span>'
            for c, n, _d in per_channel if n
        )
        legend = "".join(
            f'<span><span class="dot" style="background:{CHANNEL_HINT.get(c, ACCENT)}"></span>'
            f"{e(channel_label(c))} {n} · {d} drafts</span>"
            for c, n, d in per_channel
        )
        parts.append(f'<div class="statbar">{segments}</div><div class="legend">{legend}</div>')
    parts.append("</div>")  # /masthead

    for section in digest.sections:
        hint = CHANNEL_HINT.get(section.channel, ACCENT)
        parts.append(f'<div class="card {e(section.channel)}" style="border-left-color:{hint}">')
        parts.append(
            f'<div class="chead"><h2>{e(_section_title(section))}</h2>'
            f'<span class="ccount">{e(_section_counts_label(section))}</span></div>'
        )
        if not section.candidates:
            parts.append(f'<p class="csummary">{e(section.empty_note or "Nothing collected for this channel this run.")}</p>')
            parts.append("</div>")
            continue
        if section.channel in ("x", "linkedin"):
            parts.append(f'<p class="csummary">What happened on {e(channel_label(section.channel))}: {e(section.summary or _section_title(section))}</p>')
            if section.notable:
                parts.append('<div class="subhead">Posts getting attention</div>')
                for n in section.notable:
                    parts.append(_page_post_card(section, n))
            if section.drafts:
                parts.append('<div class="subhead">Drafts for your account (post manually)</div>')
                for i, draft in enumerate(section.drafts, 1):
                    parts.append(f'<div class="draft"><span class="dnum">{i}.</span>{_comment_html(draft)}</div>')
            if section.engagements:
                verb = "reposts" if section.channel == "x" else "reshares"
                parts.append(f'<div class="subhead">Comments and {e(verb)} worth making</div>')
                for entry in section.engagements:
                    parts.append(
                        f'<div class="eng"><span class="act {e(entry.action)}">{e(entry.action.upper())}</span>'
                        f'<a class="etitle" href="{e(entry.url)}">{e(entry.title)}</a>'
                        f'<span class="ewho"> · {e(entry.author)}</span>'
                        f'<div class="comment">{_comment_html(entry.comment)}</div></div>'
                    )
            parts.append("</div>")
            continue

        if section.summary:
            parts.append(f'<p class="csummary">{e(section.summary)}</p>')
        for n in section.notable:
            parts.append(_page_post_card(section, n))
        parts.append("</div>")

    parts.append('<footer class="run">')
    parts.append(
        f"<b>Collection:</b> {e(', '.join(f'{channel_label(k)}: {v}' for k, v in sorted(digest.collection.items())))} · "
        f"<b>xAI tool calls:</b> {digest.cost.get('search_tool_calls', 0)} · "
        f"<b>est. cost:</b> ${digest.cost.get('total_usd', 0.0):.4f} "
        f"(budget ${digest.cost.get('budget_usd', 0.25):.2f}) · "
        f"duration {digest.duration_s:.0f}s"
    )
    if digest.claude_error:
        parts.append(f'<br><span class="warn">DRAFTING DEGRADED:</span> {e(digest.claude_error)}')
    for warning in digest.warnings:
        parts.append(f"<br>WARNING: {e(warning)}")
    parts.append("</footer></div></body></html>")
    return "\n".join(parts)


def _page_post_card(section: Section, n: Notable) -> str:
    e = escape
    who = f'<span class="ewho">{e(n.author)}</span>' if n.author.strip() else ""
    chip = f'<span class="chip">↑ {n.engagement}</span>' if n.engagement > 0 else ""
    why = f'<div class="why">{e("Why it matters: " + n.why)}</div>' if (n.why and section.channel in ("x", "linkedin")) else ""
    why_hot = f'<div class="why">{e("Why hot: " + n.why)}</div>' if (n.why and section.channel not in ("x", "linkedin")) else ""
    label = "Open thread" if section.channel == "reddit" else "Open post"
    open_link = f'<div class="popen"><a href="{e(n.url)}">{label} ↗</a></div>'
    comment = ""
    if section.channel == "reddit":
        match = next((en for en in section.engagements if en.index == n.index), None)
        if match:
            comment = f'<div class="comment">{_comment_html("Suggested comment: " + match.comment)}</div>'
    thumb = (
        f'<img src="{e(n.image)}" alt="" loading="lazy">'
        if n.image
        else f'<div class="tile">{e(_initials(n.author))}</div>'
    )
    return (
        f'<div class="post"><div class="thumb">{thumb}</div>'
        f'<div><a class="ptitle" href="{e(n.url)}">{e(n.title)}</a>'
        f'<div class="pmeta">{who}{chip}</div>'
        f"{why}{why_hot}{open_link}{comment}</div></div>"
    )


# --- email (Sam's mock redesign, 2026-09-08: tables + inline styles, no JS) ---
#
# Structure and visual language from the hand-drawn mock
# (app://files/file_oTCbyaOw2gPUVhMS): a 600px white card on a light gray
# page; a PLATFORM / POSTS / TO ACT ON summary table (to-act-on counts struck
# through - every suggestion is posted manually); a disclosure line; per-post
# cards with a narrow left rail (platform, niche, time, action tags) and the
# post on the right; a light-blue suggested-comment box with Copy / Open
# buttons; a "Drafts for your account" block; a compact read-only list; and a
# small stats footer.
#
# Gmail-safe: tables + inline styles, no JavaScript, no remote images (the
# mock is text-only), under 102KB. Copy buttons cannot run JS, so they open a
# pre-addressed mailto: compose window carrying the text in the body - an
# honest, scriptless "copy". Open post / Open thread anchor the real URLs.

from urllib.parse import quote as _q  # mailto bodies

M_ACCENT = "#0080b0"      # steel blue: buttons, labels, links (from the mock)
M_ACCENT_DARK = "#00618a"
M_TINT = "#eaf5fb"        # suggested-comment box fill
M_TINT_LINE = "#cfe6f2"
M_TAG_RED = "#c62828"     # action tags, POST MANUALLY, struck to-act-on counts
M_BG = "#f0f0f2"         # page background
M_CARD_LINE = "#e4e6ea"   # card border / separators
M_INK = "#1a1d23"
M_BODY_INK = "#3a4149"
M_MUTED = "#6b7079"
M_FAINT = "#989da6"
M_BTN_LINE = "#c9ced6"
M_AMBER_BG = "#fff8e6"
M_AMBER_LINE = "#f0db9f"
M_AMBER_INK = "#8a6100"

_DRAFT_SOURCE_LABEL = {
    "claude": "claude",
    "claude-partial": "claude (partial)",
    "template-fallback": "template",
}

_EMAIL_STYLE_MOCK = (
    "@media only screen and (max-width:620px){"
    ".em-card{width:100%!important;border-left:0!important;border-right:0!important}"
    ".em-pad{padding:18px 14px!important}"
    ".em-rail{display:block!important;width:auto!important;padding:0 0 8px!important}"
    "}"
    f"a{{color:{M_ACCENT}}}"
)


def _em_open(digest: Digest) -> list[str]:
    e = escape
    posts, drafts = _totals(digest)
    preheader = (
        f"{posts} posts, {drafts} drafts ready to review. "
        f"{MANUAL_POSTING_NOTE}"
    )
    return [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(digest.subject)}</title>",
        f"<style>{_EMAIL_STYLE_MOCK}</style></head>",
        f'<body style="margin:0;padding:0;background:{M_BG};">',
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{e(preheader)}</div>',
        f'<div style="background:{M_BG};font-family:{_FONT};">',
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f0f0f2">'
        '<tr><td align="center" style="padding:20px 8px;">',
    ]


def _em_close() -> str:
    return "</td></tr></table></div></body></html>"


def _em_hr() -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="height:1px;background:{M_CARD_LINE};font-size:0;line-height:0;">&nbsp;</td></tr></table>'
    )


def _em_spacer(h: int) -> str:
    return f'<div style="height:{h}px;line-height:{h}px;font-size:{h}px;">&nbsp;</div>'


def _mailto_link(text: str, label: str, digest: Digest, subject: str) -> str:
    """Scriptless copy button: opens a compose window pre-filled with the text."""
    e = escape
    href = f"mailto:{e(digest.email_to)}?subject={_q(subject)}&body={_q(text)}"
    return (
        f'<a href="{e(href)}" style="display:inline-block;background:{M_ACCENT};'
        f"color:#ffffff;font-family:{_FONT};font-size:12px;font-weight:700;"
        f'padding:7px 14px;border-radius:4px;text-decoration:none;">'
        f"{e(label)}</a>"
    )


def _em_outline_link(url: str, label: str) -> str:
    e = escape
    return (
        f'<a href="{e(url)}" style="display:inline-block;background:#ffffff;'
        f"color:{M_INK};font-family:{_FONT};font-size:12px;font-weight:700;"
        f"padding:6px 13px;border-radius:4px;border:1px solid {M_BTN_LINE};"
        f'text-decoration:none;">{e(label)}</a>'
    )


def _to_act_on(section: Section) -> int:
    return len(section.engagements) + len(section.drafts)


def _reddit_of(digest: Digest) -> tuple[int, str]:
    """("5 of 11", plain count label) from the honest reddit intro line."""
    section = next((s for s in digest.sections if s.channel == "reddit"), None)
    if not section or not section.intro:
        return 0, ""
    import re as _re

    m = _re.search(r"best (\d+) of (\d+)", section.intro)
    if m:
        return int(m.group(2)), f"{m.group(1)} of {m.group(2)}"
    return 0, ""


def _em_summary_table(digest: Digest) -> list[str]:
    """PLATFORM / POSTS / TO ACT ON, to-act-on struck through (manual posting)."""
    rows: list[str] = []
    head = (
        f'<td style="padding:7px 10px;font-family:{_FONT};font-size:10.5px;font-weight:700;'
        f'letter-spacing:.08em;color:{M_FAINT};text-transform:uppercase;"'
    )
    rows.append(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr>{head}align="left">PLATFORM</td>{head}align="right">POSTS</td>{head}align="right">TO ACT ON</td></tr>'
    )
    reddit_total, reddit_of = _reddit_of(digest)
    for section in digest.sections:
        posts_label = (
            f"{reddit_of}" if section.channel == "reddit" and reddit_of else f"{len(section.notable)}"
        )
        act = _to_act_on(section)
        act_html = (
            f'<span style="text-decoration:line-through;color:{M_TAG_RED};">{act}</span>'
            if act
            else f'<span style="color:{M_FAINT};">0</span>'
        )
        cell = f'padding:8px 10px;font-family:{_FONT};font-size:13px;border-top:1px solid {M_CARD_LINE};'
        rows.append(
            "<tr>"
            f'<td style="{cell}color:{M_INK};font-weight:700;">{escape(channel_label(section.channel))}</td>'
            f'<td style="{cell}color:{M_BODY_INK};text-align:right;">{escape(posts_label)}</td>'
            f'<td style="{cell}text-align:right;">{act_html}</td>'
            "</tr>"
        )
    rows.append("</table>")
    return rows


def _em_header(digest: Digest) -> list[str]:
    e = escape
    disclosure = (
        f"profile: {digest.profile_name} · drafting: "
        f"{_DRAFT_SOURCE_LABEL.get(digest.draft_source, digest.draft_source)} · "
        "suggestions only, the worker never posts"
    )
    return [
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="em-card" '
        f'style="width:600px;max-width:600px;background:#ffffff;border:1px solid {M_CARD_LINE};border-radius:10px;">'
        '<tr><td class="em-pad" style="padding:22px 22px 16px;">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-family:{_FONT};font-size:22px;font-weight:800;color:{M_INK};">Feed digest</td>'
        f'<td align="right" style="font-family:{_FONT};font-size:12.5px;color:{M_MUTED};">{e(digest.run_tag)}</td>'
        "</tr></table>",
        _em_spacer(14),
        *_em_summary_table(digest),
        _em_spacer(10),
        f'<div style="font-family:{_FONT};font-size:11.5px;color:{M_MUTED};">{e(disclosure)}</div>',
        "</td></tr></table>",
    ]


def _em_tag(label: str, color: str) -> str:
    return (
        f'<div style="font-family:{_FONT};font-size:10px;font-weight:800;letter-spacing:.07em;'
        f'color:{color};padding:2px 0;">{escape(label)}</div>'
    )


def _em_pt(posted_at) -> str:
    if not posted_at:
        return ""
    try:
        from zoneinfo import ZoneInfo

        local = posted_at.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # pragma: no cover - minimal environments
        local = posted_at
    return local.strftime("%I:%M %p").lstrip("0")


def _em_action_tags(section: Section, n: Notable, engagement) -> list[str]:
    tags: list[str] = []
    if engagement is None:
        return tags
    action = (engagement.action or "").lower()
    if section.channel == "reddit":
        tags.append(_em_tag("COMMENT", M_TAG_RED))
    elif action in ("comment", "retweet", "reshare"):
        tags.append(_em_tag("COMMENT", M_TAG_RED))
        if section.channel == "x":
            tags.append(_em_tag("+ REPOST", M_MUTED))
        elif section.channel == "linkedin":
            tags.append(_em_tag("+ RESHARE", M_MUTED))
    return tags


def _em_rail(section: Section, n: Notable, engagement) -> str:
    e = escape
    lines = [
        f'<td class="em-rail" width="92" valign="top" style="width:92px;padding:0 12px 0 0;'
        f'font-family:{_FONT};vertical-align:top;">',
        f'<div style="font-size:11.5px;font-weight:800;color:{M_INK};letter-spacing:.04em;">'
        f"{e(channel_label(section.channel).upper())}</div>",
    ]
    if n.niche:
        lines.append(f'<div style="font-size:11px;color:{M_MUTED};padding-top:2px;">{e(n.niche)}</div>')
    if n.source_label:
        lines.append(f'<div style="font-size:11px;color:{M_MUTED};">{e(n.source_label)}</div>')
    if n.author.startswith("r/"):
        lines.append(f'<div style="font-size:11px;color:{M_MUTED};">{e(n.author)}</div>')
    stamp = _em_pt(n.posted_at)
    if stamp:
        lines.append(f'<div style="font-size:11.5px;color:{M_FAINT};padding-top:2px;">{e(stamp)}</div>')
    if n.engagement > 0:
        lines.append(f'<div style="font-size:11.5px;color:{M_FAINT};">↑ {n.engagement}</div>')
    lines.extend(_em_action_tags(section, n, engagement))
    lines.append("</td>")
    return "".join(lines)


def _em_comment_box(text: str, digest: Digest, url: str, open_label: str) -> str:
    e = escape
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:9px;background:{M_TINT};border:1px solid {M_TINT_LINE};border-radius:6px;">'
        f'<tr><td style="padding:10px 12px;font-family:{_FONT};">'
        f'<div style="font-size:10.5px;font-weight:800;letter-spacing:.07em;color:{M_ACCENT_DARK};'
        f'text-decoration:underline;">SUGGESTED COMMENT</div>'
        f'<div style="font-size:13px;line-height:1.5;color:{M_BODY_INK};padding-top:4px;">{e(text)}</div>'
        "</td></tr></table>"
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:9px;"><tr>'
        f"<td>{_mailto_link(text, 'Copy comment', digest, 'Digest comment draft')}</td>"
        f'<td style="width:8px;"></td>'
        f"<td>{_em_outline_link(url, open_label)}</td>"
        "</tr></table>"
    )


def _em_post_card(section: Section, n: Notable, digest: Digest, with_comment: bool) -> str:
    e = escape
    engagement = next((g for g in section.engagements if g.index == n.index), None) if with_comment else None
    open_label = "Open thread" if section.channel == "reddit" else "Open post"
    if not with_comment:
        engagement = next((g for g in section.engagements if g.index == n.index), None)
    author_line = (
        f'<div style="font-size:12.5px;color:{M_MUTED};padding-top:2px;">{e(n.author)}</div>'
        if n.author and not n.author.startswith("r/")
        else ""
    )
    why_line = f'<div style="font-size:13px;line-height:1.5;color:{M_BODY_INK};padding-top:6px;">{e(n.why)}</div>' if n.why else ""
    comment_html = ""
    if engagement and engagement.comment:
        comment_html = _em_comment_box(engagement.comment, digest, n.url, open_label)
    else:
        comment_html = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:9px;">'
            f"<tr><td>{_em_outline_link(n.url, open_label)}</td></tr></table>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"<tr>{_em_rail(section, n, engagement)}"
        f'<td valign="top" style="font-family:{_FONT};vertical-align:top;">'
        f'<a href="{e(n.url)}" style="font-size:15px;font-weight:700;color:{M_INK};'
        f'text-decoration:none;line-height:1.35;">{e(n.title)}</a>'
        f"{author_line}{why_line}{comment_html}"
        "</td></tr></table>"
    )


def _em_readonly_rows(section: Section, rows: list[Notable]) -> str:
    e = escape
    parts = [
        f'<div style="font-family:{_FONT};font-size:10.5px;font-weight:800;letter-spacing:.08em;'
        f'color:{M_FAINT};padding:12px 0 2px;">READ ONLY</div>',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">',
    ]
    open_label = "Open thread" if section.channel == "reddit" else "Open link"
    for n in rows:
        author = f' <span style="color:{M_MUTED};font-size:12px;">{e(n.author)}</span>' if n.author else ""
        parts.append(
            f'<tr><td style="padding:7px 0;border-top:1px solid {M_CARD_LINE};font-family:{_FONT};">'
            f'<a href="{e(n.url)}" style="font-size:13.5px;font-weight:600;color:{M_INK};text-decoration:none;">'
            f"{e(n.title)}</a> "
            f'<a href="{e(n.url)}" style="font-size:12px;font-weight:700;color:{M_ACCENT};'
            f'text-decoration:underline;">{open_label}</a>'
            f"{author}</td></tr>"
        )
    parts.append("</table>")
    return "".join(parts)


def _em_channel_block(section: Section, digest: Digest) -> list[str]:
    e = escape
    if not section.notable and not section.drafts:
        note = (
            "Nothing this run. LinkedIn arrives through the watched inbox; nothing reached it."
            if section.channel == "linkedin"
            else section.empty_note
        )
        return [
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="font-family:{_FONT};font-size:13.5px;color:{M_BODY_INK};padding:4px 0;">'
            f'<span style="font-weight:800;color:{M_INK};font-size:14px;">{e(channel_label(section.channel).upper())}</span>'
            f'<div style="padding-top:4px;">{e(note)}</div></td></tr></table>'
            + "".join(_em_channel_drafts(section, digest)),
        ]
    parts: list[str] = []
    if section.summary:
        parts.append(
            f'<div style="font-family:{_FONT};font-size:13px;line-height:1.5;color:{M_BODY_INK};'
            f'padding-bottom:6px;">{e(section.summary)}</div>'
        )
    readonly: list[Notable] = []
    for i, n in enumerate(section.notable):
        has_comment = any(g.index == n.index and g.comment for g in section.engagements)
        if section.channel == "reddit" and not has_comment:
            readonly.append(n)
            continue
        parts.append(_em_post_card(section, n, digest, with_comment=has_comment))
    if readonly:
        parts.append(_em_readonly_rows(section, readonly))
    parts.extend(_em_channel_drafts(section, digest))
    return parts


def _em_channel_drafts(section: Section, digest: Digest) -> list[str]:
    """The channel's drafts, inside the channel block right after its cards
    (mock: X's "Drafts for your account" sits between the X card and Reddit)."""
    if not section.drafts:
        return []
    e = escape
    count = len(section.drafts)
    parts = [
        _em_spacer(10),
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-family:{_FONT};font-size:13.5px;font-weight:800;color:{M_INK};">'
        f'{e(channel_label(section.channel))}'
        f' <span style="font-size:12px;font-weight:400;font-style:italic;color:{M_MUTED};">'
        f"{count} draft{'s' if count != 1 else ''}</span></td>"
        f'<td align="right" style="font-family:{_FONT};font-size:10px;font-weight:800;'
        f'letter-spacing:.07em;color:{M_TAG_RED};">POST MANUALLY</td></tr></table>'
    ]
    if section.channel == next((s.channel for s in digest.sections if s.drafts), None):
        # First channel with drafts carries the heading + the manual-post note
        # (mock: "Drafts for your account" heads the X drafts).
        parts[0:0] = [
            f'<div style="font-family:{_FONT};font-size:17px;font-weight:800;color:{M_INK};'
            f'padding-top:4px;">Drafts for your account</div>'
            f'<div style="font-family:{_FONT};font-size:11px;color:{M_FAINT};">'
            "The worker never posts; review and post each draft yourself.</div>",
        ]
    for i, draft in enumerate(section.drafts, 1):
        parts.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="font-family:{_FONT};font-size:13.5px;line-height:1.55;color:{M_BODY_INK};'
            f'padding:8px 0 6px;">{e(draft)}</td></tr><tr><td>'
            f"{_mailto_link(draft, f'Copy draft {i}', digest, f'Digest draft {i} (ready to post)')}"
            "</td></tr></table>"
        )
    return parts


def _em_footer(digest: Digest) -> list[str]:
    e = escape
    collected = " · ".join(
        f"{channel_label(k).capitalize()} {v}" for k, v in sorted(digest.collection.items())
    )
    parts = [
        _em_hr(),
        _em_spacer(10),
        f'<div style="font-family:{_FONT};font-size:11.5px;line-height:1.6;color:{M_FAINT};">'
        f"Collected {e(collected)} · xAI tool calls {digest.cost.get('search_tool_calls', 0)} · "
        f"est. cost ${digest.cost.get('total_usd', 0.0):.4f} of "
        f"${digest.cost.get('budget_usd', 0.25):.2f} budget · duration {digest.duration_s:.0f}s</div>",
        f'<div style="font-family:{_FONT};font-size:11px;line-height:1.6;color:{M_FAINT};">'
        "Copy buttons open a pre-addressed email to yourself with the text in the body "
        "(email cannot run scripts). The worker never posts; you post manually after review.</div>",
    ]
    if digest.claude_error:
        parts.append(
            f'<div style="font-family:{_FONT};font-size:11.5px;line-height:1.6;color:{M_TAG_RED};">'
            f"DRAFTING DEGRADED: {e(digest.claude_error)}</div>"
        )
    for warning in digest.warnings:
        parts.append(
            f'<div style="font-family:{_FONT};font-size:11.5px;line-height:1.6;color:{M_MUTED};">'
            f"WARNING: {e(warning)}</div>"
        )
    return parts


def _em_banner(digest: Digest) -> list[str]:
    if digest.draft_source not in DEGRADED_SOURCES:
        return []
    e = escape
    reason = (
        f" This run's drafting failed: {digest.claude_error}."
        if digest.claude_error
        else ""
    )
    return [
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="em-card" '
        f'style="width:600px;max-width:600px;background:{M_AMBER_BG};border:1px solid {M_AMBER_LINE};'
        'border-radius:10px;">'
        f'<tr><td style="padding:12px 16px;font-family:{_FONT};font-size:12.5px;line-height:1.5;'
        f'color:{M_AMBER_INK};">{e(TEMPLATE_BANNER)}{e(reason)}</td></tr></table>',
        _em_spacer(10),
    ]


def render_email_html(digest: Digest) -> str:
    parts = _em_open(digest)
    parts.extend(_em_banner(digest))
    parts.extend(_em_header(digest))
    parts.append(_em_spacer(10))
    body: list[str] = []
    first = True
    for section in digest.sections:
        if not first:
            body.append(_em_hr())
            body.append(_em_spacer(12))
        first = False
        body.extend(_em_channel_block(section, digest))
    body.extend(_em_footer(digest))
    parts.append(
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="em-card" '
        f'style="width:600px;max-width:600px;background:#ffffff;border:1px solid {M_CARD_LINE};'
        'border-radius:10px;">'
        f'<tr><td class="em-pad" style="padding:16px 22px 18px;">{"".join(body)}</td></tr></table>'
    )
    parts.append(_em_close())
    return "\n".join(parts)



def build_email(digest: Digest, markdown: str, html: str, from_addr: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = digest.subject
    msg["From"] = from_addr
    msg["To"] = digest.email_to
    msg["Date"] = formatdate(localtime=False)
    msg["X-Digest-Run"] = digest.run_tag
    msg.set_content(markdown)
    msg.add_alternative(html, subtype="html")
    return msg