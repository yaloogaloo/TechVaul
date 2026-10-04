"""End-to-end gate. These tests drive the UI through the existing application services."""

from __future__ import annotations

import hashlib
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
from PySide6.QtWidgets import QApplication, QMessageBox

from techvault.app.bootstrap import prepare_context
from techvault.app.messages import user_message
from techvault.archive.paths import PathLayout
from techvault.db.connection import connect
from techvault.logging_setup import configure_logging, get_logger
from techvault.security.token_store import load_token
from techvault.ui.app import main
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.main_window import MainWindow

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
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0
    assert "Traceback" not in completed.stdout
    assert "Traceback" not in completed.stderr
    database = appdata / "TechVault" / "techvault.sqlite"
    assert database.is_file()
    assert (appdata / "TechVault" / "archive").is_dir()
    assert (appdata / "TechVault" / "logs").is_dir()
    connection = connect(database)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        versions = [
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        assert versions == [1, 2, 3, 4, 5, 6]
        names = [row["name"] for row in connection.execute("SELECT name FROM categories ORDER BY sort_order, id")]
        assert names == ["Command", "AI Prompt", "Development", "Operation", "Other"]
        assert connection.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) AS n FROM repositories").fetchone()["n"] == 0
    finally:
        connection.close()


def test_startup_failure_is_visible(qtbot, tmp_path: Path, monkeypatch) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr("techvault.ui.app.default_database_path", lambda: blocker / "techvault.sqlite")
    shown: list[str] = []
    created: list[object] = []
    monkeypatch.setattr("techvault.ui.app.QMessageBox.critical", lambda *args: shown.append(args[-1]))
    monkeypatch.setattr("techvault.ui.app.MainWindow", lambda *args, **kwargs: created.append(True))
    assert main([]) == 1
    assert shown == ["数据库初始化失败"]
    assert created == []


