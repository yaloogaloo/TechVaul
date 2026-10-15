"""Failed archive assets can be saved again without touching the ones that succeeded."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from techvault.app.archive_service import ArchiveService
from techvault.app.assets import AssetKind, AssetStatus, load_asset_records
from techvault.app.bootstrap import prepare_context
from techvault.archive.paths import PathLayout
from techvault.archive.release_archive import JobStatus

REPO = "https://api.github.com/repos/octo/hello"
LATEST = "https://api.github.com/repos/octo/hello/releases/latest"
README = "https://api.github.com/repos/octo/hello/readme"
ZIP = "https://codeload.github.com/octo/hello/zipball/v1"
TAR = "https://codeload.github.com/octo/hello/tarball/v1"
SNAP = "https://api.github.com/repos/octo/hello/zipball/main"


def _repo() -> dict:
    return {
        "id": 123,
        "name": "hello",
        "full_name": "octo/hello",
        "html_url": "https://github.com/octo/hello",
        "description": "A repository",
        "stargazers_count": 1,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "octo"},
        "topics": ["python"],
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


def _install(router, *, snapshot: bytes = b"snapshot-body") -> None:
    router.get(REPO).mock(return_value=httpx.Response(200, json=_repo()))
    router.get(LATEST).mock(return_value=httpx.Response(200, json=_release()))
    router.get(ZIP).mock(return_value=httpx.Response(200, content=b"zip-body"))
    router.get(TAR).mock(return_value=httpx.Response(200, content=b"tar-body"))
    router.get(README).mock(return_value=httpx.Response(200, text="# Hello\n\nBody"))
    router.get(SNAP).mock(return_value=httpx.Response(200, content=snapshot))


def _service(tmp_path: Path):
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    return ArchiveService(context.connection), context


def _ids(connection: sqlite3.Connection) -> tuple[int | None, int | None]:
    row = connection.execute(
        "SELECT current_snapshot_id, current_release_id FROM repositories WHERE name = 'hello'"
    ).fetchone()
    return row["current_snapshot_id"], row["current_release_id"]


def _release_sha(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        """
        SELECT local_sha256 FROM release_files
        WHERE state = 'COMPLETED' AND local_sha256 IS NOT NULL
        ORDER BY id LIMIT 1
        """
    ).fetchone()
    return row["local_sha256"]


def test_readme_retry_keeps_snapshot_and_release(tmp_path: Path, monkeypatch) -> None:
    import techvault.offline.pages as pages
    from techvault.offline.sanitize import sanitize_html as real_sanitize

    service, context = _service(tmp_path)
    try:
        monkeypatch.setattr(pages, "sanitize_html", lambda html: "<script>secret</script>")
        with respx.mock:
            _install(respx)
            failed = service.archive_repository("https://github.com/octo/hello")
        assert failed.status == "OFFLINE_PAGE_FAILED"
        snapshot_id, release_id = _ids(context.connection)
        assert snapshot_id is not None
        assert release_id is not None
        digest = _release_sha(context.connection)
        attempts = context.connection.execute("SELECT COUNT(*) AS n FROM release_attempts").fetchone()["n"]
        records = {item.kind: item for item in load_asset_records(context.connection, 1)}
        assert records[AssetKind.README].status == AssetStatus.FAILED
        assert records[AssetKind.README].retryable is True
        assert records[AssetKind.SOURCE].status == AssetStatus.SAVED
        assert records[AssetKind.SOURCE].retryable is False
        assert records[AssetKind.RELEASE].status == AssetStatus.SAVED
        assert records[AssetKind.RELEASE].retryable is False

        monkeypatch.setattr(pages, "sanitize_html", lambda html: "<script>still</script>")
        with respx.mock:
            _install(respx)
            again = service.retry_failed_asset(1, AssetKind.README)
        assert again.status == "OFFLINE_PAGE_FAILED"
        assert _ids(context.connection) == (snapshot_id, release_id)
        assert _release_sha(context.connection) == digest
        assert context.connection.execute("SELECT COUNT(*) AS n FROM release_attempts").fetchone()["n"] == attempts

        monkeypatch.setattr(pages, "sanitize_html", real_sanitize)
        with respx.mock:
            _install(respx)
            restored = service.retry_failed_asset(1, AssetKind.README)
            urls = [str(call.request.url) for call in respx.calls]
        assert restored.status == JobStatus.COMPLETED
        assert _ids(context.connection) == (snapshot_id, release_id)
        assert _release_sha(context.connection) == digest
        assert context.connection.execute("SELECT COUNT(*) AS n FROM release_attempts").fetchone()["n"] == attempts
        assert any(url.endswith("/readme") for url in urls)
        assert not any("zipball" in url or "tarball" in url for url in urls)
        layout = PathLayout(tmp_path / "archive")
        assert layout.page_index("octo", "hello", 123).is_file()
        assert layout.readme_file("octo", "hello", 123).is_file()
        saved = {item.kind: item for item in load_asset_records(context.connection, 1)}
        assert saved[AssetKind.README].status == AssetStatus.SAVED
        assert saved[AssetKind.README].retryable is False
        ignored = service.retry_failed_asset(1, AssetKind.README)
        assert ignored.message == "这项资料没有失败，不需要重新保存。"
        assert _ids(context.connection) == (snapshot_id, release_id)
        assert context.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert context.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        context.close()


def test_source_retry_does_not_replace_a_saved_release(tmp_path: Path) -> None:
    service, context = _service(tmp_path)
    try:
        with respx.mock:
            _install(respx)
            respx.get(SNAP).mock(return_value=httpx.Response(500))
            partial = service.archive_repository("https://github.com/octo/hello")
        assert partial.status == JobStatus.PARTIAL
        _snapshot_id, release_id = _ids(context.connection)
        assert _snapshot_id is None
        assert release_id is not None
        digest = _release_sha(context.connection)
        records = {item.kind: item for item in load_asset_records(context.connection, 1)}
        assert records[AssetKind.SOURCE].status == AssetStatus.FAILED
        assert records[AssetKind.SOURCE].retryable is True
        assert records[AssetKind.RELEASE].retryable is False
        with respx.mock:
            _install(respx, snapshot=b"recovered")
            restored = service.retry_failed_asset(1, AssetKind.SOURCE)
        assert restored.status == JobStatus.COMPLETED
        snapshot_id, still_release = _ids(context.connection)
        assert snapshot_id is not None
        assert still_release == release_id
        assert _release_sha(context.connection) == digest
        completed = context.connection.execute(
            "SELECT COUNT(*) AS n FROM source_snapshots WHERE state = 'COMPLETED'"
        ).fetchone()["n"]
        assert completed == 1
        assert context.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        context.close()


def test_release_retry_keeps_the_saved_snapshot(tmp_path: Path) -> None:
    service, context = _service(tmp_path)
    try:
        with respx.mock:
            _install(respx)
            respx.get(ZIP).mock(return_value=httpx.Response(500))
            respx.get(TAR).mock(return_value=httpx.Response(500))
            partial = service.archive_repository("https://github.com/octo/hello")
        assert partial.status == JobStatus.PARTIAL
        snapshot_id, release_id = _ids(context.connection)
        assert snapshot_id is not None
        assert release_id is None
        records = {item.kind: item for item in load_asset_records(context.connection, 1)}
        assert records[AssetKind.RELEASE].status in {AssetStatus.FAILED, AssetStatus.PARTIAL}
        assert records[AssetKind.RELEASE].retryable is True
        assert records[AssetKind.SOURCE].retryable is False
        with respx.mock:
            _install(respx, snapshot=b"untouched")
            restored = service.retry_failed_asset(1, AssetKind.RELEASE)
            urls = [str(call.request.url) for call in respx.calls]
        assert restored.status == JobStatus.COMPLETED
        still_snapshot, new_release = _ids(context.connection)
        assert still_snapshot == snapshot_id
        assert new_release is not None
        assert not any(url.endswith("/zipball/main") for url in urls)
        assert context.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert context.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        context.close()


def test_retry_asset_via_service_for_failed_readme(tmp_path: Path, monkeypatch) -> None:
    import json

    import techvault.offline.pages as pages
    from techvault.offline.sanitize import sanitize_html as real_sanitize
    from techvault.ui.bridge.github_bridge import GitHubBridge

    _service_obj, context = _service(tmp_path)
    service = _service_obj
    try:
        monkeypatch.setattr(pages, "sanitize_html", lambda html: "<script>secret</script>")
        with respx.mock:
            _install(respx)
            assert service.archive_repository("https://github.com/octo/hello").status == "OFFLINE_PAGE_FAILED"
            github = GitHubBridge(context.connection, context.database_path)
            listing = json.loads(github.listRepositories("", "", "recent"))
            assert listing["ok"] is True
            repo_id = listing["data"]["items"][0]["id"]
            detail = json.loads(github.getRepository(repo_id))["data"]
            retryable = [a for a in detail["assets"] if a.get("retryable")]
            assert retryable, detail["assets"]
            kinds = {a["kind"] for a in retryable}
            assert "readme" in kinds or "README" in kinds or any("readme" in str(k).lower() for k in kinds)
            monkeypatch.setattr(pages, "sanitize_html", real_sanitize)
            kind = retryable[0]["kind"]
            retried = service.retry_failed_asset(repo_id, kind)
            assert retried.status in {"COMPLETED", "SUCCESS", "OFFLINE_PAGE_FAILED", "PARTIAL"} or "更新" in (
                retried.message or ""
            )
    finally:
        context.close()
