"""Application service for the local technical knowledge base."""

from __future__ import annotations

import sqlite3

from techvault.documents.models import (
    DOCUMENT_TYPES,
    UNSET,
    Category,
    Document,
    DocumentNotFoundError,
    DocumentValidationError,
    Tag,
)
from techvault.documents.repository import CategoryRepository, DocumentRepository, TagRepository


class DocumentService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._documents = DocumentRepository(connection)
        self._categories = CategoryRepository(connection)
        self._tags = TagRepository(connection)

    def create_document(
        self,
        title: str,
        content: str = "",
        *,
        doc_type: str = "NOTE",
        category_id: int | None = None,
        tags: list[str] | None = None,
    ) -> Document:
        cleaned_title = _required_text(title, field="Title")
        if not isinstance(content, str):
            raise DocumentValidationError("Content must be text")
        cleaned_type = _document_type(doc_type)
        _optional_id(category_id, field="Category")
        cleaned_tags = _tag_names(tags)
        return self._documents.create(
            title=cleaned_title,
            content=content,
            doc_type=cleaned_type,
            category_id=category_id,
            tag_names=cleaned_tags,
        )

    def get_document(self, document_id: int) -> Document:
        _required_id(document_id, field="Document")
        stored = self._documents.get(document_id)
        if stored is None:
            raise DocumentNotFoundError("Document does not exist")
        return stored

    def update_document(
        self,
        document_id: int,
        *,
        title: object = UNSET,
        content: object = UNSET,
        doc_type: object = UNSET,
        category_id: object = UNSET,
        tags: object = UNSET,
    ) -> Document:
        current = self.get_document(document_id)
        if all(value is UNSET for value in (title, content, doc_type, category_id, tags)):
            return current
        new_title = current.title if title is UNSET else _required_text(title, field="Title")
        if content is UNSET:
            new_content = current.content
        else:
            if not isinstance(content, str):
                raise DocumentValidationError("Content must be text")
            new_content = content
        new_type = current.doc_type if doc_type is UNSET else _document_type(doc_type)
        if category_id is UNSET:
            new_category = current.category_id
        else:
            _optional_id(category_id, field="Category")
            new_category = category_id
        new_tags = None if tags is UNSET else _tag_names(tags)
        return self._documents.update(
            document_id,
            title=new_title,
            content=new_content,
            doc_type=new_type,
            category_id=new_category,
            tag_names=new_tags,
            previous_title=current.title,
            previous_content=current.content,
        )

    def delete_document(self, document_id: int) -> None:
        _required_id(document_id, field="Document")
        self._documents.delete(document_id)

    def list_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        category_id: int | None = None,
        tags: list[str] | None = None,
        match_all_tags: bool = False,
        favorite_only: bool = False,
        doc_type: str | None = None,
    ) -> list[Document]:
        bounded_limit, bounded_offset = _page(limit, offset)
        _optional_id(category_id, field="Category")
        _optional_doc_type(doc_type)
        return self._documents.list_documents(
            limit=bounded_limit,
            offset=bounded_offset,
            category_id=category_id,
            tag_ids=self._tag_filter(tags, match_all_tags),
            match_all_tags=match_all_tags,
            favorite_only=favorite_only,
            fts_query=None,
            doc_type=doc_type,
        )

    def search_documents(
        self,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
        category_id: int | None = None,
        tags: list[str] | None = None,
        match_all_tags: bool = False,
        favorite_only: bool = False,
        doc_type: str | None = None,
    ) -> list[Document]:
        if not isinstance(query, str):
            raise DocumentValidationError("Search query must be text")
        fts_query = _fts_query(query)
        bounded_limit, bounded_offset = _page(limit, offset)
        _optional_id(category_id, field="Category")
        _optional_doc_type(doc_type)
        if fts_query is None:
            return []
        return self._documents.list_documents(
            limit=bounded_limit,
            offset=bounded_offset,
            category_id=category_id,
            tag_ids=self._tag_filter(tags, match_all_tags),
            match_all_tags=match_all_tags,
            favorite_only=favorite_only,
            fts_query=fts_query,
            doc_type=doc_type,
        )

    def set_favorite(self, document_id: int, favorite: bool) -> Document:
        _required_id(document_id, field="Document")
        if not isinstance(favorite, bool):
            raise DocumentValidationError("Favorite must be true or false")
        return self._documents.set_favorite(document_id, favorite)

    def toggle_favorite(self, document_id: int) -> Document:
        current = self.get_document(document_id)
        return self._documents.set_favorite(document_id, not current.is_favorite)

    def get_copy_text(self, document_id: int) -> str:
        return self.get_document(document_id).content

    def list_categories(self) -> list[Category]:
        return self._categories.list_all()

    def create_category(self, name: str, sort_order: int = 0) -> Category:
        if isinstance(sort_order, bool) or not isinstance(sort_order, int):
            raise DocumentValidationError("Category sort order must be an integer")
        return self._categories.create(_required_text(name, field="Category"), sort_order)

    def rename_category(self, category_id: int, name: str) -> Category:
        _required_id(category_id, field="Category")
        return self._categories.rename(category_id, _required_text(name, field="Category"))

    def delete_category(self, category_id: int) -> None:
        _required_id(category_id, field="Category")
        self._categories.delete(category_id)

    def create_tag(self, name: str) -> Tag:
        return self._tags.create(_required_text(name, field="Tag"))

    def rename_tag(self, tag_id: int, name: str) -> Tag:
        _required_id(tag_id, field="Tag")
        return self._tags.rename(tag_id, _required_text(name, field="Tag"))

    def delete_tag(self, tag_id: int) -> None:
        _required_id(tag_id, field="Tag")
        self._tags.delete(tag_id)

    def attach_tag(self, document_id: int, tag_id: int) -> None:
        _required_id(document_id, field="Document")
        _required_id(tag_id, field="Tag")
        self._tags.attach(document_id, tag_id)

    def detach_tag(self, document_id: int, tag_id: int) -> None:
        _required_id(document_id, field="Document")
        _required_id(tag_id, field="Tag")
        self._tags.detach(document_id, tag_id)

    def _tag_filter(self, tags: list[str] | None, match_all_tags: bool) -> list[int] | None:
        if tags is None:
            return None
        names = _tag_names(tags)
        if not names:
            return None
        found = self._tags.ids_for_names(names)
        unique_found = list(dict.fromkeys(found))
        unique_names = {name.casefold() for name in names}
        if match_all_tags and len(unique_found) != len(unique_names):
            return []
        return unique_found


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise DocumentValidationError(f"{field} must be text")
    cleaned = value.strip()
    if cleaned == "":
        raise DocumentValidationError(f"{field} cannot be empty")
    return cleaned


def _tag_names(tags: object) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise DocumentValidationError("Tags must be a list of names")
    return [_required_text(name, field="Tag") for name in tags]


def _document_type(value: object) -> str:
    if not isinstance(value, str) or value not in DOCUMENT_TYPES:
        raise DocumentValidationError("Document type is not supported")
    return value


def _optional_doc_type(value: object) -> None:
    if value is None:
        return
    _document_type(value)


def _required_id(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DocumentValidationError(f"{field} id is not valid")
    return value


def _optional_id(value: object, *, field: str) -> None:
    if value is None:
        return
    _required_id(value, field=field)


def _page(limit: object, offset: object) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise DocumentValidationError("Limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise DocumentValidationError("Offset must be a non-negative integer")
    return limit, offset


def _fts_query(query: str) -> str | None:
    tokens = query.split()
    if not tokens:
        return None
    quoted = []
    for token in tokens:
        quoted.append('"' + token.replace('"', '""') + '"*')
    return " AND ".join(quoted)
