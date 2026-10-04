"""Archive-root settings and GitHub token status. The token never enters SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from techvault.archive.paths import PathLayout
from techvault.errors import DatabaseError, KeyringUnavailableError, UnsafePathError
from techvault.security.token_store import delete_token, has_token, save_token

ARCHIVE_ROOT_KEY = "archive_root"
TOKEN_CONFIGURED = "Configured"
TOKEN_NOT_CONFIGURED = "Not configured"
TOKEN_UNAVAILABLE = "Unavailable"


class SettingsService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_archive_root(self) -> Path | None:
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?",
            (ARCHIVE_ROOT_KEY,),
        ).fetchone()
        if row is None or not str(row["value"]).strip():
            return None
        return PathLayout(row["value"]).archive_root

    def set_archive_root(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise UnsafePathError("Archive root is empty")
        root = PathLayout(Path(path).expanduser()).archive_root
        root.mkdir(parents=True, exist_ok=True)
        stored = str(root)
        try:
            self._connection.execute("BEGIN")
            self._connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (ARCHIVE_ROOT_KEY, stored),
            )
            self._connection.execute("COMMIT")
        except sqlite3.Error as exc:
            self._connection.execute("ROLLBACK")
            raise DatabaseError("Archive root could not be saved") from exc
        return root

    def token_status(self) -> str:
        try:
            if has_token():
                return TOKEN_CONFIGURED
            return TOKEN_NOT_CONFIGURED
        except KeyringUnavailableError:
            return TOKEN_UNAVAILABLE

    def save_github_token(self, token: str) -> str:
        save_token(token)
        return self.token_status()

    def clear_github_token(self) -> str:
        delete_token()
        return self.token_status()
