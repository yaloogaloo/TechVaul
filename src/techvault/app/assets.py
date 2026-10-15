"""Per-asset archive state. Retry decisions use this, not a whole-repository rerun."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


class AssetKind:
    README = "README"
    SOURCE = "SOURCE"
    RELEASE = "RELEASE"


class AssetStatus:
    SAVED = "SAVED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    ABSENT = "ABSENT"


@dataclass(frozen=True)
class AssetRecord:
    kind: str
    status: str
    message: str
    retryable: bool


def load_asset_records(connection: sqlite3.Connection, repository_id: int) -> tuple[AssetRecord, ...]:
    repository = connection.execute(
        "SELECT current_snapshot_id, current_release_id FROM repositories WHERE id = ?",
        (repository_id,),
    ).fetchone()
    if repository is None:
        return ()
    return (
        _readme(connection, repository_id),
        _source(connection, repository_id, repository["current_snapshot_id"]),
        _release(connection, repository_id, repository["current_release_id"]),
    )


def _readme(connection: sqlite3.Connection, repository_id: int) -> AssetRecord:
    page = connection.execute(
        """
        SELECT status, failure_code, failure_message
        FROM offline_pages
        WHERE repository_id = ? AND page_type = 'REPOSITORY'
        """,
        (repository_id,),
    ).fetchone()
    if page is None:
        return AssetRecord(AssetKind.README, AssetStatus.ABSENT, "尚未保存", True)
    if page["failure_code"] == "README_UNAVAILABLE":
        return AssetRecord(AssetKind.README, AssetStatus.ABSENT, "这个仓库没有 README", False)
    if page["status"] == "FAILED":
        return AssetRecord(
            AssetKind.README,
            AssetStatus.FAILED,
            _public_message(page["failure_message"]),
            True,
        )
    if page["status"] == "COMPLETED":
        return AssetRecord(AssetKind.README, AssetStatus.SAVED, "已保存", False)
    return AssetRecord(AssetKind.README, AssetStatus.ABSENT, "尚未保存", True)


def _source(connection: sqlite3.Connection, repository_id: int, current_snapshot_id: int | None) -> AssetRecord:
    if current_snapshot_id is not None:
        return AssetRecord(AssetKind.SOURCE, AssetStatus.SAVED, "已保存", False)
    job = _latest_job(connection, repository_id, "SOURCE")
    if job is not None and job["status"] == "FAILED":
        return AssetRecord(AssetKind.SOURCE, AssetStatus.FAILED, _public_message(job["failure_message"]), True)
    if job is not None and job["status"] == "PARTIAL":
        return AssetRecord(AssetKind.SOURCE, AssetStatus.PARTIAL, _public_message(job["failure_message"]), True)
    return AssetRecord(AssetKind.SOURCE, AssetStatus.ABSENT, "尚未保存", True)


def _release(connection: sqlite3.Connection, repository_id: int, current_release_id: int | None) -> AssetRecord:
    if current_release_id is not None:
        return AssetRecord(AssetKind.RELEASE, AssetStatus.SAVED, "已保存", False)
    stable = connection.execute(
        """
        SELECT id FROM releases
        WHERE repository_id = ? AND lookup_outcome = 'STABLE'
        ORDER BY id DESC LIMIT 1
        """,
        (repository_id,),
    ).fetchone()
    if stable is None:
        return AssetRecord(AssetKind.RELEASE, AssetStatus.ABSENT, "暂无正式 Release", False)
    job = _latest_job(connection, repository_id, "RELEASE")
    if job is not None and job["status"] == "PARTIAL":
        return AssetRecord(AssetKind.RELEASE, AssetStatus.PARTIAL, _public_message(job["failure_message"]), True)
    if job is not None and job["status"] == "FAILED":
        return AssetRecord(AssetKind.RELEASE, AssetStatus.FAILED, _public_message(job["failure_message"]), True)
    return AssetRecord(AssetKind.RELEASE, AssetStatus.ABSENT, "尚未保存", True)


def _latest_job(connection: sqlite3.Connection, repository_id: int, kind: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT status, failure_message FROM jobs
        WHERE repository_id = ? AND archive_kind = ?
        ORDER BY id DESC LIMIT 1
        """,
        (repository_id, kind),
    ).fetchone()


def _public_message(message: str | None) -> str:
    text = (message or "").strip()
    if not text:
        return "保存没有完成。"
    lowered = text.replace("\\", "/").lower()
    if (
        ":/" in text
        or lowered.startswith("/")
        or "repositories/" in lowered
        or "github/" in lowered
        or len(text) > 180
    ):
        return "保存没有完成。"
    return text
