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


# --- email (Gmail-first: tables + inline styles, no JS, image-block-proof) ---

_EMAIL_STYLE = (
    "@media only screen and (max-width:620px){"
    ".container{width:100%!important;border-radius:0!important}"
    ".pad{padding:16px 14px!important}"
    ".padx{padding-left:14px!important;padding-right:14px!important}"
    ".thumb{width:56px!important;height:56px!important}"
    "}"
    f"a{{color:{ACCENT}}}"
)


def _email_open(digest: Digest) -> list[str]:
    e = escape
    posts, drafts = _totals(digest)
    preheader = (
        f"{posts} posts, {drafts} drafts across X, LinkedIn, Reddit and web. "
        f"{MANUAL_POSTING_NOTE}"
    )
    return [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(digest.subject)}</title>",
        f"<style>{_EMAIL_STYLE}</style></head>",
        f'<body style="margin:0;padding:0;background:{BG};">',
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{e(preheader)}</div>',
        f'<div style="background:{BG};font-family:{_FONT};">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f2f3f7">'
        "<tr><td align=\"center\" style=\"padding:20px 8px;\">",
    ]


def _email_close() -> str:
    return "</td></tr></table></div></body></html>"


def _card_table(inner: str, radius: int = 14) -> str:
    return (
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        f'class="container" style="width:600px;max-width:600px;background:#ffffff;'
        f"border:1px solid {LINE};border-radius:{radius}px;\">"
        f"{inner}</table>"
    )


def _spacer(h: int = 14) -> str:
    return f'<div style="height:{h}px;line-height:{h}px;font-size:{h}px;">&nbsp;</div>'


def _thumb_email(n: Notable, channel: str, size: int = 72) -> str:
    e = escape
    hint = CHANNEL_HINT.get(channel, ACCENT)
    tint = CHANNEL_TINT.get(channel, ACCENT_SOFT)
    radius = 10
    cell = f'<td class="thumb" width="{size + 10}" valign="top" style="width:{size + 10}px;padding:12px 0 12px 12px;">'
    if n.image:
        inner = (
            f'<img src="{e(n.image)}" alt="" width="{size}" height="{size}" '
            f'style="display:block;width:{size}px;height:{size}px;border-radius:{radius}px;'
            f'border:1px solid {LINE};object-fit:cover;">'
        )
    else:
        mono = e(_initials(n.author))
        inner = (
            f'<div style="width:{size}px;height:{size}px;border-radius:{radius}px;background:{tint};'
            f"color:{hint};font-family:{_FONT};font-size:{round(size / 2.8)}px;font-weight:700;"
            f'text-align:center;line-height:{size}px;">{mono}</div>'
        )
    return f"{cell}{inner}</td>"


def _stat_strip_email(digest: Digest, border_top: bool = False) -> str:
    e = escape
    posts, drafts = _totals(digest)
    per_channel = _per_channel(digest)
    border = f"border-top:1px solid {LINE};" if border_top else ""
    inner = [
        f'<tr><td class="pad" style="{border}padding:16px 20px 14px;">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>',
        f'<td style="font-family:{_FONT};color:{INK};font-size:26px;font-weight:800;line-height:1;">{posts}</td>',
        f'<td style="font-family:{_FONT};color:{MUTED};font-size:13px;font-weight:600;padding-left:6px;">post{"s" if posts != 1 else ""}</td>',
        f'<td align="right" style="font-family:{_FONT};color:{INK};font-size:26px;font-weight:800;padding-right:6px;">{drafts}</td>',
        f'<td align="right" style="font-family:{_FONT};color:{MUTED};font-size:13px;font-weight:600;">draft{"s" if drafts != 1 else ""} ready</td>',
        "</tr></table>",
    ]
    bar = "".join(
        f'<td width="{round(n / posts * 100)}%" bgcolor="{CHANNEL_HINT.get(c, ACCENT)}" '
        f'style="height:8px;width:{round(n / posts * 100)}%;background:{CHANNEL_HINT.get(c, ACCENT)};"></td>'
        for c, n, _d in per_channel if n
    ) or f'<td width="100%" bgcolor="{LINE}"></td>'
    inner.append(
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">'
        f'<tr>{bar}</tr></table>'
    )
    legend = "".join(
        f'<td style="font-family:{_FONT};color:{MUTED};font-size:12px;padding-right:12px;white-space:nowrap;">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:4px;background:{CHANNEL_HINT.get(c, ACCENT)};'
        f'vertical-align:1px;"></span> {e(channel_label(c))} {n} · {d}d</td>'
        for c, n, d in per_channel
    )
    inner.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:7px;"><tr>{legend}</tr></table>')
    inner.append("</td></tr>")
    return "".join(inner)


