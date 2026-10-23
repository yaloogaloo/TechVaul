"""Map a techvault media URL path onto one content-addressed file."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_FILE = re.compile(r"^([0-9a-f]{64})\.(png|jpg|gif|webp|svg|ico|bmp|avif)$")


def resolve_media_file(root: Path, url_path: str) -> tuple[Path, str] | None:
    """Return ``(file, sha256)`` when ``url_path`` is ``/media/<sha>.<ext>``."""
    relative = (url_path or "").strip().lstrip("/")
    if not relative.startswith("media/"):
        return None
    name = relative[len("media/") :]
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    match = _FILE.fullmatch(name)
    if match is None:
        return None
    sha256, extension = match.group(1), match.group(2)
    path = (root / "objects" / sha256[:2] / f"{sha256}.{extension}").resolve()
    objects = (root / "objects").resolve()
    try:
        path.relative_to(objects)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path, sha256


def file_matches_hash(path: Path, sha256: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest() == sha256
