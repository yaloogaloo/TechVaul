"""Readable video archive paths. Staging stays outside the published tree."""

from __future__ import annotations

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
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_PLATFORMS = {"youtube", "bilibili"}
_ID = re.compile(r"^[0-9A-Za-z_-]{6,20}$")
_PREVIEW = re.compile(r"^video-preview/([0-9a-f]{16,32})\.(jpg|jpeg|png|webp)$")
_COVER = re.compile(
    r"^video-cover/(youtube|bilibili)/([0-9A-Za-z_-]{6,20})(?:/(p[1-9][0-9]{0,3}))?/(cover\.(?:jpg|jpeg|png|webp))$"
)


class VideoPathLayout:
    def __init__(self, archive_root: Path | str) -> None:
        root = Path(archive_root)
        if not str(root).strip():
            raise UnsafePathError("Archive root is empty")
        self.archive_root = Path(os.path.normpath(str(root)))

    def staging_dir(self, platform: str, video_id: str, part_key: str) -> Path:
        self._check_identity(platform, video_id)
        part = part_key or "_"
        if not re.fullmatch(r"[0-9]{0,4}", part) and part != "_":
            raise UnsafePathError("part key is not safe")
        return self._inside(self.archive_root / "_tmp" / "video" / platform / video_id / part)

    def item_dir(self, platform: str, video_id: str, part_key: str) -> Path:
        self._check_identity(platform, video_id)
        base = self.archive_root / "videos" / platform / video_id
        if part_key:
            if not re.fullmatch(r"[1-9][0-9]{0,3}", part_key):
                raise UnsafePathError("part key is not safe")
            base = base / f"p{part_key}"
        return self._inside(base)

    def preview_file(self, token: str, extension: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16,32}", token):
            raise UnsafePathError("preview token is not safe")
        if extension not in {"jpg", "jpeg", "png", "webp"}:
            raise UnsafePathError("preview extension is not safe")
        return self._inside(self.archive_root / "_tmp" / "video-preview" / f"{token}.{extension}")

    def relative(self, path: Path) -> str:
        resolved = self._inside(path)
        return resolved.relative_to(self.archive_root).as_posix()

    def resolve_relative(self, relative: str) -> Path:
        if not relative or relative.startswith(("/", "\\")):
            raise UnsafePathError("relative path is empty")
        return self._inside(self.archive_root / Path(relative))

    def _check_identity(self, platform: str, video_id: str) -> None:
        if platform not in _PLATFORMS or _ID.fullmatch(video_id or "") is None:
            raise UnsafePathError("video identity is not safe")

    def _inside(self, path: Path) -> Path:
        resolved = Path(os.path.normpath(str(path)))
        try:
            resolved.relative_to(self.archive_root)
        except ValueError as exc:
            raise UnsafePathError("path escapes the archive root") from exc
        return resolved


def safe_title(title: str) -> str:
    cleaned = []
    for char in (title or "").strip():
        if ord(char) < 32 or char in _ILLEGAL or char in {"/", "\\"}:
            cleaned.append("_")
        else:
            cleaned.append(char)
    text = "".join(cleaned).rstrip(" .")
    if len(text) > 80:
        text = text[:80].rstrip(" .")
    if text in {"", ".", ".."}:
        text = "video"
    if text.split(".", 1)[0].upper() in _RESERVED:
        text = f"_{text}"
    return text


def resolve_preview_file(archive_root: Path, url_path: str) -> Path | None:
    match = _PREVIEW.fullmatch(url_path.lstrip("/"))
    if match is None:
        return None
    try:
        return VideoPathLayout(archive_root).preview_file(match.group(1), match.group(2))
    except UnsafePathError:
        return None


def resolve_cover_file(archive_root: Path, url_path: str) -> Path | None:
    match = _COVER.fullmatch(url_path.lstrip("/"))
    if match is None:
        return None
    platform, video_id, part, name = match.groups()
    try:
        directory = VideoPathLayout(archive_root).item_dir(platform, video_id, "" if part is None else part[1:])
    except UnsafePathError:
        return None
    return directory / name
