"""Content-addressed README image pool and its SQLite index.

One index lives beside the objects. Repository deletion does not remove objects.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import httpx

from techvault.fsutil import atomic_replace
from techvault.readme_images.fetch import fetch_image
from techvault.readme_images.html_images import rewrite_cached_images
from techvault.readme_images.paths import media_url
from techvault.readme_images.sniff import MAX_IMAGE_BYTES
from techvault.readme_images.urls import canonical_url

FRESH_SECONDS = 24 * 60 * 60
_WORKERS = 4

_SCHEMA = (Path(__file__).with_name("cache_index.sql")).read_text(encoding="utf-8")


@dataclass
class ImageRecord:
    canonical_url: str
    status: str
    sha256: str | None = None
    extension: str | None = None
    mime: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    reused: bool = False

    @property
    def media(self) -> str | None:
        if self.status == "OK" and self.sha256 and self.extension:
            return media_url(self.sha256, self.extension)
        return None


class _Flight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.record: ImageRecord | None = None


class ImagePool:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.tmp = self.root / "tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._flights: dict[str, _Flight] = {}
        self._flight_lock = threading.Lock()
        self._conn = sqlite3.connect(self.root / "cache_index.sqlite", isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def sync(
        self,
        urls: list[str],
        *,
        client: httpx.Client,
        revalidate: bool = True,
        fresh_seconds: int = FRESH_SECONDS,
        cancel_event: Event | None = None,
        on_progress=None,
    ) -> list[ImageRecord]:
        ordered = list(dict.fromkeys(urls))
        if not ordered:
            return []
        records: list[ImageRecord | None] = [None] * len(ordered)
        workers = max(1, min(_WORKERS, len(ordered)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.ensure,
                    url,
                    client=client,
                    revalidate=revalidate,
                    fresh_seconds=fresh_seconds,
                    cancel_event=cancel_event,
                ): index
                for index, url in enumerate(ordered)
            }
            done = 0
            for future in as_completed(futures):
                index = futures[future]
                records[index] = future.result()
                done += 1
                if on_progress is not None:
                    on_progress(done, len(ordered))
        return [item for item in records if item is not None]

    def ensure(
        self,
        url: str,
        *,
        client: httpx.Client,
        revalidate: bool = True,
        fresh_seconds: int = FRESH_SECONDS,
        cancel_event: Event | None = None,
    ) -> ImageRecord:
        canonical = canonical_url(url)
        with self._flight_lock:
            flight = self._flights.get(canonical)
            owner = flight is None
            if flight is None:
                flight = _Flight()
                self._flights[canonical] = flight
        if not owner:
            flight.event.wait(timeout=90)
            if flight.record is not None:
                return flight.record
            return ImageRecord(canonical, "FAILED", failure_code="NETWORK", failure_message="图片下载失败")
        try:
            flight.record = self._ensure_owner(
                canonical,
                client=client,
                revalidate=revalidate,
                fresh_seconds=fresh_seconds,
                cancel_event=cancel_event,
            )
            return flight.record
        except Exception:
            flight.record = ImageRecord(
                canonical,
                "FAILED",
                failure_code="NETWORK",
                failure_message="图片下载失败",
            )
            return flight.record
        finally:
            flight.event.set()
            with self._flight_lock:
                self._flights.pop(canonical, None)

    def cache_state(self, url: str) -> tuple[str, str]:
        """Return ``(media|failed|pending, media URL or failure code)`` without downloading."""
        canonical = canonical_url(url)
        row = self._url_row(canonical)
        if row is None:
            return "pending", ""
        if row["status"] == "OK" and row["sha256"] and self._object_usable(row["sha256"]):
            obj = self._object_row(row["sha256"])
            if obj is not None:
                return "media", media_url(row["sha256"], obj["extension"])
        if row["status"] == "OK":
            self._mark_url_failed(canonical, "MISSING", "缓存文件不可用")
            return "failed", "MISSING"
        return "failed", row["failure_code"] or "FAILED"

    def rewrite_html(self, html: str) -> str:
        return rewrite_cached_images(html, self.cache_state)

    def replace_refs(
        self,
        repository_id: int,
        github_repo_id: int | None,
        records: list[ImageRecord],
        *,
        readme_relpath: str = "README.md",
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM image_refs WHERE repository_id = ?", (repository_id,))
                for record in records:
                    self._conn.execute(
                        """
                        INSERT INTO image_refs (
                            repository_id, github_repo_id, canonical_url, sha256, readme_relpath, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            repository_id,
                            github_repo_id,
                            record.canonical_url,
                            record.sha256,
                            readme_relpath,
                            now,
                        ),
                    )
                self._conn.execute("COMMIT")
            except sqlite3.Error:
                self._conn.execute("ROLLBACK")
                raise

    def summary_for_repository(self, repository_id: int) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT urls.status AS status, COUNT(*) AS n
            FROM image_refs
            LEFT JOIN urls ON urls.canonical_url = image_refs.canonical_url
            WHERE image_refs.repository_id = ?
            GROUP BY urls.status
            """,
            (repository_id,),
        ).fetchall()
        cached = failed = pending = 0
        for row in rows:
            count = int(row["n"])
            if row["status"] == "OK":
                cached += count
            elif row["status"] is None:
                pending += count
            else:
                failed += count
        return {
            "total": cached + failed + pending,
            "cached": cached,
            "failed": failed,
            "pending": pending,
        }

    def stats(self) -> dict[str, int]:
        objects = self._conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes FROM objects WHERE invalidated = 0"
        ).fetchone()
        refs = self._conn.execute("SELECT COUNT(*) AS n FROM image_refs").fetchone()
        hits = self._conn.execute(
            "SELECT value FROM pool_stats WHERE key = 'duplicate_hits'"
        ).fetchone()
        return {
            "objects": int(objects["n"]),
            "bytes": int(objects["bytes"]),
            "references": int(refs["n"]),
            "duplicateHits": 0 if hits is None else int(hits["value"]),
        }

    def _ensure_owner(
        self,
        canonical: str,
        *,
        client: httpx.Client,
        revalidate: bool,
        fresh_seconds: int,
        cancel_event: Event | None,
    ) -> ImageRecord:
        current = self._record_from_row(canonical)
        usable = current is not None and current.status == "OK" and self._object_usable(current.sha256 or "")
        if usable and current is not None and (not revalidate or _fresh(self._url_row(canonical), fresh_seconds)):
            current.reused = True
            return current
        if cancel_event is not None and cancel_event.is_set():
            if usable and current is not None:
                current.reused = True
                return current
            return ImageRecord(canonical, "FAILED", failure_code="CANCELLED", failure_message="已取消")
        row = self._url_row(canonical)
        fetched = fetch_image(
            client,
            canonical,
            etag=None if row is None else row["etag"],
            last_modified=None if row is None else row["last_modified"],
            cancel_event=cancel_event,
        )
        if fetched.not_modified:
            if usable and current is not None:
                self._touch(canonical, fetched.etag, fetched.last_modified)
                current.reused = True
                return current
            fetched = fetch_image(client, canonical, cancel_event=cancel_event)
        if not fetched.ok or fetched.data is None or fetched.extension is None or fetched.mime is None:
            if usable and current is not None and fetched.failure_code in {"NETWORK", "TIMEOUT", "CANCELLED"}:
                current.reused = True
                return current
            self._save_failure(canonical, fetched.failure_code or "NETWORK", fetched.failure_message or "图片下载失败")
            return ImageRecord(
                canonical,
                "FAILED",
                failure_code=fetched.failure_code,
                failure_message=fetched.failure_message,
            )
        return self._store(
            canonical,
            fetched.data,
            fetched.extension,
            fetched.mime,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
        )

    def _store(
        self,
        canonical: str,
        data: bytes,
        extension: str,
        mime: str,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> ImageRecord:
        sha = hashlib.sha256(data).hexdigest()
        destination = self.objects / sha[:2] / f"{sha}.{extension}"
        with self._lock:
            previous = self._url_row(canonical)
            previous_sha = None if previous is None else previous["sha256"]
            existed = self._object_row(sha) is not None and self._object_usable(sha)
            if not existed:
                self._publish_file(destination, data)
                now = _now()
                self._conn.execute(
                    """
                    INSERT INTO objects (sha256, extension, mime, size_bytes, created_at, invalidated)
                    VALUES (?, ?, ?, ?, ?, 0)
                    ON CONFLICT(sha256) DO UPDATE SET
                        extension = excluded.extension,
                        mime = excluded.mime,
                        size_bytes = excluded.size_bytes,
                        invalidated = 0
                    """,
                    (sha, extension, mime, len(data), now),
                )
            elif not destination.is_file():
                self._publish_file(destination, data)
            reuse_hit = 1 if existed and previous_sha != sha else 0
            if reuse_hit:
                self._bump_duplicate()
            now = _now()
            self._conn.execute(
                """
                INSERT INTO urls (
                    canonical_url, sha256, etag, last_modified, verified_at, status,
                    failure_code, failure_message, reuse_hit
                ) VALUES (?, ?, ?, ?, ?, 'OK', NULL, NULL, ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    sha256 = excluded.sha256,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    verified_at = excluded.verified_at,
                    status = 'OK',
                    failure_code = NULL,
                    failure_message = NULL,
                    reuse_hit = CASE WHEN excluded.reuse_hit = 1 THEN 1 ELSE urls.reuse_hit END
                """,
                (canonical, sha, etag, last_modified, now, reuse_hit),
            )
        return ImageRecord(
            canonical,
            "OK",
            sha256=sha,
            extension=extension,
            mime=mime,
            reused=bool(existed),
        )

    def _publish_file(self, destination: Path, data: bytes) -> None:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds size limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = self.tmp / f"{uuid.uuid4().hex}.part"
        with part.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(part, destination)

    def _record_from_row(self, canonical: str) -> ImageRecord | None:
        row = self._url_row(canonical)
        if row is None:
            return None
        extension = None
        mime = None
        if row["sha256"]:
            obj = self._object_row(row["sha256"])
            if obj is not None:
                extension = obj["extension"]
                mime = obj["mime"]
        return ImageRecord(
            canonical,
            row["status"],
            sha256=row["sha256"],
            extension=extension,
            mime=mime,
            failure_code=row["failure_code"],
            failure_message=row["failure_message"],
        )

    def _object_usable(self, sha256: str) -> bool:
        obj = self._object_row(sha256)
        if obj is None or int(obj["invalidated"]):
            return False
        path = self.objects / sha256[:2] / f"{sha256}.{obj['extension']}"
        if not path.is_file():
            return False
        try:
            return path.stat().st_size == int(obj["size_bytes"])
        except OSError:
            return False

    def _url_row(self, canonical: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM urls WHERE canonical_url = ?", (canonical,)).fetchone()

    def _object_row(self, sha256: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM objects WHERE sha256 = ?", (sha256,)).fetchone()

    def _touch(self, canonical: str, etag: str | None, last_modified: str | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE urls
                SET verified_at = ?, etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified)
                WHERE canonical_url = ?
                """,
                (_now(), etag, last_modified, canonical),
            )

    def _save_failure(self, canonical: str, code: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO urls (
                    canonical_url, sha256, etag, last_modified, verified_at, status,
                    failure_code, failure_message, reuse_hit
                ) VALUES (?, NULL, NULL, NULL, ?, 'FAILED', ?, ?, 0)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    sha256 = NULL,
                    verified_at = excluded.verified_at,
                    status = 'FAILED',
                    failure_code = excluded.failure_code,
                    failure_message = excluded.failure_message
                """,
                (canonical, _now(), code, message[:180]),
            )

    def _mark_url_failed(self, canonical: str, code: str, message: str) -> None:
        self._save_failure(canonical, code, message)

    def _bump_duplicate(self) -> None:
        self._conn.execute(
            """
            INSERT INTO pool_stats (key, value) VALUES ('duplicate_hits', 1)
            ON CONFLICT(key) DO UPDATE SET value = value + 1
            """
        )


def invalidate_object(root: Path, sha256: str) -> None:
    index = Path(root) / "cache_index.sqlite"
    if not index.is_file():
        return
    connection = sqlite3.connect(index, isolation_level=None)
    try:
        connection.execute("UPDATE objects SET invalidated = 1 WHERE sha256 = ?", (sha256,))
        connection.execute(
            """
            UPDATE urls
            SET status = 'FAILED', failure_code = 'INVALID', failure_message = '缓存文件与哈希不一致',
                verified_at = ?
            WHERE sha256 = ?
            """,
            (_now(), sha256),
        )
    finally:
        connection.close()


def _fresh(row: sqlite3.Row | None, fresh_seconds: int) -> bool:
    if row is None or not row["verified_at"] or fresh_seconds <= 0:
        return False
    try:
        seen = datetime.fromisoformat(row["verified_at"])
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - seen < timedelta(seconds=fresh_seconds)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
