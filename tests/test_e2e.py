"""End-to-end gates via Slate bridges and ArchiveWorker (legacy Widgets removed)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import keyring
import pytest
import respx
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from PySide6.QtCore import QThread

from techvault.app.bootstrap import prepare_context
from techvault.app.messages import user_message
from techvault.archive.paths import PathLayout
from techvault.db.connection import connect
from techvault.logging_setup import configure_logging, get_logger
from techvault.security.token_store import load_token
from techvault.ui.app import main
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.bridge.knowledge_bridge import KnowledgeBridge
from techvault.ui.bridge.settings_bridge import SettingsBridge

TOKEN = "ghp_TEST_ONLY_REDACTED"
ONLINE = "https://github.com/octocat/hello-world"
REPO_API = "https://api.github.com/repos/octocat/hello-world"
LATEST_API = "https://api.github.com/repos/octocat/hello-world/releases/latest"
README_API = "https://api.github.com/repos/octocat/hello-world/readme"
MARKDOWN_API = "https://api.github.com/markdown"
IMAGE_URL = "https://raw.githubusercontent.com/octocat/hello-world/main/logo.png"
README_HTML = (
    "<p>Read the guide</p>"
    "<script>alert(1)</script>"
    f'<img src="{IMAGE_URL}" alt="logo">'
    '<a href="javascript:alert(1)">bad</a>'
)
MARKDOWN_HTML = "<p>Use the archive.</p><script>nope</script>"
PNG = b"png-bytes"


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
def context(tmp_path):
    opened = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        yield opened
    finally:
        opened.close()




def test_fresh_instance_launches_and_exits(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    env = os.environ.copy()
    env.pop("TECHVAULT_DATABASE", None)
    env.pop("TECHVAULT_ARCHIVE_ROOT", None)
    env.pop("TECHVAULT_SLATE_SHELL_DEMO", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["LOCALAPPDATA"] = str(appdata)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["TECHVAULT_SMOKE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "techvault"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0
    assert "Traceback" not in completed.stdout
    assert "Traceback" not in completed.stderr
    database = appdata / "TechVault" / "techvault.sqlite"
    assert database.is_file()
    connection = connect(database)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        versions = [
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    finally:
        connection.close()


def test_startup_failure_is_visible(qtbot, tmp_path: Path, monkeypatch) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr("techvault.ui.app.default_database_path", lambda: blocker / "techvault.sqlite")
    monkeypatch.delenv("TECHVAULT_DATABASE", raising=False)
    shown: list[str] = []
    created: list[object] = []
    monkeypatch.setattr("techvault.ui.app.QMessageBox.critical", lambda *args: shown.append(args[-1]))
    import techvault.ui.desktop_window as desktop_mod

    def _no_window(*args, **kwargs):
        created.append(True)
        raise AssertionError("DesktopWindow must not be created when DB init fails")

    monkeypatch.setattr(desktop_mod, "DesktopWindow", _no_window)
    assert main([]) == 1
    assert shown == ["数据库初始化失败"]
    assert created == []


def test_knowledge_base_end_to_end(context) -> None:
    bridge = KnowledgeBridge(context.documents)
    git = json.loads(bridge.createCategory("Git"))["data"]
    saved = json.loads(
        bridge.saveDocument(
            json.dumps(
                {
                    "title": "Git 查看远程地址",
                    "content": "git remote -v",
                    "docType": "COMMAND",
                    "categoryId": git["id"],
                    "tags": ["git", "remote"],
                }
            )
        )
    )["data"]
    json.loads(
        bridge.saveDocument(
            json.dumps(
                {
                    "title": "其他笔记",
                    "content": "nothing",
                    "docType": "NOTE",
                    "tags": ["notes"],
                }
            )
        )
    )
    search = json.loads(bridge.listDocuments(json.dumps({"query": "remote", "offset": 0})))["data"]
    assert [i["title"] for i in search["items"]] == ["Git 查看远程地址"]
    tagged = json.loads(bridge.listDocuments(json.dumps({"tags": "remote", "offset": 0})))["data"]
    assert [i["title"] for i in tagged["items"]] == ["Git 查看远程地址"]
    fav = json.loads(bridge.setFavorite(saved["id"], True))["data"]
    assert fav["isFavorite"] is True
    only = json.loads(bridge.listDocuments(json.dumps({"favoriteOnly": True, "offset": 0})))["data"]
    assert [i["title"] for i in only["items"]] == ["Git 查看远程地址"]
    json.loads(bridge.deleteDocument(saved["id"]))
    assert json.loads(bridge.getDocument(saved["id"]))["ok"] is False


def _run(qtbot, context, url: str):
    worker = ArchiveWorker(context.database_path, url)
    done: list = []
    worker.completed.connect(done.append)
    worker.start()
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=20000)
    assert done
    return done[0], worker


def test_archive_disk_offline_page_and_thread_boundary(qtbot, context, monkeypatch, capsys) -> None:
    state = {"tag": "v1", "delay": True, "busy": False}
    gui = threading.get_ident()
    http_threads: set[int] = set()
    hash_threads: set[int] = set()
    download_threads: set[int] = set()
    page_threads: set[int] = set()
    opened: list[object] = []
    _spy_threads(monkeypatch, http_threads, hash_threads, download_threads, page_threads, opened, state)
    with respx.mock:
        _install_hello_world(state)
        state["arm"] = True
        result, worker = _run(qtbot, context, ONLINE)
    state["arm"] = False
    assert result.message == "已更新" or result.status in {"COMPLETED", "SUCCESS"}
    assert worker.thread_marker != QThread.currentThread()
    assert http_threads and gui not in http_threads
    assert hash_threads and gui not in hash_threads
    assert download_threads and gui not in download_threads
    assert page_threads and gui not in page_threads
    assert opened
    assert all(item is not context.connection for item in opened)
    layout = PathLayout(context.settings.get_archive_root())
    index = layout.page_index("octocat", "hello-world", 12345)
    zip_path = layout.release_source_file("octocat", "hello-world", 12345, "v1", kind="zip")
    assert index.is_file()
    assert zip_path.read_bytes() == b"zip-v1"
    _assert_hash(context.connection, zip_path)
    index_html = index.read_text(encoding="utf-8")
    _assert_safe(index_html)
    print("E2E_TREE_BEGIN")
    print(_tree(layout.archive_root))
    print("E2E_TREE_END")
    captured = capsys.readouterr()
    assert "github/octocat/hello-world/offline/index.html" in captured.out.replace("\\", "/")


def test_current_release_atomicity(qtbot, context) -> None:
    state = {"tag": "v1", "delay": False, "busy": False}
    with respx.mock:
        _install_hello_world(state)
        _run(qtbot, context, ONLINE)
        first = _current(context.connection)
        assert first["tag_name"] == "v1"
        state["tag"] = "v2"
        _run(qtbot, context, ONLINE)
        second = _current(context.connection)
        assert second["tag_name"] == "v2"
        assert second["id"] != first["id"]


def test_checksum_failure_and_first_archive_failure(qtbot, context, monkeypatch) -> None:
    with respx.mock:
        _install_broken()
        result, _ = _run(qtbot, context, "https://github.com/octocat/broken")
    assert _current_or_none(context.connection) is None
    assert result.status in {"FAILED", "PARTIAL"} or "失败" in result.message


def test_cancellation_from_worker(qtbot, context, monkeypatch) -> None:
    state = {"busy": False}
    workers: list[ArchiveWorker] = []
    with respx.mock:
        _install_stop(state, workers)
        worker = ArchiveWorker(context.database_path, "https://github.com/octocat/stop")
        workers.append(worker)
        done: list = []
        worker.completed.connect(done.append)
        worker.start()
        qtbot.waitUntil(lambda: state["busy"] or not worker.isRunning(), timeout=10000)
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=20000)
    assert done
    assert done[0].message == user_message("CANCELLED") or done[0].status == "CANCELLED" or "取消" in done[0].message


def test_settings_root_survives_restart(tmp_path: Path) -> None:
    first = prepare_context(tmp_path / "one.sqlite", tmp_path / "a")
    chosen = tmp_path / "chosen-root"
    try:
        bridge = SettingsBridge(first.settings)
        assert json.loads(bridge.setArchiveRoot(str(chosen)))["ok"]
    finally:
        first.close()
    # reopen same DB without archive_root override so settings table wins
    second = prepare_context(tmp_path / "one.sqlite")
    try:
        status = json.loads(SettingsBridge(second.settings).getStatus())["data"]
        assert Path(status["archiveRoot"]) == chosen.resolve()
    finally:
        second.close()


def test_token_stays_out_of_sqlite_and_logs(context, tmp_path: Path) -> None:
    bridge = SettingsBridge(context.settings)
    assert json.loads(bridge.saveToken(TOKEN))["ok"]
    status = json.loads(bridge.getStatus())["data"]
    assert status["tokenStatus"] == "已配置"
    assert TOKEN not in json.dumps(status)
    assert load_token() == TOKEN
    assert TOKEN.encode("utf-8") not in _database_bytes(context.database_path)
    log_dir = tmp_path / "logs"
    configure_logging(log_dir)
    get_logger().error("Authorization: Bearer %s", TOKEN)
    logged = (log_dir / "techvault.log").read_text(encoding="utf-8")
    assert TOKEN not in logged
    assert "[REDACTED]" in logged
    assert json.loads(bridge.clearToken())["ok"]
    assert load_token() is None


def test_existing_suite_has_no_skips() -> None:
    root = Path(__file__).resolve().parent
    for path in root.glob("test_*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("assert "):
                continue
            assert "pytest.mark.skip" not in stripped
            assert "pytest.mark.xfail" not in stripped
            assert not stripped.startswith("pytest.skip(")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


_SPEC = {
    "v1": {"id": 11, "asset_id": 501, "asset_name": "hello.txt", "asset": b"hello-asset-v1", "zip": b"zip-v1", "tar": b"tar-v1"},
    "v2": {"id": 22, "asset_id": 502, "asset_name": "hello-v2.txt", "asset": b"hello-asset-v2", "zip": b"zip-v2", "tar": b"tar-v2"},
    "v3": {"id": 33, "asset_id": 503, "asset_name": "hello-v3.txt", "asset": b"v3-asset", "zip": b"zip-v3", "tar": b"tar-v3"},
}


def _install_hello_world(state: dict) -> None:
    respx.get(REPO_API).mock(return_value=httpx.Response(200, json=_repository(12345, "hello-world", ONLINE)))
    respx.get(LATEST_API).mock(side_effect=lambda request: httpx.Response(200, json=_release(state["tag"])))
    respx.get(README_API).mock(return_value=httpx.Response(200, text=README_HTML))
    respx.post(MARKDOWN_API).mock(return_value=httpx.Response(200, text=MARKDOWN_HTML))
    respx.get(IMAGE_URL).mock(return_value=httpx.Response(200, content=PNG))
    respx.get("https://api.github.com/repos/octocat/hello-world/zipball/main").mock(
        return_value=httpx.Response(200, content=b"snapshot-main")
    )
    for tag, spec in _SPEC.items():
        respx.get(_zip_url(tag)).mock(side_effect=_zip_handler(tag, spec["zip"], state))
        respx.get(_tar_url(tag)).mock(return_value=httpx.Response(200, content=spec["tar"]))
        digest = _digest(b"not-the-body") if tag == "v3" else _digest(spec["asset"])
        respx.get(f"https://release-assets.githubusercontent.com/assets/{spec['asset_id']}").mock(
            return_value=httpx.Response(200, content=spec["asset"])
        )
        spec["digest"] = digest


def _zip_handler(tag: str, body: bytes, state: dict):
    def handler(request):
        if tag == "v1" and state.get("delay"):
            state["busy"] = True
            time.sleep(0.4)
            state["busy"] = False
        return httpx.Response(200, content=body)

    return handler


def _install_broken() -> None:
    body = b"broken-asset"
    respx.get("https://api.github.com/repos/octocat/broken").mock(
        return_value=httpx.Response(200, json=_repository(888, "broken", "https://github.com/octocat/broken"))
    )
    respx.get("https://api.github.com/repos/octocat/broken/releases/latest").mock(
        return_value=httpx.Response(
            200,
            json=_release_custom(
                tag="v9",
                github_id=90,
                asset_id=900,
                asset_name="broken.txt",
                asset_body=body,
                asset_digest=_digest(b"other-bytes"),
                zip_url="https://codeload.github.com/octocat/broken/zipball/v9",
                tar_url="https://codeload.github.com/octocat/broken/tarball/v9",
                repo="broken",
            ),
        )
    )
    respx.get("https://codeload.github.com/octocat/broken/zipball/v9").mock(
        return_value=httpx.Response(200, content=b"zip-broken")
    )
    respx.get("https://codeload.github.com/octocat/broken/tarball/v9").mock(
        return_value=httpx.Response(200, content=b"tar-broken")
    )
    respx.get("https://release-assets.githubusercontent.com/assets/900").mock(
        return_value=httpx.Response(200, content=body)
    )
    respx.get("https://api.github.com/repos/octocat/broken/zipball/main").mock(
        return_value=httpx.Response(200, content=b"snapshot-broken")
    )


def _install_stop(state: dict, workers: list[ArchiveWorker]) -> None:
    respx.get("https://api.github.com/repos/octocat/stop").mock(
        return_value=httpx.Response(200, json=_repository(77, "stop", "https://github.com/octocat/stop"))
    )
    respx.get("https://api.github.com/repos/octocat/stop/releases/latest").mock(
        return_value=httpx.Response(
            200,
            json=_release_custom(
                tag="v1",
                github_id=70,
                asset_id=700,
                asset_name="stop.txt",
                asset_body=b"stop-asset",
                asset_digest=_digest(b"stop-asset"),
                zip_url="https://codeload.github.com/octocat/stop/zipball/v1",
                tar_url="https://codeload.github.com/octocat/stop/tarball/v1",
                repo="stop",
            ),
        )
    )
    respx.get("https://codeload.github.com/octocat/stop/zipball/v1").mock(
        return_value=httpx.Response(200, content=b"zip-stop")
    )

    def tar(request):
        state["busy"] = True
        workers[0].cancel()
        time.sleep(0.4)
        state["busy"] = False
        return httpx.Response(200, content=b"tar-stop")

    respx.get("https://codeload.github.com/octocat/stop/tarball/v1").mock(side_effect=tar)
    respx.get("https://release-assets.githubusercontent.com/assets/700").mock(
        return_value=httpx.Response(200, content=b"stop-asset")
    )
    respx.get("https://api.github.com/repos/octocat/stop/zipball/main").mock(
        return_value=httpx.Response(200, content=b"snapshot-stop")
    )


def _repository(repo_id: int, name: str, html_url: str) -> dict:
    return {
        "id": repo_id,
        "name": name,
        "full_name": f"octocat/{name}",
        "html_url": html_url,
        "description": "Hello fixture",
        "stargazers_count": 3,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "octocat"},
    }


def _release(tag: str) -> dict:
    spec = _SPEC[tag]
    return _release_custom(
        tag=tag,
        github_id=spec["id"],
        asset_id=spec["asset_id"],
        asset_name=spec["asset_name"],
        asset_body=spec["asset"],
        asset_digest=spec.get("digest") or (_digest(b"not-the-body") if tag == "v3" else _digest(spec["asset"])),
        zip_url=_zip_url(tag),
        tar_url=_tar_url(tag),
        repo="hello-world",
    )


def _release_custom(
    *,
    tag: str,
    github_id: int,
    asset_id: int,
    asset_name: str,
    asset_body: bytes,
    asset_digest: str,
    zip_url: str,
    tar_url: str,
    repo: str,
) -> dict:
    return {
        "id": github_id,
        "tag_name": tag,
        "name": f"{tag} release",
        "body": "Use the archive.",
        "html_url": f"https://github.com/octocat/{repo}/releases/tag/{tag}",
        "published_at": "2026-02-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "tarball_url": tar_url,
        "zipball_url": zip_url,
        "assets": [
            {
                "id": asset_id,
                "name": asset_name,
                "size": len(asset_body),
                "content_type": "text/plain",
                "state": "uploaded",
                "digest": asset_digest,
                "browser_download_url": f"https://release-assets.githubusercontent.com/assets/{asset_id}",
                "created_at": "2026-02-01T00:00:01Z",
                "updated_at": "2026-02-01T00:00:02Z",
            }
        ],
    }


def _zip_url(tag: str) -> str:
    return f"https://codeload.github.com/octocat/hello-world/zipball/{tag}"


def _tar_url(tag: str) -> str:
    return f"https://codeload.github.com/octocat/hello-world/tarball/{tag}"


def _spy_threads(monkeypatch, http_threads, hash_threads, download_threads, page_threads, opened, state) -> None:
    import techvault.app.archive_service as archive_service
    import techvault.archive.downloader as downloader
    import techvault.archive.release_archive as release_archive
    import techvault.github.client as client_mod
    import techvault.ui.archive_worker as worker_mod

    real_request = client_mod.GitHubClient.request

    def request(self, method, url, **kwargs):
        http_threads.add(threading.get_ident())
        return real_request(self, method, url, **kwargs)

    real_sha = downloader.hashlib.sha256

    def sha256(*args, **kwargs):
        if state.get("arm"):
            hash_threads.add(threading.get_ident())
        return real_sha(*args, **kwargs)

    real_download = release_archive.download_release_file

    def download(*args, **kwargs):
        download_threads.add(threading.get_ident())
        return real_download(*args, **kwargs)

    real_pages = archive_service.build_offline_pages

    def pages(*args, **kwargs):
        page_threads.add(threading.get_ident())
        return real_pages(*args, **kwargs)

    real_connect = worker_mod.connect

    def wrapped(path):
        connection = real_connect(path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(client_mod.GitHubClient, "request", request)
    monkeypatch.setattr(downloader.hashlib, "sha256", sha256)
    monkeypatch.setattr(release_archive, "download_release_file", download)
    monkeypatch.setattr(archive_service, "build_offline_pages", pages)
    monkeypatch.setattr(worker_mod, "connect", wrapped)


def _spy_marks(monkeypatch, seen: list[tuple[str, str, str | None]]) -> None:
    import techvault.archive.release_archive as release_archive

    original = release_archive._mark

    def marked(connection, file_id, state, code, message):
        row = connection.execute("SELECT name FROM release_files WHERE id = ?", (file_id,)).fetchone()
        seen.append((row["name"], state, code))
        return original(connection, file_id, state, code, message)

    monkeypatch.setattr(release_archive, "_mark", marked)


def _assert_hash(connection, path: Path) -> None:
    row = connection.execute(
        "SELECT local_sha256, actual_size, state FROM release_files WHERE relative_path LIKE ?",
        (f"%{path.name}",),
    ).fetchone()
    data = path.read_bytes()
    assert row["state"] == "COMPLETED"
    assert row["actual_size"] == len(data)
    assert row["local_sha256"] == hashlib.sha256(data).hexdigest()


def _assert_safe(html: str) -> None:
    lowered = html.lower()
    for token in ("<script", "<iframe", "<object", "<embed", "<form", "javascript:", "vbscript:", "file:", "data:"):
        assert token not in lowered
    assert 'src="http' not in lowered
    assert "src='http" not in lowered


def _current(connection):
    row = connection.execute(
        """
        SELECT releases.id, releases.github_release_id, releases.tag_name
        FROM repositories
        JOIN releases ON releases.id = repositories.current_release_id
        WHERE repositories.github_repo_id = 12345
        """
    ).fetchone()
    assert row is not None
    return row


def _current_or_none(connection):
    row = connection.execute(
        """
        SELECT current_release_id FROM repositories
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return row["current_release_id"]


def _tree(root: Path) -> str:
    lines = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        lines.append(relative + ("/" if path.is_dir() else ""))
    return "\n".join(lines)


def _dump(connection) -> str:
    sections = []
    queries = {
        "repositories": "SELECT id, github_repo_id, owner, name, html_url, current_release_id FROM repositories",
        "releases": "SELECT id, github_release_id, tag_name, name, lookup_outcome FROM releases",
        "release_files": "SELECT release_id, kind, name, state, failure_code, actual_size, upstream_digest, local_sha256 FROM release_files",
        "offline_pages": "SELECT page_type, status, failure_code, relative_path FROM offline_pages",
        "jobs": "SELECT id, status, target_release_id FROM jobs",
    }
    for name, sql in queries.items():
        rows = connection.execute(sql).fetchall()
        sections.append(name)
        for row in rows:
            sections.append("  " + ", ".join(f"{key}={row[key]}" for key in row.keys()))
    return "\n".join(sections)


def _database_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    for suffix in ("-wal", "-shm"):
        extra = Path(str(path) + suffix)
        if extra.exists():
            data += extra.read_bytes()
    return data
