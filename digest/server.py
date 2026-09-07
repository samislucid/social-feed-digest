"""Private, token-protected digest page server (stdlib only).

Auth: token via ?token=, Authorization: Bearer, or X-Digest-Token header.
Wrong or missing token returns 404 (not 401) so the page's existence is not
advertised. The server refuses to start without DIGEST_PAGE_TOKEN.
"""
from __future__ import annotations

import hmac
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import ConfigError, Settings


class DigestPageServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], pages_dir: Path, page_token: str):
        super().__init__(address, DigestPageHandler)
        self.pages_dir = pages_dir
        self.page_token = page_token


class DigestPageHandler(BaseHTTPRequestHandler):
    server_version = "DigestPage/1.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        token = self._token()
        if not token or not hmac.compare_digest(token, self.server.page_token):
            self.send_error(404, "Not Found")
            return

        pages: Path = self.server.pages_dir
        path = urlparse(self.path).path
        if path in ("", "/"):
            target = pages / "index.html"
        elif path in ("/latest", "/latest.html"):
            target = pages / "latest.html"
        else:
            name = path.lstrip("/")
            if "/" in name or ".." in name or name.startswith("."):
                self.send_error(404, "Not Found")
                return
            if not name.endswith(".html"):
                name += ".html"
            target = pages / name

        if not target.is_file():
            self.send_error(404, "Not Found")
            return

        body = target.read_bytes()
        ctype, _ = mimetypes.guess_type(target.name)
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _token(self) -> str | None:
        query = parse_qs(urlparse(self.path).query)
        if query.get("token"):
            return query["token"][0]
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return self.headers.get("X-Digest-Token")

    def log_message(self, fmt: str, *args) -> None:
        # Strip the query string so tokens never land in the journal.
        safe_path = self.path.split("?", 1)[0]
        print(f"{self.address_string()} - {fmt % args} [path: {safe_path}]", flush=True)


def make_server(settings: Settings, host: str = "0.0.0.0", port: int = 8787) -> DigestPageServer:
    if not settings.page_token:
        raise ConfigError(
            "DIGEST_PAGE_TOKEN is not set: refusing to serve an unprotected private page. "
            "Generate one with: openssl rand -hex 24"
        )
    pages_dir = Path(settings.data_dir) / "pages"
    if not (pages_dir / "index.html").exists():
        raise ConfigError(
            f"No digest pages at {pages_dir}: run 'python -m digest run' once before starting the server"
        )
    return DigestPageServer((host, port), pages_dir, settings.page_token)