def test_knowledge_base_end_to_end(qtbot, context) -> None:
    git = context.documents.create_category("Git", sort_order=10)
    window = MainWindow(context)
    qtbot.addWidget(window)
    window.show()
    page = window.knowledge_page
    _choose_combo(page.category_combo, "Git")
    page.type_combo.setCurrentIndex(page.type_combo.findData("COMMAND"))
    page.title_edit.setText("Git 查看远程地址")
    page.tags_edit.setText("git, remote")
    page.content_edit.setPlainText("git remote -v")
    page.save_document()
    page.begin_new()
    _choose_combo(page.category_combo, "Other")
    page.type_combo.setCurrentIndex(page.type_combo.findData("NOTE"))
    page.title_edit.setText("其他笔记")
    page.tags_edit.setText("notes")
    page.content_edit.setPlainText("nothing")
    page.save_document()

    reread = connect(context.database_path)
    try:
        stored = reread.execute(
            "SELECT body, doc_type FROM documents WHERE title = ?",
            ("Git 查看远程地址",),
        ).fetchone()
        assert stored["body"] == "git remote -v"
        assert stored["doc_type"] == "COMMAND"
    finally:
        reread.close()
    command = next(item for item in page.service.list_documents() if item.title == "Git 查看远程地址")
    assert command.content == "git remote -v"
    assert command.doc_type == "COMMAND"
    assert command.category_id == git.id
    assert [tag.name for tag in command.tags] == ["git", "remote"]

    page.search_edit.setText("remote")
    qtbot.waitUntil(lambda: _titles(page) == ["Git 查看远程地址"], timeout=2000)
    assert _column(page, 1) == ["命令"]

    page.search_edit.setText("")
    qtbot.waitUntil(lambda: len(_titles(page)) == 2, timeout=2000)
    _choose_list_category(page, "Git")
    assert _titles(page) == ["Git 查看远程地址"]
    assert _column(page, 1) == ["命令"]
    assert page.category_list.currentItem().data(256) == git.id

    _choose_list_category(page, "全部")
    page.tag_filter.setText("remote")
    qtbot.waitUntil(lambda: _titles(page) == ["Git 查看远程地址"], timeout=2000)
    assert _column(page, 1) == ["命令"]
    page.tag_filter.setText("")
    qtbot.waitUntil(lambda: len(_titles(page)) == 2, timeout=2000)

    before = context.connection.execute(
        "SELECT updated_at FROM documents WHERE id = ?",
        (command.id,),
    ).fetchone()["updated_at"]
    command_row = _titles(page).index("Git 查看远程地址")
    page._on_cell(command_row, 3)
    after = context.connection.execute(
        "SELECT updated_at, is_favorite FROM documents WHERE id = ?",
        (command.id,),
    ).fetchone()
    assert after["is_favorite"] == 1
    assert after["updated_at"] == before
    page.favorite_filter.setChecked(True)
    assert _titles(page) == ["Git 查看远程地址"]
    assert _column(page, 3) == ["★"]

    reloaded = MainWindow(context)
    qtbot.addWidget(reloaded)
    reloaded.knowledge_page.favorite_filter.setChecked(True)
    assert _titles(reloaded.knowledge_page) == ["Git 查看远程地址"]
    assert _column(reloaded.knowledge_page, 3) == ["★"]
    assert context.connection.execute(
        "SELECT updated_at FROM documents WHERE id = ?",
        (command.id,),
    ).fetchone()["updated_at"] == before

    page.favorite_filter.setChecked(False)
    page._on_cell(_titles(page).index("Git 查看远程地址"), 0)
    page.copy_document()
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == "git remote -v"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("techvault.ui.knowledge_page.confirm_delete", lambda _parent: True)
    try:
        page.delete_document()
    finally:
        monkeypatch.undo()
    assert "Git 查看远程地址" not in _titles(page)
    page.search_edit.setText("remote")
    qtbot.waitUntil(lambda: _titles(page) == [], timeout=2000)
    assert page.service.search_documents("remote") == []
    assert context.connection.execute(
        "SELECT COUNT(*) AS n FROM document_tags WHERE document_id = ?",
        (command.id,),
    ).fetchone()["n"] == 0
    assert context.connection.execute(
        """
        SELECT COUNT(*) AS n FROM document_tags
        LEFT JOIN documents ON documents.id = document_tags.document_id
        WHERE documents.id IS NULL
        """
    ).fetchone()["n"] == 0
    assert any(item.name == "Git" for item in page.service.list_categories())
    assert page.service.get_document(
        next(item.id for item in page.service.list_documents() if item.title == "其他笔记")
    ).title == "其他笔记"


