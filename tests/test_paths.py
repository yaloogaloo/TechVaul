from pathlib import Path

import pytest

from techvault.archive.paths import PathLayout
from techvault.errors import ErrorCode, UnsafePathError


def test_repository_directory_uses_owner_name_and_id(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    repository = layout.repository_dir("Octo", "Hello", 123456789)
    assert repository == layout.repository_dir("renamed", "other", 123456789)
    assert repository == tmp_path / "repositories" / "123456789"
    assert layout.repository_json("Octo", "Hello", 123456789) == repository / "repository.json"
    assert layout.page_index("Octo", "Hello", 123456789) == repository / "pages" / "index.html"
    assert layout.page_release("Octo", "Hello", 123456789) == repository / "pages" / "release.html"
    assert layout.page_media_dir("Octo", "Hello", 123456789) == repository / "pages" / "media"
    release = layout.release_dir("Octo", "Hello", 123456789, 55)
    assert release == repository / "releases" / "55"
    assert layout.release_json("Octo", "Hello", 123456789, 55) == release / "release.json"
    assert layout.release_source_dir("Octo", "Hello", 123456789, 55) == release / "source"
    assert layout.release_assets_dir("Octo", "Hello", 123456789, 55) == release / "assets"
    assert layout.database_path() == tmp_path / "data" / "techvault.sqlite"
    assert layout.logs_dir() == tmp_path / "logs"
    assert layout.tmp_dir("job-1") == tmp_path / "_tmp" / "job-1"
    assert repository.is_relative_to(layout.archive_root)
    assert ".." not in repository.parts


@pytest.mark.parametrize("owner,repository", [("..", "hello"), ("octo", ".."), (r"octo\..\..", "hello")])
def test_parent_segments_are_rejected(tmp_path: Path, owner: str, repository: str) -> None:
    layout = PathLayout(tmp_path)
    with pytest.raises(UnsafePathError) as caught:
        layout.repository_dir(owner, repository, 1)
    assert caught.value.code == ErrorCode.UNSAFE_PATH


def test_windows_illegal_characters_are_sanitized(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    path = layout.release_asset_file("octo", "hello", 7, 1, 3, 'my:repo*name"')
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
    assert relative.parts[0] == "repositories"
    assert ".." not in relative.parts
    assert str(path).startswith(str(layout.archive_root))
    with pytest.raises(UnsafePathError):
        layout.tmp_dir("..")
    with pytest.raises(UnsafePathError):
        layout.release_asset_file("octo", "hello", 1, 1, 1, "a" * 300)
