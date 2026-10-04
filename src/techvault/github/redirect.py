"""Authorization policy for GitHub redirects.

Token material is sent only to api.github.com. This decision is explicit and
does not depend on httpx keeping or dropping headers by itself.
"""

from __future__ import annotations

from urllib.parse import urlparse

API_HOST = "api.github.com"


def authorization_for_url(token: str | None, url: str) -> str | None:
    if not token:
        return None
    host = urlparse(url).hostname
    if host is not None and host.lower() == API_HOST:
        return f"Bearer {token}"
    return None
