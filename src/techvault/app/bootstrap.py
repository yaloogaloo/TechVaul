"""Open the database, migrate it, and prepare application services."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from techvault.app.recovery import recover_interrupted_archives
from techvault.app.settings import SettingsService
from techvault.db.connection import connect
from techvault.db.migrate import apply_migrations, default_migrations_dir
from techvault.documents.service import DocumentService
from techvault.errors import DatabaseError

DATABASE_INIT_FAILED = "Database initialization failed"

DEFAULT_CATEGORIES = (
    "Command",
    "AI Prompt",
    "Development",
    "Operation",
    "Other",
)


@dataclass
class AppContext:
    connection: sqlite3.Connection
    database_path: Path
    documents: DocumentService
    settings: SettingsService

    def close(self) -> None:
        self.connection.close()


def default_database_path() -> Path:
    return _app_dir() / "techvault.sqlite"


def default_archive_root() -> Path:
    return _app_dir() / "archive"


def prepare_context(
    database: Path,
    archive_root: Path | None = None,
    migrations_dir: Path | None = None,
) -> AppContext:
    connection = connect(database)
    try:
        apply_migrations(connection, migrations_dir or default_migrations_dir())
        settings = SettingsService(connection)
        if archive_root is not None:
            settings.set_archive_root(str(archive_root))
        elif settings.get_archive_root() is None:
            settings.set_archive_root(str(default_archive_root()))
        documents = DocumentService(connection)
        ensure_default_categories(documents)
        recover_interrupted_archives(connection)
    except Exception:
        connection.close()
        raise
    return AppContext(connection, Path(database), documents, settings)


def open_window_context(
    database: Path,
    archive_root: Path | None = None,
    migrations_dir: Path | None = None,
) -> tuple[AppContext | None, str | None]:
    try:
        return prepare_context(database, archive_root, migrations_dir), None
    except (DatabaseError, OSError, sqlite3.Error):
        return None, DATABASE_INIT_FAILED


def ensure_default_categories(service: DocumentService) -> None:
    existing = {item.name.casefold() for item in service.list_categories()}
    for index, name in enumerate(DEFAULT_CATEGORIES):
        if name.casefold() not in existing:
            service.create_category(name, index)


def _app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home()
    return root / "TechVault"
