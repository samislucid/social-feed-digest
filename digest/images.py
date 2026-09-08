"""Best-effort post thumbnails for the digest surfaces (email HTML + page).

Cards are designed to look complete with images blocked (Gmail blocks remote
images by default), so a thumbnail is always a graceful upgrade, never a
requirement:

- an image the pipeline already holds (``item.extra["image"]``) wins and is
  never re-fetched;
- otherwise one og:image attempt on the post URL, skipping login-walled hosts
  (x.com / twitter.com / linkedin.com render no og:image without JS or auth);
- a short per-request timeout and a shared wall-clock budget keep rendering
  fast; every failure leaves the channel-tinted monogram fallback in place.

No new dependency, no new required env var, no collection or drafting change.
"""
from __future__ import annotations

import re
import ssl
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .render import Digest

USER_AGENT = "Mozilla/5.0 (compatible; SocialFeedDigest/1.0)"
WALLED_HOSTS = frozenset(
    {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "linkedin.com", "www.linkedin.com"}
)
_MAX_BYTES = 262_144  # og:image lives in the document head; never read more
_META_TAG_RE = re.compile(r"<meta[^>]*>", re.IGNORECASE)
_OG_KEY_RE = re.compile(
    r"""(?:property|name)\s*=\s*["'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["']""",
    re.IGNORECASE,
)
_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


class FetchError(RuntimeError):
    """One thumbnail attempt failed; callers fall back to the monogram tile."""


def _http_get(url: str, timeout_s: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - https only, fixed UA
        if response.status != 200:
            raise FetchError(f"HTTP {response.status}")
        return response.read(_MAX_BYTES)


def og_image(url: str, timeout_s: float = 2.0, fetch=_http_get) -> str:
    """Return the og:image URL for a public page, or raise FetchError."""
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise FetchError("non-http url")
    html = fetch(url, timeout_s).decode("utf-8", errors="replace")
    for tag in _META_TAG_RE.finditer(html):
        tag = tag.group(0)
        if not _OG_KEY_RE.search(tag):
            continue  # attribute order varies; check the key first, then read content
        content = _CONTENT_RE.search(tag)
        if not content:
            continue
        image = urljoin(url, content.group(1).strip())
        if image.startswith(("http://", "https://")):
            return image
    raise FetchError("no og:image meta tag")


def attach_post_images(
    digest: Digest,
    timeout_s: float = 2.0,
    budget_s: float = 6.0,
    max_workers: int = 8,
    fetch=_http_get,
    log=print,
) -> None:
    """Fill ``notable.image`` where cheaply possible. Never raises.

    Images the pipeline already holds (via ``Notable.image`` from shaping)
    are kept as-is; only posts without one are fetched, in parallel, inside
    the wall-clock budget. Offline or blocked hosts simply keep the fallback.
    """
    targets = []
    for section in digest.sections:
        for notable in section.notable:
            if notable.image:
                continue
            host = (urlparse(notable.url).hostname or "").lower()
            if host in WALLED_HOSTS:
                continue
            targets.append(notable)
    if not targets:
        return

    deadline = monotonic() + budget_s

    def _try(notable) -> None:
        remaining = deadline - monotonic()
        if remaining <= 0.2:
            return
        try:
            notable.image = og_image(notable.url, timeout_s=min(timeout_s, remaining), fetch=fetch)
        except Exception as exc:  # noqa: BLE001 - any failure keeps the fallback tile
            log(f"thumbnail unavailable for {notable.url}: {exc}")

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(_try, targets))
    except Exception as exc:  # noqa: BLE001 - threading must never break a run
        log(f"thumbnail pass skipped ({exc})")