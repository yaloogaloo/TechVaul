"""Repository category assignment and archived-project counts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from techvault.app.bootstrap import prepare_context
from techvault.app.library import LibraryService
from techvault.errors import DatabaseError
from techvault.ui.bridge.github_bridge import GitHubBridge

NOW = "2026-01-01T00:00:00+00:00"
SHA = "ab" * 32


def _repo(connection, github_id: int, name: str, *, description: str = "tool") -> int:
    connection.execute(
        """
        INSERT INTO repositories (
            github_repo_id, owner, name, full_name, html_url, description,
            archive_status, created_at, updated_at
        ) VALUES (?, 'octo', ?, ?, ?, ?, 'NOT_ARCHIVED', ?, ?)
        """,
        (
            github_id,
            name,
            f"octo/{name}",
            f"https://github.com/octo/{name}",
            description,
            NOW,
            NOW,
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def _snapshot(connection, repository_id: int, *, state: str = "COMPLETED", current: bool = True) -> int:
    connection.execute(
        """
        INSERT INTO source_snapshots (
            repository_id, ref, archived_at, state, local_sha256, created_at, updated_at
        ) VALUES (?, 'main', ?, ?, ?, ?, ?)
        """,
        (repository_id, NOW, state, SHA if state == "COMPLETED" else None, NOW, NOW),
    )
    snapshot_id = int(connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    if current:
        connection.execute(
            "UPDATE repositories SET current_snapshot_id = ? WHERE id = ?",
            (snapshot_id, repository_id),
        )
    return snapshot_id


def _release_file(connection, repository_id: int, *, state: str = "COMPLETED", files: int = 1) -> None:
    connection.execute(
        """
        INSERT INTO releases (
            github_release_id, repository_id, tag_name, html_url, lookup_outcome,
            created_at_local, updated_at_local
        ) VALUES (?, ?, 'v1', 'https://github.com/octo/r/releases/tag/v1', 'STABLE', ?, ?)
        """,
        (repository_id * 100, repository_id, NOW, NOW),
    )
    release_id = int(connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
    for index in range(files):
        connection.execute(
            """
            INSERT INTO release_files (
                release_id, kind, github_asset_id, name, state, local_sha256, created_at, updated_at
            ) VALUES (?, 'ASSET', ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                index + 1,
                f"file-{index}.zip",
                state,
                SHA if state == "COMPLETED" else None,
                NOW,
                NOW,
            ),
        )
    connection.execute(
        "UPDATE repositories SET current_release_id = ? WHERE id = ?",
        (release_id, repository_id),
    )


