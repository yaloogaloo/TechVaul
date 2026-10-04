from pathlib import Path

import httpx
import keyring
import pytest
import respx
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from PySide6.QtCore import QObject, QThread, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox

from techvault.app.bootstrap import DATABASE_INIT_FAILED, open_window_context, prepare_context
from techvault.app.messages import user_message
from techvault.archive.paths import PathLayout
from techvault.documents.models import DocumentNotFoundError
from techvault.ui.app import main
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.knowledge_page import SEARCH_DELAY_MS
from techvault.ui.main_window import MainWindow
from techvault.ui.tags import parse_tag_input

REPO_URL = "https://api.github.com/repos/octo/hello"
LATEST_URL = "https://api.github.com/repos/octo/hello/releases/latest"
LIST_URL = "https://api.github.com/repos/octo/hello/releases?per_page=100"
README_URL = "https://api.github.com/repos/octo/hello/readme"
ZIP_URL = "https://codeload.github.com/octo/hello/zipball/v1"
TAR_URL = "https://codeload.github.com/octo/hello/tarball/v1"
ONLINE_URL = "https://github.com/octo/hello-online"
TOKEN = "ghp_phase6secretvalue"


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


class FailingKeyring(KeyringBackend):
    priority = 1

    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("keyring down")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("keyring down")

    def delete_password(self, service: str, username: str) -> None:
        raise RuntimeError("keyring down")


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
def vault(tmp_path):
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    try:
        yield context
    finally:
        context.close()


@pytest.fixture
def window(qtbot, vault):
    host = MainWindow(vault)
    qtbot.addWidget(host)
    host.show()
    yield host
    host.knowledge_page._search_timer.stop()


def test_parse_tag_input_trims_and_deduplicates() -> None:
    assert parse_tag_input(" git, Git, powershell, , windows ") == ["git", "powershell", "windows"]


def test_main_window_starts_with_modules(window: MainWindow) -> None:
    labels = [window.nav.item(row).text() for row in range(window.nav.count())]
    assert labels == ["知识", "GitHub 项目", "设置"]
    assert window.stack.currentWidget() is window.knowledge_page
    assert window.knowledge_page.objectName() == "knowledgePage"
    assert window.archive_page.objectName() == "archivePage"


def test_main_window_switches_sections(window: MainWindow) -> None:
    window.show_section(1)
    assert window.stack.currentWidget() is window.archive_page
    window.show_section(2)
    assert window.stack.currentWidget() is window.settings_page
    window.show_section(0)
    assert window.stack.currentWidget() is window.knowledge_page


def test_categories_come_from_the_database(window: MainWindow) -> None:
    stored = window.context.documents.list_categories()
    names = [window.knowledge_page.category_list.item(row).text() for row in range(1, window.knowledge_page.category_list.count())]
    assert names == [item.name for item in stored]
    assert "全部" == window.knowledge_page.category_list.item(0).text()
    for row, category in enumerate(stored, start=1):
        assert window.knowledge_page.category_list.item(row).data(256) == category.id


def test_new_document_defaults_do_not_write(window: MainWindow) -> None:
    page = window.knowledge_page
    before = page.service.list_documents()
    page.begin_new()
    assert page.title_edit.text() == ""
    assert page.content_edit.toPlainText() == ""
    assert page.type_combo.currentData() == "NOTE"
    assert page.favorite_box.isChecked() is False
    assert page.service.list_documents() == before


def test_create_save_edit_and_preserve_content(window: MainWindow) -> None:
    page = window.knowledge_page
    content = "if ($true) {\n    Write-Output 'x' \n}\n"
    page.title_edit.setText("PowerShell tips")
    page.content_edit.setPlainText(content)
    page.type_combo.setCurrentIndex(page.type_combo.findData("TIP"))
    page.save_document()
    stored = page.service.get_document(page._document_id)
    assert stored.content == content
    assert stored.doc_type == "TIP"
    page.title_edit.setText("PowerShell tips 2")
    page.content_edit.setPlainText(content + "  ")
    page.save_document()
    stored = page.service.get_document(stored.id)
    assert stored.title == "PowerShell tips 2"
    assert stored.content == content + "  "


