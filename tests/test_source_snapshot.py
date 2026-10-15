"""Source snapshots, the local repository library, and download progress."""

from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Event

import httpx
import pytest
import respx

from techvault.app.archive_service import ArchiveService
from techvault.app.bootstrap import prepare_context
from techvault.app.library import LibraryService
from techvault.app.messages import user_message
from techvault.archive.progress import TransferPlan
from techvault.ui.bridge.github_bridge import GitHubBridge

REPO = "https://api.github.com/repos/octo/empty"
LATEST = "https://api.github.com/repos/octo/empty/releases/latest"
LIST = "https://api.github.com/repos/octo/empty/releases?per_page=100"
ZIP = "https://api.github.com/repos/octo/empty/zipball/main"
README = "https://api.github.com/repos/octo/empty/readme"
BODY = b"PK\x03\x04snapshot"


def _repository(name: str = "empty") -> dict:
    return {
        "id": 50,
        "name": name,
        "full_name": f"octo/{name}",
        "html_url": f"https://github.com/octo/{name}",
        "description": "A sample tool",
        "stargazers_count": 2,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "octo"},
        "topics": ["python", "cli"],
    }


def _install_no_release(zip_body: bytes = BODY, zip_status: int = 200) -> None:
    respx.get(REPO).mock(return_value=httpx.Response(200, json=_repository()))
    respx.get(LATEST).mock(return_value=httpx.Response(404))
    respx.get(LIST).mock(return_value=httpx.Response(200, json=[]))
    respx.get(README).mock(return_value=httpx.Response(404))
    respx.get(ZIP).mock(return_value=httpx.Response(zip_status, content=zip_body))


