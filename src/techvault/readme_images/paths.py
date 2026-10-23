"""Resolve the shared README image pool without hard-coding a machine path."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

POOL_DIRNAME = "github_readme_images"
MEDIA_URL_PREFIX = "techvault://app/media/"


def media_url(sha256: str, extension: str) -> str:
    return f"{MEDIA_URL_PREFIX}{sha256}.{extension}"


def resolve_readme_image_root(database_path: Path | None = None) -> Path:
    """Image pool directory for this process.

    ``TECHVAULT_README_IMAGE_ROOT`` wins. Otherwise the pool sits next to the
    SQLite database, which is the app data directory (repository ``data/`` in a
    source checkout, the per-user TechVault directory when installed).
    """
    override = os.environ.get("TECHVAULT_README_IMAGE_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    if database_path is not None:
        return Path(database_path).resolve().parent / POOL_DIRNAME
    from techvault.app.bootstrap import default_data_dir

    return default_data_dir() / POOL_DIRNAME


def connection_database_path(connection: sqlite3.Connection) -> Path | None:
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None:
        return None
    file_name = row["file"] if isinstance(row, sqlite3.Row) else row[2]
    if not file_name:
        return None
    return Path(file_name)


def image_root_for_connection(connection: sqlite3.Connection) -> Path:
    return resolve_readme_image_root(connection_database_path(connection))
