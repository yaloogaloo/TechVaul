"""Knowledge workspace. Widgets call DocumentService and do not query SQLite."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from techvault.documents.models import Document, DocumentNotFoundError, DocumentValidationError
from techvault.documents.service import DocumentService
from techvault.ui.i18n import validation_text
from techvault.ui.markdown_view import MarkdownView
from techvault.ui.tags import parse_tag_input
from techvault.ui.theme import MD, SM, restyle

TYPE_CHOICES = (
    ("COMMAND", "命令"),
    ("PROMPT", "AI 提示词"),
    ("TIP", "技巧"),
    ("NOTE", "笔记"),
)
SEARCH_DELAY_MS = 250
_ID_ROLE = Qt.ItemDataRole.UserRole
_FIELDS_ROLE = Qt.ItemDataRole.UserRole + 1
_TYPE_LABELS = dict(TYPE_CHOICES)


class KnowledgePage(QWidget):
    def __init__(self, service: DocumentService) -> None:
        super().__init__()
        self.service = service
        self._document_id: int | None = None
        self._doc_type: str | None = None
        self._loading = False
        self._baseline: tuple[str, str, object, object, str, bool] | None = None
        self.setObjectName("knowledgePage")

        title = QLabel("知识")
        title.setObjectName("pageTitle")
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchBox")
        self.search_edit.setPlaceholderText("搜索命令、提示词、技巧或笔记")
        self.search_edit.setClearButtonEnabled(True)
        self.new_button = QPushButton("新建")
        self.new_button.setObjectName("newDocument")
        self.new_button.setProperty("role", "primary")

        self.type_list = QListWidget()
        self.type_list.setObjectName("typeList")
        self.type_list.addItem("全部")
        self.type_list.item(0).setData(_ID_ROLE, None)
        for code, label in TYPE_CHOICES:
            self.type_list.addItem(label)
            self.type_list.item(self.type_list.count() - 1).setData(_ID_ROLE, code)
        self.type_list.setCurrentRow(0)
        self.type_list.setFixedHeight(132)
        self.type_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.type_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.favorite_filter = QCheckBox("只看收藏")
        self.favorite_filter.setObjectName("favoriteFilter")
        self.category_list = QListWidget()
        self.category_list.setObjectName("categoryList")
        self.category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.category_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tag_filter = QLineEdit()
        self.tag_filter.setObjectName("tagFilter")
        self.tag_filter.setPlaceholderText("标签")
        self.tag_filter.setClearButtonEnabled(True)

        self.document_list = QListWidget()
        self.document_list.setObjectName("documentList")
        self.document_list.setWordWrap(True)
        self.document_list.setUniformItemSizes(False)
        self.document_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_message = QLabel("还没有知识")
        self.list_message.setObjectName("knowledgeEmpty")
        self.list_message.setProperty("tone", "muted")
        self.list_message.setWordWrap(True)

        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("titleEdit")
        self.title_edit.setPlaceholderText("标题")
        self.type_combo = QComboBox()
        self.type_combo.setObjectName("typeCombo")
        for code, label in TYPE_CHOICES:
            self.type_combo.addItem(label, code)
        self.category_combo = QComboBox()
        self.category_combo.setObjectName("categoryCombo")
        self.tags_edit = QLineEdit()
        self.tags_edit.setObjectName("tagsEdit")
        self.tags_edit.setPlaceholderText("git, powershell, windows")
        self.favorite_box = QCheckBox("收藏")
        self.favorite_box.setObjectName("favoriteBox")
        self.content_edit = QPlainTextEdit()
        self.content_edit.setObjectName("contentEdit")
        self.content_edit.setPlaceholderText("正文")
        self.content_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("saveDocument")
        self.save_button.setProperty("role", "primary")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelDocument")
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("deleteDocument")
        self.delete_button.setProperty("role", "danger")
        self.copy_button = QPushButton("复制")
        self.copy_button.setObjectName("copyDocument")
        self.copy_button.setProperty("role", "quiet")
        self.edit_button = QPushButton("编辑")
        self.edit_button.setObjectName("editDocument")
        self.edit_button.setProperty("role", "primary")
        self.status_label = QLabel("")
        self.status_label.setObjectName("knowledgeStatus")

        self.read_title = QLabel("")
        self.read_title.setObjectName("readTitle")
        self.read_title.setWordWrap(True)
        self.read_meta = QLabel("")
        self.read_meta.setObjectName("metaLine")
        self.read_meta.setWordWrap(True)
        self.read_body = MarkdownView()
        self.read_tags = QLabel("")
        self.read_tags.setObjectName("metaLine")
        self.read_tags.setWordWrap(True)
        self.empty_detail = QLabel("选择一条知识。")
        self.empty_detail.setProperty("tone", "muted")
        self.empty_detail.setWordWrap(True)

        header = QHBoxLayout()
        header.setSpacing(SM)
        header.addWidget(title)
        header.addWidget(self.search_edit, 1)
        header.addWidget(self.new_button)

        rail = QVBoxLayout()
        rail.setContentsMargins(0, 0, 0, 0)
        rail.setSpacing(SM)
        rail.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        rail.addWidget(self._section("类型"))
        rail.addWidget(self.type_list)
        rail.addWidget(self.favorite_filter)
        rail.addWidget(self._section("分类"))
        rail.addWidget(self.category_list, 1)
        rail.addWidget(self.tag_filter)
        rail_host = QWidget()
        rail_host.setObjectName("filterRail")
        rail_host.setLayout(rail)
        rail_host.setFixedWidth(148)

        list_box = QVBoxLayout()
        list_box.setContentsMargins(0, 0, 0, 0)
        list_box.setSpacing(SM)
        list_box.addWidget(self.list_message)
        list_box.addWidget(self.document_list, 1)
        list_host = QWidget()
        list_host.setLayout(list_box)
        list_host.setMinimumWidth(220)

        browse_actions = QHBoxLayout()
        browse_actions.setSpacing(SM)
        browse_actions.addWidget(self.copy_button)
        browse_actions.addWidget(self.edit_button)
        browse_actions.addWidget(self.favorite_box)
        browse_actions.addStretch(1)
        browse_actions.addWidget(self.delete_button)
        browse = QWidget()
        browse_layout = QVBoxLayout(browse)
        browse_layout.setContentsMargins(MD, 0, 0, 0)
        browse_layout.setSpacing(SM)
        browse_layout.addWidget(self.read_title)
        browse_layout.addWidget(self.read_meta)
        browse_layout.addWidget(self.read_body, 1)
        browse_layout.addWidget(self.read_tags)
        browse_layout.addLayout(browse_actions)

        edit_meta = QHBoxLayout()
        edit_meta.setSpacing(SM)
        edit_meta.addWidget(self._section("类型"))
        edit_meta.addWidget(self.type_combo, 1)
        edit_meta.addWidget(self._section("分类"))
        edit_meta.addWidget(self.category_combo, 1)
        edit_actions = QHBoxLayout()
        edit_actions.setSpacing(SM)
        edit_actions.addWidget(self.save_button)
        edit_actions.addWidget(self.cancel_button)
        edit_actions.addWidget(self.status_label, 1)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(MD, 0, 0, 0)
        editor_layout.setSpacing(SM)
        editor_layout.addWidget(self.title_edit)
        editor_layout.addLayout(edit_meta)
        editor_layout.addWidget(self.content_edit, 1)
        editor_layout.addWidget(self.tags_edit)
        editor_layout.addLayout(edit_actions)

        blank = QWidget()
        blank_layout = QVBoxLayout(blank)
        blank_layout.addStretch(1)
        blank_layout.addWidget(self.empty_detail)
        blank_layout.addStretch(1)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(browse)
        self.detail_stack.addWidget(editor)
        self.detail_stack.addWidget(blank)
        self.detail_stack.setMinimumWidth(320)

        splitter = QSplitter()
        splitter.setObjectName("knowledgeSplit")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(rail_host)
        splitter.addWidget(list_host)
        splitter.addWidget(self.detail_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(MD, SM, MD, SM)
        layout.setSpacing(SM)
        layout.addLayout(header)
        layout.addWidget(splitter, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.refresh_documents)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        self.category_list.currentRowChanged.connect(self._category_changed)
        self.favorite_filter.toggled.connect(lambda _checked: self.refresh_documents())
        self.tag_filter.textChanged.connect(lambda _text: self._search_timer.start())
        self.type_list.currentRowChanged.connect(self._type_changed)
        self.document_list.currentRowChanged.connect(self._row_changed)
        self.new_button.clicked.connect(self._request_new)
        self.save_button.clicked.connect(self.save_document)
        self.cancel_button.clicked.connect(self.cancel_edit)
        self.delete_button.clicked.connect(self.delete_document)
        self.copy_button.clicked.connect(self.copy_document)
        self.edit_button.clicked.connect(self._begin_edit)
        self.favorite_box.toggled.connect(self._favorite_toggled)
        self.reload_categories()
        self.refresh_documents()

    def row_count(self) -> int:
        return self.document_list.count()

    def cell_text(self, row: int, column: int) -> str:
        item = self.document_list.item(row)
        if item is None:
            return ""
        fields = item.data(_FIELDS_ROLE) or ()
        if column < 0 or column >= len(fields):
            return ""
        return str(fields[column])

    def reload_categories(self) -> None:
        categories = self.service.list_categories()
        self._categories = {item.id: item.name for item in categories}
        current = self.category_list.currentItem()
        selected = None if current is None else current.data(_ID_ROLE)
        self.category_list.blockSignals(True)
        self.category_list.clear()
        self.category_list.addItem("全部")
        self.category_list.item(0).setData(_ID_ROLE, None)
        for category in categories:
            self.category_list.addItem(category.name)
            self.category_list.item(self.category_list.count() - 1).setData(_ID_ROLE, category.id)
        self.category_list.setCurrentRow(0)
        if selected is not None:
            for row in range(self.category_list.count()):
                if self.category_list.item(row).data(_ID_ROLE) == selected:
                    self.category_list.setCurrentRow(row)
                    break
        self.category_list.blockSignals(False)
        self.category_combo.clear()
        self.category_combo.addItem("未分类", None)
        for category in categories:
            self.category_combo.addItem(category.name, category.id)

    def refresh_documents(self) -> None:
        category_id = self._selected_category_id()
        favorite_only = self.favorite_filter.isChecked()
        tags = parse_tag_input(self.tag_filter.text()) or None
        query = self.search_edit.text()
        if query.strip():
            documents = self.service.search_documents(
                query,
                category_id=category_id,
                tags=tags,
                favorite_only=favorite_only,
                doc_type=self._doc_type,
            )
        else:
            documents = self.service.list_documents(
                category_id=category_id,
                tags=tags,
                favorite_only=favorite_only,
                doc_type=self._doc_type,
            )
        self._loading = True
        self.document_list.clear()
        chosen = None
        chosen_document = None
        for document in documents:
            type_label = _TYPE_LABELS.get(document.doc_type, document.doc_type)
            category = self._categories.get(document.category_id, "")
            favorite = "★" if document.is_favorite else "☆"
            updated = document.updated_at[:10] if document.updated_at else ""
            fields = (document.title, type_label, category, favorite, updated)
            summary = _summary(document.content)
            meta = " · ".join(part for part in (type_label, category) if part)
            lines = [f"★ {document.title}" if document.is_favorite else document.title]
            if summary:
                lines.append(summary)
            if meta:
                lines.append(meta)
            row = QListWidgetItem("\n".join(lines))
            row.setData(_ID_ROLE, document.id)
            row.setData(_FIELDS_ROLE, fields)
            row.setToolTip(document.title)
            self.document_list.addItem(row)
            if self._document_id == document.id:
                chosen = row
                chosen_document = document
        filtered = bool(query.strip() or favorite_only or tags or category_id is not None or self._doc_type)
        editing = self.detail_stack.currentIndex() == 1
        if documents:
            self.list_message.setVisible(False)
        elif filtered:
            self.list_message.setText("没有符合条件的知识")
            self.list_message.setVisible(True)
        else:
            self.list_message.setText("还没有知识")
            self.list_message.setVisible(True)
        if chosen_document is not None and not editing:
            self._show_document(chosen_document)
        elif chosen is not None:
            self.document_list.setCurrentItem(chosen)
        elif documents and not editing:
            self._show_document(documents[0])
        elif not documents and not filtered and not editing:
            self.begin_new()
        elif not documents and filtered and not editing:
            self.empty_detail.setText("没有符合条件的知识。")
            self._show_mode("empty")
            self.copy_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.edit_button.setEnabled(False)
        self._loading = False

    def begin_new(self) -> None:
        self._document_id = None
        self._loading = True
        self.title_edit.setText("")
        self._mark_title(False)
        self._set_type("NOTE")
        self.category_combo.setCurrentIndex(0)
        self.tags_edit.setText("")
        self.favorite_box.setChecked(False)
        self.content_edit.setPlainText("")
        self.status_label.setText("")
        self.copy_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self._capture_baseline()
        self._show_mode("edit")
        self._loading = False

    def save_document(self) -> None:
        content = self.content_edit.toPlainText()
        tags = parse_tag_input(self.tags_edit.text())
        doc_type = self.type_combo.currentData()
        category_id = self.category_combo.currentData()
        try:
            if self._document_id is None:
                document = self.service.create_document(
                    self.title_edit.text(),
                    content,
                    doc_type=doc_type,
                    category_id=category_id,
                    tags=tags,
                )
            else:
                document = self.service.update_document(
                    self._document_id,
                    title=self.title_edit.text(),
                    content=content,
                    doc_type=doc_type,
                    category_id=category_id,
                    tags=tags,
                )
            if document.is_favorite != self.favorite_box.isChecked():
                document = self.service.set_favorite(document.id, self.favorite_box.isChecked())
        except DocumentValidationError as exc:
            self._mark_title(True)
            self._show_mode("edit")
            self.status_label.setText(validation_text(str(exc)))
            return
        self._mark_title(False)
        self._show_document(document)
        self.refresh_documents()
        self.status_label.setText("已保存")

    def cancel_edit(self) -> None:
        if self._document_id is None:
            self.begin_new()
            return
        try:
            document = self.service.get_document(self._document_id)
        except DocumentNotFoundError:
            self.begin_new()
            return
        self._show_document(document)

    def delete_document(self) -> None:
        if self._document_id is None:
            return
        if not confirm_delete(self):
            return
        self.service.delete_document(self._document_id)
        self._document_id = None
        self._baseline = None
        self._show_mode("empty")
        self.refresh_documents()
        self.status_label.setText("已删除")

    def copy_document(self) -> None:
        if self._document_id is None:
            return
        text = self.service.get_copy_text(self._document_id)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.status_label.setText("已复制")

    def _on_cell(self, row: int, column: int) -> None:
        item = self.document_list.item(row)
        if item is None:
            return
        document_id = int(item.data(_ID_ROLE))
        if column == 3:
            self.service.toggle_favorite(document_id)
            self.refresh_documents()
        if document_id != self._document_id and not self._leave_editor():
            return
        document = self.service.get_document(document_id)
        self._show_document(document)

    def _show_document(self, document: Document) -> None:
        self._loading = True
        self._document_id = document.id
        self.title_edit.setText(document.title)
        self._mark_title(False)
        self._set_type(document.doc_type)
        index = self.category_combo.findData(document.category_id)
        self.category_combo.setCurrentIndex(0 if index < 0 else index)
        self.tags_edit.setText(", ".join(tag.name for tag in document.tags))
        self.favorite_box.setChecked(document.is_favorite)
        self.content_edit.setPlainText(document.content)
        type_label = _TYPE_LABELS.get(document.doc_type, document.doc_type)
        category = self._categories.get(document.category_id, "未分类")
        self.read_title.setText(document.title)
        self.read_meta.setText(f"{type_label}  ·  {category}")
        self.read_body.set_markdown(document.content)
        tags = ", ".join(tag.name for tag in document.tags)
        self.read_tags.setText(tags)
        self.read_tags.setVisible(bool(tags))
        self.copy_button.setEnabled(True)
        self.delete_button.setEnabled(True)
        self.edit_button.setEnabled(True)
        self._capture_baseline()
        self._show_mode("browse")
        for row in range(self.document_list.count()):
            item = self.document_list.item(row)
            if item is not None and item.data(_ID_ROLE) == document.id:
                self.document_list.setCurrentRow(row)
                break
        self._loading = False

    def _favorite_toggled(self, checked: bool) -> None:
        if self._loading or self._document_id is None or self.detail_stack.currentIndex() != 0:
            return
        self.service.set_favorite(self._document_id, checked)
        self._capture_baseline()
        self.refresh_documents()

    def _request_new(self) -> None:
        if not self._leave_editor():
            return
        self.begin_new()

    def _leave_editor(self) -> bool:
        if not self._dirty():
            return True
        choice = confirm_leave(self)
        if choice == "cancel":
            return False
        if choice == "discard":
            return True
        self.save_document()
        return not self._dirty()

    def _begin_edit(self) -> None:
        if self._document_id is None:
            self.begin_new()
            return
        self._show_mode("edit")

    def _row_changed(self, row: int) -> None:
        if self._loading or row < 0:
            return
        self._on_cell(row, 0)

    def _type_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self.type_list.item(row)
        self._doc_type = None if item is None else item.data(_ID_ROLE)
        if not self._loading:
            self.refresh_documents()

    def _category_changed(self, _row: int) -> None:
        if not self._loading:
            self.refresh_documents()

    def _filter_type(self, code: str | None) -> None:
        self._doc_type = code
        for row in range(self.type_list.count()):
            if self.type_list.item(row).data(_ID_ROLE) == code:
                self.type_list.setCurrentRow(row)
                return
        self.refresh_documents()

    def _set_type(self, code: str) -> None:
        index = self.type_combo.findData(code)
        self.type_combo.setCurrentIndex(0 if index < 0 else index)

    def _selected_category_id(self) -> int | None:
        item = self.category_list.currentItem()
        if item is None:
            return None
        value = item.data(_ID_ROLE)
        return None if value is None else int(value)

    def _capture_baseline(self) -> None:
        self._baseline = (
            self.title_edit.text(),
            self.content_edit.toPlainText(),
            self.type_combo.currentData(),
            self.category_combo.currentData(),
            self.tags_edit.text(),
            self.favorite_box.isChecked(),
        )

    def _dirty(self) -> bool:
        if self._baseline is None or self.detail_stack.currentIndex() != 1:
            return False
        current = (
            self.title_edit.text(),
            self.content_edit.toPlainText(),
            self.type_combo.currentData(),
            self.category_combo.currentData(),
            self.tags_edit.text(),
            self.favorite_box.isChecked(),
        )
        return current != self._baseline

    def _show_mode(self, mode: str) -> None:
        self.detail_stack.setCurrentIndex({"browse": 0, "edit": 1, "empty": 2}[mode])

    def _mark_title(self, invalid: bool) -> None:
        self.title_edit.setProperty("error", invalid)
        restyle(self.title_edit)

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label


def _summary(content: str) -> str:
    line = " ".join(content.split())
    if len(line) <= 42:
        return line
    return line[:41] + "…"


def confirm_delete(parent: QWidget) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("删除")
    box.setText("删除这篇知识？")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    yes = box.button(QMessageBox.StandardButton.Yes)
    no = box.button(QMessageBox.StandardButton.No)
    if yes is not None:
        yes.setText("删除")
    if no is not None:
        no.setText("取消")
        box.setDefaultButton(no)
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_leave(parent: QWidget) -> str:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("尚未保存")
    box.setText("这篇知识有尚未保存的修改。")
    save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return "save"
    if clicked is discard:
        return "discard"
    return "cancel"
