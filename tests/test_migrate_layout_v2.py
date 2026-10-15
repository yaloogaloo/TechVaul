"""GitHub Archive Layout V1 → V2 migration (isolated temp trees only)."""

from __future__ import annotations

from pathlib import Path

from techvault.app.bootstrap import prepare_context
from techvault.archive.migrate_layout_v2 import apply_migration, cleanup_v1, plan_migration
from techvault.archive.paths import PathLayout
from techvault.archive.storage import repository_dir_for


def _seed_v1(tmp_path: Path) -> tuple:
    archive = tmp_path / "archive"
    context = prepare_context(tmp_path / "db.sqlite", archive)
    connection = context.connection
    connection.execute(
        """
        INSERT INTO repositories (
            github_repo_id, owner, name, full_name, html_url, default_branch,
            created_at, updated_at
        ) VALUES (135, 'eternity4719', 'HowToLiveBetter', 'eternity4719/HowToLiveBetter',
                  'https://github.com/eternity4719/HowToLiveBetter', 'main', 't', 't')
        """
    )
    layout = PathLayout(archive)
    v1 = layout.v1_repository_dir(135)
    (v1 / "readme").mkdir(parents=True)
    (v1 / "readme" / "README.md").write_text("# Guide\n", encoding="utf-8")
    snap_dir = v1 / "source" / "1"
    snap_dir.mkdir(parents=True)
    (snap_dir / "source-code.zip").write_bytes(b"SNAP-BYTES")
    release_dir = v1 / "releases" / "99" / "attempts" / "6"
    (release_dir / "source").mkdir(parents=True)
    (release_dir / "assets").mkdir(parents=True)
    (release_dir / "source" / "source-code.zip").write_bytes(b"REL-ZIP")
    (release_dir / "assets" / "100_book.epub").write_bytes(b"EPUB")
    pages = v1 / "pages"
    media = pages / "media"
    media.mkdir(parents=True)
    (pages / "index.html").write_text("<html>index</html>", encoding="utf-8")
    (media / "pic.bin").write_bytes(b"PIC")

    connection.execute(
        """
        INSERT INTO source_snapshots (
            repository_id, ref, default_branch, archived_at, state, size, local_sha256,
            relative_path, archive_root, created_at, updated_at
        ) VALUES (1, 'main', 'main', '2026-10-15T00:30:00+00:00', 'COMPLETED', 10,
                  NULL, ?, ?, 't', 't')
        """,
        ("repositories/135/source/1/source-code.zip", str(archive)),
    )
    connection.execute(
        """
        INSERT INTO releases (
            repository_id, github_release_id, tag_name, name, html_url,
            draft, prerelease, lookup_outcome, created_at_local, updated_at_local
        ) VALUES (
            1, 99, 'v1.0.0', 'One',
            'https://github.com/eternity4719/HowToLiveBetter/releases/tag/v1.0.0',
            0, 0, 'STABLE', 't', 't'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO release_attempts (release_id, status, created_at, finished_at)
        VALUES (1, 'COMPLETED', 't', 't')
        """
    )
    connection.execute(
        """
        INSERT INTO release_files (
            release_id, attempt_id, kind, name, github_asset_id, state, actual_size,
            relative_path, archive_root, created_at, updated_at
        ) VALUES
        (1, 1, 'SOURCE_ZIP', 'source-code.zip', NULL, 'COMPLETED', 7, ?, ?, 't', 't'),
        (1, 1, 'ASSET', 'book.epub', 100, 'COMPLETED', 4, ?, ?, 't', 't')
        """,
        (
            "repositories/135/releases/99/attempts/6/source/source-code.zip",
            str(archive),
            "repositories/135/releases/99/attempts/6/assets/100_book.epub",
            str(archive),
        ),
    )
    connection.execute(
        """
        INSERT INTO offline_pages (
            repository_id, page_type, status, relative_path, archive_root, built_at, created_at, updated_at
        ) VALUES (1, 'REPOSITORY', 'COMPLETED', ?, ?, 't', 't', 't')
        """,
        ("repositories/135/pages/index.html", str(archive)),
    )
    connection.execute(
        """
        INSERT INTO page_assets (
            offline_page_id, source_url, status, relative_path
        ) VALUES (1, 'https://example.com/pic.bin', 'COMPLETED', ?)
        """,
        ("repositories/135/pages/media/pic.bin",),
    )
    connection.execute(
        "UPDATE repositories SET storage_relpath = ?, current_snapshot_id = 1, current_release_id = 1 WHERE id = 1",
        ("repositories/135",),
    )
    connection.commit()
    return context, layout


