"""The only archive path authority.

New files are placed under repositories/<github_repo_id>/.
legacy_repository_dir() exists only so an older tree can be adopted.
Business code asks this object for a path instead of joining fragments itself.
"""

from __future__ import annotations

import os
from pathlib import Path

from techvault.errors import UnsafePathError

_MAX_PATH_LENGTH = 240
_ILLEGAL = set('<>:"|?*')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class PathLayout:
    def __init__(self, archive_root: Path | str) -> None:
        root = Path(archive_root)
        if not str(root).strip():
            raise UnsafePathError("Archive root is empty")
        self.archive_root = Path(os.path.normpath(str(root)))

    def database_path(self) -> Path:
        return self._inside(self.archive_root / "data" / "techvault.sqlite")

    def logs_dir(self) -> Path:
        return self._inside(self.archive_root / "logs")

    def tmp_dir(self, job_id: str) -> Path:
        segment = _segment(str(job_id), field="job_id")
        return self._inside(self.archive_root / "_tmp" / segment)

    def repository_root(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.repository_dir(owner, repository, github_repo_id)

    def readme_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(self.repository_root(owner, repository, github_repo_id) / "readme")

    def readme_file(self, owner: str, repository: str, github_repo_id: int) -> Path:
        directory = self.readme_dir(owner, repository, github_repo_id)
        return self._direct_child(directory / "README.md", directory)

    def snapshot_path(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        snapshot_id: int,
    ) -> Path:
        return self.snapshot_source_file(owner, repository, github_repo_id, snapshot_id)

    def release_root(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
    ) -> Path:
        return self.release_dir(owner, repository, github_repo_id, github_release_id)

    def temp_dir(self, job_id: str) -> Path:
        return self.tmp_dir(job_id)

    def repository_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        _segment(owner, field="owner")
        _segment(repository, field="repository")
        repo_id = _id_segment(github_repo_id, field="github_repo_id")
        return self._inside(self.archive_root / "repositories" / repo_id)

    def legacy_repository_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        owner_segment = _segment(owner, field="owner")
        repo_segment = _segment(repository, field="repository")
        repo_id = _id_segment(github_repo_id, field="github_repo_id")
        return self._inside(
            self.archive_root / "repositories" / owner_segment / f"{repo_segment}__{repo_id}"
        )

    def repository_json(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(
            self.repository_dir(owner, repository, github_repo_id) / "repository.json"
        )

    def pages_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(self.repository_dir(owner, repository, github_repo_id) / "pages")

    def page_index(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(self.pages_dir(owner, repository, github_repo_id) / "index.html")

    def page_release(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(
            self.pages_dir(owner, repository, github_repo_id) / "release.html"
        )

    def page_media_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(self.pages_dir(owner, repository, github_repo_id) / "media")

    def page_media_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        filename: str,
    ) -> Path:
        safe_name = _segment(filename, field="media")
        directory = self.page_media_dir(owner, repository, github_repo_id)
        return self._direct_child(directory / safe_name, directory)

    def snapshot_source_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        snapshot_id: int,
    ) -> Path:
        directory = self._inside(
            self.repository_dir(owner, repository, github_repo_id)
            / "source"
            / _id_segment(snapshot_id, field="snapshot_id")
        )
        return self._direct_child(directory / "source-code.zip", directory)

    def release_attempt_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        attempt_id: int,
    ) -> Path:
        return self._inside(
            self.release_dir(owner, repository, github_repo_id, github_release_id)
            / "attempts"
            / _id_segment(attempt_id, field="attempt_id")
        )

    def release_attempt_source_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        attempt_id: int,
        filename: str,
    ) -> Path:
        if filename not in {"source-code.zip", "source-code.tar.gz"}:
            raise UnsafePathError("Source archive filename is not allowed")
        directory = self._inside(
            self.release_attempt_dir(owner, repository, github_repo_id, github_release_id, attempt_id)
            / "source"
        )
        return self._direct_child(directory / filename, directory)

    def release_attempt_asset_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        attempt_id: int,
        asset_id: int,
        asset_name: str,
    ) -> Path:
        safe_name = _segment(asset_name, field="asset")
        prefix = _id_segment(asset_id, field="asset_id")
        filename = f"{prefix}_{safe_name}"
        if _is_reserved(filename):
            filename = f"_{filename}"
        directory = self._inside(
            self.release_attempt_dir(owner, repository, github_repo_id, github_release_id, attempt_id)
            / "assets"
        )
        return self._direct_child(directory / filename, directory)

    def release_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
    ) -> Path:
        release_id = _id_segment(github_release_id, field="github_release_id")
        return self._inside(
            self.repository_dir(owner, repository, github_repo_id) / "releases" / release_id
        )

    def release_json(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
    ) -> Path:
        return self._inside(
            self.release_dir(owner, repository, github_repo_id, github_release_id)
            / "release.json"
        )

    def release_source_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
    ) -> Path:
        return self._inside(
            self.release_dir(owner, repository, github_repo_id, github_release_id) / "source"
        )

    def release_assets_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
    ) -> Path:
        return self._inside(
            self.release_dir(owner, repository, github_repo_id, github_release_id) / "assets"
        )

    def release_source_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        filename: str,
    ) -> Path:
        if filename not in {"source-code.zip", "source-code.tar.gz"}:
            raise UnsafePathError("Source archive filename is not allowed")
        path = self.release_source_dir(owner, repository, github_repo_id, github_release_id) / filename
        return self._direct_child(
            path,
            self.release_source_dir(owner, repository, github_repo_id, github_release_id),
        )

    def release_asset_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        asset_id: int,
        asset_name: str,
    ) -> Path:
        safe_name = _segment(asset_name, field="asset")
        prefix = _id_segment(asset_id, field="asset_id")
        filename = f"{prefix}_{safe_name}"
        if _is_reserved(filename):
            filename = f"_{filename}"
        directory = self.release_assets_dir(owner, repository, github_repo_id, github_release_id)
        return self._direct_child(directory / filename, directory)

    def part_file(self, job_id: str, file_id: int) -> Path:
        name = f"{_id_segment(file_id, field='file_id')}.part"
        return self._direct_child(self.tmp_dir(job_id) / name, self.tmp_dir(job_id))

    def _inside(self, path: Path) -> Path:
        normalized = Path(os.path.normpath(str(path)))
        try:
            relative = normalized.relative_to(self.archive_root)
        except ValueError as exc:
            raise UnsafePathError("Path escapes the archive root") from exc
        if any(part == ".." for part in relative.parts):
            raise UnsafePathError("Path escapes the archive root")
        if len(str(normalized)) > _MAX_PATH_LENGTH:
            raise UnsafePathError("Path exceeds the Windows path length limit")
        return normalized

    def _direct_child(self, path: Path, parent: Path) -> Path:
        normalized = self._inside(path)
        if normalized.parent != parent:
            raise UnsafePathError("Path escapes the release directory")
        return normalized


def _segment(value: str, *, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise UnsafePathError(f"{field} is empty")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise UnsafePathError(f"{field} contains a path separator")
    if ".." in Path(value).parts:
        raise UnsafePathError(f"{field} contains '..'")
    cleaned: list[str] = []
    for char in value:
        if ord(char) < 32 or char in _ILLEGAL:
            cleaned.append("_")
        else:
            cleaned.append(char)
    segment = "".join(cleaned).rstrip(" .")
    if segment in {"", ".", ".."}:
        raise UnsafePathError(f"{field} is empty after sanitization")
    if _is_reserved(segment):
        segment = f"_{segment}"
    return segment


def _is_reserved(segment: str) -> bool:
    stem = segment.split(".", 1)[0]
    return stem.upper() in _RESERVED


def _id_segment(value: int, *, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UnsafePathError(f"{field} must be a non-negative integer")
    return str(value)
