"""WebChannel bridge for Web Archive workspace."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QDesktopServices

from techvault.ui.bridge._json import err, ok
from techvault.web_archive.browser_profile import clear_zhihu_session, zhihu_profile_dir
from techvault.web_archive.models import ArticleDetail, ArticleListItem, CaptureResult
from techvault.web_archive.service import WebArchiveService
from techvault.web_archive.urls import parse_web_url

PAGE_SIZE = 50


class WebArchiveBridge(QObject):
    def __init__(
        self,
        service: WebArchiveService,
        database_path: Path,
        parent=None,
        task_bridge=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._database_path = Path(database_path)
        self._capture_window = None
        self._snapshot_window = None
        self._task_bridge = task_bridge

    @Slot(result=str)
    def getStatus(self) -> str:  # noqa: N802
        return ok(
            {
                "ready": True,
                "pageSize": PAGE_SIZE,
                "supportedSites": ["zhihu", "xiaoheihe", "generic"],
                "browserCaptureReady": True,
                "zhihuProfile": str(zhihu_profile_dir(anonymous=False)),
            }
        )

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

    @Slot(str, result=str)
    def openBrowserCapture(self, url: str) -> str:  # noqa: N802
        cleaned = (url or "").strip()
        if not cleaned:
            return err("INVALID_URL", "请输入网页链接")
        parsed = parse_web_url(cleaned)
        if parsed.kind == "question":
            return err("QUESTION_HUB", parsed.message or "请打开具体回答链接")
        if parsed.kind == "unsupported":
            return err("UNSUPPORTED", parsed.message or "暂不支持该链接")
        target = parsed.canonical or cleaned
        from techvault.ui.web_capture_window import WebCaptureWindow

        if self._capture_window is not None:
            try:
                self._capture_window.close()
            except RuntimeError:
                pass
        window = WebCaptureWindow(
            self._database_path,
            target,
            use_login_profile=False,
            parent=None,
        )
        window.captureFinished.connect(self._on_browser_capture_finished)
        self._capture_window = window
        window.show()
        window.raise_()
        window.activateWindow()
        return ok({"opened": True, "url": target, "mode": "anonymous"})

    @Slot(result=str)
    def clearZhihuSession(self) -> str:  # noqa: N802
        path = clear_zhihu_session()
        return ok({"cleared": True, "profile": str(path)})

    @Slot(result=str)
    def getSessionStatus(self) -> str:  # noqa: N802
        login_dir = zhihu_profile_dir(anonymous=False)
        cookies = login_dir / "Cookies"
        return ok(
            {
                "profileDir": str(login_dir),
                "hasPersistedData": any(login_dir.iterdir()) if login_dir.is_dir() else False,
                "cookieStorePresent": cookies.exists(),
                "note": "会话可能过期；配置目录含敏感数据，勿分享。",
            }
        )

    @Slot(int, result=str)
    def listCaptures(self, article_id: int) -> str:  # noqa: N802
        rows = self._service.list_captures(int(article_id))
        return ok(
            {
                "items": [
                    {
                        "id": r.id,
                        "version": r.version,
                        "status": r.status,
                        "capturedAt": r.captured_at,
                        "captureMode": r.capture_mode,
                        "snapshotStatus": r.snapshot_status,
                        "snapshotBytes": r.snapshot_bytes,
                        "snapshotFailure": r.snapshot_failure,
                        "hasSnapshotFile": r.has_snapshot_file,
                        "relativeSnapshot": r.relative_snapshot,
                    }
                    for r in rows
                ]
            }
        )

    @Slot(str, result=str)
    def openSnapshotPreview(self, payload_json: str) -> str:  # noqa: N802
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return err("INVALID_PAYLOAD", "参数无效")
        article_id = int(payload.get("articleId") or payload.get("id") or 0)
        capture_id_raw = payload.get("captureId")
        capture_id = None if capture_id_raw in (None, "", "null") else int(capture_id_raw)
        if not article_id:
            return err("INVALID_PAYLOAD", "缺少文章 id")
        detail = self._service.get_article(article_id)
        if detail is None:
            return err("NOT_FOUND", "文章不存在")
        path = self._service.resolve_snapshot_path(article_id, capture_id)
        if path is None or not path.is_file():
            return err("NO_SNAPSHOT", "此文章尚未保存网页快照。")
        from techvault.ui.snapshot_viewer_window import SnapshotViewerWindow

        if self._snapshot_window is not None:
            try:
                self._snapshot_window.close()
            except RuntimeError:
                pass
        window = SnapshotViewerWindow(
            path,
            title=detail.title or "网页快照",
            subtitle=(
                f"捕获时间：{detail.snapshot_captured_at or detail.captured_at} · "
                f"大小：{detail.snapshot_bytes or path.stat().st_size} 字节 · 离线预览"
            ),
            parent=None,
        )
        self._snapshot_window = window
        window.show()
        window.raise_()
        window.activateWindow()
        return ok({"opened": True, "path": str(path), "offline": True})

    @Slot(str, result=str)
    def openSnapshotFile(self, payload_json: str) -> str:  # noqa: N802
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return err("INVALID_PAYLOAD", "参数无效")
        article_id = int(payload.get("articleId") or payload.get("id") or 0)
        capture_id_raw = payload.get("captureId")
        capture_id = None if capture_id_raw in (None, "", "null") else int(capture_id_raw)
        path = self._service.resolve_snapshot_path(article_id, capture_id)
        if path is None or not path.is_file():
            return err("NO_SNAPSHOT", "此文章尚未保存网页快照。")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        return ok({"path": str(path)})

    def _on_browser_capture_finished(self, result: CaptureResult) -> None:
        # Manual browser save: publish task snapshot so React refreshes list + selection.
        # Skip when TaskBridge already owns an auto-fallback capture (busy / same window).
        tb = self._task_bridge
        if tb is None:
            return
        try:
            if getattr(tb, "_capture_window", None) is not None:
                return
            if tb._state.get("active"):  # noqa: SLF001
                return
            tb._finalize_web(result)  # noqa: SLF001
        except Exception:
            pass


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
            # bodyMd / plainText / metadataJson omitted from hot path to keep WebChannel payloads small.
            "bodyMd": "",
            "plainText": "",
            "archiveFolder": detail.archive_folder,
            "mediaTotal": detail.media_total,
            "mediaMissing": detail.media_missing,
            "contentHash": detail.content_hash,
            "captureMode": detail.capture_mode,
            "failureMessage": detail.failure_message,
            "categoryId": detail.category_id,
            "categoryName": detail.category_name,
            "metadataJson": "",
            "snapshotStatus": detail.snapshot_status,
            "relativeSnapshot": detail.relative_snapshot,
            "snapshotBytes": detail.snapshot_bytes,
            "snapshotSha256": detail.snapshot_sha256,
            "snapshotCapturedAt": detail.snapshot_captured_at,
            "snapshotFailure": detail.snapshot_failure,
            "snapshotPath": detail.snapshot_path,
            "hasSnapshot": bool(detail.snapshot_path) and detail.snapshot_status == "COMPLETE",
        }
    )
    return base
