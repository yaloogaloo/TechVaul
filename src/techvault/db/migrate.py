"""Ordered SQL migrations. Each file runs in its own transaction."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from techvault.errors import DatabaseError
from techvault.logging_setup import get_logger

logger = get_logger()


def default_migrations_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "migrations"


def apply_migrations(
    connection: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> list[int]:
    if not _foreign_keys_on(connection):
        raise DatabaseError("SQLite foreign keys are disabled on this connection")

    directory = migrations_dir if migrations_dir is not None else default_migrations_dir()
    applied: list[int] = []
    for version, path in _discover(directory):
        if version in _applied_versions(connection):
            continue
        script = path.read_text(encoding="utf-8")
        try:
            connection.execute("BEGIN")
            for statement in _split_sql(script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            connection.execute("COMMIT")
        except Exception as exc:
            _rollback(connection)
            logger.error("Migration failed code=DATABASE version=%s", version)
            raise DatabaseError(f"Migration {version} failed") from exc
        applied.append(version)
        logger.info("Migration applied version=%s", version)
    _install_pointer_guards(connection)
    return applied


def _install_pointer_guards(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'repositories'"
    ).fetchone()
    if row is None:
        return
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS repositories_snapshot_same_repo_update
        BEFORE UPDATE OF current_snapshot_id ON repositories
        WHEN NEW.current_snapshot_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1 FROM source_snapshots
            WHERE source_snapshots.id = NEW.current_snapshot_id
              AND source_snapshots.repository_id = NEW.id
         )
        BEGIN
            SELECT RAISE(ABORT, 'current_snapshot_id belongs to another repository');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS repositories_release_same_repo_update
        BEFORE UPDATE OF current_release_id ON repositories
        WHEN NEW.current_release_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1 FROM releases
            WHERE releases.id = NEW.current_release_id
              AND releases.repository_id = NEW.id
         )
        BEGIN
            SELECT RAISE(ABORT, 'current_release_id belongs to another repository');
        END
        """
    )


def _foreign_keys_on(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA foreign_keys").fetchone()
    return row is not None and int(row[0]) == 1


def _applied_versions(connection: sqlite3.Connection) -> set[int]:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if row is None:
        return set()
    return {
        int(item[0])
        for item in connection.execute("SELECT version FROM schema_migrations")
    }


def _discover(directory: Path) -> list[tuple[int, Path]]:
    if not directory.is_dir():
        raise DatabaseError(f"Migrations directory does not exist: {directory}")
    found: list[tuple[int, Path]] = []
    seen: set[int] = set()
    for path in directory.iterdir():
        if path.suffix.lower() != ".sql":
            continue
        version_text = path.name.split("_", 1)[0]
        if not version_text.isdigit():
            raise DatabaseError(f"Migration file name must start with a number: {path.name}")
        version = int(version_text)
        if version in seen:
            raise DatabaseError(f"Duplicate migration version: {version}")
        seen.add(version)
        found.append((version, path))
    found.sort(key=lambda item: item[0])
    return found


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        logger.error("Migration rollback failed code=DATABASE")


def _split_sql(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    index = 0
    length = len(script)
    while index < length:
        char = script[index]
        if in_string:
            current.append(char)
            if char == "'":
                if index + 1 < length and script[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            current.append(char)
            index += 1
            continue
        if char == "-" and index + 1 < length and script[index + 1] == "-":
            index += 2
            while index < length and script[index] != "\n":
                index += 1
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements
