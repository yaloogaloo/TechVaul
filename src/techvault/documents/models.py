"""Document knowledge-base values and validation errors."""

from __future__ import annotations

from dataclasses import dataclass


DOCUMENT_TYPES = ("COMMAND", "PROMPT", "TIP", "NOTE")


class _UnsetType:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    sort_order: int


@dataclass(frozen=True)
class Tag:
    id: int
    name: str


@dataclass(frozen=True)
class Document:
    id: int
    title: str
    content: str
    doc_type: str
    category_id: int | None
    is_favorite: bool
    created_at: str
    updated_at: str
    tags: tuple[Tag, ...] = ()


class DocumentValidationError(ValueError):
    pass


class DocumentNotFoundError(LookupError):
    pass
