"""Physical layout for web archives under archive_root/web/."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from techvault.errors import UnsafePathError

_ILLEGAL = set('<>:"|?*')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MAX_TITLE = 60
_MAX_PATH = 240


class WebPathLayout:
    def __init__(self, archive_root: Path | str) -> None:
        root = Path(archive_root)
        if not str(root).strip():
            raise UnsafePathError("Archive root is empty")
        self.archive_root = Path(os.path.normpath(str(root)))

    def web_root(self) -> Path:
        return self._inside(self.archive_root / "web")

    def tmp_dir(self, job_id: str) -> Path:
        return self._inside(self.archive_root / "_tmp" / "web" / _segment(job_id, "job_id"))

    def article_dir(self, domain: str, title: str, stable_id: str) -> Path:
        rel = self.article_relpath(domain, title, stable_id)
        return self._inside(self.archive_root / rel)

    def article_relpath(self, domain: str, title: str, stable_id: str) -> str:
        domain_seg = _segment(domain, "domain")
        title_seg = _title_segment(title)
        id_seg = _segment(stable_id, "stable_id")
        return f"web/{domain_seg}/{title_seg}-{id_seg}"

    def article_html(self, article_root: Path) -> Path:
        root = self._inside(article_root)
        return self._child(root / "article.html", root)

    def article_md(self, article_root: Path) -> Path:
        root = self._inside(article_root)
        return self._child(root / "article.md", root)

    def metadata_json(self, article_root: Path) -> Path:
        root = self._inside(article_root)
        return self._child(root / "metadata.json", root)

    def media_dir(self, article_root: Path) -> Path:
        root = self._inside(article_root)
        return self._inside(root / "media")

    def media_file(self, article_root: Path, filename: str) -> Path:
        directory = self.media_dir(article_root)
        return self._child(directory / _segment(filename, "media"), directory)

    def versions_dir(self, article_root: Path) -> Path:
        root = self._inside(article_root)
        return self._inside(root / "versions")

    def version_dir(self, article_root: Path, stamp: str) -> Path:
        return self._inside(self.versions_dir(article_root) / _segment(stamp, "version"))

    def resolve_relpath(self, relpath: str) -> Path:
        rel = relpath.replace("\\", "/").strip("/")
        if not rel or ".." in Path(rel).parts:
            raise UnsafePathError("web archive relpath is unsafe")
        return self._inside(self.archive_root / Path(rel))

    def _inside(self, path: Path) -> Path:
        normalized = Path(os.path.normpath(str(path)))
        try:
            relative = normalized.relative_to(self.archive_root)
        except ValueError as exc:
            raise UnsafePathError("Path escapes the archive root") from exc
        if any(part == ".." for part in relative.parts):
            raise UnsafePathError("Path escapes the archive root")
        if len(str(normalized)) > _MAX_PATH:
            raise UnsafePathError("Path exceeds the Windows path length limit")
        return normalized

    def _child(self, path: Path, parent: Path) -> Path:
        normalized = self._inside(path)
        if normalized.parent != parent:
            raise UnsafePathError("Path escapes the article directory")
        return normalized


def stable_short_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return digest[:10]


def _title_segment(title: str) -> str:
    cleaned = _segment(title or "untitled", "title")
    if len(cleaned) > _MAX_TITLE:
        cleaned = cleaned[:_MAX_TITLE].rstrip(" ._-")
    return cleaned or "untitled"


def _segment(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UnsafePathError(f"{field} is empty")
    if value in {".", ".."}:
        raise UnsafePathError(f"{field} is empty")
    chars: list[str] = []
    for char in value.strip():
        if char in {"/", "\\"} or ord(char) < 32 or char in _ILLEGAL:
            chars.append("_")
        elif char.isspace():
            chars.append("-")
        else:
            chars.append(char)
    segment = re.sub(r"-{2,}", "-", "".join(chars)).strip(" ._-")
    if not segment or segment in {".", ".."}:
        raise UnsafePathError(f"{field} is empty after sanitization")
    stem = segment.split(".", 1)[0]
    if stem.upper() in _RESERVED:
        segment = f"_{segment}"
    return segment
