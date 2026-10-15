"""Archive path authority — GitHub Archive Layout V2.

New writes use:
  github/<owner>/<repo>/{README.md,source/,releases/<tag>/,offline/}

V1 trees under repositories/<github_repo_id>/ remain readable via stored
relative_path / archive_root and the v1_* helpers used by migration.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
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
_RELEASE_SOURCE_ZIP = "release-source.zip"
_RELEASE_SOURCE_TAR = "release-source.tar.gz"
_LAYOUT_MARKER = "github"


class PathLayout:
    def __init__(self, archive_root: Path | str) -> None:
        root = Path(archive_root)
        if not str(root).strip():
            raise UnsafePathError("Archive root is empty")
        self.archive_root = Path(os.path.normpath(str(root)))

    # --- roots / temp -------------------------------------------------

    def database_path(self) -> Path:
        return self._inside(self.archive_root / "data" / "techvault.sqlite")

    def logs_dir(self) -> Path:
        return self._inside(self.archive_root / "logs")

    def tmp_dir(self, job_id: str) -> Path:
        segment = _segment(str(job_id), field="job_id")
        return self._inside(self.archive_root / "_tmp" / segment)

    def temp_dir(self, job_id: str) -> Path:
        return self.tmp_dir(job_id)

    def part_file(self, job_id: str, file_id: int) -> Path:
        name = f"{_id_segment(file_id, field='file_id')}.part"
        return self._direct_child(self.tmp_dir(job_id) / name, self.tmp_dir(job_id))

    # --- repository roots (V2 + V1) -----------------------------------

    def repository_root(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.repository_dir(owner, repository, github_repo_id)

    def repository_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        """Canonical V2 directory for new writes (may add __<id> on collision)."""
        return self._inside(self.archive_root / self.repository_relpath(owner, repository, github_repo_id))

    def repository_relpath(self, owner: str, repository: str, github_repo_id: int) -> str:
        owner_seg = _segment(owner, field="owner")
        repo_seg = _segment(repository, field="repository")
        preferred = f"{_LAYOUT_MARKER}/{owner_seg}/{repo_seg}"
        return self._resolve_repo_relpath(preferred, github_repo_id)

    def repository_dir_from_relpath(self, storage_relpath: str) -> Path:
        rel = _normalize_rel(storage_relpath)
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise UnsafePathError("storage_relpath is unsafe")
        return self._inside(self.archive_root / Path(rel))

    def v1_repository_dir(self, github_repo_id: int) -> Path:
        repo_id = _id_segment(github_repo_id, field="github_repo_id")
        return self._inside(self.archive_root / "repositories" / repo_id)

    def legacy_repository_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        owner_segment = _segment(owner, field="owner")
        repo_segment = _segment(repository, field="repository")
        repo_id = _id_segment(github_repo_id, field="github_repo_id")
        return self._inside(
            self.archive_root / "repositories" / owner_segment / f"{repo_segment}__{repo_id}"
        )

    def resolve_existing_repository_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        *,
        storage_relpath: str | None = None,
    ) -> Path:
        """Prefer stored V2/V1 path, then on-disk V1, then allocated V2."""
        if storage_relpath:
            path = self.repository_dir_from_relpath(storage_relpath)
            if path.is_dir():
                return path
        v1 = self.v1_repository_dir(github_repo_id)
        if v1.is_dir():
            return v1
        legacy = self.legacy_repository_dir(owner, repository, github_repo_id)
        if legacy.is_dir():
            return legacy
        return self.repository_dir(owner, repository, github_repo_id)

    # --- README / offline ---------------------------------------------

    def readme_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.repository_dir(owner, repository, github_repo_id)

    def readme_file(self, owner: str, repository: str, github_repo_id: int) -> Path:
        directory = self.repository_dir(owner, repository, github_repo_id)
        return self._direct_child(directory / "README.md", directory)

    def readme_file_at(self, repo_root: Path) -> Path:
        root = self._inside(repo_root)
        return self._direct_child(root / "README.md", root)

    def pages_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.pages_dir_at(self.repository_dir(owner, repository, github_repo_id))

    def pages_dir_at(self, repo_root: Path) -> Path:
        return self._inside(self._inside(repo_root) / "offline")

    def page_index(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.page_index_at(self.repository_dir(owner, repository, github_repo_id))

    def page_index_at(self, repo_root: Path) -> Path:
        return self._inside(self.pages_dir_at(repo_root) / "index.html")

    def page_release(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.page_release_at(self.repository_dir(owner, repository, github_repo_id))

    def page_release_at(self, repo_root: Path) -> Path:
        return self._inside(self.pages_dir_at(repo_root) / "release.html")

    def page_media_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.page_media_dir_at(self.repository_dir(owner, repository, github_repo_id))

    def page_media_dir_at(self, repo_root: Path) -> Path:
        return self._inside(self.pages_dir_at(repo_root) / "media")

    def page_media_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        filename: str,
    ) -> Path:
        return self.page_media_file_at(
            self.repository_dir(owner, repository, github_repo_id),
            filename,
        )

    def page_media_file_at(self, repo_root: Path, filename: str) -> Path:
        safe_name = _segment(filename, field="media")
        directory = self.page_media_dir_at(repo_root)
        return self._direct_child(directory / safe_name, directory)

    # --- source snapshots ---------------------------------------------

    def source_dir(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self.source_dir_at(self.repository_dir(owner, repository, github_repo_id))

    def source_dir_at(self, repo_root: Path) -> Path:
        return self._inside(self._inside(repo_root) / "source")

    def snapshot_path(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        snapshot_id: int,
    ) -> Path:
        # Backward-compatible helper used by older tests; prefer snapshot_source_file.
        return self.snapshot_source_file(
            owner,
            repository,
            github_repo_id,
            snapshot_id,
            branch="main",
            commit_sha=None,
            when=None,
        )

    def snapshot_source_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        snapshot_id: int,
        *,
        branch: str = "main",
        commit_sha: str | None = None,
        when: datetime | None = None,
    ) -> Path:
        return self.snapshot_source_file_at(
            self.repository_dir(owner, repository, github_repo_id),
            snapshot_id,
            branch=branch,
            commit_sha=commit_sha,
            when=when,
        )

    def snapshot_source_file_at(
        self,
        repo_root: Path,
        snapshot_id: int,
        *,
        branch: str = "main",
        commit_sha: str | None = None,
        when: datetime | None = None,
    ) -> Path:
        directory = self.source_dir_at(repo_root)
        filename = snapshot_filename(branch, commit_sha=commit_sha, when=when, snapshot_id=snapshot_id)
        candidate = directory / filename
        if candidate.exists():
            stem = candidate.stem
            filename = f"{stem}-s{_id_segment(snapshot_id, field='snapshot_id')}.zip"
        return self._direct_child(directory / filename, directory)

    def v1_snapshot_source_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        snapshot_id: int,
    ) -> Path:
        directory = self._inside(
            self.v1_repository_dir(github_repo_id) / "source" / _id_segment(snapshot_id, field="snapshot_id")
        )
        return self._direct_child(directory / "source-code.zip", directory)

    # --- releases -----------------------------------------------------

    def release_root(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        *,
        tag_name: str | None = None,
    ) -> Path:
        if tag_name:
            return self.release_dir(owner, repository, github_repo_id, tag_name)
        # Legacy signature: numeric id — map to V1 location for compat readers.
        return self.v1_release_dir(github_repo_id, github_release_id)

    def release_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        tag_name: str,
    ) -> Path:
        return self.release_dir_at(self.repository_dir(owner, repository, github_repo_id), tag_name)

    def release_dir_at(self, repo_root: Path, tag_name: str) -> Path:
        tag = _tag_segment(tag_name)
        return self._inside(self._inside(repo_root) / "releases" / tag)

    def release_source_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        tag_name: str,
        *,
        kind: str = "zip",
    ) -> Path:
        return self.release_source_file_at(
            self.repository_dir(owner, repository, github_repo_id),
            tag_name,
            kind=kind,
        )

    def release_source_file_at(self, repo_root: Path, tag_name: str, *, kind: str = "zip") -> Path:
        directory = self.release_dir_at(repo_root, tag_name)
        name = _RELEASE_SOURCE_ZIP if kind == "zip" else _RELEASE_SOURCE_TAR
        return self._direct_child(directory / name, directory)

    def release_asset_file(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        tag_name: str,
        asset_id: int,
        asset_name: str,
    ) -> Path:
        return self.release_asset_file_at(
            self.repository_dir(owner, repository, github_repo_id),
            tag_name,
            asset_id,
            asset_name,
        )

    def release_asset_file_at(
        self,
        repo_root: Path,
        tag_name: str,
        asset_id: int,
        asset_name: str,
    ) -> Path:
        directory = self.release_dir_at(repo_root, tag_name)
        filename = _asset_filename(asset_name, asset_id, reserved={_RELEASE_SOURCE_ZIP, _RELEASE_SOURCE_TAR})
        return self._direct_child(directory / filename, directory)

    def v1_release_dir(self, github_repo_id: int, github_release_id: int) -> Path:
        release_id = _id_segment(github_release_id, field="github_release_id")
        return self._inside(self.v1_repository_dir(github_repo_id) / "releases" / release_id)

    # Deprecated attempt paths — kept only so migration can locate V1 files.
    def release_attempt_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        attempt_id: int,
    ) -> Path:
        return self._inside(
            self.v1_release_dir(github_repo_id, github_release_id)
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

    # Legacy V1 helpers still referenced by older tests / identity adopt.
    def repository_json(self, owner: str, repository: str, github_repo_id: int) -> Path:
        return self._inside(self.repository_dir(owner, repository, github_repo_id) / "repository.json")

    def release_json(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        *,
        tag_name: str | None = None,
    ) -> Path:
        if tag_name:
            directory = self.release_dir(owner, repository, github_repo_id, tag_name)
        else:
            directory = self.v1_release_dir(github_repo_id, github_release_id)
        return self._inside(directory / "release.json")

    def release_source_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        *,
        tag_name: str | None = None,
    ) -> Path:
        if tag_name:
            return self.release_dir(owner, repository, github_repo_id, tag_name)
        return self._inside(self.v1_release_dir(github_repo_id, github_release_id) / "source")

    def release_assets_dir(
        self,
        owner: str,
        repository: str,
        github_repo_id: int,
        github_release_id: int,
        *,
        tag_name: str | None = None,
    ) -> Path:
        if tag_name:
            return self.release_dir(owner, repository, github_repo_id, tag_name)
        return self._inside(self.v1_release_dir(github_repo_id, github_release_id) / "assets")

    # --- internals ----------------------------------------------------

    def _resolve_repo_relpath(self, preferred: str, github_repo_id: int) -> str:
        """Avoid Windows case collisions / reserved collisions with a stable suffix."""
        preferred_path = self.archive_root / Path(preferred)
        parent = preferred_path.parent
        name = preferred_path.name
        if not parent.is_dir():
            return preferred
        conflict = False
        for existing in parent.iterdir():
            if not existing.is_dir():
                continue
            if existing.name == name:
                # Same exact name — reuse (idempotent for same repo).
                return preferred
            if existing.name.casefold() == name.casefold():
                conflict = True
                break
        if _is_reserved(name):
            conflict = True
        if not conflict:
            return preferred
        owner_seg, repo_seg = preferred.split("/")[1], preferred.split("/")[2]
        return f"{_LAYOUT_MARKER}/{owner_seg}/{repo_seg}__{_id_segment(github_repo_id, field='github_repo_id')}"

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


def snapshot_filename(
    branch: str,
    *,
    commit_sha: str | None,
    when: datetime | None,
    snapshot_id: int,
) -> str:
    branch_seg = _segment(branch or "HEAD", field="branch")
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    short = None
    if commit_sha and re.fullmatch(r"[0-9a-fA-F]{7,40}", commit_sha):
        short = commit_sha[:7].lower()
    if short:
        return f"{branch_seg}-{short}-{stamp}.zip"
    return f"{branch_seg}-{stamp}.zip"


def repo_root_from_relative(relative_path: str) -> str | None:
    """Infer github/<owner>/<repo> or repositories/<id> from a stored relative path."""
    parts = Path(_normalize_rel(relative_path)).parts
    if len(parts) >= 3 and parts[0] == _LAYOUT_MARKER:
        return "/".join(parts[:3])
    if len(parts) >= 2 and parts[0] == "repositories":
        return "/".join(parts[:2])
    return None


def _asset_filename(asset_name: str, asset_id: int, *, reserved: set[str]) -> str:
    safe = _segment(asset_name, field="asset")
    if _is_reserved(safe):
        safe = f"_{safe}"
    if safe.casefold() in {item.casefold() for item in reserved}:
        safe = f"{_id_segment(asset_id, field='asset_id')}_{safe}"
    return safe


def _tag_segment(tag_name: str) -> str:
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise UnsafePathError("tag_name is empty")
    # Tags may contain '/' — flatten to a single segment.
    flattened = tag_name.replace("/", "_").replace("\\", "_")
    return _segment(flattened, field="tag_name")


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
    if len(segment) > 80:
        segment = segment[:80].rstrip(" .")
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


def _normalize_rel(value: str) -> str:
    return value.replace("\\", "/").strip("/")