def test_database_path_uses_project_data_and_keeps_a_legacy_library(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TECHVAULT_DATA_DIR", raising=False)
    monkeypatch.delenv("TECHVAULT_DATABASE", raising=False)
    from techvault.app.bootstrap import default_data_dir, default_database_path, resolve_database_path
    from techvault.readme_images.paths import resolve_readme_image_root

    checkout = Path(__file__).resolve().parents[1]
    assert default_data_dir() == checkout / "data"

    legacy = tmp_path / "legacy" / "techvault.sqlite"
    missing = tmp_path / "missing-data"
    assert resolve_database_path(missing, legacy_database=legacy) == missing / "techvault.sqlite"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy")
    assert resolve_database_path(missing, legacy_database=legacy) == legacy
    current = tmp_path / "data" / "techvault.sqlite"
    current.parent.mkdir()
    current.write_bytes(b"current")
    assert resolve_database_path(current.parent, legacy_database=legacy) == current
    assert resolve_readme_image_root(current).name == "github_readme_images"

    isolated = tmp_path / "isolated"
    monkeypatch.setenv("TECHVAULT_DATA_DIR", str(isolated))
    assert default_data_dir() == isolated
    assert default_database_path() == isolated / "techvault.sqlite"
    assert not default_database_path().exists()


def test_category_assignment_filter_rename_delete_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    context = prepare_context(database, tmp_path / "archive")
    closed = False
    try:
        library = LibraryService(context.connection)
        voice = _repo(context.connection, 1, "VoiceStudio", description="voice")
        other = _repo(context.connection, 2, "Other", description="notes")
        _snapshot(context.connection, voice)
        _snapshot(context.connection, other)
        category = library.create_category("AI 工具")
        library.set_repository_category(voice, category.id)
        assert library.list_repositories(category_id=category.id)[0].name == "VoiceStudio"
        assert library.list_repositories(uncategorized=True)[0].name == "Other"
        library.rename_category(category.id, "AI 工具箱")
        assert library.get_repository(voice).category_name == "AI 工具箱"
        library.set_repository_category(voice, None)
        assert library.get_repository(voice).category_id is None
        library.set_repository_category(voice, category.id)
        library.delete_category(category.id)
        assert library.get_repository(voice).category_id is None
        with pytest.raises(DatabaseError):
            library.set_repository_category(voice, 999)
        assert library.get_repository(voice).category_id is None
        kept = library.create_category("保留")
        library.set_repository_category(other, kept.id)
        context.close()
        closed = True
        reopened = prepare_context(database, tmp_path / "archive")
        try:
            again = LibraryService(reopened.connection).get_repository(other)
            assert again is not None
            assert again.category_name == "保留"
        finally:
            reopened.close()
    finally:
        if not closed:
            context.close()


def test_archived_count_ignores_snapshots_releases_metadata_and_pages(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        library = LibraryService(context.connection)
        assert library.count_archived_repositories() == (0, 0)

        only_meta = _repo(context.connection, 1, "meta")
        downloading = _repo(context.connection, 2, "busy")
        _snapshot(context.connection, downloading, state="DOWNLOADING")
        partial = _repo(context.connection, 3, "partial")
        _release_file(context.connection, partial, state="FAILED")
        assert library.count_archived_repositories() == (0, 0)

        voice = _repo(context.connection, 4, "VoiceStudio", description="voice studio")
        _snapshot(context.connection, voice)
        _snapshot(context.connection, voice, current=False)
        _release_file(context.connection, voice, files=3)
        other = _repo(context.connection, 5, "Notes", description="notebook")
        _snapshot(context.connection, other)
        total, matched = library.count_archived_repositories()
        assert total == 2
        assert matched == 2
        listed = library.list_repositories()
        assert len(listed) == 5
        assert {item.name for item in listed} >= {"meta", "busy", "partial", "VoiceStudio", "Notes"}
        assert all(item.category_id is None for item in listed)
        page = library.list_repositories()[:1]
        assert len(page) == 1
        assert library.count_archived_repositories()[0] == 2

        category = library.create_category("AI 工具")
        library.set_repository_category(voice, category.id)
        assert library.count_archived_repositories(category_id=category.id) == (2, 1)
        assert library.count_archived_repositories(query="voice") == (2, 1)
        assert library.count_archived_repositories(uncategorized=True) == (2, 1)
        assert library.list_repositories(category_id=category.id)[0].id == voice
        del only_meta
    finally:
        context.close()


def test_legacy_repository_remains_listed_when_it_is_not_archived(tmp_path: Path, qapp) -> None:
    del qapp
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        repo_id = _repo(context.connection, 3, "Legacy")
        bridge = GitHubBridge(context.connection, tmp_path / "db.sqlite")
        listed = json.loads(bridge.listRepositories("", "", "recent"))
        assert listed["ok"] is True
        assert listed["data"]["hasRepositories"] is True
        assert listed["data"]["archivedCount"] == 0
        assert listed["data"]["items"][0]["id"] == repo_id
        assert listed["data"]["items"][0]["categoryId"] is None
        failed = json.loads(bridge.listRepositories("", "not-a-category", "recent"))
        assert failed["ok"] is False
        assert "items" not in (failed.get("data") or {})
    finally:
        context.close()


def test_bridge_category_failure_does_not_change_the_repository(tmp_path: Path, qapp) -> None:
    del qapp
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        library = LibraryService(context.connection)
        repo_id = _repo(context.connection, 9, "VoiceStudio")
        _snapshot(context.connection, repo_id)
        category = library.create_category("AI 工具")
        bridge = GitHubBridge(context.connection, tmp_path / "db.sqlite")
        saved = json.loads(bridge.setRepositoryCategory(repo_id, str(category.id)))
        assert saved["ok"] is True
        assert saved["data"]["categoryName"] == "AI 工具"
        failed = json.loads(bridge.setRepositoryCategory(repo_id, "999"))
        assert failed["ok"] is False
        assert library.get_repository(repo_id).category_id == category.id
        cleared = json.loads(bridge.setRepositoryCategory(repo_id, "none"))
        assert cleared["ok"] is True
        assert library.get_repository(repo_id).category_id is None
        listed = json.loads(bridge.listRepositories("", str(category.id), "name"))
        assert listed["data"]["archivedCount"] == 1
        assert listed["data"]["matchedArchivedCount"] == 0
        assert listed["data"]["items"] == []
    finally:
        context.close()