def test_type_combo_stores_the_code_not_the_label(window: MainWindow, tmp_path: Path) -> None:
    page = window.knowledge_page
    page.title_edit.setText("Rebase")
    page.type_combo.setCurrentIndex(page.type_combo.findData("COMMAND"))
    assert page.type_combo.currentText() == "命令"
    page.content_edit.setPlainText("git rebase")
    page.save_document()
    stored = page.service.get_document(page._document_id)
    assert stored.doc_type == "COMMAND"
    assert "命令".encode("utf-8") not in _database_bytes(window.context.database_path)


def test_tags_are_deduplicated_without_changing_content(window: MainWindow) -> None:
    page = window.knowledge_page
    body = "Get-ChildItem\n"
    page.title_edit.setText("Listing")
    page.tags_edit.setText("git, Git, powershell")
    page.content_edit.setPlainText(body)
    page.save_document()
    stored = page.service.get_document(page._document_id)
    assert [tag.name for tag in stored.tags] == ["git", "powershell"]
    assert stored.content == body


def test_document_list_does_not_include_the_body(window: MainWindow) -> None:
    page = window.knowledge_page
    sentinel = "UNIQUE_BODY_SENTINEL_SHOULD_NOT_BE_IN_LIST"
    page.title_edit.setText("Visible title")
    page.content_edit.setPlainText(sentinel)
    page.save_document()
    texts = []
    for row in range(page.document_list.count()):
        for column in range(5):
            texts.append(page.cell_text(row, column))
    assert sentinel not in "\n".join(texts)
    assert "Visible title" in texts


def test_delete_cancel_does_not_change_the_database(window: MainWindow, monkeypatch) -> None:
    page = _save_note(window, "Keep", "body")
    monkeypatch.setattr("techvault.ui.knowledge_page.confirm_delete", lambda _parent: False)
    page.delete_document()
    assert page.service.get_document(page._document_id).title == "Keep"


def test_delete_confirm_removes_the_document(window: MainWindow, monkeypatch) -> None:
    page = _save_note(window, "Gone", "body")
    document_id = page._document_id
    monkeypatch.setattr("techvault.ui.knowledge_page.confirm_delete", lambda _parent: True)
    page.delete_document()
    with pytest.raises(DocumentNotFoundError):
        page.service.get_document(document_id)


def test_favorite_click_calls_toggle_favorite(window: MainWindow) -> None:
    page = _save_note(window, "Star me", "body")
    document_id = page._document_id
    called = []
    original = page.service.toggle_favorite

    def wrapped(value: int):
        called.append(value)
        return original(value)

    page.service.toggle_favorite = wrapped
    page._on_cell(0, 3)
    assert called == [document_id]
    assert page.service.get_document(document_id).is_favorite is True
    assert page.cell_text(0, 3) == "★"


def test_search_calls_search_documents(window: MainWindow) -> None:
    page = window.knowledge_page
    _save_note(window, "git rebase notes", "rebase carefully")
    _save_note(window, "unrelated", "nothing here")
    calls = []
    listed = []
    original_search = page.service.search_documents
    original_list = page.service.list_documents

    def search(*args, **kwargs):
        calls.append((args, kwargs))
        return original_search(*args, **kwargs)

    def listed_documents(*args, **kwargs):
        listed.append((args, kwargs))
        return original_list(*args, **kwargs)

    page.service.search_documents = search
    page.service.list_documents = listed_documents
    page.search_edit.setText("git rebase")
    page.refresh_documents()
    assert calls
    assert calls[0][0][0] == "git rebase"
    assert listed == []
    titles = [page.cell_text(row, 0) for row in range(page.document_list.count())]
    assert titles == ["git rebase notes"]


def test_search_debounce_waits_before_querying(qtbot, window: MainWindow) -> None:
    page = window.knowledge_page
    _save_note(window, "git rebase notes", "rebase carefully")
    _save_note(window, "unrelated", "nothing here")
    assert page.document_list.count() == 2
    assert SEARCH_DELAY_MS == 250
    page.search_edit.setText("rebase")
    assert page.document_list.count() == 2
    qtbot.waitUntil(lambda: page.document_list.count() == 1, timeout=1000)


