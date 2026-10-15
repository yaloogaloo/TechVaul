"""WebArchiveService — list/search/CRUD over web_articles."""

from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from techvault.web_archive.capture import archive_url
from techvault.web_archive.models import (
    ArchiveStatus,
    ArticleDetail,
    ArticleListItem,
    CaptureResult,
)
from techvault.web_archive.paths import WebPathLayout

_STATUS_LABELS = {
    ArchiveStatus.COMPLETE: "完整归档",
    ArchiveStatus.PARTIAL: "部分归档",
    ArchiveStatus.FAILED: "失败",
    ArchiveStatus.NEEDS_LOGIN: "需要登录",
    ArchiveStatus.LINK_ONLY: "仅链接",
    ArchiveStatus.CANCELLED: "已取消",
}


class WebArchiveService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def archive(
        self,
        url: str,
        *,
        cancel_event: Event | None = None,
        on_progress=None,
    ) -> CaptureResult:
        return archive_url(
            self._connection,
            url,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )

    def list_articles(
        self,
        *,
        query: str = "",
        site_key: str | None = None,
        favorite_only: bool = False,
        category_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ArticleListItem]:
        if query.strip():
            return self.search_articles(
                query,
                site_key=site_key,
                favorite_only=favorite_only,
                category_id=category_id,
                limit=limit,
                offset=offset,
            )
        clauses = ["1=1"]
        params: list = []
        if site_key:
            clauses.append("site_key = ?")
            params.append(site_key)
        if favorite_only:
            clauses.append("is_favorite = 1")
        if category_id is not None:
            clauses.append("category_id = ?")
            params.append(category_id)
        params.extend([limit, offset])
        rows = self._connection.execute(
            f"""
            SELECT web_articles.*, categories.name AS category_name
            FROM web_articles
            LEFT JOIN categories ON categories.id = web_articles.category_id
            WHERE {" AND ".join(clauses)}
            ORDER BY captured_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [_list_item(row) for row in rows]

    def search_articles(
        self,
        query: str,
        *,
        site_key: str | None = None,
        favorite_only: bool = False,
        category_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ArticleListItem]:
        text = query.strip()
        # CJK-friendly: FTS unicode61 does not segment Chinese; also use LIKE.
        clauses = [
            "("
            "web_articles.id IN (SELECT rowid FROM web_article_search WHERE web_article_search MATCH ?) "
            "OR web_articles.title LIKE ? OR IFNULL(web_articles.author,'') LIKE ? "
            "OR IFNULL(web_articles.plain_text,'') LIKE ?"
            ")"
        ]
        like = f"%{text}%"
        params: list = [text, like, like, like]
        if site_key:
            clauses.append("web_articles.site_key = ?")
            params.append(site_key)
        if favorite_only:
            clauses.append("web_articles.is_favorite = 1")
        if category_id is not None:
            clauses.append("web_articles.category_id = ?")
            params.append(category_id)
        params.extend([limit, offset])
        rows = self._connection.execute(
            f"""
            SELECT web_articles.*, categories.name AS category_name
            FROM web_articles
            LEFT JOIN categories ON categories.id = web_articles.category_id
            WHERE {" AND ".join(clauses)}
            ORDER BY web_articles.captured_at DESC, web_articles.id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [_list_item(row) for row in rows]

    def get_article(self, article_id: int) -> ArticleDetail | None:
        row = self._connection.execute(
            """
            SELECT web_articles.*, categories.name AS category_name
            FROM web_articles
            LEFT JOIN categories ON categories.id = web_articles.category_id
            WHERE web_articles.id = ?
            """,
            (article_id,),
        ).fetchone()
        if row is None:
            return None
        body_html = ""
        body_md = ""
        meta_text = ""
        folder = ""
        if row["archive_root"] and row["storage_relpath"]:
            root = Path(row["archive_root"])
            folder_path = root / row["storage_relpath"]
            folder = str(folder_path)
            html_path = folder_path / "article.html"
            md_path = folder_path / "article.md"
            meta_path = folder_path / "metadata.json"
            if html_path.is_file():
                body_html = _rewrite_media_to_file_urls(html_path.read_text(encoding="utf-8"), folder_path)
            if md_path.is_file():
                body_md = md_path.read_text(encoding="utf-8")
            if meta_path.is_file():
                meta_text = meta_path.read_text(encoding="utf-8")
        item = _list_item(row)
        return ArticleDetail(
            **item.__dict__,
            source_url=row["source_url"] or "",
            published_at=row["published_at"] or "",
            body_html=body_html,
            body_md=body_md,
            plain_text=row["plain_text"] or "",
            archive_folder=folder,
            media_total=int(row["media_total"] or 0),
            media_missing=int(row["media_missing"] or 0),
            content_hash=row["content_hash"] or "",
            capture_mode=row["capture_mode"] or "URL_AUTO",
            failure_message=row["failure_message"] or "",
            category_id=None if row["category_id"] is None else int(row["category_id"]),
            category_name=row["category_name"] or "",
            metadata_json=meta_text,
        )

    def update_article(
        self,
        article_id: int,
        *,
        is_favorite: bool | None = None,
        category_id: int | None | object = ...,
    ) -> ArticleDetail | None:
        row = self._connection.execute("SELECT id FROM web_articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            return None
        if is_favorite is not None:
            self._connection.execute(
                "UPDATE web_articles SET is_favorite = ?, updated_at = ? WHERE id = ?",
                (1 if is_favorite else 0, _now(), article_id),
            )
        if category_id is not ...:
            self._connection.execute(
                "UPDATE web_articles SET category_id = ?, updated_at = ? WHERE id = ?",
                (category_id, _now(), article_id),
            )
        self._connection.commit()
        return self.get_article(article_id)

    def delete_article(self, article_id: int, *, remove_files: bool = True) -> bool:
        row = self._connection.execute(
            "SELECT archive_root, storage_relpath FROM web_articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if row is None:
            return False
        row_full = self._connection.execute(
            "SELECT title, author, plain_text FROM web_articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if row_full is not None:
            self._connection.execute(
                "INSERT INTO web_article_search(web_article_search, rowid, title, author, plain_text) "
                "VALUES('delete', ?, ?, ?, ?)",
                (
                    article_id,
                    row_full["title"] or "",
                    row_full["author"] or "",
                    row_full["plain_text"] or "",
                ),
            )
        self._connection.execute("DELETE FROM web_captures WHERE article_id = ?", (article_id,))
        self._connection.execute("DELETE FROM web_articles WHERE id = ?", (article_id,))
        self._connection.commit()
        if remove_files and row["archive_root"] and row["storage_relpath"]:
            folder = Path(row["archive_root"]) / row["storage_relpath"]
            # Safety: only delete under .../web/
            try:
                layout = WebPathLayout(row["archive_root"])
                resolved = folder.resolve()
                web_root = layout.web_root().resolve()
                if resolved.is_dir() and resolved.is_relative_to(web_root):
                    shutil.rmtree(resolved, ignore_errors=True)
            except OSError:
                pass
        return True

    def site_keys(self) -> list[dict]:
        rows = self._connection.execute(
            """
            SELECT site_key, site_label, COUNT(*) AS count
            FROM web_articles
            GROUP BY site_key, site_label
            ORDER BY count DESC
            """
        ).fetchall()
        return [
            {"siteKey": r["site_key"], "siteLabel": r["site_label"], "count": int(r["count"])}
            for r in rows
        ]


def _list_item(row: sqlite3.Row) -> ArticleListItem:
    plain = row["plain_text"] or ""
    summary = plain[:120].replace("\n", " ")
    return ArticleListItem(
        id=int(row["id"]),
        title=row["title"] or "",
        author=row["author"] or "",
        site_key=row["site_key"] or "",
        site_label=row["site_label"] or "",
        status=row["status"] or "",
        status_label=_STATUS_LABELS.get(row["status"], row["status"] or ""),
        captured_at=row["captured_at"] or "",
        is_favorite=bool(row["is_favorite"]),
        canonical_url=row["canonical_url"] or "",
        summary=summary,
    )


def _rewrite_media_to_file_urls(html: str, article_root: Path) -> str:
    media_dir = article_root / "media"
    if not media_dir.is_dir():
        return html

    def repl(match: re.Match[str]) -> str:
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:", "file:")):
            return match.group(0)
        path = (article_root / src).resolve()
        try:
            if path.is_file() and path.is_relative_to(article_root.resolve()):
                return f"{prefix}{path.as_uri()}{suffix}"
        except (OSError, ValueError):
            return match.group(0)
        return match.group(0)

    return re.sub(r'(src=")([^"]+)(")', repl, html)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
