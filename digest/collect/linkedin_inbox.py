"""Watched-inbox reader for LinkedIn items supplied by Bright sessions.

Contract: Bright writes one file per batch to <data_dir>/<linkedin.inbox>
(default data/inbox/linkedin). Accepted formats:

  - .json: {"title": "...", "url": "https://...", "summary": "...",
            "author": "...", "posted_at": "ISO-8601"} or a list of those.
  - .md:   first "# " heading is the title, first markdown link is the URL,
            remaining body is the summary.

Parsed files move to <inbox>/processed/ so nothing is ingested twice. The
server never reads LinkedIn on its own; this is a pure local file reader.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..config import Settings
from ..items import Item


def collect_linkedin(settings: Settings, profile: dict, log=print) -> list[Item]:
    cfg = profile.get("linkedin") or {}
    inbox = settings.data_dir / (cfg.get("inbox") or "inbox/linkedin")
    if not inbox.exists():
        return []

    processed = inbox / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    items: list[Item] = []
    for path in sorted(inbox.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".json", ".md", ".markdown"):
            continue
        try:
            parsed = _parse_file(path)
        except (ValueError, json.JSONDecodeError) as exc:
            log(f"WARN: linkedin inbox file skipped ({path.name}): {exc}")
            continue
        for item in parsed:
            item.channel = "linkedin"
        items.extend(parsed)
        target = processed / path.name
        if target.exists():
            target = processed / f"{path.stem}-{datetime.now(timezone.utc):%H%M%S}{path.suffix}"
        path.rename(target)
        log(f"ingested {len(parsed)} linkedin item(s) from {path.name} -> processed/")
    return items


def _parse_file(path: Path) -> list[Item]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        records = data if isinstance(data, list) else [data]
        items = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("json records must be objects")
            items.append(_item_from_record(record, path.name))
        return items

    title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    link_match = re.search(r"\((https?://[^\s)]+)\)", text)
    if not title_match or not link_match:
        raise ValueError("markdown item needs a '# title' and a link")
    title = title_match.group(1).strip()
    url = link_match.group(1)
    body_start = text.find(title_match.group(0)) + len(title_match.group(0))
    summary = re.sub(r"\(https?://[^\s)]+\)", "", text[body_start:]).strip()[:400]
    return [Item(channel="linkedin", title=title, url=url, summary=summary, source_label="LinkedIn (Bright inbox)")]


def _item_from_record(record: dict, label: str) -> Item:
    title = str(record.get("title") or "").strip()
    url = str(record.get("url") or "").strip()
    if not title or not url.startswith("http"):
        raise ValueError(f"json item needs non-empty title and http url ({label})")
    posted = None
    raw_posted = str(record.get("posted_at") or "").strip()
    if raw_posted:
        try:
            posted = datetime.fromisoformat(raw_posted.replace("Z", "+00:00"))
        except ValueError:
            posted = None
    return Item(
        channel="linkedin",
        title=title,
        url=url,
        summary=str(record.get("summary") or "")[:400],
        source_label=f"LinkedIn: {record.get('author') or label}",
        published=posted,
    )
