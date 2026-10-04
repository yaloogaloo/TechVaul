"""Parse a GitHub Link header. A missing next relation ends pagination."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

_LINK = re.compile(r"<([^>]*)>([^<]*)")
_REL_QUOTED = re.compile(r'rel\s*=\s*"([^"]*)"', re.IGNORECASE)
_REL_TOKEN = re.compile(r"rel\s*=\s*([^;\s]+)", re.IGNORECASE)


class PaginationLinkError(ValueError):
    """The Link header is present but cannot be used safely."""


def next_page_url(link_header: str | None, current_url: str) -> str | None:
    if link_header is None or link_header.strip() == "":
        return None
    matches = list(_LINK.finditer(link_header))
    if not matches:
        raise PaginationLinkError("GitHub Link header has no usable links")
    next_url: str | None = None
    for match in matches:
        target = match.group(1).strip()
        params = match.group(2)
        if "next" not in _rel_values(params):
            continue
        if target == "":
            raise PaginationLinkError("GitHub pagination next link is empty")
        absolute = urljoin(current_url, target)
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.github.com":
            raise PaginationLinkError("GitHub pagination next link is not an API URL")
        next_url = absolute
    return next_url


def _rel_values(params: str) -> set[str]:
    quoted = _REL_QUOTED.search(params)
    if quoted is not None:
        return set(quoted.group(1).split())
    token = _REL_TOKEN.search(params)
    if token is None:
        return set()
    return set(token.group(1).strip("'\"").split())