def test_category_filter_uses_the_database_id(window: MainWindow) -> None:
    page = window.knowledge_page
    command = next(item for item in page.service.list_categories() if item.name == "Command")
    other = next(item for item in page.service.list_categories() if item.name == "Other")
    _save_with_category(page, "alpha", command.id)
    _save_with_category(page, "beta", other.id)
    _select_category(page, "Command")
    titles = [page.cell_text(row, 0) for row in range(page.document_list.count())]
    assert titles == ["alpha"]
    assert page.category_list.currentItem().data(256) == command.id


def test_favorite_filter_uses_the_service(window: MainWindow) -> None:
    page = window.knowledge_page
    _save_note(window, "plain", "a")
    _save_note(window, "saved", "b")
    page.favorite_box.setChecked(True)
    page.save_document()
    page.favorite_filter.setChecked(True)
    titles = [page.cell_text(row, 0) for row in range(page.document_list.count())]
    assert titles == ["saved"]


def test_copy_puts_only_the_body_on_the_clipboard(window: MainWindow) -> None:
    page = _save_note(window, "Title stays out", "echo hi\n")
    page.copy_document()
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == "echo hi\n"


def test_archive_success_writes_real_files(qtbot, window: MainWindow) -> None:
    _save_note(window, "Keep me", "still here")
    page = window.archive_page
    with respx.mock:
        _install_success_routes()
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    layout = PathLayout(window.context.settings.get_archive_root())
    index = layout.page_index("octo", "hello", 123)
    archive = layout.release_source_file("octo", "hello", 123, 9, "source-code.zip")
    assert index.is_file()
    assert archive.is_file()
    assert archive.read_bytes() == b"zip-body"
    assert page.offline_label.text() == str(index)
    assert page.release_label.text() == "v1"
    assert page.repository_label.text() == "octo/hello"
    assert page.open_offline_button.isEnabled() is True
    assert "Traceback" not in page.result_label.text()
    assert window.knowledge_page.service.get_document(window.knowledge_page._document_id).title == "Keep me"
    current = window.context.connection.execute(
        "SELECT current_release_id FROM repositories WHERE github_repo_id = 123"
    ).fetchone()
    assert current["current_release_id"] is not None


def test_archive_root_change_is_used_by_the_next_archive(qtbot, window: MainWindow, tmp_path: Path) -> None:
    page = window.archive_page
    first = PathLayout(tmp_path / "a")
    second_root = tmp_path / "b"
    with respx.mock:
        _install_success_routes()
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
        assert first.page_index("octo", "hello", 123).is_file()
        window.settings_page.root_edit.setText(str(second_root))
        window.settings_page.save_root()
        page.result_label.setText("")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    second = PathLayout(second_root)
    assert second.page_index("octo", "hello", 123).is_file()
    assert second.release_source_file("octo", "hello", 123, 9, "source-code.tar.gz").is_file()
    assert first.page_index("octo", "hello", 123).is_file()
    assert first.page_index("octo", "hello", 123) != second.page_index("octo", "hello", 123)


