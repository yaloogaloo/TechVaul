"""Canonical archive paths stay on one repository root."""

from pathlib import Path

from techvault.archive.identity import resolve_stored_path
from techvault.archive.paths import PathLayout


def test_readme_snapshot_and_release_share_the_repository_root(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "archive")
    root = layout.repository_root("octo", "hello", 123)
    readme = layout.readme_file("octo", "hello", 123)
    snapshot = layout.snapshot_path("octo", "hello", 123, 4)
    release = layout.release_root("octo", "hello", 123, 9)
    temporary = layout.temp_dir("job-1")
    assert readme.is_relative_to(root)
    assert snapshot.is_relative_to(root)
    assert release.is_relative_to(root)
    assert root == layout.repository_root("renamed", "other-name", 123)
    assert "octo" not in root.parts
    assert "hello" not in root.parts
    for path in (readme, snapshot, release):
        parts = path.relative_to(layout.archive_root).parts
        assert parts.count("repositories") == 1
        assert "repository" not in parts
        assert all(left != right for left, right in zip(parts, parts[1:]))
    assert temporary.is_relative_to(layout.archive_root)
    assert "repositories" not in temporary.relative_to(layout.archive_root).parts


def test_stored_archive_root_is_not_replaced_by_the_current_root(tmp_path: Path) -> None:
    historical = tmp_path / "historical"
    current = tmp_path / "current"
    relative = "repositories/123/pages/index.html"
    resolved = resolve_stored_path(str(historical), relative, current)
    assert resolved is not None
    assert resolved.is_relative_to(historical)
    assert not resolved.is_relative_to(current)
