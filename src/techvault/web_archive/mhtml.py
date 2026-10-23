"""MHTML snapshot validation and atomic attach to an existing reading archive.

Does not fetch pages via httpx. Callers supply a completed staging file from WebEngine.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from techvault.fsutil import atomic_replace
from techvault.web_archive.paths import WebPathLayout

# Snapshot status (independent from reading-edition ArchiveStatus).
SNAPSHOT_COMPLETE = "COMPLETE"
SNAPSHOT_FAILED = "FAILED"
SNAPSHOT_NOT_CAPTURED = "NOT_CAPTURED"
SNAPSHOT_CANCELLED = "CANCELLED"
SNAPSHOT_SAVING = "SAVING"

_MIN_BYTES = 256
_MHTML_MARKERS = (
    b"multipart/related",
    b"Content-Type: multipart/related",
    b"MIME-Version:",
    b"Content-Location:",
    b"Snapshot-Content-Location:",
)


@dataclass
class MhtmlValidation:
    ok: bool
    message: str
    size: int = 0
    sha256: str | None = None
    looks_like_mhtml: bool = False


@dataclass
class MhtmlAttachResult:
    ok: bool
    status: str
    message: str
    relative_path: str | None = None
    size: int = 0
    sha256: str | None = None
    path: str | None = None


def validate_mhtml_file(path: Path | str) -> MhtmlValidation:
    target = Path(path)
    if not target.is_file():
        return MhtmlValidation(False, "快照文件不存在")
    try:
        size = target.stat().st_size
    except OSError:
        return MhtmlValidation(False, "无法读取快照文件大小")
    if size < _MIN_BYTES:
        return MhtmlValidation(False, f"快照文件过小（{size} 字节）")
    try:
        head = target.read_bytes()[:8192]
    except OSError as exc:
        return MhtmlValidation(False, f"无法读取快照内容：{type(exc).__name__}")
    lowered = head.lower()
    looks = any(marker.lower() in lowered for marker in _MHTML_MARKERS)
    # Some Chromium builds emit .mhtml that starts with "From: <Saved by...>"
    if not looks and (b"from:" in lowered[:200] or b"<html" in lowered):
        looks = True
    if not looks:
        return MhtmlValidation(False, "文件内容不像有效的 MHTML 快照", size=size)
    digest = hashlib.sha256()
    try:
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return MhtmlValidation(False, f"无法计算快照校验：{type(exc).__name__}", size=size)
    return MhtmlValidation(
        True,
        "ok",
        size=size,
        sha256=digest.hexdigest(),
        looks_like_mhtml=True,
    )


def attach_mhtml_snapshot(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    staging_path: Path | str,
    failure_message: str | None = None,
) -> MhtmlAttachResult:
    """Publish a validated staging MHTML into the article folder and update DB.

    On failure, leaves any previous successful snapshot.mhtml and DB pointers intact.
    """
    row = connection.execute(
        "SELECT * FROM web_articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return MhtmlAttachResult(False, SNAPSHOT_FAILED, "文章不存在")
    if not row["archive_root"] or not row["storage_relpath"]:
        return MhtmlAttachResult(False, SNAPSHOT_FAILED, "文章尚未写入本地归档目录")

    layout = WebPathLayout(row["archive_root"])
    article_root = layout.resolve_relpath(row["storage_relpath"])
    storage_rel = str(row["storage_relpath"]).replace("\\", "/")
    relative_snapshot = f"{storage_rel}/snapshot.mhtml"
    final_path = article_root / "snapshot.mhtml"
    part_path = article_root / "snapshot.mhtml.part"

    if failure_message:
        _record_snapshot_failure(connection, article_id, failure_message, preserve_previous=True)
        return MhtmlAttachResult(False, SNAPSHOT_FAILED, failure_message)

    staging = Path(staging_path)
    validation = validate_mhtml_file(staging)
    if not validation.ok:
        _record_snapshot_failure(
            connection, article_id, validation.message, preserve_previous=True
        )
        return MhtmlAttachResult(False, SNAPSHOT_FAILED, validation.message, size=validation.size)

    try:
        article_root.mkdir(parents=True, exist_ok=True)
        if part_path.exists():
            part_path.unlink()
        shutil.copy2(staging, part_path)
        part_check = validate_mhtml_file(part_path)
        if not part_check.ok:
            part_path.unlink(missing_ok=True)
            _record_snapshot_failure(
                connection, article_id, part_check.message, preserve_previous=True
            )
            return MhtmlAttachResult(False, SNAPSHOT_FAILED, part_check.message)
        atomic_replace(part_path, final_path)
    except OSError as exc:
        try:
            part_path.unlink(missing_ok=True)
        except OSError:
            pass
        msg = f"快照写入失败：{type(exc).__name__}"
        _record_snapshot_failure(connection, article_id, msg, preserve_previous=True)
        return MhtmlAttachResult(False, SNAPSHOT_FAILED, msg)

    captured_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        UPDATE web_articles SET
            snapshot_status = ?,
            relative_snapshot = ?,
            snapshot_bytes = ?,
            snapshot_sha256 = ?,
            snapshot_captured_at = ?,
            snapshot_failure = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (
            SNAPSHOT_COMPLETE,
            relative_snapshot,
            validation.size,
            validation.sha256,
            captured_at,
            captured_at,
            article_id,
        ),
    )
    # Update latest capture row for this article.
    cap = connection.execute(
        "SELECT id FROM web_captures WHERE article_id = ? ORDER BY version DESC LIMIT 1",
        (article_id,),
    ).fetchone()
    if cap is not None:
        connection.execute(
            """
            UPDATE web_captures SET
                snapshot_status = ?,
                relative_snapshot = ?,
                snapshot_bytes = ?,
                snapshot_sha256 = ?,
                snapshot_failure = NULL
            WHERE id = ?
            """,
            (
                SNAPSHOT_COMPLETE,
                relative_snapshot,
                validation.size,
                validation.sha256,
                int(cap["id"]),
            ),
        )
    _patch_metadata_json(
        article_root,
        {
            "snapshot_status": SNAPSHOT_COMPLETE,
            "snapshot_bytes": validation.size,
            "snapshot_sha256": validation.sha256,
            "snapshot_captured_at": captured_at,
            "relative_snapshot": "snapshot.mhtml",
        },
    )
    connection.commit()
    return MhtmlAttachResult(
        True,
        SNAPSHOT_COMPLETE,
        "网页快照已保存",
        relative_path=relative_snapshot,
        size=validation.size,
        sha256=validation.sha256,
        path=str(final_path),
    )


def mark_snapshot_failed(
    connection: sqlite3.Connection,
    article_id: int,
    message: str,
) -> None:
    _record_snapshot_failure(connection, article_id, message, preserve_previous=True)
    connection.commit()


def _record_snapshot_failure(
    connection: sqlite3.Connection,
    article_id: int,
    message: str,
    *,
    preserve_previous: bool,
) -> None:
    row = connection.execute(
        "SELECT snapshot_status, relative_snapshot FROM web_articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    if preserve_previous and row["snapshot_status"] == SNAPSHOT_COMPLETE and row["relative_snapshot"]:
        # Keep pointers to the last good snapshot; only record failure on latest capture.
        connection.execute(
            "UPDATE web_articles SET snapshot_failure = ?, updated_at = ? WHERE id = ?",
            (message, now, article_id),
        )
    else:
        connection.execute(
            """
            UPDATE web_articles SET
                snapshot_status = ?,
                snapshot_failure = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (SNAPSHOT_FAILED, message, now, article_id),
        )
    cap = connection.execute(
        "SELECT id FROM web_captures WHERE article_id = ? ORDER BY version DESC LIMIT 1",
        (article_id,),
    ).fetchone()
    if cap is not None:
        connection.execute(
            """
            UPDATE web_captures SET
                snapshot_status = ?,
                snapshot_failure = ?
            WHERE id = ?
            """,
            (SNAPSHOT_FAILED, message, int(cap["id"])),
        )


def _patch_metadata_json(article_root: Path, fields: dict) -> None:
    meta_path = article_root / "metadata.json"
    data: dict = {}
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data.update(fields)
    try:
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