def test_archive_disk_offline_page_and_thread_boundary(qtbot, context, monkeypatch, capsys) -> None:
    window = MainWindow(context)
    qtbot.addWidget(window)
    window.show()
    state = {"tag": "v1", "delay": True, "busy": False}
    gui = threading.get_ident()
    http_threads: set[int] = set()
    hash_threads: set[int] = set()
    download_threads: set[int] = set()
    page_threads: set[int] = set()
    opened: list[object] = []
    _spy_threads(monkeypatch, http_threads, hash_threads, download_threads, page_threads, opened, state)
    page = window.archive_page
    with respx.mock:
        _install_hello_world(state)
        state["arm"] = True
        page.url_edit.setText(ONLINE)
        page.archive_button.click()
        qtbot.waitUntil(lambda: state["busy"], timeout=10000)
        window.show_section(0)
        window.knowledge_page.search_edit.setText("responsive")
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    state["arm"] = False
    assert window.knowledge_page.search_edit.text() == "responsive"
    assert page._worker is not None
    assert not page._worker.isRunning()
    assert page._worker.thread_marker != QThread.currentThread()
    assert http_threads and gui not in http_threads
    assert hash_threads and gui not in hash_threads
    assert download_threads and gui not in download_threads
    assert page_threads and gui not in page_threads
    assert opened
    assert all(item is not context.connection for item in opened)

    layout = PathLayout(context.settings.get_archive_root())
    repo_dir = layout.repository_dir("octocat", "hello-world", 12345)
    index = layout.page_index("octocat", "hello-world", 12345)
    release_page = layout.page_release("octocat", "hello-world", 12345)
    zip_path = layout.release_source_file("octocat", "hello-world", 12345, 11, "source-code.zip")
    tar_path = layout.release_source_file("octocat", "hello-world", 12345, 11, "source-code.tar.gz")
    asset_path = layout.release_asset_file("octocat", "hello-world", 12345, 11, 501, "hello.txt")
    assert index.is_file() and release_page.is_file()
    assert zip_path.read_bytes() == b"zip-v1"
    assert tar_path.read_bytes() == b"tar-v1"
    assert asset_path.read_bytes() == _SPEC["v1"]["asset"]
    assert list(repo_dir.rglob("*.part")) == []
    _assert_hash(context.connection, zip_path)
    _assert_hash(context.connection, tar_path)
    _assert_hash(context.connection, asset_path)
    asset = context.connection.execute(
        "SELECT upstream_digest, state FROM release_files WHERE name = ?",
        ("hello.txt",),
    ).fetchone()
    assert asset["state"] == "COMPLETED"
    assert asset["upstream_digest"] == _digest(_SPEC["v1"]["asset"])
    files = context.connection.execute(
        "SELECT state FROM release_files WHERE release_id = (SELECT current_release_id FROM repositories)"
    ).fetchall()
    assert files and all(row["state"] == "COMPLETED" for row in files)
    pages = context.connection.execute("SELECT page_type, status FROM offline_pages ORDER BY page_type").fetchall()
    assert [(row["page_type"], row["status"]) for row in pages] == [
        ("RELEASE", "COMPLETED"),
        ("REPOSITORY", "COMPLETED"),
    ]
    repository = context.connection.execute(
        "SELECT github_repo_id, owner, name, html_url, current_release_id FROM repositories"
    ).fetchone()
    assert repository["github_repo_id"] == 12345
    assert repository["owner"] == "octocat"
    assert repository["name"] == "hello-world"
    assert repository["html_url"] == ONLINE
    assert repository["current_release_id"] is not None
    index_html = index.read_text(encoding="utf-8")
    release_html = release_page.read_text(encoding="utf-8")
    assert "hello-world" in index_html
    assert "Hello fixture" in index_html
    assert "Read the guide" in index_html
    assert "v1" in release_html
    assert "v1 release" in release_html
    assert "Use the archive." in release_html
    assert "hello.txt" in release_html
    assert 'src="media/' in index_html
    assert any(path.is_file() for path in layout.page_media_dir("octocat", "hello-world", 12345).iterdir())
    _assert_safe(index_html)
    _assert_safe(release_html)
    assert page.release_label.text() == "v1"
    assert page.repository_label.text() == "octocat/hello-world"
    assert page.offline_label.text() == str(index)
    print("E2E_TREE_BEGIN")
    print(_tree(layout.archive_root))
    print("E2E_TREE_END")
    print("E2E_DB_BEGIN")
    print(_dump(context.connection))
    print("E2E_DB_END")
    captured = capsys.readouterr()
    assert "E2E_TREE_BEGIN" in captured.out
    tree = captured.out.replace("\\", "/")
    assert "repositories/12345/pages/index.html" in tree
    assert "repositories/octocat/hello-world__12345/" not in tree


def test_current_release_atomicity(qtbot, context) -> None:
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.archive_page
    state = {"tag": "v1", "delay": False, "busy": False}
    with respx.mock:
        _install_hello_world(state)
        _archive(qtbot, page, "已更新")
        first = _current(context.connection)
        assert first["tag_name"] == "v1"
        state["tag"] = "v2"
        _archive(qtbot, page, "已更新")
        second = _current(context.connection)
        assert second["tag_name"] == "v2"
        assert second["id"] != first["id"]
        state["tag"] = "v3"
        _archive(qtbot, page, user_message("PARTIAL"))
        kept = _current(context.connection)
        assert kept["id"] == second["id"]
        assert kept["tag_name"] == "v2"
        assert kept["github_release_id"] != 33
    layout = PathLayout(context.settings.get_archive_root())
    assert layout.release_source_file("octocat", "hello-world", 12345, 22, "source-code.zip").read_bytes() == b"zip-v2"
    assert page.release_label.text() == "v2"
    assert page.result_label.text() != "已更新"


