"""Slate-era UI integration via ArchiveService/Worker and bridges (no legacy Widgets pages)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import keyring
import pytest
import respx
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from PySide6.QtCore import QThread

from techvault.app.archive_service import ArchiveService
from techvault.app.bootstrap import prepare_context
from techvault.app.messages import user_message
from techvault.archive.paths import PathLayout
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.bridge.github_bridge import GitHubBridge
from techvault.ui.bridge.settings_bridge import SettingsBridge
from techvault.ui.tags import parse_tag_input

REPO_URL = "https://api.github.com/repos/octo/hello"
LATEST_URL = "https://api.github.com/repos/octo/hello/releases/latest"
README_URL = "https://api.github.com/repos/octo/hello/readme"
ZIP_URL = "https://codeload.github.com/octo/hello/zipball/v1"
TAR_URL = "https://codeload.github.com/octo/hello/tarball/v1"
SNAP_URL = "https://api.github.com/repos/octo/hello/zipball/main"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.passwords[(service, username)]
        except KeyError as exc:
            raise PasswordDeleteError("missing") from exc


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


@pytest.fixture
def context(tmp_path: Path):
    ctx = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        yield ctx
    finally:
        ctx.close()


def _install_success_routes() -> None:
    respx.get(REPO_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 123,
                "name": "hello",
                "full_name": "octo/hello",
                "owner": {"login": "octo"},
                "description": "demo",
                "html_url": "https://github.com/octo/hello",
                "default_branch": "main",
                "stargazers_count": 1,
                "license": {"spdx_id": "MIT"},
                "topics": ["python"],
            },
        )
    )
    release = {
        "id": 9,
        "tag_name": "v1",
        "name": "v1",
        "body": "notes",
        "html_url": "https://github.com/octo/hello/releases/tag/v1",
        "published_at": "2024-01-01T00:00:00Z",
        "created_at": "2024-01-01T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "tarball_url": TAR_URL,
        "zipball_url": ZIP_URL,
        "assets": [],
    }
    respx.get(LATEST_URL).mock(return_value=httpx.Response(200, json=release))
    respx.get(README_URL).mock(return_value=httpx.Response(200, text="<p>Hello</p>"))
    respx.get(ZIP_URL).mock(return_value=httpx.Response(200, content=b"zip-body"))
    respx.get(TAR_URL).mock(return_value=httpx.Response(200, content=b"tar-body"))
    respx.get(SNAP_URL).mock(return_value=httpx.Response(200, content=b"snapshot-body"))


def test_parse_tag_input_dedupes() -> None:
    assert parse_tag_input("a, b, a, c") == ["a", "b", "c"]


def test_archive_success_writes_real_files(context) -> None:
    context.documents.create_document("Keep me", "still here")
    with respx.mock:
        _install_success_routes()
        result = ArchiveService(context.connection).archive_repository("https://github.com/octo/hello")
    assert result.status in {"COMPLETED", "PARTIAL", "OFFLINE_PAGE_FAILED"}
    layout = PathLayout(context.settings.get_archive_root())
    archive = layout.release_source_file("octo", "hello", 123, "v1", kind="zip")
    assert archive.is_file()
    assert archive.read_bytes() == b"zip-body"
    docs = context.documents.list_documents()
    assert any(doc.title == "Keep me" for doc in docs)
    github = GitHubBridge(context.connection, context.database_path)
    listing = json.loads(github.listRepositories("", "", "recent"))
    assert listing["ok"] is True
    assert listing["data"]["hasRepositories"] is True


def test_archive_root_change_is_used_by_the_next_archive(context, tmp_path: Path) -> None:
    settings = SettingsBridge(context.settings)
    first = PathLayout(tmp_path / "a")
    second_root = tmp_path / "b"
    with respx.mock:
        _install_success_routes()
        ArchiveService(context.connection).archive_repository("https://github.com/octo/hello")
        assert first.release_source_file("octo", "hello", 123, "v1", kind="zip").is_file()
        saved = json.loads(settings.setArchiveRoot(str(second_root)))
        assert saved["ok"] is True
        ArchiveService(context.connection).archive_repository("https://github.com/octo/hello")
    second = PathLayout(second_root)
    assert second.release_source_file("octo", "hello", 123, "v1", kind="zip").is_file()


def test_invalid_url_status(context) -> None:
    result = ArchiveService(context.connection).archive_repository("http://github.com/octo/hello")
    assert result.message == user_message("INVALID_URL")


def test_repo_not_visible_status(context) -> None:
    with respx.mock:
        respx.get(REPO_URL).mock(return_value=httpx.Response(404))
        result = ArchiveService(context.connection).archive_repository("https://github.com/octo/hello")
    assert result.message == user_message("REPO_NOT_VISIBLE")


def test_archive_worker_is_not_the_gui_thread(qtbot, context) -> None:
    with respx.mock:
        _install_success_routes()
        worker = ArchiveWorker(context.database_path, "https://github.com/octo/hello")
        worker.start()
        qtbot.waitUntil(lambda: worker.thread_marker is not None, timeout=10000)
        assert worker.thread_marker is not QThread.currentThread()
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=20000)


def test_settings_archive_root_round_trip(context, tmp_path: Path) -> None:
    bridge = SettingsBridge(context.settings)
    chosen = tmp_path / "chosen"
    payload = json.loads(bridge.setArchiveRoot(str(chosen)))
    assert payload["ok"] is True
    status = json.loads(bridge.getStatus())
    assert Path(status["data"]["archiveRoot"]) == chosen.resolve()


def test_token_save_shows_configured_and_stays_out_of_sqlite(context, memory_keyring) -> None:
    bridge = SettingsBridge(context.settings)
    payload = json.loads(bridge.saveToken("ghp_phase6secretvalue"))
    assert payload["ok"] is True
    assert payload["data"]["tokenStatus"] == "已配置"
    blob = Path(context.database_path).read_bytes()
    assert b"ghp_phase6secretvalue" not in blob


def test_token_clear(context, memory_keyring) -> None:
    bridge = SettingsBridge(context.settings)
    json.loads(bridge.saveToken("ghp_clear_me"))
    cleared = json.loads(bridge.clearToken())
    assert cleared["ok"] is True
    assert cleared["data"]["tokenStatus"] == "未配置"
