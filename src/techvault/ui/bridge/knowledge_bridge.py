"""Knowledge bridge — thin adapter over DocumentService."""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QGuiApplication

from techvault.documents.models import Document, DocumentNotFoundError, DocumentValidationError
from techvault.documents.service import DocumentService
from techvault.ui.bridge._json import err, ok
from techvault.ui.content_render import compose_readme_html
from techvault.ui.i18n import validation_text
from techvault.ui.tags import parse_tag_input

_TYPE_LABELS = {
    "COMMAND": "命令",
    "PROMPT": "AI 提示词",
    "TIP": "技巧",
    "NOTE": "笔记",
}
_MD_NOISE = re.compile(r"[#>*`_\-[\]()!]+")
PAGE_SIZE = 50


class KnowledgeBridge(QObject):
    def __init__(self, documents: DocumentService, parent=None) -> None:
        super().__init__(parent)
        self._documents = documents

    @Slot(result=str)
    def getStatus(self) -> str:  # noqa: N802
        return ok({"ready": True, "pageSize": PAGE_SIZE})

    @Slot(result=str)
    def listCategories(self) -> str:  # noqa: N802
        items = [
            {"id": c.id, "name": c.name, "sortOrder": c.sort_order}
            for c in self._documents.list_categories()
        ]
        return ok(items)

    @Slot(str, result=str)
    def listDocuments(self, payload_json: str) -> str:  # noqa: N802
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return err("INVALID_PAYLOAD", "筛选参数无效")
        query = str(payload.get("query") or "")
        offset = int(payload.get("offset") or 0)
        favorite_only = bool(payload.get("favoriteOnly") or False)
        doc_type = payload.get("docType") or None
        if doc_type == "" or doc_type == "all":
            doc_type = None
        category_raw = payload.get("categoryId")
        category_id = None if category_raw in (None, "", "all", "null") else int(category_raw)
        tags_raw = payload.get("tags") or ""
        tags = parse_tag_input(str(tags_raw)) or None
        try:
            if query.strip():
                rows = self._documents.search_documents(
                    query,
                    limit=PAGE_SIZE + 1,
                    offset=offset,
                    category_id=category_id,
                    tags=tags,
                    favorite_only=favorite_only,
                    doc_type=doc_type,
                )
            else:
                rows = self._documents.list_documents(
                    limit=PAGE_SIZE + 1,
                    offset=offset,
                    category_id=category_id,
                    tags=tags,
                    favorite_only=favorite_only,
                    doc_type=doc_type,
                )
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        has_more = len(rows) > PAGE_SIZE
        page = rows[:PAGE_SIZE]
        categories = {c.id: c.name for c in self._documents.list_categories()}
        return ok(
            {
                "items": [_list_item(doc, categories) for doc in page],
                "offset": offset,
                "pageSize": PAGE_SIZE,
                "hasMore": has_more,
            }
        )

    @Slot(int, result=str)
    def getDocument(self, document_id: int) -> str:  # noqa: N802
        try:
            document = self._documents.get_document(int(document_id))
        except DocumentNotFoundError:
            return err("NOT_FOUND", "文档不存在")
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        categories = {c.id: c.name for c in self._documents.list_categories()}
        return ok(_detail(document, categories))

    @Slot(str, result=str)
    def saveDocument(self, payload_json: str) -> str:  # noqa: N802
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return err("INVALID_PAYLOAD", "保存参数无效")
        title = str(payload.get("title") or "")
        content = str(payload.get("content") or "")
        doc_type = str(payload.get("docType") or "NOTE")
        category_raw = payload.get("categoryId")
        category_id = None if category_raw in (None, "", "null") else int(category_raw)
        tags = payload.get("tags") or []
        if isinstance(tags, str):
            tags = parse_tag_input(tags)
        favorite = bool(payload.get("favorite") or False)
        document_id = payload.get("id")
        categories = {c.id: c.name for c in self._documents.list_categories()}
        try:
            if document_id is None:
                document = self._documents.create_document(
                    title,
                    content,
                    doc_type=doc_type,
                    category_id=category_id,
                    tags=list(tags),
                )
            else:
                document = self._documents.update_document(
                    int(document_id),
                    title=title,
                    content=content,
                    doc_type=doc_type,
                    category_id=category_id,
                    tags=list(tags),
                )
            if document.is_favorite != favorite:
                document = self._documents.set_favorite(document.id, favorite)
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        except DocumentNotFoundError:
            return err("NOT_FOUND", "文档不存在")
        return ok(_detail(document, categories))

    @Slot(int, result=str)
    def deleteDocument(self, document_id: int) -> str:  # noqa: N802
        try:
            self._documents.delete_document(int(document_id))
        except DocumentNotFoundError:
            return err("NOT_FOUND", "文档不存在")
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        return ok({"id": int(document_id)})

    @Slot(int, bool, result=str)
    def setFavorite(self, document_id: int, favorite: bool) -> str:  # noqa: N802
        try:
            document = self._documents.set_favorite(int(document_id), bool(favorite))
        except DocumentNotFoundError:
            return err("NOT_FOUND", "文档不存在")
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        categories = {c.id: c.name for c in self._documents.list_categories()}
        return ok(_detail(document, categories))

    @Slot(int, result=str)
    def copyDocument(self, document_id: int) -> str:  # noqa: N802
        try:
            text = self._documents.get_copy_text(int(document_id))
        except DocumentNotFoundError:
            return err("NOT_FOUND", "文档不存在")
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return err("CLIPBOARD", "无法访问剪贴板")
        clipboard.setText(text)
        return ok({"copied": True, "length": len(text)})

    @Slot(str, result=str)
    def createCategory(self, name: str) -> str:  # noqa: N802
        try:
            category = self._documents.create_category(name)
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        return ok({"id": category.id, "name": category.name, "sortOrder": category.sort_order})

    @Slot(int, str, result=str)
    def renameCategory(self, category_id: int, name: str) -> str:  # noqa: N802
        try:
            category = self._documents.rename_category(int(category_id), name)
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        return ok({"id": category.id, "name": category.name, "sortOrder": category.sort_order})

    @Slot(int, result=str)
    def deleteCategory(self, category_id: int) -> str:  # noqa: N802
        try:
            self._documents.delete_category(int(category_id))
        except DocumentValidationError as exc:
            return err("VALIDATION", validation_text(str(exc)))
        return ok({"id": int(category_id)})


def _list_item(document: Document, categories: dict[int, str]) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "summary": _summary(document.content),
        "docType": document.doc_type,
        "docTypeLabel": _TYPE_LABELS.get(document.doc_type, document.doc_type),
        "categoryId": document.category_id,
        "categoryName": categories.get(document.category_id or -1, "未分类")
        if document.category_id
        else "未分类",
        "isFavorite": document.is_favorite,
        "updatedAt": document.updated_at,
        "updatedLabel": _date_label(document.updated_at),
    }


def _detail(document: Document, categories: dict[int, str]) -> dict:
    html = compose_readme_html("markdown", document.content)
    return {
        **_list_item(document, categories),
        "content": document.content,
        "contentHtml": html,
        "tags": [tag.name for tag in document.tags],
        "createdAt": document.created_at,
    }


def _summary(content: str) -> str:
    text = _MD_NOISE.sub(" ", content)
    text = " ".join(text.split())
    if len(text) <= 80:
        return text
    return text[:79] + "…"


def _date_label(value: str) -> str:
    if not value:
        return ""
    # ISO-ish: 2026-10-14T...
    day = value[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        return f"{int(day[5:7])}月{int(day[8:10])}日"
    return value[:10]
