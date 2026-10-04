"""Final check before an offline page is allowed onto disk.

Dangerous schemes are rejected only when they remain in a URL attribute.
Ordinary text such as "NFT Data:" is not a URL and does not fail the page.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from techvault.offline.links import unsafe_scheme, url_candidates

_FORBIDDEN_TAGS = {"script", "iframe", "object", "embed", "form", "style"}
_URL_ATTRIBUTES = {"href", "src", "action", "formaction", "poster", "cite", "background", "srcset"}


class HtmlIntegrityError(Exception):
    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


def assert_offline_html(html: str, media_files: dict[str, Path]) -> None:
    violations = _violations(html, media_files)
    if violations:
        raise HtmlIntegrityError(violations)


def _violations(html: str, media_files: dict[str, Path]) -> list[str]:
    found: list[str] = []
    parser = _GateParser(media_files, found)
    parser.feed(html)
    parser.close()
    return found


class _GateParser(HTMLParser):
    def __init__(self, media_files: dict[str, Path], found: list[str]) -> None:
        super().__init__(convert_charrefs=True)
        self._media = media_files
        self._found = found

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in _FORBIDDEN_TAGS:
            self._found.append(f"forbidden tag {name}")
        values = {key.lower(): value for key, value in attrs}
        for key, value in values.items():
            if key.startswith("on"):
                self._found.append(f"event handler {key}")
            if key in {"style", "class", "id"}:
                self._found.append(f"forbidden attribute {key}")
            if value and key in _URL_ATTRIBUTES:
                for candidate in url_candidates(key, value):
                    scheme = unsafe_scheme(candidate)
                    if scheme is not None:
                        self._found.append(f"dangerous URL scheme={scheme} attribute={key}")
        if name == "link":
            rel = (values.get("rel") or "").lower()
            if "stylesheet" in rel:
                self._found.append("external CSS")
            else:
                self._found.append("external link element")
        if name == "img":
            src = values.get("src") or ""
            path = self._media.get(src)
            if path is None or not _local_media_src(src) or not path.is_file():
                self._found.append("remote or missing image")


def _local_media_src(src: str) -> bool:
    if not src.startswith("media/"):
        return False
    name = src[len("media/") :]
    return name != "" and "/" not in name and "\\" not in name and ".." not in name and not name.startswith("/")