def test_no_release_repository_archives_source_snapshot(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        with respx.mock:
            _install_no_release()
            result = ArchiveService(context.connection).archive_repository("https://github.com/octo/empty")
        assert result.status == "COMPLETED"
        assert result.current_release_id is None
        assert result.message == user_message("COMPLETED")
        rows = context.connection.execute("SELECT COUNT(*) AS n FROM releases").fetchone()
        assert rows["n"] == 0
        snapshot = context.connection.execute(
            "SELECT ref, default_branch, state, local_sha256, size, relative_path FROM source_snapshots"
        ).fetchone()
        assert snapshot["ref"] == "main"
        assert snapshot["default_branch"] == "main"
        assert snapshot["state"] == "COMPLETED"
        assert snapshot["local_sha256"] == hashlib.sha256(BODY).hexdigest()
        assert snapshot["size"] == len(BODY)
        path = tmp_path / "a" / snapshot["relative_path"]
        assert path.is_file()
        assert path.name.endswith(".zip")
        assert path.name.startswith("main-")
        assert "source" in path.parts
        assert "github" in path.parts
        assert path.read_bytes() == BODY
        index = list((tmp_path / "a").rglob("index.html"))
        assert index and "该项目没有可用的正式 Release。" in index[0].read_text(encoding="utf-8")
        topics = [
            row["name"]
            for row in context.connection.execute("SELECT name FROM repository_topics ORDER BY name")
        ]
        assert topics == ["cli", "python"]
    finally:
        context.close()


def test_source_snapshot_failure_keeps_the_previous_archive(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        service = ArchiveService(context.connection)
        with respx.mock:
            _install_no_release()
            assert service.archive_repository("https://github.com/octo/empty").status == "COMPLETED"
        first = context.connection.execute(
            "SELECT id, relative_path FROM source_snapshots WHERE state = 'COMPLETED'"
        ).fetchone()
        first_path = tmp_path / "a" / first["relative_path"]
        with respx.mock:
            _install_no_release(zip_status=500)
            failed = service.archive_repository("https://github.com/octo/empty")
        assert failed.status == "FAILED"
        current = context.connection.execute(
            "SELECT current_snapshot_id FROM repositories"
        ).fetchone()
        assert current["current_snapshot_id"] == first["id"]
        assert first_path.read_bytes() == BODY
        failed_row = context.connection.execute(
            "SELECT state FROM source_snapshots WHERE id != ?",
            (first["id"],),
        ).fetchone()
        assert failed_row["state"] == "FAILED"
    finally:
        context.close()


def test_source_snapshot_cancel_does_not_promote(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        with respx.mock:
            _install_no_release()
            result = ArchiveService(context.connection).archive_repository(
                "https://github.com/octo/empty",
                cancel_event=Event(),
            )
        event = Event()
        event.set()
        with respx.mock:
            _install_no_release()
            cancelled = ArchiveService(context.connection).archive_repository(
                "https://github.com/octo/empty",
                cancel_event=event,
            )
        assert result.status == "COMPLETED"
        assert cancelled.status == "CANCELLED"
        current = context.connection.execute("SELECT current_snapshot_id FROM repositories").fetchone()
        promoted = context.connection.execute(
            "SELECT id FROM source_snapshots WHERE state = 'COMPLETED'"
        ).fetchall()
        assert len(promoted) == 1
        assert current["current_snapshot_id"] == promoted[0]["id"]
    finally:
        context.close()


def test_api_error_is_not_treated_as_no_release(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        with respx.mock:
            respx.get(REPO).mock(return_value=httpx.Response(200, json=_repository()))
            respx.get(LATEST).mock(return_value=httpx.Response(500))
            result = ArchiveService(context.connection).archive_repository("https://github.com/octo/empty")
        assert result.status == "API_ERROR"
        assert result.message == user_message("API_ERROR")
        assert context.connection.execute("SELECT COUNT(*) AS n FROM source_snapshots").fetchone()["n"] == 0
    finally:
        context.close()


def test_duplicate_repository_url_stays_one_asset(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        service = ArchiveService(context.connection)
        with respx.mock:
            _install_no_release()
            service.archive_repository("https://github.com/octo/empty")
            service.archive_repository("https://github.com/octo/empty.git")
        count = context.connection.execute("SELECT COUNT(*) AS n FROM repositories").fetchone()["n"]
        assert count == 1
        snapshots = context.connection.execute(
            "SELECT COUNT(*) AS n FROM source_snapshots WHERE state = 'COMPLETED'"
        ).fetchone()["n"]
        assert snapshots == 2
    finally:
        context.close()


def test_repository_library_search_filter_category_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    context = prepare_context(database, tmp_path / "a")
    try:
        with respx.mock:
            _install_no_release()
            ArchiveService(context.connection).archive_repository("https://github.com/octo/empty")
        library = LibraryService(context.connection)
        category = library.create_category("开发工具")
        repo_id = library.list_repositories()[0].id
        library.set_repository_category(repo_id, category.id)
        assert library.list_repositories(query="sample")[0].name == "empty"
        assert library.list_repositories(query="octo")[0].owner == "octo"
        assert library.list_repositories(query="开发工具")[0].category_name == "开发工具"
        assert library.list_repositories(category_id=category.id)[0].id == repo_id
        assert library.list_repositories(uncategorized=True) == []
        library.rename_category(category.id, "系统工具")
        assert library.list_repositories()[0].category_name == "系统工具"
        library.delete_category(category.id)
        assert library.list_repositories()[0].name == "empty"
        assert library.list_repositories()[0].category_id is None
        detail = library.get_repository(repo_id)
        assert detail is not None
        assert detail.topics == ("cli", "python")
        assert detail.snapshot is not None
        assert detail.snapshot.local_sha256 == hashlib.sha256(BODY).hexdigest()
        assert detail.release_tag == ""
        assert detail.state == "已完成"
        context.close()
        reopened = prepare_context(database, tmp_path / "a")
        try:
            again = LibraryService(reopened.connection).list_repositories(sort="name")
            assert [item.name for item in again] == ["empty"]
            assert again[0].state == "已完成"
        finally:
            reopened.close()
    except Exception:
        if context.connection:
            context.close()
        raise


def test_progress_single_file_multi_file_and_unknown_length() -> None:
    single = TransferPlan([400])
    single.begin()
    event = single.observe(100, 400, force=True, current_file="source-code.zip", total_files=1)
    assert event is not None
    assert event.percentage == 25
    assert event.overall_downloaded_bytes == 100
    assert event.overall_expected_bytes == 400

    multi = TransferPlan([100, 300])
    multi.begin()
    multi.finish(100)
    multi.begin()
    event = multi.observe(50, 300, force=True, completed_files=1, total_files=2)
    assert event is not None
    assert event.overall_downloaded_bytes == 150
    assert event.overall_expected_bytes == 400
    assert event.percentage == 37

    unknown = TransferPlan([None])
    unknown.begin()
    event = unknown.observe(128, None, force=True, current_file="source-code.zip")
    assert event is not None
    assert event.percentage is None
    assert event.overall_expected_bytes is None
    assert event.overall_downloaded_bytes == 128


def test_github_bridge_lists_a_repository(tmp_path: Path) -> None:
    import json

    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        with respx.mock:
            _install_no_release()
            ArchiveService(context.connection).archive_repository("https://github.com/octo/empty")
        github = GitHubBridge(context.connection, context.database_path)
        listing = json.loads(github.listRepositories("", "", "recent"))
        assert listing["ok"] is True
        assert listing["data"]["hasRepositories"] is True
        assert len(listing["data"]["items"]) == 1
        assert "empty" in listing["data"]["items"][0]["fullName"]
        missing = json.loads(github.listRepositories("missing", "", "recent"))
        assert missing["data"]["items"] == []
        found = json.loads(github.listRepositories("empty", "", "recent"))
        assert len(found["data"]["items"]) == 1
        detail = json.loads(github.getRepository(found["data"]["items"][0]["id"]))["data"]
        assert "python" in detail["topics"]
    finally:
        context.close()
