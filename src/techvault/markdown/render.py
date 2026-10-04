"""Turn Markdown into HTML the desktop viewer can show.

Relative links and images resolve only against the caller's base directory.
They are never interpreted from the process working directory.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

_SAFE_SCHEMES = {"http", "https", "mailto"}

_ATTR = re.compile(r'(?P<attr>src|href)\s*=\s*"(?P<url>[^"]*)"', re.IGNORECASE)
_CHECKBOX = re.compile(r"<input\b[^>]*type=\"checkbox\"[^>]*>", re.IGNORECASE)

_parser = MarkdownIt("gfm-like", {"html": False, "linkify": False})
_parser.use(tasklists_plugin, enabled=True)


def rebase_html(html: str, *, base: Path | None = None) -> str:
    """Rewrite relative resources in HTML that was already rendered."""
    if not html.strip():
        return ""
    return _ATTR.sub(lambda match: _replace_attr(match, base), html)


def render_markdown(source: str, *, base: Path | None = None) -> str:
    if not isinstance(source, str) or source.strip() == "":
        return ""
    html = _parser.render(source)
    html = _CHECKBOX.sub(_checkbox_mark, html)
    return _ATTR.sub(lambda match: _replace_attr(match, base), html)


def _checkbox_mark(match: re.Match[str]) -> str:
    return "☑ " if "checked" in match.group(0).lower() else "☐ "


def _replace_attr(match: re.Match[str], base: Path | None) -> str:
    resolved = _resolve(match.group("url"), base)
    return f'{match.group("attr")}="{resolved}"'


def _resolve(url: str, base: Path | None) -> str:
    cleaned = url.strip()
    if cleaned.startswith("#"):
        return cleaned
    scheme = urlparse(cleaned).scheme.lower()
    if scheme in _SAFE_SCHEMES:
        return cleaned
    if scheme:
        return ""
    if _is_absolute(cleaned) or base is None:
        return ""
    root = base.resolve()
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return ""
    if not candidate.is_file():
        return ""
    return candidate.as_uri()


def _is_absolute(value: str) -> bool:
    if value.startswith(("/", "\\")):
        return True
    return len(value) > 2 and value[1] == ":"
