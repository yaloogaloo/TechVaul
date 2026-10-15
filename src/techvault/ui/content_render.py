"""Safe README HTML composition for the Slate Web UI.

Pipeline: source → parse/rewrite → sanitize → display HTML.
Does not change on-disk archive format.
"""

from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin

from techvault.markdown.render import rebase_html
from techvault.offline.sanitize import sanitize_html

_parser = MarkdownIt("gfm-like", {"html": True, "linkify": False})
_parser.use(tasklists_plugin, enabled=True)
_CHECKBOX = re.compile(r"<input\b[^>]*type=\"checkbox\"[^>]*>", re.IGNORECASE)
_README_SECTION = re.compile(
    r"<h2>\s*README\s*</h2>(.*?)(?:<h2>\s*Current release\s*</h2>|<h2>\s*Assets\s*</h2>|$)",
    re.IGNORECASE | re.DOTALL,
)


def compose_readme_html(kind: str, body: str, *, base: Path | None = None) -> str:
    """Return sanitized HTML safe for WebEngine display."""
    if not body.strip():
        return ""
    if kind == "html":
        fragment = extract_readme_html_fragment(body) or body
        cleaned = sanitize_html(fragment)
        return rebase_html(cleaned, base=base)
    # markdown (and unknown): allow inline HTML then sanitize the full result
    rendered = _parser.render(body)
    rendered = _CHECKBOX.sub(_checkbox_mark, rendered)
    cleaned = sanitize_html(rendered)
    return rebase_html(cleaned, base=base)


def extract_readme_html_fragment(html: str) -> str:
    """Prefer README section until known offline-page sibling headings."""
    match = _README_SECTION.search(html)
    if not match:
        return ""
    fragment = match.group(1).strip()
    if "readme unavailable" in fragment.lower():
        return ""
    return fragment


def _checkbox_mark(match: re.Match[str]) -> str:
    return "☑ " if "checked" in match.group(0).lower() else "☐ "