def test_checksum_failure_and_first_archive_failure(qtbot, context, monkeypatch) -> None:
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.archive_page
    seen: list[tuple[str, str, str | None]] = []
    _spy_marks(monkeypatch, seen)
    with respx.mock:
        _install_broken()
        page.url_edit.setText("https://github.com/octocat/broken")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == user_message("PARTIAL"), timeout=20000)
    assert _current_or_none(context.connection) is None
    failed = context.connection.execute(
        "SELECT state, failure_code FROM release_files WHERE name = 'broken.txt'"
    ).fetchone()
    assert failed["state"] == "FAILED"
    assert failed["failure_code"] == "CHECKSUM_FAILURE"
    assert ("broken.txt", "VERIFYING", None) in seen
    assert page.result_label.text() != "已更新"
    assert "Traceback" not in page.result_label.text()
    root = PathLayout(context.settings.get_archive_root()).archive_root
    assert all("_tmp" in path.parts for path in root.rglob("*.part"))
    broken_dir = PathLayout(root).repository_dir("octocat", "broken", 888)
    assert list(broken_dir.rglob("*.part")) == []

    state = {"tag": "v1", "delay": False, "busy": False}
    with respx.mock:
        _install_hello_world(state)
        _archive(qtbot, page, "已更新")
        kept = _current(context.connection)
        state["tag"] = "v3"
        _archive(qtbot, page, user_message("PARTIAL"))
    assert _current(context.connection)["id"] == kept["id"]
    later = context.connection.execute(
        "SELECT state, failure_code, relative_path FROM release_files WHERE name = 'hello-v3.txt'"
    ).fetchone()
    assert later["state"] == "FAILED"
    assert later["failure_code"] == "CHECKSUM_FAILURE"
    assert later["relative_path"] is None
    layout = PathLayout(context.settings.get_archive_root())
    assert not layout.release_asset_file("octocat", "hello-world", 12345, 33, 503, "hello-v3.txt").exists()
    assert layout.release_source_file("octocat", "hello-world", 12345, 11, "source-code.zip").is_file()
    assert ("hello-v3.txt", "VERIFYING", None) in seen


def test_cancellation_from_the_ui(qtbot, context, monkeypatch) -> None:
    window = MainWindow(context)
    qtbot.addWidget(window)
    window.show()
    page = window.archive_page
    workers: list[ArchiveWorker] = []
    original = ArchiveWorker.start

    def start(self):
        workers.append(self)
        original(self)

    monkeypatch.setattr(ArchiveWorker, "start", start)
    state = {"busy": False}
    with respx.mock:
        _install_stop(state, workers)
        page.url_edit.setText("https://github.com/octocat/stop")
        page.archive_button.click()
        qtbot.waitUntil(lambda: state["busy"], timeout=10000)
        window.show_section(0)
        window.knowledge_page.search_edit.setText("alive")
        qtbot.waitUntil(lambda: page.result_label.text() == "已取消", timeout=20000)
    assert window.knowledge_page.search_edit.text() == "alive"
    assert page._worker is not None and not page._worker.isRunning()
    assert page.result_label.text() != "已更新"
    job = context.connection.execute("SELECT status FROM jobs ORDER BY id DESC").fetchone()
    assert job["status"] == "CANCELLED"
    assert _current_or_none(context.connection) is None
    layout = PathLayout(context.settings.get_archive_root())
    repo_dir = layout.repository_dir("octocat", "stop", 77)
    assert layout.release_source_file("octocat", "stop", 77, 70, "source-code.zip").is_file()
    assert not layout.release_source_file("octocat", "stop", 77, 70, "source-code.tar.gz").exists()
    parts = list(layout.archive_root.rglob("*.part"))
    assert parts
    assert all("_tmp" in path.parts for path in parts)
    assert list(repo_dir.rglob("*.part")) == []


