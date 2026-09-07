"""Rendering: markdown artifact, standalone HTML page, and MIME email message."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
from html import escape

from .rank import Topic

CHANNEL_NAMES = {"x": "X", "reddit": "Reddit", "linkedin": "LinkedIn", "web": "Web", "news": "News"}


@dataclass
class Digest:
    """Everything one run produced, ready to render."""

    run_tag: str
    generated_at: datetime
    profile_name: str
    topics: list[Topic]
    post_ideas: dict[str, list[str]]
    draft_source: str
    collection: dict[str, int]
    cost: dict
    email_to: str
    subject_prefix: str = "[Feed Digest]"
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def subject(self) -> str:
        return f"{self.subject_prefix} {self.run_tag} - {len(self.topics)} topics"


def channel_label(channel: str) -> str:
    return CHANNEL_NAMES.get(channel, channel)


def topic_channels(topic: Topic) -> str:
    return ", ".join(channel_label(c) for c in topic.channel_labels)


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
    if digest.draft_source == "template-fallback":
        from .draft import TEMPLATE_MARKER

        lines.append("")
        lines.append(f"> {TEMPLATE_MARKER}: comments and post ideas below are placeholders until")
        lines.append("> the claude CLI is installed and authenticated (see RUNBOOK.md).")
    lines.append("")
    lines.append(f"## Topics ({len(digest.topics)}, ranked)")
    for i, topic in enumerate(digest.topics, 1):
        lines.append("")
        lines.append(f"### {i}. {topic.title}")
        lines.append(f"- Why hot: {topic.why_hot}")
        lines.append(f"- Niche: {topic.niche or 'unclassified'} · Channels: {topic_channels(topic)}")
        lines.append(f"- Source: {topic.source_url}")
        lines.append(f"- Quiet share: {topic.quiet_share_url}")
        lines.append(f"- Suggested comment: {topic.comment}")
    lines.append("")
    lines.append("## Post ideas")
    for channel, ideas in digest.post_ideas.items():
        lines.append("")
        lines.append(f"### {channel_label(channel)} ({len(ideas)})")
        for j, idea in enumerate(ideas, 1):
            lines.append(f"{j}. {idea}")
    lines.append("")
    lines.append("## Run footer")
    searches = digest.cost.get("search_tool_calls", 0)
    lines.append(
        f"- Collection: " + ", ".join(f"{channel_label(k)}: {v}" for k, v in sorted(digest.collection.items()))
    )
    lines.append(f"- xAI Live Search tool calls: {searches} · est. cost: ${digest.cost.get('total_usd', 0.0):.4f} "
                 f"(budget ${digest.cost.get('budget_usd', 0.25):.2f})")
    lines.append(f"- Run duration: {digest.duration_s:.1f}s")
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
        "<h2>Topics (ranked)</h2>",
    ]
    for i, topic in enumerate(digest.topics, 1):
        comment_html = e(topic.comment)
        if topic.comment.startswith("[TEMPLATE DRAFT"):
            comment_html = f'<span class="template">{e("[TEMPLATE DRAFT]")}</span>' + e(topic.comment.split("]", 1)[-1])
        parts.append(
            f'<div class="topic"><h3>{i}. {e(topic.title)}</h3>'
            f'<p>{e(topic.why_hot)}</p>'
            f'<p class="meta">Niche: {e(topic.niche or "unclassified")} · Channels: {e(topic_channels(topic))}</p>'
            f'<p class="meta">Source: <a href="{e(topic.source_url)}">{e(topic.source_url)}</a></p>'
            f'<p class="meta">Quiet share: <a href="{e(topic.quiet_share_url)}">{e(topic.quiet_share_url)}</a></p>'
            f'<div class="comment">{comment_html}</div></div>'
        )
    parts.append("<h2>Post ideas</h2>")
    for channel, ideas in digest.post_ideas.items():
        parts.append(f"<h3>{e(channel_label(channel))}</h3><ul>")
        for idea in ideas:
            parts.append(f"<li>{e(idea)}</li>")
        parts.append("</ul>")
    parts.append("<footer>")
    parts.append(
        f'Collection: {e(", ".join(f"{channel_label(k)}: {v}" for k, v in sorted(digest.collection.items())))} · '
        f'xAI tool calls: {digest.cost.get("search_tool_calls", 0)} · '
        f'est. cost: ${digest.cost.get("total_usd", 0.0):.4f} '
        f'(budget ${digest.cost.get("budget_usd", 0.25):.2f}) · '
        f"duration {digest.duration_s:.0f}s"
    )
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