def _email_header_card(digest: Digest) -> str:
    e = escape
    inner = [
        '<tr><td class="pad" style="padding:20px 20px 14px;">',
        f'<div style="font-family:{_FONT};color:{ACCENT};font-size:11px;font-weight:700;'
        f'letter-spacing:.14em;">SOCIAL FEED DIGEST</div>',
        f'<h1 style="margin:6px 0 2px;font-family:{_FONT};color:{INK};font-size:21px;'
        f'font-weight:800;letter-spacing:-.01em;">{e(digest.run_tag)}</h1>',
        f'<p style="margin:2px 0 0;font-family:{_FONT};color:{MUTED};font-size:13px;line-height:1.5;">'
        f'{e(digest.generated_at.strftime("%Y-%m-%d %H:%M %Z"))} · profile: {e(digest.profile_name)} · '
        f"drafting: {e(digest.draft_source)}</p>",
        f'<p style="margin:8px 0 0;font-family:{_FONT};color:{FAINT};font-size:12px;line-height:1.5;">'
        f"{e(MANUAL_POSTING_NOTE)}</p>",
    ]
    if digest.draft_source in DEGRADED_SOURCES:
        inner.append(
            f'<div style="margin-top:12px;background:{AMBER_BG};border:1px solid {AMBER_LINE};'
            f'border-radius:10px;padding:10px 12px;font-family:{_FONT};color:{AMBER_INK};'
            f'font-size:12.5px;font-weight:600;line-height:1.5;">{e(_degraded_banner_text(digest))}</div>'
        )
    inner.append("</td></tr>")
    inner.append(_stat_strip_email(digest, border_top=True))
    return _card_table("".join(inner))


def _email_post_card(section: Section, n: Notable) -> str:
    e = escape
    chip = ""
    if n.engagement > 0:
        chip = (
            f' <span style="display:inline-block;padding:1px 7px;border-radius:10px;background:{ACCENT_SOFT};'
            f'color:{ACCENT_INK};font-size:11.5px;font-weight:700;">↑ {n.engagement}</span>'
        )
    who = f"{e(n.author)}{chip}" if n.author.strip() else (chip.strip() or "")
    why_label = "Why it matters" if section.channel in ("x", "linkedin") else "Why hot"
    why = f'<div style="margin-top:7px;font-family:{_FONT};color:{BODY_INK};font-size:14px;line-height:1.5;">{e(why_label + ": " + n.why)}</div>' if n.why else ""
    label = "Open thread" if section.channel == "reddit" else "Open post"
    open_link = (
        f'<div style="margin-top:7px;"><a href="{e(n.url)}" style="font-family:{_FONT};color:{ACCENT};'
        f'font-weight:600;font-size:13px;text-decoration:none;">{label} ↗</a></div>'
    )
    comment = ""
    if section.channel == "reddit":
        match = next((en for en in section.engagements if en.index == n.index), None)
        if match:
            comment = (
                f'<div style="margin-top:8px;background:#f6f7f9;border-left:3px solid {LINE};'
                f'border-radius:0 8px 8px 0;padding:8px 11px;font-family:{_FONT};color:{BODY_INK};'
                f'font-size:13px;line-height:1.5;">{e("Suggested comment: " + match.comment)}</div>'
            )
    body = [
        f'<a href="{e(n.url)}" style="font-family:{_FONT};color:{INK};text-decoration:none;'
        f'font-weight:700;font-size:15.5px;line-height:1.4;">{e(n.title)}</a>',
    ]
    if who:
        body.append(f'<div style="margin-top:5px;font-family:{_FONT};color:{MUTED};font-size:13px;">{who}</div>')
    body.append(why)
    body.append(open_link)
    body.append(comment)
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid {LINE};border-radius:12px;"><tr>'
        f"{_thumb_email(n, section.channel)}"
        f'<td valign="top" style="padding:12px 14px;font-family:{_FONT};">{"".join(body)}</td>'
        "</tr></table>"
    )


