"""One sanitizer for README HTML and rendered release HTML."""

from __future__ import annotations

import re

import nh3

_ALLOWED_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "pre",
    "code",
    "blockquote",
    "img",
    "a",
    "strong",
    "em",
    "del",
    "br",
    "hr",
}
_ACTIVE_TAGS = ("script", "style", "iframe", "object", "embed", "form")
_PAIRED_ACTIVE = re.compile(
    r"<\s*(script|style|iframe|object|embed|form)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_ACTIVE = re.compile(
    r"<\s*(script|style|iframe|object|embed|form)\b[^>]*>.*",
    re.IGNORECASE | re.DOTALL,
)
_ALLOWED_ATTRIBUTES = {
    "a": {"href"},
    "img": {"src", "alt"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


def sanitize_html(html: str | None) -> str:
    cleaned = _strip_active_content(html or "")
    return nh3.clean(
        cleaned,
        tags=_ALLOWED_TAGS,
        clean_content_tags=set(_ACTIVE_TAGS),
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel=None,
        strip_comments=True,
    )


def _strip_active_content(html: str) -> str:
    current = html
    previous = None
    while current != previous:
        previous = current
        current = _PAIRED_ACTIVE.sub("", current)
    return _OPEN_ACTIVE.sub("", current)
