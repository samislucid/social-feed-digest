"""Delivery: dated artifacts, static private page, retention prune, SMTP email."""
from __future__ import annotations

import json
import shutil
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from .config import Settings
from .render import Digest, channel_label

def _pacific_zone():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/Los_Angeles")
    except Exception:  # pragma: no cover - minimal environments
        return timezone.utc


def run_tag_for(now: datetime) -> str:
    """Pacific-time run tag (Sam's local time); falls back to UTC without zoneinfo."""
    return now.astimezone(_pacific_zone()).strftime("%Y-%m-%d_%H%M")


def local_date() -> str:
    """Today in Pacific time, used for the once-a-day portfolio sweep marker."""
    return datetime.now(_pacific_zone()).date().isoformat()


def digest_to_dict(digest: Digest) -> dict:
    return {
        "run_tag": digest.run_tag,
        "generated_at": digest.generated_at.isoformat(),
        "profile_name": digest.profile_name,
        "draft_source": digest.draft_source,
        "sections": [
            {
                "channel": s.channel,
                "summary": s.summary,
                "intro": s.intro,
                "empty_note": s.empty_note,
                "notable": [
                    {
                        "index": n.index,
                        "title": n.title,
                        "url": n.url,
                        "author": n.author,
                        "why": n.why,
                        "engagement": n.engagement,
                        "image": n.image,
                    }
                    for n in s.notable
                ],
                "drafts": s.drafts,
                "engagements": [
                    {
                        "index": e.index,
                        "title": e.title,
                        "url": e.url,
                        "author": e.author,
                        "action": e.action,
                        "comment": e.comment,
                    }
                    for e in s.engagements
                ],
            }
            for s in digest.sections
        ],
        "collection": digest.collection,
        "cost": digest.cost,
        "warnings": digest.warnings,
        "duration_s": round(digest.duration_s, 1),
        "claude_error": digest.claude_error,
    }


def store_digest(digest: Digest, markdown: str, html: str, message: EmailMessage, settings: Settings) -> Path:
    """Write the dated artifact set and refresh the private page directory."""
    run_dir = settings.data_dir / "digests" / digest.run_tag
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "digest.md").write_text(markdown, encoding="utf-8")
    (run_dir / "digest.html").write_text(html, encoding="utf-8")
    (run_dir / "digest.json").write_text(
        json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "digest.eml").write_bytes(message.as_bytes())

    pages_dir = settings.data_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / f"{digest.run_tag}.html").write_text(html, encoding="utf-8")
    (pages_dir / "latest.html").write_text(html, encoding="utf-8")
    (pages_dir / "index.html").write_text(_index_html(digest, settings), encoding="utf-8")
    return run_dir


def _index_html(digest: Digest, settings: Settings) -> str:
    from html import escape

    runs = sorted((settings.data_dir / "digests").glob("*_*"), reverse=True)[:60]
    rows = "\n".join(
        f'<li><a href="{escape(p.name)}.html">{escape(p.name)}</a></li>' for p in runs if (settings.data_dir / "digests" / p.name / "digest.html").exists()
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Digest index</title><style>"
        "body{margin:0;background:#f2f3f7;color:#3d4350;"
        "font:15px/1.55 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}"
        ".wrap{max-width:640px;margin:0 auto;padding:36px 20px 56px}"
        "h1{font-size:22px;color:#16181d;margin:0 0 4px}"
        ".sub{color:#5f6470;font-size:13.5px;margin:0 0 18px}"
        ".card{background:#fff;border:1px solid #e4e6ee;border-radius:14px;padding:8px 6px}"
        "ul{list-style:none;margin:0;padding:0}li{border-top:1px solid #f0f1f5}"
        "li:first-child{border-top:0}"
        "a{display:block;padding:10px 14px;color:#4f46e5;text-decoration:none;font-size:14.5px;font-weight:600}"
        "a:hover{background:#eef0fe;border-radius:8px}"
        "</style></head><body><div class='wrap'>"
        "<h1>Digests</h1>"
        f"<p class='sub'>Latest run: {escape(digest.run_tag)} · <a href='latest.html'>open latest</a></p>"
        f"<div class='card'><ul>{rows}</ul></div></div></body></html>"
    )


def prune(settings: Settings, retention_days: int, log=print) -> int:
    """Delete run directories (and their page files) older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
    removed = 0
    digests_dir = settings.data_dir / "digests"
    pages_dir = settings.data_dir / "pages"
    if not digests_dir.exists():
        return 0
    for run_dir in sorted(digests_dir.iterdir()):
        stamp = _parse_tag(run_dir.name)
        if stamp is None or stamp >= cutoff:
            continue
        shutil.rmtree(run_dir, ignore_errors=True)
        for page in (pages_dir / f"{run_dir.name}.html",):
            page.unlink(missing_ok=True)
        removed += 1
    if removed:
        log(f"pruned {removed} run(s) older than {retention_days} days")
    return removed


def _parse_tag(tag: str) -> datetime | None:
    try:
        local = datetime.strptime(tag, "%Y-%m-%d_%H%M")
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo

        return local.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    except Exception:  # pragma: no cover
        return local.replace(tzinfo=timezone.utc)


def send_email(message: EmailMessage, settings: Settings, dry_run: bool, log=print) -> str:
    """Send via SMTP unless dry_run; dry runs verify rendering and keep the .eml.

    The SMTP server's final DATA response is logged verbatim (Resend's bridge
    returns its queued line there, which is the acceptance proof for a send).
    """
    if dry_run:
        log("dry run: email not sent (digest.eml written with the run artifacts)")
        return "skipped-dry-run"
    if not settings.smtp_host:
        log("SMTP_HOST not set: email not sent (digest.eml written with the run artifacts)")
        return "skipped-no-smtp"

    try:
        from email import policy as email_policy

        payload = message.as_bytes(policy=email_policy.SMTP)  # CRLF wire format
    except Exception:  # pragma: no cover - older message objects
        payload = message.as_bytes()

    if settings.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=60, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=60)

    with server:
        server.ehlo()
        if settings.smtp_port != 465:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password or "")
        from_addr = str(message["From"])
        rcpt = str(message["To"])
        code, resp = server.mail(from_addr)
        if code >= 400:
            raise smtplib.SMTPSenderRefused(code, resp, from_addr)
        code, resp = server.rcpt(rcpt)
        if code >= 400:
            raise smtplib.SMTPRecipientsRefused({rcpt: (code, resp)})
        data_result = server.data(payload)
        # Python 3.11 smtplib returns (code, msg); be tolerant of a bare response.
        if isinstance(data_result, tuple):
            data_code, raw = int(data_result[0]), data_result[1]
        else:
            data_code, raw = 250, data_result
        data_resp = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if data_code != 250:
            # Rejections surface verbatim (e.g. Resend's testing-sender constraint).
            raise smtplib.SMTPDataError(data_code, data_resp)
    status = f"sent (server: {data_resp})"
    log(f"email sent to {rcpt}: {data_resp}")
    return status
