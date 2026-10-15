"""WebChannel bridge for Web Archive workspace."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from techvault.ui.bridge._json import err, ok
from techvault.web_archive.models import ArticleDetail, ArticleListItem
from techvault.web_archive.service import WebArchiveService
from techvault.web_archive.urls import parse_web_url

PAGE_SIZE = 50


class WebArchiveBridge(QObject):
    def __init__(self, service: WebArchiveService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

    @Slot(result=str)
    def getStatus(self) -> str:  # noqa: N802
        return ok({"ready": True, "pageSize": PAGE_SIZE, "supportedSites": ["zhihu", "generic"]})

    @Slot(str, result=str)
    def parseUrl(self, url: str) -> str:  # noqa: N802
        parsed = parse_web_url(url or "")
        return ok(
            {
                "kind": parsed.kind,
                "siteKey": parsed.site_key,
                "siteLabel": parsed.site_label,
                "canonical": parsed.canonical,
                "message": parsed.message,
                "ok": parsed.kind in {"answer", "article", "generic"},
            }
        )

    @Slot(str, result=str)
    def listArticles(self, payload_json: str) -> str:  # noqa: N802
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return err("INVALID_PAYLOAD", "筛选参数无效")
        query = str(payload.get("query") or "")
        offset = int(payload.get("offset") or 0)
        favorite_only = bool(payload.get("favoriteOnly") or False)
        site_key = payload.get("siteKey") or None
        if site_key in ("", "all", None):
            site_key = None
        category_raw = payload.get("categoryId")
        category_id = None if category_raw in (None, "", "all", "null") else int(category_raw)
        rows = self._service.list_articles(
            query=query,
            site_key=site_key,
            favorite_only=favorite_only,
            category_id=category_id,
            limit=PAGE_SIZE + 1,
            offset=offset,
        )
        has_more = len(rows) > PAGE_SIZE
        page = rows[:PAGE_SIZE]
        return ok(
            {
                "items": [_list_json(item) for item in page],
                "offset": offset,
                "pageSize": PAGE_SIZE,
                "hasMore": has_more,
            }
        )

    @Slot(int, result=str)
    def getArticle(self, article_id: int) -> str:  # noqa: N802
        detail = self._service.get_article(int(article_id))
        if detail is None:
            return err("NOT_FOUND", "文章不存在")
        return ok(_detail_json(detail))

    @Slot(str, result=str)
    def updateArticle(self, payload_json: str) -> str:  # noqa: N802
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return err("INVALID_PAYLOAD", "参数无效")
        article_id = int(payload.get("id") or 0)
        if not article_id:
            return err("INVALID_PAYLOAD", "缺少文章 id")
        kwargs = {}
        if "favorite" in payload or "isFavorite" in payload:
            kwargs["is_favorite"] = bool(payload.get("favorite", payload.get("isFavorite")))
        if "categoryId" in payload:
            raw = payload.get("categoryId")
            kwargs["category_id"] = None if raw in (None, "", "null") else int(raw)
        detail = self._service.update_article(article_id, **kwargs)
        if detail is None:
            return err("NOT_FOUND", "文章不存在")
        return ok(_detail_json(detail))

    @Slot(int, result=str)
    def deleteArticle(self, article_id: int) -> str:  # noqa: N802
        if not self._service.delete_article(int(article_id)):
            return err("NOT_FOUND", "文章不存在")
        return ok({"deleted": True, "id": int(article_id)})

    @Slot(int, result=str)
    def openLocalArchive(self, article_id: int) -> str:  # noqa: N802
        detail = self._service.get_article(int(article_id))
        if detail is None or not detail.archive_folder:
            return err("NOT_FOUND", "本地目录不存在")
        path = Path(detail.archive_folder)
        if not path.is_dir():
            return err("MISSING", "本地目录不存在")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        return ok({"path": str(path)})

    @Slot(int, result=str)
    def openOriginalUrl(self, article_id: int) -> str:  # noqa: N802
        detail = self._service.get_article(int(article_id))
        if detail is None or not detail.canonical_url:
            return err("NOT_FOUND", "没有原链接")
        QDesktopServices.openUrl(QUrl(detail.canonical_url))
        return ok({"url": detail.canonical_url})

    @Slot(result=str)
    def listSites(self) -> str:  # noqa: N802
        return ok(self._service.site_keys())


def _list_json(item: ArticleListItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "author": item.author,
        "siteKey": item.site_key,
        "siteLabel": item.site_label,
        "status": item.status,
        "statusLabel": item.status_label,
        "capturedAt": item.captured_at,
        "isFavorite": item.is_favorite,
        "canonicalUrl": item.canonical_url,
        "summary": item.summary,
    }


def _detail_json(detail: ArticleDetail) -> dict:
    base = _list_json(detail)
    base.update(
        {
            "sourceUrl": detail.source_url,
            "publishedAt": detail.published_at,
            "bodyHtml": detail.body_html,
            "bodyMd": detail.body_md,
            "plainText": detail.plain_text,
            "archiveFolder": detail.archive_folder,
            "mediaTotal": detail.media_total,
            "mediaMissing": detail.media_missing,
            "contentHash": detail.content_hash,
            "captureMode": detail.capture_mode,
            "failureMessage": detail.failure_message,
            "categoryId": detail.category_id,
            "categoryName": detail.category_name,
            "metadataJson": detail.metadata_json,
        }
    )
    return base
