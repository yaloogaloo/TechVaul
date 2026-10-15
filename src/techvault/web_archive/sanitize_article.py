"""HTML sanitizer for archived article bodies (slightly richer than README)."""

from __future__ import annotations

import re

import nh3

from techvault.offline.sanitize import _strip_active_content

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
    "div",
    "span",
    "figure",
    "figcaption",
    "section",
    "sup",
    "sub",
}
_ACTIVE = ("script", "style", "iframe", "object", "embed", "form")
_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    "div": {"class"},
}


def sanitize_article_html(html: str | None) -> str:
    cleaned = _strip_active_content(html or "")
    return nh3.clean(
        cleaned,
        tags=_ALLOWED_TAGS,
        clean_content_tags=set(_ACTIVE),
        attributes=_ATTRS,
        url_schemes={"http", "https", "mailto", "data"},
        link_rel=None,
        strip_comments=True,
    )


def html_to_plain_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def html_to_markdown_rough(html: str, title: str) -> str:
    """Lightweight markdown export — not a full converter."""
    plain = html_to_plain_text(html)
    lines = [f"# {title}", "", plain]
    return "\n".join(lines).strip() + "\n"
