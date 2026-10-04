"""Phase 11 authority, lifecycle, and identity contracts."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from techvault.app.archive_service import ArchiveService
from techvault.app.bootstrap import prepare_context
from techvault.app.library import LibraryService
from techvault.app.recovery import INTERRUPTED, recover_interrupted_archives
from techvault.archive.identity import adopt_legacy_tree
from techvault.archive.paths import PathLayout
from techvault.archive.progress import ProgressEvent
from techvault.archive.release_archive import JobStatus
from techvault.ui.main_window import MainWindow

REPO = "https://api.github.com/repos/octo/hello"
LATEST = "https://api.github.com/repos/octo/hello/releases/latest"
README = "https://api.github.com/repos/octo/hello/readme"
ZIP = "https://codeload.github.com/octo/hello/zipball/v1"
TAR = "https://codeload.github.com/octo/hello/tarball/v1"
SNAP = "https://api.github.com/repos/octo/hello/zipball/main"
BAR = "https://api.github.com/repos/foo/bar"
BAR_LATEST = "https://api.github.com/repos/foo/bar/releases/latest"
BAR_README = "https://api.github.com/repos/foo/bar/readme"
BAR_SNAP = "https://api.github.com/repos/foo/bar/zipball/main"
BAR_LIST = "https://api.github.com/repos/foo/bar/releases?per_page=100"
OTHER = "https://api.github.com/repos/octo/other"
OTHER_LATEST = "https://api.github.com/repos/octo/other/releases/latest"
OTHER_README = "https://api.github.com/repos/octo/other/readme"
OTHER_SNAP = "https://api.github.com/repos/octo/other/zipball/main"
OTHER_LIST = "https://api.github.com/repos/octo/other/releases?per_page=100"


def _repo(repo_id: int = 123, name: str = "hello", owner: str = "octo", description: str = "A repository") -> dict:
    return {
        "id": repo_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "html_url": f"https://github.com/{owner}/{name}",
        "description": description,
        "stargazers_count": 1,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": owner},
        "topics": ["cli"],
    }


def _release() -> dict:
    return {
        "id": 9,
        "tag_name": "v1",
        "name": "One",
        "body": "",
        "html_url": "https://github.com/octo/hello/releases/tag/v1",
        "published_at": "2026-02-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "tarball_url": TAR,
        "zipball_url": ZIP,
        "assets": [],
    }


def _install(router, *, release_status: int = 200, snapshot: bytes = b"snapshot-body") -> None:
    router.get(REPO).mock(return_value=httpx.Response(200, json=_repo()))
    router.get(LATEST).mock(return_value=httpx.Response(release_status, json=_release()))
    router.get(ZIP).mock(return_value=httpx.Response(200, content=b"zip-body"))
    router.get(TAR).mock(return_value=httpx.Response(200, content=b"tar-body"))
    router.get(README).mock(return_value=httpx.Response(200, text="<p>Hi</p>"))
    router.get(SNAP).mock(return_value=httpx.Response(200, content=snapshot))


def _service(tmp_path: Path) -> tuple[ArchiveService, object]:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    return ArchiveService(context.connection), context


def test_release_artifact_survives_a_failed_rearchive(tmp_path: Path) -> None:
    service, context = _service(tmp_path)
    try:
        with respx.mock:
            _install(respx)
            first = service.archive_repository("https://github.com/octo/hello")
        assert first.status == JobStatus.COMPLETED
        artifact = context.connection.execute(
            """
            SELECT release_files.local_sha256, release_files.relative_path, release_files.archive_root,
                   release_files.state, repositories.current_release_id
            FROM release_files
            JOIN releases ON releases.artifact_attempt_id = release_files.attempt_id
            JOIN repositories ON repositories.current_release_id = releases.id
            WHERE release_files.kind = 'SOURCE_ZIP'
            """
        ).fetchone()
        original = (Path(artifact["archive_root"]) / artifact["relative_path"]).read_bytes()
        assert hashlib.sha256(original).hexdigest() == artifact["local_sha256"]
        with respx.mock:
            _install(respx)
            respx.get(ZIP).mock(return_value=httpx.Response(500))
            respx.get(TAR).mock(return_value=httpx.Response(500))
            second = service.archive_repository("https://github.com/octo/hello")
        assert second.status == JobStatus.PARTIAL
        current = context.connection.execute(
            "SELECT current_release_id, current_snapshot_id FROM repositories"
        ).fetchone()
        assert current["current_release_id"] == artifact["current_release_id"]
        kept = context.connection.execute(
            """
            SELECT local_sha256, relative_path, state
            FROM release_files
            WHERE attempt_id = (
                SELECT artifact_attempt_id FROM releases WHERE id = ?
            ) AND kind = 'SOURCE_ZIP'
            """,
            (artifact["current_release_id"],),
        ).fetchone()
        assert kept["state"] == "COMPLETED"
        assert kept["local_sha256"] == artifact["local_sha256"]
        assert kept["relative_path"] == artifact["relative_path"]
        assert (Path(artifact["archive_root"]) / kept["relative_path"]).read_bytes() == original
        library = LibraryService(context.connection)
        detail = library.get_repository(1)
        assert detail is not None
        assert detail.state == "已完成"
        assert detail.operation_label == "最近一次更新：部分完成"
        assert detail.release_tag == "v1"
    finally:
        context.close()


def test_snapshot_success_and_release_failure_keep_both_facts(tmp_path: Path) -> None:
    service, context = _service(tmp_path)
    try:
        with respx.mock:
            _install(respx, snapshot=b"snap-a")
            assert service.archive_repository("https://github.com/octo/hello").status == JobStatus.COMPLETED
        page = context.connection.execute(
            "SELECT built_at, relative_path, archive_root FROM offline_pages WHERE page_type = 'REPOSITORY'"
        ).fetchone()
        html = (Path(page["archive_root"]) / page["relative_path"]).read_bytes()
        with respx.mock:
            _install(respx, snapshot=b"snap-b")
            respx.get(REPO).mock(return_value=httpx.Response(200, json=_repo(description="changed")))
            respx.get(ZIP).mock(return_value=httpx.Response(500))
            result = service.archive_repository("https://github.com/octo/hello")
        assert result.status == JobStatus.PARTIAL
        row = context.connection.execute(
            "SELECT current_snapshot_id, current_release_id, description FROM repositories"
        ).fetchone()
        assert row["current_release_id"] is not None
        assert row["current_snapshot_id"] is not None
        assert row["description"] == "changed"
        snap = context.connection.execute(
            "SELECT local_sha256 FROM source_snapshots WHERE id = ?",
            (row["current_snapshot_id"],),
        ).fetchone()
        assert snap["local_sha256"] == hashlib.sha256(b"snap-b").hexdigest()
        again = context.connection.execute(
            "SELECT built_at, relative_path, archive_root FROM offline_pages WHERE page_type = 'REPOSITORY'"
        ).fetchone()
        assert again["built_at"] == page["built_at"]
        assert (Path(again["archive_root"]) / again["relative_path"]).read_bytes() == html
        detail = LibraryService(context.connection).get_repository(1)
        assert detail is not None
        assert detail.snapshot is not None
        assert detail.release_tag == "v1"
        assert detail.state == "已完成"
        assert "部分完成" in detail.operation_label
        assert detail.offline_built_at == page["built_at"]
    finally:
        context.close()


def test_interrupted_archiving_job_is_closed_without_clearing_pointers(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        context.connection.execute(
            """
            INSERT INTO repositories (
                github_repo_id, owner, name, full_name, html_url, created_at, updated_at,
                current_release_id, current_snapshot_id
            ) VALUES (5, 'octo', 'hello', 'octo/hello', 'https://github.com/octo/hello', 't', 't', NULL, NULL)
            """
        )
        context.connection.execute(
            """
            INSERT INTO source_snapshots (
                repository_id, ref, archived_at, state, created_at, updated_at
            ) VALUES (1, 'main', 't', 'COMPLETED', 't', 't')
            """
        )
        context.connection.execute("UPDATE repositories SET current_snapshot_id = 1 WHERE id = 1")
        context.connection.execute(
            """
            INSERT INTO jobs (
                repository_id, status, created_at, archive_kind, target_snapshot_id
            ) VALUES (1, 'ARCHIVING', 't', 'SOURCE', 1)
            """
        )
        context.connection.execute(
            """
            INSERT INTO source_snapshots (
                repository_id, ref, archived_at, state, created_at, updated_at
            ) VALUES (1, 'main', 't', 'DOWNLOADING', 't', 't')
            """
        )
        assert recover_interrupted_archives(context.connection) == 1
        job = context.connection.execute("SELECT status, failure_code FROM jobs").fetchone()
        assert job["status"] == "FAILED"
        assert job["failure_code"] == INTERRUPTED
        pointers = context.connection.execute(
            "SELECT current_snapshot_id, current_release_id FROM repositories"
        ).fetchone()
        assert pointers["current_snapshot_id"] == 1
        assert pointers["current_release_id"] is None
        kept = context.connection.execute(
            "SELECT state FROM source_snapshots WHERE id = 1"
        ).fetchone()
        assert kept["state"] == "COMPLETED"
        failed = context.connection.execute(
            "SELECT state, failure_code FROM source_snapshots WHERE id = 2"
        ).fetchone()
        assert failed["state"] == "FAILED"
        assert failed["failure_code"] == INTERRUPTED
        detail = LibraryService(context.connection).get_repository(1)
        assert detail is not None
        assert detail.state == "已完成"
        assert detail.state != "更新中"
        assert "失败" in detail.operation_label
    finally:
        context.close()


def test_rename_keeps_one_repository_and_the_old_snapshot(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = ArchiveService(context.connection)
    try:
        with respx.mock:
            respx.get(BAR).mock(return_value=httpx.Response(200, json=_repo(50, "bar", "foo")))
            respx.get(BAR_LATEST).mock(return_value=httpx.Response(404))
            respx.get(BAR_LIST).mock(return_value=httpx.Response(200, json=[]))
            respx.get(BAR_README).mock(return_value=httpx.Response(404))
            respx.get(BAR_SNAP).mock(return_value=httpx.Response(200, content=b"bar-bytes"))
            assert service.archive_repository("https://github.com/foo/bar").status == JobStatus.COMPLETED
        first = context.connection.execute(
            "SELECT id, relative_path, archive_root FROM source_snapshots WHERE state = 'COMPLETED'"
        ).fetchone()
        old_file = Path(first["archive_root"]) / first["relative_path"]
        assert old_file.read_bytes() == b"bar-bytes"
        with respx.mock:
            respx.get("https://api.github.com/repos/foo/baz").mock(
                return_value=httpx.Response(200, json=_repo(50, "baz", "foo"))
            )
            respx.get("https://api.github.com/repos/foo/baz/releases/latest").mock(
                return_value=httpx.Response(404)
            )
            respx.get("https://api.github.com/repos/foo/baz/releases?per_page=100").mock(
                return_value=httpx.Response(200, json=[])
            )
            respx.get("https://api.github.com/repos/foo/baz/readme").mock(return_value=httpx.Response(404))
            respx.get("https://api.github.com/repos/foo/baz/zipball/main").mock(
                return_value=httpx.Response(200, content=b"baz-bytes")
            )
            assert service.archive_repository("https://github.com/foo/baz").status == JobStatus.COMPLETED
        assert context.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 1
        stored = context.connection.execute(
            "SELECT github_repo_id, owner, name FROM repositories"
        ).fetchone()
        assert stored["github_repo_id"] == 50
        assert stored["name"] == "baz"
        assert old_file.read_bytes() == b"bar-bytes"
        folder = PathLayout(tmp_path / "archive").repository_dir("foo", "baz", 50)
        assert folder == tmp_path / "archive" / "repositories" / "50"
        assert old_file.is_relative_to(folder)
        other = PathLayout(tmp_path / "archive").repository_dir("foo", "unrelated", 99)
        assert other != folder
        assert not other.exists()
    finally:
        context.close()


def test_archive_root_change_keeps_old_files_addressable(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    context = prepare_context(tmp_path / "db.sqlite", root_a)
    service = ArchiveService(context.connection)
    try:
        with respx.mock:
            respx.get(BAR).mock(return_value=httpx.Response(200, json=_repo(50, "bar", "foo")))
            respx.get(BAR_LATEST).mock(return_value=httpx.Response(404))
            respx.get(BAR_LIST).mock(return_value=httpx.Response(200, json=[]))
            respx.get(BAR_README).mock(return_value=httpx.Response(200, text="<p>Hi</p>"))
            respx.get(BAR_SNAP).mock(return_value=httpx.Response(200, content=b"rooted"))
            assert service.archive_repository("https://github.com/foo/bar").status == JobStatus.COMPLETED
        context.settings.set_archive_root(str(tmp_path / "b"))
        context.close()
        restarted = prepare_context(tmp_path / "db.sqlite")
        detail = LibraryService(restarted.connection).get_repository(1)
        assert detail is not None
        assert Path(detail.offline_absolute).is_file()
        assert Path(detail.offline_absolute).is_relative_to(root_a)
        assert Path(detail.archive_folder).is_dir()
        assert Path(detail.archive_folder).is_relative_to(root_a)
        assert b"rooted" in list(Path(detail.archive_folder).rglob("source-code.zip"))[0].read_bytes()
        restarted.close()
    except Exception:
        if context.connection:
            context.close()
        raise


def test_library_partial_states_follow_successful_assets(tmp_path: Path) -> None:
    service, context = _service(tmp_path)
    try:
        with respx.mock:
            respx.get(REPO).mock(return_value=httpx.Response(200, json=_repo()))
            respx.get(LATEST).mock(return_value=httpx.Response(404))
            respx.get("https://api.github.com/repos/octo/hello/releases?per_page=100").mock(
                return_value=httpx.Response(200, json=[])
            )
            respx.get(README).mock(return_value=httpx.Response(404))
            respx.get(SNAP).mock(return_value=httpx.Response(500))
            failed = service.archive_repository("https://github.com/octo/hello")
        assert failed.status == JobStatus.FAILED
        library = LibraryService(context.connection)
        empty = library.list_repositories()[0]
        assert empty.state == "失败"
        assert empty.operation == "FAILED"

        with respx.mock:
            respx.get(BAR).mock(return_value=httpx.Response(200, json=_repo(50, "bar", "foo")))
            respx.get(BAR_LATEST).mock(return_value=httpx.Response(404))
            respx.get(BAR_LIST).mock(return_value=httpx.Response(200, json=[]))
            respx.get(BAR_README).mock(return_value=httpx.Response(404))
            respx.get(BAR_SNAP).mock(return_value=httpx.Response(200, content=b"only-snap"))
            assert service.archive_repository("https://github.com/foo/bar").status == JobStatus.COMPLETED
        snapshot_only = next(item for item in library.list_repositories() if item.name == "bar")
        assert snapshot_only.state == "已完成"
        assert snapshot_only.archive_kind == "源码快照"

        with respx.mock:
            _install(respx, snapshot=b"will-fail")
            respx.get(SNAP).mock(return_value=httpx.Response(500))
            release_only = service.archive_repository("https://github.com/octo/hello")
        assert release_only.status == JobStatus.PARTIAL
        hello = next(item for item in library.list_repositories() if item.name == "hello")
        assert hello.state == "已完成"
        assert hello.operation == "PARTIAL"
        assert hello.release_tag == "v1"
        detail = library.get_repository(hello.id)
        assert detail is not None
        assert detail.snapshot is None
        assert detail.release_tag == "v1"

        foreign = context.connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = context.connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert foreign == []
        assert integrity == "ok"
        with pytest.raises(sqlite3.IntegrityError):
            context.connection.execute(
                "UPDATE repositories SET current_snapshot_id = ? WHERE name = 'hello'",
                (context.connection.execute(
                    "SELECT current_snapshot_id FROM repositories WHERE name = 'bar'"
                ).fetchone()[0],),
            )
    finally:
        context.close()


def test_legacy_owner_directory_moves_under_the_repository_id(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        context.connection.execute(
            """
            INSERT INTO repositories (
                github_repo_id, owner, name, full_name, html_url, created_at, updated_at
            ) VALUES (50, 'foo', 'bar', 'foo/bar', 'https://github.com/foo/bar', 't', 't')
            """
        )
        context.connection.execute(
            """
            INSERT INTO source_snapshots (
                repository_id, ref, archived_at, state, relative_path, created_at, updated_at
            ) VALUES (1, 'main', 't', 'COMPLETED', ?, 't', 't')
            """,
            ("repositories/foo/bar__50/source/1/source-code.zip",),
        )
        layout = PathLayout(tmp_path / "archive")
        legacy = layout.legacy_repository_dir("foo", "bar", 50)
        target = legacy / "source" / "1"
        target.mkdir(parents=True)
        (target / "source-code.zip").write_bytes(b"legacy")
        adopt_legacy_tree(
            context.connection,
            layout,
            owner="foo",
            name="bar",
            github_repo_id=50,
        )
        stable = layout.repository_dir("foo", "baz", 50)
        assert (stable / "source" / "1" / "source-code.zip").read_bytes() == b"legacy"
        assert not legacy.exists()
        stored = context.connection.execute("SELECT relative_path FROM source_snapshots").fetchone()[0]
        assert stored.replace("\\", "/").startswith("repositories/50/")
        assert (tmp_path / "archive" / stored).read_bytes() == b"legacy"
    finally:
        context.close()


def test_selection_search_and_progress_use_the_selected_repository(qtbot, tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    host = MainWindow(context)
    qtbot.addWidget(host)
    host.show()
    page = host.archive_page
    assert page.empty_label.text() == "还没有项目"
    event = ProgressEvent(
        job_id=1,
        file_id=1,
        current_file="source-code.zip",
        downloaded_bytes=10,
        expected_bytes=10,
        overall_downloaded_bytes=10,
        overall_expected_bytes=10,
        percentage=100,
        phase="downloading",
    )
    page.show_transfer(event)
    assert page.percent_label.text() == "100%"
    assert page.operation_label.text() == "正在校验"
    page.show_transfer(
        ProgressEvent(
            job_id=1,
            file_id=1,
            current_file="source-code.zip",
            downloaded_bytes=4,
            expected_bytes=None,
            overall_downloaded_bytes=4,
            overall_expected_bytes=None,
            percentage=None,
            phase="verifying",
        )
    )
    assert page.percent_label.text() == "正在校验"
    assert page.progress_bar.maximum() == 0

    def install_other() -> None:
        respx.get(OTHER).mock(return_value=httpx.Response(200, json=_repo(456, "other")))
        respx.get(OTHER_LATEST).mock(return_value=httpx.Response(404))
        respx.get(OTHER_LIST).mock(return_value=httpx.Response(200, json=[]))
        respx.get(OTHER_README).mock(return_value=httpx.Response(200, text="<p>Other</p>"))
        respx.get(OTHER_SNAP).mock(return_value=httpx.Response(200, content=b"other-bytes"))

    with respx.mock:
        _install(respx)
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
        install_other()
        page.url_edit.setText("https://github.com/octo/other")
        page.archive_button.click()
        qtbot.waitUntil(lambda: "other" in page.repository_label.text(), timeout=20000)

    hello = next(
        page.repository_list.item(row)
        for row in range(page.repository_list.count())
        if "hello" in page.repository_list.item(row).text()
    )
    page.repository_list.setCurrentItem(hello)
    opened: list[QUrl] = []
    qtbot.waitUntil(lambda: "hello" in page.detail_label.text(), timeout=2000)

    def capture(url: QUrl) -> bool:
        opened.append(url)
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(QDesktopServices, "openUrl", capture)
    try:
        page.open_offline_page()
        page.open_online_repository()
        page.open_archive_folder()
    finally:
        monkeypatch.undo()
    assert Path(opened[0].toLocalFile()).is_file()
    assert "hello" in opened[1].toString()
    assert opened[1].toString().endswith("/hello")
    assert Path(opened[2].toLocalFile()) == tmp_path / "archive" / "repositories" / "123"
    other = next(
        page.repository_list.item(row)
        for row in range(page.repository_list.count())
        if "other" in page.repository_list.item(row).text()
    )
    page.repository_list.setCurrentItem(other)
    qtbot.waitUntil(lambda: "暂无正式 Release" in page.detail_label.text(), timeout=2000)
    assert "尚未归档版本" not in page.detail_label.text()

    page.library.create_category("游戏")
    page._reload_categories()
    page.category_filter.setCurrentIndex(page.category_filter.findText("游戏"))
    qtbot.waitUntil(lambda: page.repository_list.count() == 0, timeout=2000)
    assert page.empty_label.text() == "没有符合条件的项目"
    page.category_filter.setCurrentIndex(0)
    qtbot.waitUntil(lambda: page.repository_list.count() == 2, timeout=2000)

    page.search_edit.setText("missing-project")
    qtbot.waitUntil(lambda: page.repository_list.count() == 0, timeout=2000)
    assert page.empty_label.text() == "没有符合条件的项目"
    assert page.detail_label.text() == "没有符合条件的项目。"
    assert page.open_offline_button.isEnabled() is False
    assert page.open_online_button.isEnabled() is False
    assert page.open_folder_button.isEnabled() is False

    page.search_edit.setText("")
    qtbot.waitUntil(lambda: page.repository_list.count() == 2, timeout=2000)
    host.close()
    context.close()
    restarted = prepare_context(tmp_path / "db.sqlite")
    again = MainWindow(restarted)
    qtbot.addWidget(again)
    again.show()
    restored = again.archive_page
    qtbot.waitUntil(lambda: restored.repository_list.count() == 2, timeout=2000)
    hello_again = next(
        restored.repository_list.item(row)
        for row in range(restored.repository_list.count())
        if "hello" in restored.repository_list.item(row).text()
    )
    restored.repository_list.setCurrentItem(hello_again)
    qtbot.waitUntil(lambda: restored.open_offline_button.isEnabled(), timeout=2000)
    assert restored.open_online_button.isEnabled() is True
    assert restored.open_folder_button.isEnabled() is True
    restarted.close()