def test_offline_failure_keeps_the_archived_release(qtbot, context, monkeypatch) -> None:
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.archive_page
    state = {"tag": "v1", "delay": False, "busy": False}
    import techvault.offline.pages as pages
    from techvault.offline.sanitize import sanitize_html as real_sanitize

    with respx.mock:
        _install_hello_world(state)
        monkeypatch.setattr(pages, "sanitize_html", lambda html: "<script>secret</script>")
        _archive(qtbot, page, user_message("OFFLINE_PAGE_FAILED"))
        layout = PathLayout(context.settings.get_archive_root())
        zip_path = layout.release_source_file("octocat", "hello-world", 12345, 11, "source-code.zip")
        assert zip_path.read_bytes() == b"zip-v1"
        current = _current(context.connection)
        assert current["tag_name"] == "v1"
        statuses = [row["status"] for row in context.connection.execute("SELECT status FROM offline_pages")]
        assert statuses
        assert set(statuses) == {"FAILED"}
        assert not layout.page_index("octocat", "hello-world", 12345).is_file()
        monkeypatch.setattr(pages, "sanitize_html", real_sanitize)
        _archive(qtbot, page, "已更新")
    assert _current(context.connection)["id"] == current["id"]
    assert zip_path.read_bytes() == b"zip-v1"
    assert layout.page_index("octocat", "hello-world", 12345).is_file()
    restored = [row["status"] for row in context.connection.execute("SELECT status FROM offline_pages")]
    assert restored
    assert set(restored) == {"COMPLETED"}


def test_settings_root_survives_restart(qtbot, tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    first = prepare_context(database, tmp_path / "first")
    window = MainWindow(first)
    qtbot.addWidget(window)
    chosen = tmp_path / "chosen"
    window.settings_page.root_edit.setText(str(chosen))
    window.settings_page.save_root()
    first.close()
    second = prepare_context(database)
    try:
        assert second.settings.get_archive_root() == chosen
        restored = MainWindow(second)
        qtbot.addWidget(restored)
        assert restored.settings_page.root_edit.text() == str(chosen)
    finally:
        second.close()


def test_token_stays_out_of_sqlite_and_logs(qtbot, context, tmp_path: Path) -> None:
    window = MainWindow(context)
    qtbot.addWidget(window)
    page = window.settings_page
    page.token_edit.setText(TOKEN)
    page.save_token()
    assert page.token_status.text() == "已配置"
    assert TOKEN not in page.token_status.text()
    assert TOKEN not in page.status_label.text()
    assert TOKEN not in page.token_edit.text()
    assert load_token() == TOKEN
    assert TOKEN.encode("utf-8") not in _database_bytes(context.database_path)
    log_dir = tmp_path / "logs"
    configure_logging(log_dir)
    get_logger().error("Authorization: Bearer %s", TOKEN)
    logged = (log_dir / "techvault.log").read_text(encoding="utf-8")
    assert TOKEN not in logged
    assert "[REDACTED]" in logged
    page.clear_token()
    assert page.token_status.text() == "未配置"
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


def _archive(qtbot, page, expected: str) -> None:
    page.url_edit.setText(ONLINE)
    page.archive_button.click()
    qtbot.waitUntil(lambda: page.result_label.text() == expected, timeout=20000)


def _titles(page) -> list[str]:
    return [_cell(page, row, 0) for row in range(page.document_list.count())]


def _column(page, column: int) -> list[str]:
    return [_cell(page, row, column) for row in range(page.document_list.count())]


def _cell(page, row: int, column: int) -> str:
    return page.cell_text(row, column)


def _choose_combo(combo, text: str) -> None:
    for index in range(combo.count()):
        if combo.itemText(index) == text:
            combo.setCurrentIndex(index)
            return
    raise AssertionError(text)


def _choose_list_category(page, name: str) -> None:
    for row in range(page.category_list.count()):
        if page.category_list.item(row).text() == name:
            page.category_list.setCurrentRow(row)
            return
    raise AssertionError(name)


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