def _email_draft_block(i: int, draft: str) -> str:
    text = _comment_email(draft)
    prefix = f'<span style="color:{ACCENT_INK};font-weight:800;">{i}.</span> '
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin-top:8px;"><tr><td style="background:{ACCENT_SOFT};border-left:3px solid {ACCENT};'
        f'border-radius:0 8px 8px 0;padding:9px 12px;font-family:{_FONT};color:{INK};'
        f'font-size:14px;line-height:1.55;">{prefix}{text}</td></tr></table>'
    )


def _comment_email(text: str) -> str:
    e = escape
    if text.startswith("[TEMPLATE DRAFT"):
        return (
            f'<span style="color:{AMBER_INK};font-weight:700;">{e("[TEMPLATE DRAFT]")}</span>'
            + e(text.split("]", 1)[-1])
        )
    return e(text)


def _email_engagement_row(entry: Engagement, channel: str) -> str:
    e = escape
    hint = CHANNEL_HINT.get(channel, ACCENT)
    tint = CHANNEL_TINT.get(channel, ACCENT_SOFT)
    return (
        '<tr><td style="padding:9px 0;border-top:1px solid ' + LINE + ';">'
        f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{tint};'
        f'color:{hint};font-family:{_FONT};font-size:11px;font-weight:700;letter-spacing:.05em;">'
        f"{e(entry.action.upper())}</span> "
        f'<a href="{e(entry.url)}" style="font-family:{_FONT};color:{INK};font-weight:600;'
        f'font-size:14.5px;text-decoration:none;">{e(entry.title)}</a>'
        f' <span style="font-family:{_FONT};color:{MUTED};font-size:12.5px;">{e(entry.author)}</span>'
        f'<div style="margin-top:6px;background:#f6f7f9;border-left:3px solid {LINE};'
        f'border-radius:0 8px 8px 0;padding:8px 11px;font-family:{_FONT};color:{BODY_INK};'
        f'font-size:13px;line-height:1.5;">{e(entry.comment)}</div>'
        "</td></tr>"
    )


