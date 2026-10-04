"""Rewrite relative GitHub URLs onto the repository's online base URL.

Dangerous schemes are removed from URL attributes before a page is published.
The visible text stays. A single unsafe link does not reject the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

_ALLOWED_SCHEMES = {"http", "https", "mailto"}
_URL_ATTRIBUTES = {"href", "src", "action", "formaction", "poster", "cite", "background", "srcset"}
_UNSAFE_SCHEME = re.compile(r"^(javascript|vbscript|file|data)\s*:", re.IGNORECASE)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


@dataclass(frozen=True)
class BlockedUrl:
    """A URL attribute that was removed. `url` has no query string or userinfo."""

    scheme: str
    tag: str
    attribute: str
    url: str


def unsafe_scheme(url: str) -> str | None:
    """Return javascript, vbscript, file, or data when that scheme starts the URL."""
    match = _UNSAFE_SCHEME.match(url.strip())
    if match is None:
        return None
    return match.group(1).lower()


def describe_url(url: str) -> str:
    """Short public form of a URL. Query strings and userinfo are omitted."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    if scheme == "":
        return url.strip()[:48]
    host = parsed.hostname or ""
    path = parsed.path[:40]
    shown = f"{scheme}://{host}{path}" if host else f"{scheme}:{path}"
    return shown[:80]


def url_candidates(attribute: str, value: str) -> list[str]:
    if attribute != "srcset":
        return [value]
    candidates: list[str] = []
    for item in value.split(","):
        parts = item.strip().split()
        if parts:
            candidates.append(parts[0])
    return candidates


def neutralize_unsafe_urls(html: str) -> tuple[str, tuple[BlockedUrl, ...]]:
    """Drop unsafe URL attributes. Keep link text. Replace unsafe images with text."""
    blocked: list[BlockedUrl] = []

    def on_tag(tag: str, attrs: list[tuple[str, str | None]]) -> tuple[list[tuple[str, str | None]], str | None]:
        name = tag.lower()
        kept: list[tuple[str, str | None]] = []
        blocked_image = False
        for key, value in attrs:
            lower = key.lower()
            if value is not None and lower in _URL_ATTRIBUTES:
                scheme = next(
                    (
                        found
                        for found in (unsafe_scheme(candidate) for candidate in url_candidates(lower, value))
                        if found
                    ),
                    None,
                )
                if scheme is not None:
                    blocked.append(BlockedUrl(scheme, name, lower, describe_url(value)))
                    if name == "img" and lower == "src":
                        blocked_image = True
                    continue
            kept.append((key, value))
        if blocked_image:
            return kept, "[Blocked unsafe image]"
        return kept, None

    return _walk(html, on_tag), tuple(blocked)


def repository_base(repository_url: str) -> str:
    base = repository_url.strip()
    if not base.endswith("/"):
        base += "/"
    return base


def absolutize(url: str, repository_url: str) -> str | None:
    value = url.strip()
    if value == "":
        return None
    if value.startswith("#"):
        return value
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme in _ALLOWED_SCHEMES:
        return value
    if scheme:
        return None
    if value.startswith("//"):
        return "https:" + value
    base = repository_base(repository_url)
    if value.startswith("/"):
        return urljoin(base, value.lstrip("/"))
    return urljoin(base, value)


def rewrite_github_urls(html: str, repository_url: str) -> str:
    def on_tag(tag: str, attrs: list[tuple[str, str | None]]) -> tuple[list[tuple[str, str | None]], str | None]:
        rewritten: list[tuple[str, str | None]] = []
        for key, value in attrs:
            if key.lower() in {"href", "src"} and value is not None:
                absolute = absolutize(value, repository_url)
                if absolute is None:
                    continue
                rewritten.append((key, absolute))
            else:
                rewritten.append((key, value))
        return rewritten, None

    return _walk(html, on_tag)


def _walk(html: str, on_tag) -> str:
    parser = _FragmentParser(on_tag)
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


class _FragmentParser(HTMLParser):
    def __init__(self, on_tag) -> None:
        super().__init__(convert_charrefs=True)
        self._on_tag = on_tag
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs, end=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs, end=False)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _VOID:
            return
        self.parts.append(f"</{tag.lower()}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def _emit(self, tag: str, attrs: list[tuple[str, str | None]], end: bool) -> None:
        del end
        new_attrs, replacement = self._on_tag(tag, attrs)
        if replacement is not None:
            self.parts.append(replacement)
            return
        name = tag.lower()
        chunks = [f"<{name}"]
        for key, value in new_attrs:
            if value is None:
                chunks.append(f" {key}")
            else:
                chunks.append(f' {key}="{escape(value, quote=True)}"')
        chunks.append(">")
        self.parts.append("".join(chunks))
