"""SQLite connection factory. Every connection enables foreign keys and WAL."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(database: Path | str) -> sqlite3.Connection:
    path = Path(database)
    if path.parent != Path(path.anchor):
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def foreign_keys_enabled(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA foreign_keys").fetchone()
    return row is not None and int(row[0]) == 1
