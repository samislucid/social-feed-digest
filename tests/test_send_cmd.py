from __future__ import annotations

import argparse
from email import policy as email_policy
from email.message import EmailMessage

from digest import deliver
from digest.__main__ import cmd_send


class _FakeSMTP:
    last_instance = None
    last_rcpt = None
    data_response = (250, "Ok: queued as abc123")

    def __init__(self, host, port, timeout=None, context=None):
        _FakeSMTP.last_instance = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def mail(self, from_addr):
        return 250, "ok"

    def rcpt(self, rcpt):
        _FakeSMTP.last_rcpt = rcpt
        return 250, "ok"

    def data(self, payload):
        return _FakeSMTP.data_response


def _args(run_tag=None, to=None) -> argparse.Namespace:
    return argparse.Namespace(run_tag=run_tag, to=to)


def test_send_without_smtp_host_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert cmd_send(_args()) == 2


def test_send_with_no_artifacts_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))
    assert cmd_send(_args()) == 2


def test_send_applies_dgest_email_to_override(monkeypatch, tmp_path):
    monkeypatch.setattr(deliver.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "onboarding@resend.dev")
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DIGEST_EMAIL_TO", "samislucid98@gmail.com")
    run_dir = tmp_path / "digests" / "2026-09-07_1000"
    run_dir.mkdir(parents=True)
    msg = EmailMessage()
    msg["From"] = "digest@localhost"
    msg["To"] = "samjookim@gmail.com"
    msg["Subject"] = "test digest"
    msg.set_content("body")
    (run_dir / "digest.eml").write_bytes(msg.as_bytes(policy=email_policy.SMTP))
    assert cmd_send(_args()) == 0
    assert _FakeSMTP.last_rcpt == "samislucid98@gmail.com"
