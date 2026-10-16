"""Web archive domain models."""

from __future__ import annotations

from dataclasses import dataclass, field


class ArchiveStatus:
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    NEEDS_LOGIN = "NEEDS_LOGIN"
    LINK_ONLY = "LINK_ONLY"
    CANCELLED = "CANCELLED"


@dataclass
class ExtractedArticle:
    title: str
    author: str | None
    published_at: str | None
    body_html: str
    plain_text: str
    site_key: str
    site_label: str
    canonical_url: str
    source_url: str
    extractor: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class CaptureResult:
    status: str
    message: str
    article_id: int | None = None
    title: str | None = None
    archive_folder: str | None = None
    failure_code: str | None = None
    capture_mode: str = "URL_AUTO"
    suggest_browser: bool = False
    diagnostics: dict | None = None
    snapshot_status: str = "NOT_CAPTURED"
    snapshot_message: str | None = None
    snapshot_path: str | None = None
    snapshot_bytes: int | None = None


@dataclass(frozen=True)
class ArticleListItem:
    id: int
    title: str
    author: str
    site_key: str
    site_label: str
    status: str
    status_label: str
    captured_at: str
    is_favorite: bool
    canonical_url: str
    summary: str


@dataclass(frozen=True)
class ArticleDetail(ArticleListItem):
    source_url: str
    published_at: str
    body_html: str
    body_md: str
    plain_text: str
    archive_folder: str
    media_total: int
    media_missing: int
    content_hash: str
    capture_mode: str
    failure_message: str
    category_id: int | None
    category_name: str
    metadata_json: str
    snapshot_status: str = "NOT_CAPTURED"
    relative_snapshot: str = ""
    snapshot_bytes: int | None = None
    snapshot_sha256: str = ""
    snapshot_captured_at: str = ""
    snapshot_failure: str = ""
    snapshot_path: str = ""


@dataclass(frozen=True)
class CaptureVersionInfo:
    id: int
    version: int
    status: str
    captured_at: str
    capture_mode: str
    snapshot_status: str
    snapshot_bytes: int | None
    snapshot_failure: str
    has_snapshot_file: bool
    relative_snapshot: str

