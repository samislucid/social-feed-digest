from __future__ import annotations

import argparse

from digest.__main__ import cmd_send


def _args(run_tag=None) -> argparse.Namespace:
    return argparse.Namespace(run_tag=run_tag)


def test_send_without_smtp_host_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert cmd_send(_args()) == 2


def test_send_with_no_artifacts_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))
    assert cmd_send(_args()) == 2
