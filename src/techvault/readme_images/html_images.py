"""Collect README images from rendered HTML and rewrite them to the local pool.

Markdown is parsed first, so code fences stay text and are not treated as images.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

from techvault.readme_images.urls import resolve_readme_image_url

_parser = MarkdownIt("gfm-like", {"html": True, "linkify": False})
_parser.use(tasklists_plugin, enabled=True)
_CHECKBOX = re.compile(r"<input\b[^>]*type=\"checkbox\"[^>]*>", re.IGNORECASE)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


def render_markdown_body(source: str) -> str:
    html = _parser.render(source or "")
    return _CHECKBOX.sub(lambda match: "☑ " if "checked" in match.group(0).lower() else "☐ ", html)


def absolutize_images(
    html: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    allow_relative: bool,
) -> tuple[str, list[tuple[str, str | None]]]:
    """Rewrite image references to canonical remote URLs.

    Each outcome is ``("url", canonical)`` or ``("blocked", None)``.
    """
    outcomes: list[tuple[str, str | None]] = []
    pending: list[str | None] = [None]

    def on_tag(tag: str, attrs: list[tuple[str, str | None]]):
        name = tag.lower()
        if name == "source":
            pending[0] = _first_srcset(_attr(attrs, "srcset") or _attr(attrs, "src") or "")
            return attrs, ""
        if name != "img":
            return attrs, None
        raw = _pick_url(attrs, pending[0])
        pending[0] = None
        alt = _attr(attrs, "alt") or ""
        absolute = (
            resolve_readme_image_url(
                raw,
                owner=owner,
                repo=repo,
                branch=branch,
                allow_relative=allow_relative,
            )
            if raw
            else None
        )
        if absolute is None:
            outcomes.append(("blocked", None))
            return attrs, _placeholder("blocked", alt, "UNSAFE_URL")
        outcomes.append(("url", absolute))
        alt_attr = f' alt="{escape(alt, quote=True)}"' if alt else ""
        return attrs, f'<img src="{escape(absolute, quote=True)}"{alt_attr}>'

    return _walk(html, on_tag), outcomes


def rewrite_cached_images(html: str, resolve) -> str:
    """Replace remote image URLs using ``resolve(url) -> (state, value)``.

    ``state`` is ``media`` (value is a techvault URL), ``failed``, or ``pending``.
    """

    def on_tag(tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "img":
            return attrs, None
        src = _attr(attrs, "src") or ""
        alt = _attr(attrs, "alt") or ""
        if not src.lower().startswith(("http://", "https://")):
            return attrs, None
        state, value = resolve(src)
        if state == "media" and value:
            alt_attr = f' alt="{escape(alt, quote=True)}"' if alt else ""
            return attrs, f'<img src="{escape(value, quote=True)}"{alt_attr}>'
        return attrs, _placeholder(state, alt, value or "")

    return _walk(html, on_tag)


def _pick_url(attrs: list[tuple[str, str | None]], pending_source: str | None) -> str:
    src = (_attr(attrs, "src") or "").strip()
    data_src = (_attr(attrs, "data-src") or _attr(attrs, "data-original") or "").strip()
    srcset = (_attr(attrs, "srcset") or "").strip()
    if src and not src.lower().startswith("data:"):
        return src
    if data_src and not data_src.lower().startswith("data:"):
        return data_src
    if srcset:
        picked = _first_srcset(srcset)
        if picked:
            return picked
    if pending_source:
        return pending_source
    return src


def _first_srcset(value: str) -> str:
    for item in value.split(","):
        parts = item.strip().split()
        if parts:
            return parts[0]
    return ""


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    for key, value in attrs:
        if key.lower() == name:
            return value or ""
    return None


def _placeholder(state: str, alt: str, code: str) -> str:
    if state == "pending":
        label = "图片尚未缓存"
    else:
        label = "图片未能加载"
    if alt:
        label = f"{label}：{alt}"
    title = f' title="{escape(code, quote=True)}"' if code else ""
    return f'<span class="tv-img-missing"{title}>{escape(label)}</span>'


def _walk(html: str, on_tag) -> str:
    parser = _FragmentParser(on_tag)
    parser.feed(html or "")
    parser.close()
    return "".join(parser.parts)


class _FragmentParser(HTMLParser):
    def __init__(self, on_tag) -> None:
        super().__init__(convert_charrefs=True)
        self._on_tag = on_tag
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._emit(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _VOID:
            return
        self.parts.append(f"</{tag.lower()}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))

    def _emit(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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
