from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest

from digest.config import ConfigError
from digest.server import make_server


@pytest.fixture
def server(settings):
    pages = settings.data_dir / "pages"
    pages.mkdir(parents=True)
    (pages / "index.html").write_text("<html><body>index</body></html>", encoding="utf-8")
    (pages / "latest.html").write_text("<html><body>latest digest</body></html>", encoding="utf-8")
    (pages / "2026-09-07_0930.html").write_text("<html><body>run page</body></html>", encoding="utf-8")
    settings.page_token = "tok123"
    srv = make_server(settings, host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address[:2]
    yield f"http://{host}:{port}"
    srv.shutdown()
    srv.server_close()


def _get(base: str, path: str, token: str | None = None, header: bool = False):
    req = urllib.request.Request(base + path)
    if token is not None:
        if header:
            req.add_header("Authorization", f"Bearer {token}")
        else:
            sep = "&" if "?" in path else "?"
            req = urllib.request.Request(f"{base}{path}{sep}token={token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def test_correct_token_serves_index(server):
    status, body = _get(server, "/", token="tok123")
    assert status == 200
    assert "index" in body


def test_wrong_or_missing_token_returns_404(server):
    assert _get(server, "/")[0] == 404
    assert _get(server, "/", token="wrong")[0] == 404
    assert _get(server, "/latest.html")[0] == 404


def test_bearer_header_token_works(server):
    status, body = _get(server, "/latest", token="tok123", header=True)
    assert status == 200
    assert "latest digest" in body


def test_latest_and_run_pages(server):
    assert _get(server, "/latest", token="tok123")[0] == 200
    status, body = _get(server, "/2026-09-07_0930", token="tok123")
    assert status == 200 and "run page" in body


def test_path_traversal_blocked(server):
    assert _get(server, "/..%2F..%2Fsecrets.txt", token="tok123")[0] == 404


def test_server_refuses_to_start_without_token(settings):
    settings.page_token = None
    with pytest.raises(ConfigError, match="DIGEST_PAGE_TOKEN"):
        make_server(settings, host="127.0.0.1", port=0)
