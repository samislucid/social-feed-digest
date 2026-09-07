from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import pytest

from digest import deliver
from digest.deliver import prune, run_tag_for, send_email, store_digest
from digest.items import Item
from digest.rank import build_topics
from digest.render import Digest, build_email, render_html, render_markdown
from digest.shape import Engagement, Notable, shape_sections


def _digest(base_profile, run_tag: str = "2026-09-07_0930") -> Digest:
    items = [Item(channel="x", title="Open-weights model tops evals", url="https://x.com/u/status/1")]
    topics = build_topics(items, base_profile)
    sections = shape_sections(topics, base_profile)
    x = next(s for s in sections if s.channel == "x")
    x.summary = "One X post on your topics."
    x.notable = [
        Notable(index=1, title="Open-weights model tops evals", url="https://x.com/u/status/1", author="@modelwatcher", why="Beats closed models on evals.")
    ]
    x.drafts = ["Draft one.", "Draft two."]
    x.engagements = [
        Engagement(index=1, title="Open-weights model tops evals", url="https://x.com/u/status/1", author="@modelwatcher", action="comment", comment="Numbers check out.")
    ]
    return Digest(
        run_tag=run_tag,
        generated_at=datetime(2026, 9, 7, 16, 30, tzinfo=timezone.utc),
        profile_name=base_profile["name"],
        sections=sections,
        draft_source="template-fallback",
        collection={"x": 1},
        cost={"search_tool_calls": 2, "total_usd": 0.01, "budget_usd": 0.25, "calls": []},
        email_to="samislucid98@gmail.com",
    )


def test_run_tag_is_pacific_local():
    # 2026-09-07 02:30 UTC == 2026-09-06 19:30 Pacific (PDT, UTC-7)
    assert run_tag_for(datetime(2026, 9, 7, 2, 30, tzinfo=timezone.utc)) == "2026-09-06_1930"


def test_digest_json_serializes_channel_sections(base_profile, settings):
    digest = _digest(base_profile)
    data = deliver.digest_to_dict(digest)
    assert data["sections"][0]["channel"] == "x"
    assert data["sections"][0]["drafts"] == ["Draft one.", "Draft two."]
    assert data["sections"][0]["engagements"][0]["action"] == "comment"
    assert data["sections"][0]["notable"][0]["url"] == "https://x.com/u/status/1"
    assert "post_ideas" not in data
    assert "shortlist" not in data
    assert "topics" not in data


def test_store_digest_writes_all_artifacts(base_profile, settings):
    digest = _digest(base_profile)
    md = render_markdown(digest)
    html = render_html(digest)
    msg = build_email(digest, md, html, "digest@example.com")
    run_dir = store_digest(digest, md, html, msg, settings)

    names = {p.name for p in run_dir.iterdir()}
    assert {"digest.md", "digest.html", "digest.json", "digest.eml"} <= names

    pages = settings.data_dir / "pages"
    assert (pages / "latest.html").read_text(encoding="utf-8") == html
    assert (pages / f"{digest.run_tag}.html").exists()
    assert digest.run_tag in (pages / "index.html").read_text(encoding="utf-8")


class _FakeSMTP:
    """Records the SMTP dialogue; data() replays a scripted (code, response)."""

    last_instance: "_FakeSMTP | None" = None
    data_response: tuple = (250, "Ok: queued as abc123")

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        _FakeSMTP.last_instance = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def mail(self, from_addr):
        self.calls.append(("mail", from_addr))
        return 250, "ok"

    def rcpt(self, rcpt):
        self.calls.append(("rcpt", rcpt))
        return 250, "ok"

    def data(self, payload):
        self.calls.append(("data", len(payload)))
        return _FakeSMTP.data_response


def _email_message() -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "onboarding@resend.dev"
    msg["To"] = "samislucid98@gmail.com"
    msg["Subject"] = "test digest"
    msg.set_content("body")
    return msg


def test_send_email_reports_server_acceptance(monkeypatch, settings):
    monkeypatch.setattr(deliver.smtplib, "SMTP", _FakeSMTP)
    settings.smtp_host, settings.smtp_port = "smtp.resend.com", 587
    settings.smtp_user, settings.smtp_password = "resend", "re_test"
    lines: list[str] = []
    status = send_email(_email_message(), settings, dry_run=False, log=lines.append)
    assert status.startswith("sent")
    assert any("Ok: queued as abc123" in line for line in lines)
    smtp = _FakeSMTP.last_instance
    assert ("login", "resend", "re_test") in smtp.calls
    assert ("mail", "onboarding@resend.dev") in smtp.calls
    assert ("rcpt", "samislucid98@gmail.com") in smtp.calls


def test_send_email_raises_verbatim_on_rejection(monkeypatch, settings):
    _FakeSMTP.data_response = (551, b"You can only send testing emails to your own email address")
    monkeypatch.setattr(deliver.smtplib, "SMTP", _FakeSMTP)
    settings.smtp_host, settings.smtp_port = "smtp.resend.com", 587
    settings.smtp_user, settings.smtp_password = "resend", "re_test"
    with pytest.raises(smtplib.SMTPDataError, match="testing emails"):
        send_email(_email_message(), settings, dry_run=False, log=lambda _m: None)


def test_prune_removes_old_runs_but_keeps_recent(base_profile, settings):
    old = _digest(base_profile, run_tag="2020-01-01_0000")
    md = render_markdown(old)
    html = render_html(old)
    msg: EmailMessage = build_email(old, md, html, "digest@example.com")
    store_digest(old, md, html, msg, settings)
    new = _digest(base_profile)
    store_digest(new, md, html, msg, settings)

    removed = prune(settings, retention_days=30)

    digests = settings.data_dir / "digests"
    assert not (digests / "2020-01-01_0000").exists()
    assert (digests / new.run_tag).exists()
    assert removed >= 1