def test_current_release_label_follows_current_release_id(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    assert page.release_label.text() == "暂无正式 Release"
    with respx.mock:
        _install_success_routes()
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    current = window.context.connection.execute(
        """
        SELECT releases.tag_name
        FROM repositories
        JOIN releases ON releases.id = repositories.current_release_id
        WHERE repositories.github_repo_id = 123
        """
    ).fetchone()
    assert page.release_label.text() == current["tag_name"]
    repository = window.context.connection.execute(
        "SELECT id FROM repositories WHERE github_repo_id = 123"
    ).fetchone()
    window.context.connection.execute(
        """
        INSERT INTO releases (
            github_release_id, repository_id, tag_name, name, body, html_url,
            published_at, created_at, prerelease, draft, tarball_url, zipball_url,
            lookup_outcome, created_at_local, updated_at_local
        ) VALUES (99, ?, 'nightly', 'Nightly', '', 'https://github.com/octo/hello/releases/tag/nightly',
                  '2030-01-01T00:00:00Z', '2030-01-01T00:00:00Z', 0, 0, '', '',
                  'STABLE', '2030-01-01T00:00:00Z', '2030-01-01T00:00:00Z')
        """,
        (int(repository["id"]),),
    )
    assert page.release_label.text() == "v1"


def test_invalid_url_is_shown_without_a_traceback(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    page.url_edit.setText("http://github.com/octo/hello")
    page.archive_button.click()
    qtbot.waitUntil(lambda: page.result_label.text() != "", timeout=10000)
    assert page.result_label.text() == user_message("INVALID_URL")
    assert "Traceback" not in page.result_label.text()
    assert page.open_offline_button.isEnabled() is False


def test_repo_not_visible_status(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    with respx.mock:
        respx.get(REPO_URL).mock(return_value=httpx.Response(404))
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == user_message("REPO_NOT_VISIBLE"), timeout=10000)
    assert "Traceback" not in page.result_label.text()
    assert page.release_label.text() == "暂无正式 Release"


def test_no_releases_status(qtbot, window: MainWindow) -> None:
    _assert_source_snapshot_without_release(qtbot, window, [])


def test_only_prerelease_status(qtbot, window: MainWindow) -> None:
    _assert_source_snapshot_without_release(qtbot, window, [_release(prerelease=True)])


def test_no_official_latest_status(qtbot, window: MainWindow) -> None:
    _assert_source_snapshot_without_release(qtbot, window, [_release()])


def test_auth_invalid_status(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    with respx.mock:
        respx.get(REPO_URL).mock(return_value=httpx.Response(401))
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == user_message("AUTH_INVALID"), timeout=10000)
    assert "Traceback" not in page.result_label.text()
    assert page.result_label.text() != "已更新"


def test_api_error_status(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    with respx.mock:
        respx.get(REPO_URL).mock(return_value=httpx.Response(500))
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "GitHub API 请求失败。", timeout=10000)
    assert page.result_label.text() != "Unknown error"


def test_cancel_does_not_report_success(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    workers: list[ArchiveWorker] = []
    original = ArchiveWorker.start

    def start(self):
        workers.append(self)
        original(self)

    page_module_start = start

    def zip_response(request):
        workers[0].cancel()
        return httpx.Response(200, content=b"zip-body")

    with respx.mock:
        _install_success_routes(zip_handler=zip_response)
        ArchiveWorker.start = page_module_start
        try:
            page.url_edit.setText("https://github.com/octo/hello")
            page.archive_button.click()
            qtbot.waitUntil(lambda: page.result_label.text() == "已取消", timeout=20000)
        finally:
            ArchiveWorker.start = original
    assert page.result_label.text() != "已更新"
    assert page.open_offline_button.isEnabled() is False
    assert page.release_label.text() == "暂无正式 Release"
    current = window.context.connection.execute(
        "SELECT current_release_id FROM repositories WHERE github_repo_id = 123"
    ).fetchone()
    assert current["current_release_id"] is None


def test_archive_worker_is_not_the_gui_thread(qtbot, window: MainWindow) -> None:
    page = window.archive_page
    with respx.mock:
        _install_success_routes()
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(
            lambda: page._worker is not None
            and page._worker.thread_marker is not None
            and not page._worker.isRunning(),
            timeout=20000,
        )
    assert page._worker.thread_marker != QThread.currentThread()
    assert page.operation_label.text() != ""


def test_progress_updates_on_the_gui_thread(qtbot, window: MainWindow, monkeypatch) -> None:
    seen: list[QThread] = []

    class Probe(QObject):
        @Slot(str, str, int, int)
        def take(self, operation: str, current_file: str, completed: int, total: int) -> None:
            seen.append(QThread.currentThread())

    probe = Probe()
    original = ArchiveWorker.__init__

    def init(self, database_path, url):
        original(self, database_path, url)
        self.progress.connect(probe.take)

    monkeypatch.setattr(ArchiveWorker, "__init__", init)
    page = window.archive_page
    with respx.mock:
        _install_success_routes()
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    qtbot.waitUntil(lambda: len(seen) > 0, timeout=2000)
    assert seen[0] == QThread.currentThread()
    assert "/" in page.count_label.text()


def test_worker_source_does_not_touch_widgets() -> None:
    source = Path("src/techvault/ui/archive_worker.py").read_text(encoding="utf-8")
    assert "QWidget" not in source
    assert "setText" not in source
    assert "QLabel" not in source
    assert "sqlite3" not in source
    assert ".execute(" not in source


def test_open_offline_and_online_use_authority_values(qtbot, window: MainWindow, monkeypatch) -> None:
    page = window.archive_page
    opened: list[QUrl] = []

    def capture(url: QUrl) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", capture)
    with respx.mock:
        _install_success_routes()
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    page.open_offline_page()
    page.open_online_repository()
    layout = PathLayout(window.context.settings.get_archive_root())
    index = layout.page_index("octo", "hello", 123)
    assert Path(opened[0].toLocalFile()) == index
    assert index.name == "index.html"
    assert opened[1].toString() == ONLINE_URL


def test_settings_archive_root_round_trip(window: MainWindow, tmp_path: Path) -> None:
    chosen = tmp_path / "chosen"
    window.settings_page.root_edit.setText(str(chosen))
    window.settings_page.save_root()
    assert window.context.settings.get_archive_root() == chosen
    assert window.settings_page.root_edit.text() == str(chosen)


def test_token_save_shows_configured_and_stays_out_of_sqlite(window: MainWindow) -> None:
    page = window.settings_page
    page.token_edit.setText(TOKEN)
    page.save_token()
    assert page.token_status.text() == "已配置"
    assert TOKEN not in page.token_status.text()
    assert TOKEN not in page.status_label.text()
    assert TOKEN not in page.token_edit.text()
    assert TOKEN.encode("utf-8") not in _database_bytes(window.context.database_path)
    rows = window.context.connection.execute("SELECT key, value FROM settings").fetchall()
    assert all(TOKEN not in row["value"] for row in rows)


def test_token_clear(window: MainWindow) -> None:
    page = window.settings_page
    page.token_edit.setText(TOKEN)
    page.save_token()
    page.clear_token()
    assert page.token_status.text() == "未配置"
    assert TOKEN not in page.token_status.text()


def test_token_unavailable(window: MainWindow) -> None:
    keyring.set_keyring(FailingKeyring())
    page = window.settings_page
    page.token_edit.setText(TOKEN)
    page.save_token()
    assert page.token_status.text() == "不可用"
    assert TOKEN not in page.token_status.text()
    assert TOKEN not in page.status_label.text()


def test_migration_failure_does_not_open_a_window(tmp_path: Path) -> None:
    context, error = open_window_context(tmp_path / "db.sqlite", tmp_path / "a", tmp_path / "missing")
    assert context is None
    assert error == DATABASE_INIT_FAILED
    assert error == "Database initialization failed"


def test_main_stops_when_database_initialization_fails(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(
        "techvault.ui.app.open_window_context",
        lambda *args, **kwargs: (None, DATABASE_INIT_FAILED),
    )
    shown = []
    monkeypatch.setattr("techvault.ui.app.QMessageBox.critical", lambda *args: shown.append(args[-1]))
    assert main([]) == 1
    assert shown == ["数据库初始化失败"]


def test_backend_packages_do_not_import_the_ui() -> None:
    root = Path("src/techvault")
    for name in ("documents", "github", "archive", "offline", "db", "app"):
        for path in (root / name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "PySide6" not in text
            assert "QWidget" not in text
            assert "QApplication" not in text
            assert "techvault.ui" not in text


def test_ui_does_not_own_backend_work() -> None:
    for path in Path("src/techvault/ui").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "httpx" not in text
        assert "GitHubClient" not in text
        assert "hashlib" not in text
        assert "sanitize_html" not in text
        assert ".execute(" not in text


def test_knowledge_validation_does_not_block_the_archive_page(window: MainWindow) -> None:
    page = window.knowledge_page
    page.title_edit.setText("   ")
    page.save_document()
    assert page.status_label.text() == "标题不能为空"
    window.show_section(1)
    window.archive_page.url_edit.setText("https://github.com/octo/hello")
    assert window.archive_page.url_edit.text() == "https://github.com/octo/hello"
    assert window.stack.currentWidget() is window.archive_page


def _save_note(window: MainWindow, title: str, content: str):
    page = window.knowledge_page
    page.begin_new()
    page.title_edit.setText(title)
    page.content_edit.setPlainText(content)
    page.save_document()
    return page


def _save_with_category(page, title: str, category_id: int) -> None:
    page.begin_new()
    page.title_edit.setText(title)
    page.content_edit.setPlainText("body")
    page.category_combo.setCurrentIndex(page.category_combo.findData(category_id))
    page.save_document()


def _select_category(page, name: str) -> None:
    for row in range(page.category_list.count()):
        if page.category_list.item(row).text() == name:
            page.category_list.setCurrentRow(row)
            return
    raise AssertionError(name)


def _repository() -> dict[str, object]:
    return {
        "id": 123,
        "name": "hello",
        "full_name": "octo/hello",
        "html_url": ONLINE_URL,
        "description": "A repository",
        "stargazers_count": 1,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "octo"},
    }


def _release(*, prerelease: bool = False, draft: bool = False) -> dict[str, object]:
    return {
        "id": 9,
        "tag_name": "v1",
        "name": "One",
        "body": "",
        "html_url": "https://github.com/octo/hello/releases/tag/v1",
        "published_at": "2026-02-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "prerelease": prerelease,
        "draft": draft,
        "tarball_url": TAR_URL,
        "zipball_url": ZIP_URL,
        "assets": [],
    }


def _install_success_routes(zip_handler=None) -> None:
    respx.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
    respx.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release()))
    if zip_handler is None:
        respx.get(ZIP_URL).mock(return_value=httpx.Response(200, content=b"zip-body"))
    else:
        respx.get(ZIP_URL).mock(side_effect=zip_handler)
    respx.get(TAR_URL).mock(return_value=httpx.Response(200, content=b"tar-body"))
    respx.get(README_URL).mock(return_value=httpx.Response(200, text="<p>Hi</p>"))
    respx.get("https://api.github.com/repos/octo/hello/zipball/main").mock(
        return_value=httpx.Response(200, content=b"snapshot-body")
    )


def _assert_source_snapshot_without_release(qtbot, window: MainWindow, releases) -> None:
    page = window.archive_page
    with respx.mock:
        respx.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        respx.get(LATEST_URL).mock(return_value=httpx.Response(404))
        respx.get(LIST_URL).mock(return_value=httpx.Response(200, json=releases))
        respx.get(README_URL).mock(return_value=httpx.Response(200, text="<p>Hi</p>"))
        respx.get("https://api.github.com/repos/octo/hello/zipball/main").mock(
            return_value=httpx.Response(200, content=b"snapshot-zip")
        )
        page.url_edit.setText("https://github.com/octo/hello")
        page.archive_button.click()
        qtbot.waitUntil(lambda: page.result_label.text() == "已更新", timeout=20000)
    assert "Traceback" not in page.result_label.text()
    assert page.release_label.text() == "暂无正式 Release"
    assert page.open_offline_button.isEnabled() is True
    current = window.context.connection.execute(
        "SELECT current_release_id, current_snapshot_id FROM repositories WHERE github_repo_id = 123"
    ).fetchone()
    assert current["current_release_id"] is None
    assert current["current_snapshot_id"] is not None
    stored = window.context.connection.execute(
        "SELECT COUNT(*) AS n FROM releases"
    ).fetchone()
    assert stored["n"] == 0


def _database_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    for suffix in ("-wal", "-shm"):
        extra = Path(str(path) + suffix)
        if extra.exists():
            data += extra.read_bytes()
    return data
