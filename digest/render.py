"""Rendering: markdown artifact, standalone HTML page, and MIME email message.

Channel-first layout, per Sam's 2026-09-07 review: X and LinkedIn sections are
action-first (activity summary, posts getting attention, ready-to-post drafts,
recommended comments/reposts/reshares); Reddit is capped at its best few posts;
web/news is compact context. Every draft and comment is a suggestion Sam posts
manually - the worker never posts anywhere.
"""
from __future__ import annotations

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
TEMPLATE_BANNER = (
    "TEMPLATE DRAFT (claude CLI unavailable in this environment): placeholder content below"
    " until the claude CLI is installed and authenticated (see RUNBOOK.md, section 5)."
)
MANUAL_POSTING_NOTE = "All drafts and comments are suggestions to post manually; the worker never posts."


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
        return s


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
    if digest.draft_source == "template-fallback":
        lines.append("")
        lines.append(f"> {TEMPLATE_BANNER}")
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


_CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;max-width:820px;
margin:24px auto;padding:0 16px;color:#1a1a1a;line-height:1.5}
h1{font-size:1.5rem}h2{font-size:1.15rem;border-bottom:1px solid #ddd;padding-bottom:4px}
h3{font-size:1.02rem;margin-bottom:2px}
.topic{border:1px solid #e3e3e3;border-radius:8px;padding:12px 16px;margin:12px 0}
.meta{color:#555;font-size:.88rem}.comment{background:#f6f6f4;border-left:3px solid #888;
padding:8px 12px;margin:8px 0;border-radius:0 6px 6px 0}
.template{color:#9a6700;font-weight:600}
a{color:#0b57d0;word-break:break-all}ul{padding-left:20px}
footer{color:#555;font-size:.85rem;margin-top:24px;border-top:1px solid #ddd;padding-top:8px}
"""


def _comment_html(text: str) -> str:
    if text.startswith("[TEMPLATE DRAFT"):
        return f'<span class="template">{escape("[TEMPLATE DRAFT]")}</span>' + escape(text.split("]", 1)[-1])
    return escape(text)


def _template_marked(text: str) -> bool:
    return text.startswith("[TEMPLATE DRAFT")


def render_html(digest: Digest) -> str:
    e = escape
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(digest.subject)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{e('Social Feed Digest')} <small>{e(digest.run_tag)}</small></h1>",
        f'<p class="meta">{e(digest.generated_at.strftime("%Y-%m-%d %H:%M %Z"))} · '
        f"profile: {e(digest.profile_name)} · drafting: {e(digest.draft_source)} · "
        f'est. external cost: ${digest.cost.get("total_usd", 0.0):.4f}</p>',
        f'<p class="meta">{e(MANUAL_POSTING_NOTE)}</p>',
    ]
    if digest.draft_source == "template-fallback":
        parts.append(f'<p class="template">{e(TEMPLATE_BANNER)}</p>')

    for section in digest.sections:
        parts.append(f"<h2>{e(_section_title(section))}</h2>")
        if not section.candidates:
            parts.append(f"<p>{e(section.empty_note or 'Nothing collected for this channel this run.')}</p>")
            continue
        if section.channel in ("x", "linkedin"):
            parts.append(f"<p>{e(section.summary)}</p>")
            if section.notable:
                parts.append("<h3>Posts getting attention</h3>")
                for n in section.notable:
                    who = f" - {n.author}" if n.author.strip() else ""
                    why = f'<p>{e("Why it matters: " + n.why)}</p>' if n.why else ""
                    parts.append(
                        f'<div class="topic"><h3>{n.index}. {e(n.title)}{e(who)}</h3>'
                        f'<p class="meta">Post: <a href="{e(n.url)}">{e(n.url)}</a></p>{why}</div>'
                    )
            if section.drafts:
                parts.append("<h3>Drafts for your account (post manually)</h3>")
                for draft in section.drafts:
                    marked = _template_marked(draft)
                    body = _comment_html(draft)
                    cls = "comment template" if marked else "comment"
                    parts.append(f'<div class="{cls}">{body}</div>')
            if section.engagements:
                verb = "reposts" if section.channel == "x" else "reshares"
                parts.append(f"<h3>Comments and {e(verb)} worth making</h3><ul>")
                for entry in section.engagements:
                    parts.append(
                        f'<li>{e(_engagement_line(entry)[2:])}</li>'  # strip leading "- "
                    )
                parts.append("</ul>")
            continue

        if section.summary:
            parts.append(f"<p>{e(section.summary)}</p>")
        for n in section.notable:
            who = f" - {n.author}" if n.author.strip() else ""
            why = f'<p>{e("Why hot: " + n.why)}</p>' if n.why else ""
            comment = ""
            if section.channel == "reddit":
                match = next((en for en in section.engagements if en.index == n.index), None)
                if match:
                    comment = f'<div class="comment">{_comment_html("Suggested comment: " + match.comment)}</div>'
            parts.append(
                f'<div class="topic"><h3>{n.index}. {e(n.title)}{e(who)}</h3>'
                f'<p class="meta">Post: <a href="{e(n.url)}">{e(n.url)}</a></p>{why}{comment}</div>'
            )

    parts.append("<footer>")
    parts.append(
        f'Collection: {e(", ".join(f"{channel_label(k)}: {v}" for k, v in sorted(digest.collection.items())))} · '
        f'xAI tool calls: {digest.cost.get("search_tool_calls", 0)} · '
        f'est. cost: ${digest.cost.get("total_usd", 0.0):.4f} '
        f'(budget ${digest.cost.get("budget_usd", 0.25):.2f}) · '
        f"duration {digest.duration_s:.0f}s"
    )
    if digest.claude_error:
        parts.append(f"<br>DRAFTING DEGRADED: {e(digest.claude_error)}")
    for warning in digest.warnings:
        parts.append(f"<br>WARNING: {e(warning)}")
    parts.append("</footer></body></html>")
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
