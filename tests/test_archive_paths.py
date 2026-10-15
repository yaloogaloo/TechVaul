"""Canonical archive paths use GitHub Archive Layout V2."""

from pathlib import Path

from techvault.archive.identity import resolve_stored_path
from techvault.archive.paths import PathLayout


def test_readme_snapshot_and_release_share_the_repository_root(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "archive")
    root = layout.repository_root("octo", "hello", 123)
    readme = layout.readme_file("octo", "hello", 123)
    snapshot = layout.snapshot_source_file("octo", "hello", 123, 4, branch="main")
    release = layout.release_dir("octo", "hello", 123, "v1.0.0")
    temporary = layout.temp_dir("job-1")
    assert readme.is_relative_to(root)
    assert snapshot.is_relative_to(root)
    assert release.is_relative_to(root)
    assert root.parts[-3:] == ("github", "octo", "hello")
    assert readme.name == "README.md"
    assert snapshot.parent.name == "source"
    assert release.name == "v1.0.0"
    assert temporary.is_relative_to(layout.archive_root)
    assert "repositories" not in temporary.relative_to(layout.archive_root).parts
    assert "attempts" not in release.parts


def test_release_files_use_tag_not_attempt_dirs(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "archive")
    zip_path = layout.release_source_file("octo", "hello", 123, "v1.14.0", kind="zip")
    asset = layout.release_asset_file("octo", "hello", 123, "v1.14.0", 9, "app.exe")
    assert zip_path.name == "release-source.zip"
    assert asset.name == "app.exe"
    assert "attempts" not in zip_path.parts
    assert zip_path.parent == asset.parent


def test_stored_archive_root_is_not_replaced_by_the_current_root(tmp_path: Path) -> None:
    historical = tmp_path / "historical"
    current = tmp_path / "current"
    relative = "github/octo/hello/offline/index.html"
    resolved = resolve_stored_path(str(historical), relative, current)
    assert resolved is not None
    assert resolved.is_relative_to(historical)
    assert not resolved.is_relative_to(current)


def test_v1_repository_dir_still_resolvable(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / "archive")
    v1 = layout.v1_repository_dir(1359648985)
    assert v1.parts[-2:] == ("repositories", "1359648985")
