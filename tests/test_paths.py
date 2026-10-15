from pathlib import Path

import pytest

from techvault.archive.paths import PathLayout
from techvault.errors import ErrorCode, UnsafePathError


def test_repository_directory_uses_owner_and_name(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    repository = layout.repository_dir("Octo", "Hello", 123456789)
    assert repository == tmp_path / "github" / "Octo" / "Hello"
    # Rename does not relocate until storage_relpath is set — V2 preferred path follows names.
    renamed = layout.repository_dir("renamed", "other", 123456789)
    assert renamed == tmp_path / "github" / "renamed" / "other"
    assert layout.page_index("Octo", "Hello", 123456789) == repository / "offline" / "index.html"
    assert layout.page_release("Octo", "Hello", 123456789) == repository / "offline" / "release.html"
    assert layout.page_media_dir("Octo", "Hello", 123456789) == repository / "offline" / "media"
    assert layout.readme_file("Octo", "Hello", 123456789) == repository / "README.md"
    release = layout.release_dir("Octo", "Hello", 123456789, "v1.0.0")
    assert release == repository / "releases" / "v1.0.0"
    assert layout.release_source_file("Octo", "Hello", 123456789, "v1.0.0", kind="zip") == release / "release-source.zip"
    assert layout.database_path() == tmp_path / "data" / "techvault.sqlite"
    assert layout.logs_dir() == tmp_path / "logs"
    assert layout.tmp_dir("job-1") == tmp_path / "_tmp" / "job-1"
    assert repository.is_relative_to(layout.archive_root)
    assert "attempts" not in release.parts


@pytest.mark.parametrize("owner,repository", [("..", "hello"), ("octo", ".."), (r"octo\..\..", "hello")])
def test_parent_segments_are_rejected(tmp_path: Path, owner: str, repository: str) -> None:
    layout = PathLayout(tmp_path)
    with pytest.raises(UnsafePathError) as caught:
        layout.repository_dir(owner, repository, 1)
    assert caught.value.code == ErrorCode.UNSAFE_PATH


def test_windows_illegal_characters_are_sanitized(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    path = layout.release_asset_file("octo", "hello", 7, "v1", 3, 'my:repo*name"')
    assert "my_repo_name_" in path.name
    assert not any(char in path.name for char in '<>:"|?*')
    assert path.is_relative_to(layout.archive_root)


def test_reserved_device_names_are_rewritten(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    path = layout.page_media_file("octo", "hello", 4, "CON.txt")
    assert path.name.startswith("_")
    assert path.is_relative_to(layout.archive_root)


def test_paths_stay_inside_archive_root(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    path = layout.repository_dir("octo", "hello", 12)
    relative = path.relative_to(layout.archive_root)
    assert relative.parts[0] == "github"
    assert ".." not in relative.parts
    assert str(path).startswith(str(layout.archive_root))
    with pytest.raises(UnsafePathError):
        layout.tmp_dir("..")
    # Overlong asset names are truncated to a safe segment (not rejected).
    long_asset = layout.release_asset_file("octo", "hello", 1, "v1", 1, "a" * 300)
    assert len(long_asset.name) <= 80
    assert long_asset.is_relative_to(layout.archive_root)


def test_tag_slash_is_flattened(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    path = layout.release_dir("octo", "hello", 1, "feat/v1")
    assert path.name == "feat_v1"