def test_dry_run_lists_v1_to_v2_without_changes(tmp_path: Path) -> None:
    context, layout = _seed_v1(tmp_path)
    try:
        report = plan_migration(context.connection, layout)
        assert report.total_files >= 5
        assert report.conflicts == 0
        assert report.missing == 0
        # Disk untouched
        assert (layout.archive_root / "repositories" / "135" / "readme" / "README.md").is_file()
        assert not (layout.archive_root / "github").exists()
        assert context.connection.execute(
            "SELECT storage_relpath FROM repositories WHERE id = 1"
        ).fetchone()[0] == "repositories/135"
    finally:
        context.close()


def test_apply_migrates_and_is_idempotent(tmp_path: Path) -> None:
    context, layout = _seed_v1(tmp_path)
    try:
        first = apply_migration(context.connection, layout)
        assert not first.errors
        assert first.conflicts == 0
        assert first.migrated >= 4
        repo = layout.archive_root / "github" / "eternity4719" / "HowToLiveBetter"
        assert (repo / "README.md").read_text(encoding="utf-8") == "# Guide\n"
        zips = list((repo / "source").glob("*.zip"))
        assert len(zips) == 1
        assert zips[0].read_bytes() == b"SNAP-BYTES"
        assert (repo / "releases" / "v1.0.0" / "release-source.zip").read_bytes() == b"REL-ZIP"
        assert (repo / "releases" / "v1.0.0" / "book.epub").read_bytes() == b"EPUB"
        assert not (repo / "releases" / "v1.0.0" / "attempts").exists()
        assert (repo / "offline" / "index.html").is_file()
        assert (repo / "offline" / "media" / "pic.bin").read_bytes() == b"PIC"
        # Old V1 retained by default
        assert (layout.archive_root / "repositories" / "135" / "readme" / "README.md").is_file()
        storage = context.connection.execute(
            "SELECT storage_relpath FROM repositories WHERE id = 1"
        ).fetchone()[0]
        assert storage.replace("\\", "/") == "github/eternity4719/HowToLiveBetter"
        snap_rel = context.connection.execute(
            "SELECT relative_path FROM source_snapshots WHERE id = 1"
        ).fetchone()[0]
        assert snap_rel.replace("\\", "/").startswith("github/eternity4719/HowToLiveBetter/source/")
        folder = repository_dir_for(context.connection, layout, repository_id=1)
        assert folder == repo

        second = apply_migration(context.connection, layout)
        assert not second.errors
        assert second.migrated == 0
        assert second.already_v2 >= 4
        assert len(list((repo / "source").glob("*.zip"))) == 1
    finally:
        context.close()


def test_apply_conflict_does_not_overwrite(tmp_path: Path) -> None:
    context, layout = _seed_v1(tmp_path)
    try:
        dest = layout.archive_root / "github" / "eternity4719" / "HowToLiveBetter" / "releases" / "v1.0.0"
        dest.mkdir(parents=True)
        (dest / "release-source.zip").write_bytes(b"OTHER")
        report = apply_migration(context.connection, layout)
        assert report.conflicts >= 1
        assert report.errors
        assert (dest / "release-source.zip").read_bytes() == b"OTHER"
        # V1 source still intact
        assert (
            layout.archive_root
            / "repositories"
            / "135"
            / "releases"
            / "99"
            / "attempts"
            / "6"
            / "source"
            / "source-code.zip"
        ).read_bytes() == b"REL-ZIP"
    finally:
        context.close()


def test_cleanup_v1_requires_confirm_and_keeps_v2(tmp_path: Path) -> None:
    context, layout = _seed_v1(tmp_path)
    try:
        apply_migration(context.connection, layout)
        denied = cleanup_v1(context.connection, layout, confirm=False)
        assert denied.errors
        assert (layout.archive_root / "repositories" / "135").is_dir()
        cleaned = cleanup_v1(context.connection, layout, confirm=True)
        assert not cleaned.errors
        assert not (layout.archive_root / "repositories" / "135").exists()
        repo = layout.archive_root / "github" / "eternity4719" / "HowToLiveBetter"
        assert (repo / "README.md").is_file()
        assert (repo / "releases" / "v1.0.0" / "book.epub").is_file()
    finally:
        context.close()


def test_cli_dry_run_json(tmp_path: Path) -> None:
    context, layout = _seed_v1(tmp_path)
    context.close()
    from techvault.archive.migrate_layout_v2 import main

    code = main(
        [
            "dry-run",
            "--database",
            str(tmp_path / "db.sqlite"),
            "--archive-root",
            str(layout.archive_root),
        ]
    )
    assert code == 0