def _email_section_card(section: Section) -> str:
    e = escape
    hint = CHANNEL_HINT.get(section.channel, ACCENT)
    inner = [
        '<tr><td class="pad" style="padding:14px 18px 10px;">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>',
        f'<td width="4" style="width:4px;background:{hint};border-radius:2px;"></td>',
        f'<td style="padding-left:10px;font-family:{_FONT};color:{INK};font-size:17px;'
        f'font-weight:800;">{e(_section_title(section))}</td>',
        f'<td align="right" valign="middle" style="font-family:{_FONT};color:{MUTED};'
        f'font-size:12px;font-weight:600;white-space:nowrap;">{e(_section_counts_label(section))}</td>',
        "</tr></table></td></tr>",
    ]
    if not section.candidates:
        inner.append(
            f'<tr><td class="padx" style="padding:2px 18px 16px;font-family:{_FONT};color:{MUTED};'
            f'font-size:13.5px;line-height:1.55;">{e(section.empty_note or "Nothing collected for this channel this run.")}</td></tr>'
        )
        return _card_table("".join(inner))

    if section.channel in ("x", "linkedin"):
        inner.append(
            f'<tr><td class="padx" style="padding:2px 18px 12px;font-family:{_FONT};color:{BODY_INK};'
            f'font-size:14.5px;line-height:1.55;">{e(section.summary or _section_title(section))}</td></tr>'
        )
        blocks: list[str] = []
        if section.notable:
            blocks.append(
                f'<div style="font-family:{_FONT};color:{MUTED};font-size:12px;font-weight:700;'
                f'letter-spacing:.06em;margin:4px 0 8px;">Posts getting attention</div>'
            )
            for n in section.notable:
                blocks.append(_email_post_card(section, n))
                blocks.append('<div style="height:8px;font-size:8px;line-height:8px;">&nbsp;</div>')
        if section.drafts:
            blocks.append(
                f'<div style="font-family:{_FONT};color:{MUTED};font-size:12px;font-weight:700;'
                f'letter-spacing:.06em;margin:8px 0 2px;">Drafts for your account (post manually)</div>'
            )
            for i, draft in enumerate(section.drafts, 1):
                blocks.append(_email_draft_block(i, draft))
        if section.engagements:
            verb = "reposts" if section.channel == "x" else "reshares"
            blocks.append(
                f'<div style="font-family:{_FONT};color:{MUTED};font-size:12px;font-weight:700;'
                f'letter-spacing:.06em;margin:14px 0 2px;">Comments and {e(verb)} worth making</div>'
            )
            rows = "".join(_email_engagement_row(entry, section.channel) for entry in section.engagements)
            blocks.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>')
        inner.append(f'<tr><td class="padx" style="padding:0 18px 16px;">{"".join(blocks)}</td></tr>')
        return _card_table("".join(inner))

    # Reddit and web/news: capped post list with inline suggested comments.
    parts = []
    if section.summary:
        parts.append(
            f'<tr><td class="padx" style="padding:2px 18px 12px;font-family:{_FONT};color:{BODY_INK};'
            f'font-size:14.5px;line-height:1.55;">{e(section.summary)}</td></tr>'
        )
    cards = []
    for n in section.notable:
        cards.append(_email_post_card(section, n))
        cards.append('<div style="height:8px;font-size:8px;line-height:8px;">&nbsp;</div>')
    parts.append(f'<tr><td class="padx" style="padding:0 18px 16px;">{"".join(cards)}</td></tr>')
    inner.extend(parts)
    return _card_table("".join(inner))


def _email_footer_card(digest: Digest) -> str:
    e = escape
    lines = [
        f"<b>Collection:</b> {e(', '.join(f'{channel_label(k)}: {v}' for k, v in sorted(digest.collection.items())))}",
        f"<b>xAI tool calls:</b> {digest.cost.get('search_tool_calls', 0)}",
        f"<b>est. cost:</b> ${digest.cost.get('total_usd', 0.0):.4f} (budget ${digest.cost.get('budget_usd', 0.25):.2f})",
        f"duration {digest.duration_s:.0f}s",
    ]
    html = f'<tr><td style="padding:14px 18px;font-family:{_FONT};color:{MUTED};font-size:12px;line-height:1.7;">{" · ".join(lines)}'
    if digest.claude_error:
        html += f'<br><span style="color:{AMBER_INK};font-weight:700;">DRAFTING DEGRADED:</span> {e(digest.claude_error)}'
    for warning in digest.warnings:
        html += f"<br>WARNING: {e(warning)}"
    html += "</td></tr>"
    return _card_table(html, radius=12)


def render_email_html(digest: Digest) -> str:
    """The Gmail-facing HTML part: table-based, inline-styled, no JavaScript.

    Cards keep their structure and color tiles when Gmail blocks remote
    images (the default): thumbnails reserve fixed boxes over channel-tinted
    cells and author monograms, so a blocked image reads as an empty tile.
    """
    parts = _email_open(digest)
    parts.append(_email_header_card(digest))
    for section in digest.sections:
        parts.append(_spacer())
        parts.append(_email_section_card(section))
    parts.append(_spacer(10))
    parts.append(_email_footer_card(digest))
    parts.append(_email_close())
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