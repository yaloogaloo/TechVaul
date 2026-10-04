import sqlite3
from pathlib import Path

import pytest

from techvault.db.connection import connect, foreign_keys_enabled
from techvault.db.migrate import apply_migrations, default_migrations_dir
from techvault.errors import DatabaseError, ErrorCode


def test_foreign_keys_enabled_on_new_connection(tmp_path: Path) -> None:
    connection = connect(tmp_path / "data" / "techvault.sqlite")
    try:
        assert foreign_keys_enabled(connection) is True
        row = connection.execute("PRAGMA foreign_keys").fetchone()
        assert int(row[0]) == 1
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        assert str(mode[0]).lower() == "wal"
    finally:
        connection.close()


def test_migration_applies_once(tmp_path: Path) -> None:
    connection = connect(tmp_path / "techvault.sqlite")
    try:
        first = apply_migrations(connection, default_migrations_dir())
        second = apply_migrations(connection, default_migrations_dir())
        assert first == [1, 2, 3, 4, 5, 6]
        assert second == []
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        assert versions == [1, 2, 3, 4, 5, 6]
    finally:
        connection.close()


def test_migration_failure_rolls_back(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    source = (default_migrations_dir() / "001_initial.sql").read_text(encoding="utf-8")
    (migrations / "001_initial.sql").write_text(source, encoding="utf-8")
    (migrations / "002_bad.sql").write_text(
        "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table (id) VALUES (1);\n",
        encoding="utf-8",
    )
    connection = connect(tmp_path / "techvault.sqlite")
    try:
        with pytest.raises(DatabaseError) as caught:
            apply_migrations(connection, migrations)
        assert caught.value.code == ErrorCode.DATABASE
        versions = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        assert versions == [1]
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'should_rollback'"
        ).fetchone()
        assert row is None
    finally:
        connection.close()


def test_foreign_key_violation_is_rejected(tmp_path: Path) -> None:
    connection = connect(tmp_path / "techvault.sqlite")
    try:
        apply_migrations(connection, default_migrations_dir())
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO releases (
                    github_release_id, repository_id, tag_name, html_url,
                    lookup_outcome, created_at_local, updated_at_local
                ) VALUES (1, 999999, 'v1', 'https://github.com/octo/hello/releases/tag/v1',
                          'STABLE', '2026-09-30T00:00:00+00:00', '2026-09-30T00:00:00+00:00')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO repositories (
                    github_repo_id, owner, name, full_name, html_url,
                    current_release_id, created_at, updated_at
                ) VALUES (
                    10, 'octo', 'hello', 'octo/hello', 'https://github.com/octo/hello',
                    999999, '2026-09-30T00:00:00+00:00', '2026-09-30T00:00:00+00:00'
                )
                """
            )
    finally:
        connection.close()


def test_pending_release_id_is_absent(tmp_path: Path) -> None:
    sql = (default_migrations_dir() / "001_initial.sql").read_text(encoding="utf-8")
    assert "pending_release_id" not in sql
    connection = connect(tmp_path / "techvault.sqlite")
    try:
        apply_migrations(connection, default_migrations_dir())
        columns = [row[1] for row in connection.execute("PRAGMA table_info(repositories)")]
        assert "current_release_id" in columns
        assert "pending_release_id" not in columns
        job_columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)")]
        assert "target_release_id" in job_columns
    finally:
        connection.close()


def test_release_file_digest_columns(tmp_path: Path) -> None:
    connection = connect(tmp_path / "techvault.sqlite")
    try:
        apply_migrations(connection, default_migrations_dir())
        connection.execute(
            """
            INSERT INTO repositories (
                github_repo_id, owner, name, full_name, html_url, created_at, updated_at
            ) VALUES (10, 'octo', 'hello', 'octo/hello', 'https://github.com/octo/hello', 't', 't')
            """
        )
        connection.execute(
            """
            INSERT INTO releases (
                github_release_id, repository_id, tag_name, html_url,
                lookup_outcome, created_at_local, updated_at_local
            ) VALUES (5, 1, 'v1', 'https://github.com/octo/hello/releases/tag/v1',
                      'STABLE', 't', 't')
            """
        )
        digest = "a" * 64
        connection.execute(
            """
            INSERT INTO release_files (
                release_id, kind, name, upstream_digest, local_sha256, state, created_at, updated_at
            ) VALUES (1, 'asset', 'app.exe', 'sha256:f4bc', ?, 'COMPLETED', 't', 't')
            """,
            (digest,),
        )
        stored = connection.execute(
            "SELECT upstream_digest, local_sha256 FROM release_files"
        ).fetchone()
        assert stored["upstream_digest"] == "sha256:f4bc"
        assert stored["local_sha256"] == digest
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO release_files (
                    release_id, kind, github_asset_id, name, local_sha256, state, created_at, updated_at
                ) VALUES (1, 'asset', 9, 'other.bin', 'sha256:abcd', 'COMPLETED', 't', 't')
                """
            )
    finally:
        connection.close()


def test_schema_is_not_created_from_business_code() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS" not in text
        relative = path.relative_to(source_root)
        if relative.parts[:1] == ("techvault",) and relative.parts[1:2] == ("ui",):
            continue
        if relative == Path("techvault") / "__main__.py":
            continue
        assert "PySide6" not in text
