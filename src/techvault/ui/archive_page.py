"""GitHub archive page. The worker performs the archive; this page only displays it."""

from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from techvault.app.archive_service import ArchiveResult
from techvault.app.library import LibraryService
from techvault.archive.progress import ProgressEvent, format_bytes
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.i18n import NO_CURRENT_RELEASE, operation_text, result_heading, result_status_text
from techvault.ui.markdown_view import MarkdownView
from techvault.ui.theme import LG, MD, SM, restyle

_ID_ROLE = Qt.ItemDataRole.UserRole


class _DetailView(QTextBrowser):
    """Read-only repository detail. text() stays plain so existing checks keep working."""

    def __init__(self) -> None:
        super().__init__()
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 80)

    def sizeHint(self) -> QSize:
        return QSize(480, 240)

    def setText(self, text: str) -> None:
        if text.lstrip().startswith("<"):
            self.setHtml(text)
        else:
            self.setPlainText(text)

    def text(self) -> str:
        return self.toPlainText()


class ArchivePage(QWidget):
    def __init__(self, database_path: Path, connection: sqlite3.Connection | None = None) -> None:
        super().__init__()
        self._database_path = Path(database_path)
        self._worker: ArchiveWorker | None = None
        self._result: ArchiveResult | None = None
        self._loading = False
        self._applying_result = False
        self.library = None if connection is None else LibraryService(connection)
        self.setObjectName("archivePage")

        title = QLabel("GitHub 项目")
        title.setObjectName("pageTitle")

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("repositorySearch")
        self.search_edit.setPlaceholderText("搜索项目、所有者、描述或分类")
        self.category_filter = QComboBox()
        self.category_filter.setObjectName("repositoryCategory")
        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("repositorySort")
        self.sort_combo.addItem("最近更新", "recent")
        self.sort_combo.addItem("项目名称", "name")
        self.sort_combo.addItem("所有者", "owner")
        self.repository_list = QListWidget()
        self.repository_list.setObjectName("repositoryList")
        self.empty_label = QLabel("还没有项目")
        self.empty_label.setProperty("tone", "muted")
        self.detail_label = _DetailView()
        self.detail_label.setObjectName("repositoryDetail")
        self.detail_label.setText("选择一个项目。")
        self.detail_label.setMaximumHeight(148)
        self.readme_view = MarkdownView()
        self.asset_host = QWidget()
        self.asset_rows = QVBoxLayout(self.asset_host)
        self.asset_rows.setContentsMargins(0, 0, 0, 0)
        self.asset_rows.setSpacing(SM)
        self.assign_combo = QComboBox()
        self.assign_combo.setObjectName("assignCategory")
        self.rearchive_button = QPushButton("更新项目")
        self.rearchive_button.setObjectName("rearchiveRepository")
        self.new_category_button = QPushButton("新建分类")
        self.new_category_button.setObjectName("newRepositoryCategory")
        self.rename_category_button = QPushButton("重命名分类")
        self.rename_category_button.setObjectName("renameRepositoryCategory")
        self.delete_category_button = QPushButton("删除分类")
        self.delete_category_button.setObjectName("deleteRepositoryCategory")

        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("repositoryUrl")
        self.url_edit.setPlaceholderText("https://github.com/owner/repo")
        self.archive_button = QPushButton("添加项目")
        self.archive_button.setObjectName("archiveRepository")
        self.archive_button.setProperty("role", "primary")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelArchive")
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("archiveProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.percent_label = QLabel("")
        self.percent_label.setObjectName("archivePercent")
        self.bytes_label = QLabel("")
        self.bytes_label.setObjectName("archiveBytes")
        self.heading_label = QLabel("")
        self.heading_label.setObjectName("archiveHeading")
        self.operation_label = QLabel("")
        self.operation_label.setObjectName("archiveOperation")
        self.file_label = QLabel("")
        self.file_label.setObjectName("archiveFile")
        self.file_label.setWordWrap(True)
        self.count_label = QLabel("")
        self.count_label.setObjectName("archiveCount")
        self.result_label = QLabel("")
        self.result_label.setObjectName("archiveResult")
        self.result_label.setWordWrap(True)
        self.release_label = QLabel(NO_CURRENT_RELEASE)
        self.release_label.setObjectName("currentRelease")
        self.repository_label = QLabel("")
        self.repository_label.setObjectName("archiveRepositoryName")
        self.offline_label = QLabel("")
        self.offline_label.setObjectName("offlinePagePath")
        self.offline_label.setWordWrap(True)
        self.open_offline_button = QPushButton("查看 README")
        self.open_offline_button.setObjectName("openOfflinePage")
        self.open_online_button = QPushButton("打开 GitHub")
        self.open_online_button.setObjectName("openOnlineRepository")
        self.open_folder_button = QPushButton("打开本地")
        self.open_folder_button.setObjectName("openArchiveFolder")
        for button in (
            self.open_offline_button,
            self.open_online_button,
            self.open_folder_button,
            self.rearchive_button,
            self.cancel_button,
        ):
            button.setEnabled(False)
        self.repository_list.setWordWrap(True)
        self.repository_list.setUniformItemSizes(False)
        self.heading_label.setProperty("tone", "muted")
        self.operation_label.setProperty("tone", "muted")
        self.file_label.setProperty("tone", "muted")
        self.count_label.setProperty("tone", "muted")
        self.repository_label.setProperty("tone", "muted")
        self.release_label.setProperty("tone", "muted")
        self.offline_label.setProperty("tone", "muted")
        self.repository_label.setVisible(False)
        self.release_label.setVisible(False)
        self.offline_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.percent_label.setVisible(False)
        self.bytes_label.setVisible(False)
        self.file_label.setVisible(False)
        self.count_label.setVisible(False)
        self.percent_label.setProperty("tone", "muted")
        self.bytes_label.setProperty("tone", "muted")

        self.open_add_button = QPushButton("添加项目")
        self.open_add_button.setProperty("role", "primary")
        self.manage_categories_button = QPushButton("管理分类")
        self.manage_categories_button.setProperty("role", "quiet")
        self.rearchive_button.setProperty("role", "primary")
        for button in (
            self.open_online_button,
            self.open_folder_button,
            self.open_offline_button,
        ):
            button.setProperty("role", "quiet")
        self.category_rail = QListWidget()
        self.category_rail.setObjectName("repositoryCategoryRail")
        self.category_rail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.category_rail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.category_filter.setVisible(False)

        self.add_dialog = QDialog(self)
        self.add_dialog.setWindowTitle("添加项目")
        add_layout = QVBoxLayout(self.add_dialog)
        add_layout.addWidget(QLabel("GitHub 地址"))
        add_layout.addWidget(self.url_edit)
        add_actions = QHBoxLayout()
        add_actions.addStretch(1)
        add_actions.addWidget(self.cancel_button)
        add_actions.addWidget(self.archive_button)
        add_layout.addLayout(add_actions)

        self.category_dialog = QDialog(self)
        self.category_dialog.setWindowTitle("管理分类")
        category_layout = QVBoxLayout(self.category_dialog)
        category_layout.addWidget(QLabel("分类只影响本地整理，不会改写 GitHub Topics。"))
        category_actions = QHBoxLayout()
        category_actions.addWidget(self.new_category_button)
        category_actions.addWidget(self.rename_category_button)
        category_actions.addWidget(self.delete_category_button)
        category_layout.addLayout(category_actions)

        filters = QHBoxLayout()
        filters.setSpacing(SM)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.sort_combo)
        filters.addWidget(self.manage_categories_button)
        filters.addWidget(self.open_add_button)

        rail = QVBoxLayout()
        rail.setContentsMargins(0, 0, 0, 0)
        rail.setSpacing(SM)
        rail.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        rail.addWidget(self._section("分类"))
        rail.addWidget(self.category_rail, 1)
        rail_host = QWidget()
        rail_host.setLayout(rail)
        rail_host.setFixedWidth(132)

        list_box = QVBoxLayout()
        list_box.setContentsMargins(0, 0, 0, 0)
        list_box.setSpacing(SM)
        list_box.addWidget(self.empty_label)
        list_box.addWidget(self.repository_list, 1)
        list_host = QWidget()
        list_host.setLayout(list_box)
        list_host.setMinimumWidth(260)

        assign_row = QHBoxLayout()
        assign_row.setSpacing(SM)
        category_name = QLabel("分类")
        category_name.setObjectName("sectionLabel")
        assign_row.addWidget(category_name)
        assign_row.addWidget(self.assign_combo, 1)
        actions = QHBoxLayout()
        actions.setSpacing(SM)
        actions.addWidget(self.open_online_button)
        actions.addWidget(self.open_offline_button)
        actions.addWidget(self.open_folder_button)
        actions.addStretch(1)
        actions.addWidget(self.rearchive_button)
        status_row = QHBoxLayout()
        status_row.setSpacing(SM)
        status_row.addWidget(self.operation_label)
        status_row.addWidget(self.percent_label)
        status_row.addWidget(self.bytes_label)
        status_row.addWidget(self.count_label)
        status_row.addWidget(self.file_label, 1)
        detail_box = QVBoxLayout()
        detail_box.setContentsMargins(MD, 0, 0, 0)
        detail_box.setSpacing(SM)
        detail_box.addWidget(self.detail_label, 0)
        detail_box.addWidget(self.readme_view, 1)
        detail_box.addWidget(self.asset_host, 0)
        detail_box.addLayout(assign_row)
        detail_box.addLayout(actions)
        detail_box.addWidget(self.progress_bar)
        detail_box.addLayout(status_row)
        detail_box.addWidget(self.result_label)
        detail_box.addWidget(self.heading_label)
        detail_box.addWidget(self.repository_label)
        detail_box.addWidget(self.release_label)
        detail_box.addWidget(self.offline_label)
        detail_frame = QWidget()
        detail_frame.setLayout(detail_box)
        detail_frame.setMinimumWidth(340)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(rail_host)
        splitter.addWidget(list_host)
        splitter.addWidget(detail_frame)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(MD, SM, MD, SM)
        layout.setSpacing(SM)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        layout.addLayout(filters)
        layout.addWidget(splitter, 1)

        self.search_edit.textChanged.connect(lambda _text: self.refresh_library())
        self.category_filter.currentIndexChanged.connect(self._category_filter_changed)
        self.category_rail.currentRowChanged.connect(self._category_rail_changed)
        self.open_add_button.clicked.connect(self.add_dialog.show)
        self.manage_categories_button.clicked.connect(self.category_dialog.show)
        self.sort_combo.currentIndexChanged.connect(lambda _index: self.refresh_library())
        self.repository_list.currentItemChanged.connect(self._show_selected)
        self.assign_combo.currentIndexChanged.connect(self._assign_category)
        self.rearchive_button.clicked.connect(self.rearchive_selected)
        self.new_category_button.clicked.connect(self.create_category)
        self.rename_category_button.clicked.connect(self.rename_category)
        self.delete_category_button.clicked.connect(self.delete_category)
        self.archive_button.clicked.connect(self.start_archive)
        self.cancel_button.clicked.connect(self.cancel_archive)
        self.open_offline_button.clicked.connect(self.open_offline_page)
        self.open_online_button.clicked.connect(self.open_online_repository)
        self.open_folder_button.clicked.connect(self.open_archive_folder)
        self._reload_categories()
        self.refresh_library()

    def start_archive(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._result = None
        self._set_heading("", "")
        self.result_label.setText("")
        self.operation_label.setText("正在准备")
        self.file_label.setText("")
        self.count_label.setText("")
        self.percent_label.setText("")
        self.bytes_label.setText("")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.open_offline_button.setEnabled(False)
        self.open_online_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.add_dialog.hide()
        self._note("正在更新项目")
        self._worker = ArchiveWorker(self._database_path, self.url_edit.text())
        self._worker.progress.connect(self.show_progress)
        self._worker.detail.connect(self.show_transfer)
        self._worker.completed.connect(self.show_result)
        self._worker.start()

    def retry_asset(self, kind: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        item = self.repository_list.currentItem()
        if item is None:
            return
        self._result = None
        self._set_heading("", "")
        self.result_label.setText("")
        self.operation_label.setText("正在准备")
        self.file_label.setText("")
        self.count_label.setText("")
        self.percent_label.setText("")
        self.bytes_label.setText("")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.cancel_button.setEnabled(True)
        self._note("正在重新保存")
        self._worker = ArchiveWorker(
            self._database_path,
            "",
            repository_id=int(item.data(_ID_ROLE)),
            asset=kind,
        )
        self._worker.progress.connect(self.show_progress)
        self._worker.detail.connect(self.show_transfer)
        self._worker.completed.connect(self.show_result)
        self._worker.start()

    def rearchive_selected(self) -> None:
        item = self.repository_list.currentItem()
        if item is None or self.library is None:
            return
        detail = self.library.get_repository(int(item.data(_ID_ROLE)))
        if detail is None:
            return
        if not confirm_rearchive(self, f"{detail.owner}/{detail.name}"):
            return
        self.url_edit.setText(detail.html_url)
        self.start_archive()

    def cancel_archive(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    @Slot(str, str, int, int)
    def show_progress(self, operation: str, current_file: str, completed: int, total: int) -> None:
        self.operation_label.setText(operation_text(operation))
        self.file_label.setText(current_file)
        self.file_label.setVisible(True)
        self.count_label.setText(f"{completed} / {total}")
        self.count_label.setVisible(True)
        self.percent_label.setVisible(True)
        self.bytes_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self._note(self.operation_label.text())

    @Slot(object)
    def show_transfer(self, event: ProgressEvent) -> None:
        self.progress_bar.setVisible(True)
        self.percent_label.setVisible(True)
        self.bytes_label.setVisible(True)
        phase = event.phase
        if phase == "committing":
            self.operation_label.setText("正在提交")
        elif phase == "verifying" or event.percentage == 100:
            self.operation_label.setText("正在校验")
        if event.percentage is None:
            self.progress_bar.setRange(0, 0)
            self.percent_label.setText("正在校验" if phase == "verifying" else "正在下载")
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(event.percentage)
            self.percent_label.setText(f"{event.percentage}%")
        downloaded = format_bytes(event.overall_downloaded_bytes)
        if event.overall_expected_bytes is None:
            self.bytes_label.setText(f"已下载 {downloaded}")
        else:
            self.bytes_label.setText(f"{downloaded} / {format_bytes(event.overall_expected_bytes)}")
        if event.current_file:
            self.file_label.setText(event.current_file)
            self.file_label.setVisible(True)

    @Slot(object)
    def show_result(self, result: ArchiveResult) -> None:
        self._result = result
        self._applying_result = True
        heading, tone = result_heading(result.status)
        self._set_heading("", "")
        self.result_label.setText(result_status_text(result.status, result.message))
        self.repository_label.setText(result.repository_name or "")
        self.offline_label.setText(result.offline_page or "")
        completed = result.status == "COMPLETED"
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if completed else 0)
        self.progress_bar.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.percent_label.setVisible(False)
        self.bytes_label.setVisible(False)
        self.file_label.setVisible(False)
        self.count_label.setVisible(False)
        self.operation_label.setText(heading)
        self.operation_label.setProperty("tone", tone)
        restyle(self.operation_label)
        self._note(result.message)
        self.refresh_library(select_name=result.repository_name)
        detail = self._current_detail()
        if detail is not None and detail.release_tag:
            self.release_label.setText(detail.release_tag)
        elif detail is not None and detail.snapshot is not None:
            self.release_label.setText("暂无正式 Release")
        elif result.current_release_id is None:
            self.release_label.setText(NO_CURRENT_RELEASE)
        else:
            self.release_label.setText(result.release_tag or "")
        self._apply_actions(detail)
        self._applying_result = False

    def refresh_library(self, select_name: str | None = None) -> None:
        if self.library is None:
            self.empty_label.setVisible(True)
            return
        category = self.category_filter.currentData()
        items = self.library.list_repositories(
            query=self.search_edit.text(),
            category_id=category if isinstance(category, int) else None,
            uncategorized=category == "none",
            sort=self.sort_combo.currentData() or "recent",
        )
        current = self.repository_list.currentItem()
        current_id = None if current is None else current.data(_ID_ROLE)
        self.repository_list.blockSignals(True)
        self.repository_list.clear()
        chosen = None
        for item in items:
            description = _clip(" ".join((item.description or "").split()), 42)
            topics = [part.strip() for part in item.topics.split("·") if part.strip()] if item.topics else []
            topic_text = " ".join(topics[:3])
            if len(topics) > 3:
                topic_text += " …"
            facts = [part for part in (item.category_name, topic_text) if part]
            assets: list[str] = []
            if item.snapshot_ref:
                assets.append("源码已保存")
            if item.release_tag:
                assets.append(f"Release {item.release_tag}")
            lines = [_clip(f"{item.owner} / {item.name}", 72)]
            if description:
                lines.append(description)
            meta = " · ".join(facts + assets)
            if meta:
                lines.append(meta)
            text = "\n".join(lines)
            row = QListWidgetItem(text)
            row.setData(_ID_ROLE, item.id)
            row.setToolTip(item.description or item.html_url)
            self.repository_list.addItem(row)
            if select_name and item.full_name == select_name:
                chosen = row
            elif chosen is None and current_id == item.id:
                chosen = row
        has_repositories = self.library.has_repositories()
        if items:
            self.empty_label.setText("还没有项目")
        elif has_repositories:
            self.empty_label.setText("没有符合条件的项目")
        else:
            self.empty_label.setText("还没有项目")
        self.empty_label.setVisible(not items)
        self.repository_list.blockSignals(False)
        if chosen is not None:
            self.repository_list.setCurrentItem(chosen)
            return
        self.repository_list.setCurrentItem(None)
        if items and current_id is None and select_name is None:
            self.repository_list.setCurrentRow(0)
            return
        if items:
            self.detail_label.setText("选择一个项目。")
            self.readme_view.set_unavailable("")
            self._clear_assets()
            self._apply_actions(None)
        elif has_repositories:
            self._clear_detail(filtered=True)
        else:
            self._clear_detail(filtered=False)

    def create_category(self) -> None:
        if self.library is None:
            return
        name = ask_text(self, "新建分类", "分类名称")
        if not name:
            return
        self.library.create_category(name)
        self._reload_categories()
        self.refresh_library()

    def rename_category(self) -> None:
        if self.library is None:
            return
        category_id = self._selected_category_id()
        if category_id is None:
            return
        current = next((item.name for item in self.library.list_categories() if item.id == category_id), "")
        name = ask_text(self, "重命名分类", "分类名称", current)
        if not name:
            return
        self.library.rename_category(category_id, name)
        self._reload_categories()
        self.refresh_library()

    def delete_category(self) -> None:
        if self.library is None:
            return
        category_id = self._selected_category_id()
        if category_id is None:
            return
        if not confirm_delete_category(self):
            return
        self.library.delete_category(category_id)
        self._reload_categories()
        self.refresh_library()

    def open_offline_page(self) -> None:
        detail = self._current_detail()
        if detail is None or not detail.offline_absolute or not Path(detail.offline_absolute).is_file():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(detail.offline_absolute))

    def open_online_repository(self) -> None:
        detail = self._current_detail()
        if detail is None or not detail.html_url:
            return
        QDesktopServices.openUrl(QUrl(detail.html_url))

    def open_archive_folder(self) -> None:
        detail = self._current_detail()
        if detail is None or not detail.archive_folder or not Path(detail.archive_folder).is_dir():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(detail.archive_folder))

    def _current_detail(self):
        item = self.repository_list.currentItem()
        if item is None or self.library is None:
            return None
        return self.library.get_repository(int(item.data(_ID_ROLE)))

    def _apply_actions(self, detail) -> None:
        offline = detail is not None and bool(detail.offline_absolute) and Path(detail.offline_absolute).is_file()
        online = detail is not None and bool(detail.html_url)
        folder = detail is not None and bool(detail.archive_folder) and Path(detail.archive_folder).is_dir()
        self.open_offline_button.setEnabled(offline)
        self.open_online_button.setEnabled(online)
        self.open_folder_button.setEnabled(folder)
        self.rearchive_button.setEnabled(detail is not None)

    def _clear_detail(self, *, filtered: bool) -> None:
        if filtered:
            self.detail_label.setText("没有符合条件的项目。")
        else:
            self.detail_label.setText("还没有项目。")
        self.readme_view.set_unavailable("")
        self._clear_assets()
        self._apply_actions(None)

    def _show_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None or self.library is None:
            return
        running = self._worker is not None and self._worker.isRunning()
        if not self._applying_result and not running:
            self.operation_label.setText("")
            self.result_label.setText("")
            self.progress_bar.setVisible(False)
        detail = self.library.get_repository(int(current.data(_ID_ROLE)))
        if detail is None:
            return
        if detail.snapshot is None:
            snapshot = "尚未保存"
        else:
            size = "大小未知" if detail.snapshot.size is None else format_bytes(detail.snapshot.size)
            when = detail.snapshot.archived_at.replace("T", " ").replace("Z", "")
            snapshot = f"{detail.snapshot.ref} · {when} · {size}"
        if detail.release_tag:
            names = "  ".join(item.name for item in detail.release_files)
            published = (detail.release_published_at or "").replace("T", " ").replace("Z", "")
            release = " · ".join(
                part
                for part in (
                    f"{detail.release_tag}  {detail.release_name}".strip(),
                    published,
                    names,
                )
                if part
            )
        else:
            release = "暂无正式 Release"
        topics = "  ".join(detail.topics) if detail.topics else "无"
        category = detail.category_name or "未分类"
        self.detail_label.setText(
            "".join(
                [
                    f"<p><b>{escape(detail.owner)}/{escape(detail.name)}</b></p>",
                    f"<p>{escape(detail.description or '没有描述')}</p>",
                    f"<p>分类 {escape(category)} · Topics {escape(topics)}</p>",
                    f"<p>源码快照 {escape(snapshot)}</p>",
                    f"<p>正式版本 {escape(release)}</p>",
                ]
            )
        )
        self._show_readme(detail.id)
        self._show_assets(detail.id)
        self._apply_actions(detail)
        self._loading = True
        index = self.assign_combo.findData(detail.category_id)
        self.assign_combo.setCurrentIndex(0 if index < 0 else index)
        self._loading = False

    def _show_readme(self, repository_id: int) -> None:
        if self.library is None:
            self.readme_view.set_unavailable("尚未保存")
            return
        kind, body, base = self.library.readme_document(repository_id)
        resource = Path(base) if base else None
        if kind == "markdown":
            self.readme_view.set_markdown(body, base=resource)
        elif kind == "html":
            self.readme_view.set_html(body, base=resource)
        else:
            self.readme_view.set_unavailable("尚未保存")

    def _show_assets(self, repository_id: int) -> None:
        self._clear_assets()
        if self.library is None:
            return
        labels = {"README": "README", "SOURCE": "源码快照", "RELEASE": "正式版本"}
        states = {
            "SAVED": "已保存",
            "FAILED": "保存失败",
            "PARTIAL": "部分保存",
            "ABSENT": "尚未保存",
        }
        for asset in self.library.asset_records(repository_id):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            title = QLabel(labels.get(asset.kind, asset.kind))
            state_text = states.get(asset.status, asset.status)
            if asset.status == "ABSENT" and asset.message:
                state_text = asset.message
            state = QLabel(state_text)
            state.setObjectName("metaLine")
            if asset.status == "FAILED":
                state.setProperty("tone", "error")
            elif asset.status == "PARTIAL":
                state.setProperty("tone", "warning")
            else:
                state.setProperty("tone", "muted")
            restyle(state)
            layout.addWidget(title)
            layout.addWidget(state)
            if asset.message and asset.message not in {state.text(), "已保存"}:
                note = QLabel(asset.message)
                note.setProperty("tone", "muted")
                layout.addWidget(note, 1)
            else:
                layout.addStretch(1)
            if asset.retryable:
                button = QPushButton("重新保存" if asset.status in {"FAILED", "PARTIAL"} else "保存")
                button.setObjectName(f"retry{asset.kind}")
                button.setProperty("role", "primary" if asset.status in {"FAILED", "PARTIAL"} else "quiet")
                button.clicked.connect(lambda _checked=False, kind=asset.kind: self.retry_asset(kind))
                layout.addWidget(button)
            self.asset_rows.addWidget(row)

    def _clear_assets(self) -> None:
        while self.asset_rows.count():
            item = self.asset_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _assign_category(self, _index: int) -> None:
        if self._loading or self.library is None:
            return
        item = self.repository_list.currentItem()
        if item is None:
            return
        self.library.set_repository_category(int(item.data(_ID_ROLE)), self.assign_combo.currentData())
        self.refresh_library()

    def _reload_categories(self) -> None:
        self._loading = True
        chosen = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.assign_combo.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("全部分类", None)
        self.category_filter.addItem("未分类", "none")
        self.assign_combo.clear()
        self.assign_combo.addItem("未分类", None)
        if self.library is not None:
            for category in self.library.list_categories():
                self.category_filter.addItem(category.name, category.id)
                self.assign_combo.addItem(category.name, category.id)
        index = self.category_filter.findData(chosen)
        self.category_filter.setCurrentIndex(0 if index < 0 else index)
        self.category_rail.blockSignals(True)
        self.category_rail.clear()
        for row in range(self.category_filter.count()):
            self.category_rail.addItem(self.category_filter.itemText(row).replace("全部分类", "全部项目"))
            self.category_rail.item(row).setData(_ID_ROLE, self.category_filter.itemData(row))
        self.category_rail.setCurrentRow(self.category_filter.currentIndex())
        self.category_rail.blockSignals(False)
        self.category_filter.blockSignals(False)
        self.assign_combo.blockSignals(False)
        self._loading = False

    def _category_filter_changed(self, _index: int) -> None:
        if self.category_rail.currentRow() != self.category_filter.currentIndex():
            self.category_rail.blockSignals(True)
            self.category_rail.setCurrentRow(self.category_filter.currentIndex())
            self.category_rail.blockSignals(False)
        self.refresh_library()

    def _category_rail_changed(self, row: int) -> None:
        if self._loading or row < 0 or row == self.category_filter.currentIndex():
            return
        self.category_filter.setCurrentIndex(row)

    def _selected_category_id(self) -> int | None:
        value = self.category_filter.currentData()
        if isinstance(value, int):
            return value
        item = self.repository_list.currentItem()
        if item is None or self.library is None:
            return None
        detail = self.library.get_repository(int(item.data(_ID_ROLE)))
        if detail is None:
            return None
        return detail.category_id

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _set_heading(self, text: str, tone: str) -> None:
        self.heading_label.setText(text)
        self.heading_label.setProperty("tone", tone)
        restyle(self.heading_label)

    def _note(self, text: str) -> None:
        host = self.window()
        status_bar = getattr(host, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(text)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def confirm_rearchive(parent: QWidget, name: str) -> bool:
    answer = QMessageBox.question(parent, "更新项目", f"更新 {name}？")
    return answer == QMessageBox.StandardButton.Yes


def confirm_delete_category(parent: QWidget) -> bool:
    answer = QMessageBox.question(parent, "删除分类", "删除这个分类？项目会保留。")
    return answer == QMessageBox.StandardButton.Yes


def ask_text(parent: QWidget, title: str, label: str, current: str = "") -> str | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    edit = QLineEdit(current)
    ok = QPushButton("确定")
    cancel = QPushButton("取消")
    ok.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    row = QHBoxLayout()
    row.addWidget(ok)
    row.addWidget(cancel)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))
    layout.addWidget(edit)
    layout.addLayout(row)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    text = edit.text().strip()
    return text or None
