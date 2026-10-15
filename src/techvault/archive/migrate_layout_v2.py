"""Migrate GitHub Archive Layout V1 → V2 (explicit dry-run / apply; never auto)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from techvault.archive.paths import PathLayout, snapshot_filename
from techvault.db.connection import connect
from techvault.db.migrate import apply_migrations, default_migrations_dir

SOURCE_ZIP = "SOURCE_ZIP"
SOURCE_TARBALL = "SOURCE_TARBALL"


@dataclass
class FilePlan:
    table: str
    row_id: int
    source: str
    destination: str
    dest_relative: str
    archive_root: str
    size: int | None = None
    status: str = "pending"
    note: str = ""


@dataclass
class RepoPlan:
    repository_id: int
    github_repo_id: int
    owner: str
    name: str
    old_storage: str | None
    new_storage: str
    files: list[FilePlan] = field(default_factory=list)
    readme_extra: FilePlan | None = None


@dataclass
class MigrationReport:
    mode: str
    archive_root: str
    repositories: list[RepoPlan] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    conflicts: int = 0
    missing: int = 0
    already_v2: int = 0
    migrated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def plan_migration(connection: sqlite3.Connection, layout: PathLayout) -> MigrationReport:
    report = MigrationReport(mode="dry-run", archive_root=str(layout.archive_root))
    rows = connection.execute(
        """
        SELECT id, owner, name, github_repo_id, storage_relpath
        FROM repositories
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        repo_plan = _plan_repository(connection, layout, row)
        if repo_plan is None:
            continue
        report.repositories.append(repo_plan)
        for item in _iter_files(repo_plan):
            report.total_files += 1
            if item.size:
                report.total_bytes += item.size
            if item.status == "conflict":
                report.conflicts += 1
            elif item.status == "missing_source":
                report.missing += 1
            elif item.status == "already_v2":
                report.already_v2 += 1
            elif item.status == "skip":
                report.skipped += 1
    return report


def apply_migration(connection: sqlite3.Connection, layout: PathLayout) -> MigrationReport:
    report = plan_migration(connection, layout)
    report.mode = "apply"
    if report.conflicts:
        report.errors.append("Conflicts present; resolve or skip conflicting targets before apply.")
        return report
    for repo in report.repositories:
        repo_errors = 0
        for item in _iter_files(repo):
            if item.status in {"skip", "missing_source", "conflict"}:
                continue
            try:
                if item.status == "pending":
                    _publish_file(Path(item.source), Path(item.destination))
                    item.status = "migrated"
                    report.migrated += 1
                # Always align DB pointers to the V2 relative path when destination is ready.
                if item.table != "readme" and Path(item.destination).is_file():
                    _update_relative(
                        connection,
                        item.table,
                        item.row_id,
                        item.dest_relative,
                        str(layout.archive_root),
                    )
            except OSError as exc:
                item.status = "error"
                item.note = str(exc)
                report.errors.append(f"{item.source}: {exc}")
                repo_errors += 1
        if repo_errors == 0:
            connection.execute(
                "UPDATE repositories SET storage_relpath = ? WHERE id = ?",
                (repo.new_storage, repo.repository_id),
            )
    connection.commit()
    return report


def cleanup_v1(
    connection: sqlite3.Connection,
    layout: PathLayout,
    *,
    confirm: bool,
) -> MigrationReport:
    report = MigrationReport(mode="cleanup-v1", archive_root=str(layout.archive_root))
    if not confirm:
        report.errors.append("Refusing cleanup without --confirm.")
        return report
    rows = connection.execute(
        "SELECT id, github_repo_id, storage_relpath, owner, name FROM repositories ORDER BY id"
    ).fetchall()
    for row in rows:
        rel = (row["storage_relpath"] or "").replace("\\", "/")
        if not rel.startswith("github/"):
            report.errors.append(
                f"repository {row['id']} storage_relpath is not V2 ({rel or 'NULL'}); skip cleanup"
            )
            continue
        v2 = layout.repository_dir_from_relpath(rel)
        if not v2.is_dir():
            report.errors.append(f"V2 folder missing for repository {row['id']}: {v2}")
            continue
        # Verify current pointers resolve under V2 when present.
        if not _pointers_ok(connection, layout, int(row["id"]), v2):
            report.errors.append(f"Pointers still reference missing V2 files for repository {row['id']}")
            continue
        v1 = layout.v1_repository_dir(int(row["github_repo_id"]))
        if v1.is_dir():
            try:
                shutil.rmtree(v1)
                report.migrated += 1
            except OSError as exc:
                report.errors.append(f"Failed to remove {v1}: {exc}")
    return report


def _plan_repository(connection: sqlite3.Connection, layout: PathLayout, row: sqlite3.Row) -> RepoPlan | None:
    repository_id = int(row["id"])
    github_repo_id = int(row["github_repo_id"])
    owner = row["owner"]
    name = row["name"]
    stored = row["storage_relpath"]
    stored_norm = stored.replace("\\", "/") if stored else None

    # Prefer existing V2 binding; otherwise allocate a fresh V2 relpath (without writing).
    if stored_norm and stored_norm.startswith("github/"):
        new_storage = stored_norm
    else:
        new_storage = layout.repository_relpath(owner, name, github_repo_id)

    repo_root = layout.repository_dir_from_relpath(new_storage)
    plan = RepoPlan(
        repository_id=repository_id,
        github_repo_id=github_repo_id,
        owner=owner,
        name=name,
        old_storage=stored_norm,
        new_storage=new_storage,
    )

    # Source snapshots
    for snap in connection.execute(
        """
        SELECT id, ref, default_branch, commit_sha, archived_at, relative_path, archive_root, size, local_sha256
        FROM source_snapshots
        WHERE repository_id = ? AND relative_path IS NOT NULL
        ORDER BY id
        """,
        (repository_id,),
    ):
        plan.files.append(
            _plan_snapshot(layout, repo_root, snap, new_storage)
        )

    # Release files (all attempts that have a stored path)
    for rf in connection.execute(
        """
        SELECT rf.id, rf.kind, rf.name, rf.github_asset_id, rf.relative_path, rf.archive_root,
               rf.actual_size, rf.local_sha256, r.tag_name
        FROM release_files rf
        JOIN releases r ON r.id = rf.release_id
        WHERE r.repository_id = ? AND rf.relative_path IS NOT NULL
        ORDER BY rf.id
        """,
        (repository_id,),
    ):
        plan.files.append(_plan_release_file(layout, repo_root, rf))

    # Offline pages
    for page in connection.execute(
        """
        SELECT id, page_type, relative_path, archive_root
        FROM offline_pages
        WHERE repository_id = ? AND relative_path IS NOT NULL
        ORDER BY id
        """,
        (repository_id,),
    ):
        plan.files.append(_plan_offline_page(layout, repo_root, page))

    # Page assets (paths are relative to the page's archive_root)
    for asset in connection.execute(
        """
        SELECT pa.id, pa.relative_path, pa.sha256 AS local_sha256, op.archive_root
        FROM page_assets pa
        JOIN offline_pages op ON op.id = pa.offline_page_id
        WHERE op.repository_id = ? AND pa.relative_path IS NOT NULL
        ORDER BY pa.id
        """,
        (repository_id,),
    ):
        plan.files.append(_plan_page_asset(layout, repo_root, asset))

    # README on disk (may not have its own table row)
    readme_plan = _plan_readme(layout, github_repo_id, repo_root, new_storage)
    if readme_plan is not None:
        plan.readme_extra = readme_plan

    if not plan.files and plan.readme_extra is None and stored_norm and stored_norm.startswith("github/"):
        return None
    if not plan.files and plan.readme_extra is None:
        # Still may need storage_relpath bind if V1 tree exists empty — skip.
        v1 = layout.v1_repository_dir(github_repo_id)
        if not v1.is_dir():
            return None
    return plan


def _plan_snapshot(layout: PathLayout, repo_root: Path, snap: sqlite3.Row, new_storage: str) -> FilePlan:
    root = Path(snap["archive_root"]) if snap["archive_root"] else layout.archive_root
    source = root / snap["relative_path"]
    rel = str(snap["relative_path"]).replace("\\", "/")
    if rel.startswith("github/") and source.is_file():
        return FilePlan(
            table="source_snapshots",
            row_id=int(snap["id"]),
            source=str(source),
            destination=str(source),
            dest_relative=rel,
            archive_root=str(root),
            size=source.stat().st_size if source.is_file() else snap["size"],
            status="already_v2",
        )
    when = _parse_ts(snap["archived_at"])
    branch = snap["default_branch"] or snap["ref"] or "main"
    # Deterministic migrated name includes snapshot id so re-runs stay idempotent.
    base = snapshot_filename(
        branch,
        commit_sha=snap["commit_sha"],
        when=when,
        snapshot_id=int(snap["id"]),
    )
    stem = base[:-4] if base.endswith(".zip") else base
    filename = f"{stem}-s{int(snap['id'])}.zip"
    dest = layout.source_dir_at(repo_root) / filename
    dest_rel = dest.relative_to(layout.archive_root).as_posix()
    return _classify_move(
        table="source_snapshots",
        row_id=int(snap["id"]),
        source=source,
        destination=dest,
        dest_relative=dest_rel,
        archive_root=str(layout.archive_root),
        expected_sha=snap["local_sha256"],
        expected_size=snap["size"],
    )


def _plan_release_file(layout: PathLayout, repo_root: Path, rf: sqlite3.Row) -> FilePlan:
    root = Path(rf["archive_root"]) if rf["archive_root"] else layout.archive_root
    source = root / rf["relative_path"]
    rel = str(rf["relative_path"]).replace("\\", "/")
    if rel.startswith("github/") and source.is_file():
        return FilePlan(
            table="release_files",
            row_id=int(rf["id"]),
            source=str(source),
            destination=str(source),
            dest_relative=rel,
            archive_root=str(root),
            size=source.stat().st_size if source.is_file() else rf["actual_size"],
            status="already_v2",
        )
    tag = rf["tag_name"] or "unknown"
    kind = rf["kind"]
    if kind == SOURCE_ZIP:
        dest = layout.release_source_file_at(repo_root, tag, kind="zip")
    elif kind == SOURCE_TARBALL:
        dest = layout.release_source_file_at(repo_root, tag, kind="tar")
    else:
        asset_id = int(rf["github_asset_id"] or rf["id"])
        dest = layout.release_asset_file_at(repo_root, tag, asset_id, rf["name"] or "asset.bin")
    dest_rel = dest.relative_to(layout.archive_root).as_posix()
    return _classify_move(
        table="release_files",
        row_id=int(rf["id"]),
        source=source,
        destination=dest,
        dest_relative=dest_rel,
        archive_root=str(layout.archive_root),
        expected_sha=rf["local_sha256"],
        expected_size=rf["actual_size"],
    )


def _plan_offline_page(layout: PathLayout, repo_root: Path, page: sqlite3.Row) -> FilePlan:
    root = Path(page["archive_root"]) if page["archive_root"] else layout.archive_root
    source = root / page["relative_path"]
    rel = str(page["relative_path"]).replace("\\", "/")
    if "/offline/" in rel and source.is_file():
        return FilePlan(
            table="offline_pages",
            row_id=int(page["id"]),
            source=str(source),
            destination=str(source),
            dest_relative=rel,
            archive_root=str(root),
            size=source.stat().st_size if source.is_file() else None,
            status="already_v2",
        )
    page_type = page["page_type"]
    if page_type == "RELEASE":
        dest = layout.page_release_at(repo_root)
    else:
        dest = layout.page_index_at(repo_root)
    dest_rel = dest.relative_to(layout.archive_root).as_posix()
    return _classify_move(
        table="offline_pages",
        row_id=int(page["id"]),
        source=source,
        destination=dest,
        dest_relative=dest_rel,
        archive_root=str(layout.archive_root),
        expected_sha=None,
        expected_size=None,
    )


def _plan_page_asset(layout: PathLayout, repo_root: Path, asset: sqlite3.Row) -> FilePlan:
    root = Path(asset["archive_root"]) if asset["archive_root"] else layout.archive_root
    source = root / asset["relative_path"]
    rel = str(asset["relative_path"]).replace("\\", "/")
    if "/offline/media/" in rel and source.is_file():
        return FilePlan(
            table="page_assets",
            row_id=int(asset["id"]),
            source=str(source),
            destination=str(source),
            dest_relative=rel,
            archive_root=str(root),
            size=source.stat().st_size if source.is_file() else None,
            status="already_v2",
        )
    filename = Path(rel).name
    dest = layout.page_media_file_at(repo_root, filename)
    dest_rel = dest.relative_to(layout.archive_root).as_posix()
    return _classify_move(
        table="page_assets",
        row_id=int(asset["id"]),
        source=source,
        destination=dest,
        dest_relative=dest_rel,
        archive_root=str(layout.archive_root),
        expected_sha=asset["local_sha256"],
        expected_size=None,
    )


def _plan_readme(
    layout: PathLayout,
    github_repo_id: int,
    repo_root: Path,
    new_storage: str,
) -> FilePlan | None:
    v1 = layout.v1_repository_dir(github_repo_id) / "readme" / "README.md"
    v2 = layout.readme_file_at(repo_root)
    if v2.is_file():
        return FilePlan(
            table="readme",
            row_id=0,
            source=str(v2),
            destination=str(v2),
            dest_relative=f"{new_storage}/README.md",
            archive_root=str(layout.archive_root),
            size=v2.stat().st_size,
            status="already_v2",
        )
    if not v1.is_file():
        return None
    return _classify_move(
        table="readme",
        row_id=0,
        source=v1,
        destination=v2,
        dest_relative=f"{new_storage}/README.md",
        archive_root=str(layout.archive_root),
        expected_sha=None,
        expected_size=None,
    )


def _classify_move(
    *,
    table: str,
    row_id: int,
    source: Path,
    destination: Path,
    dest_relative: str,
    archive_root: str,
    expected_sha: str | None,
    expected_size: int | None,
) -> FilePlan:
    plan = FilePlan(
        table=table,
        row_id=row_id,
        source=str(source),
        destination=str(destination),
        dest_relative=dest_relative,
        archive_root=archive_root,
        size=expected_size,
    )
    if not source.is_file():
        plan.status = "missing_source"
        plan.note = "source file missing"
        return plan
    plan.size = source.stat().st_size
    if destination.is_file():
        if _same_file(source, destination, expected_sha):
            plan.status = "already_v2"
            plan.note = "destination already matches source"
        else:
            plan.status = "conflict"
            plan.note = "destination exists with different content"
        return plan
    plan.status = "pending"
    return plan


def _same_file(source: Path, destination: Path, expected_sha: str | None) -> bool:
    if source.resolve() == destination.resolve():
        return True
    if source.stat().st_size != destination.stat().st_size:
        return False
    if expected_sha:
        return _sha256(destination) == expected_sha.lower()
    return _sha256(source) == _sha256(destination)


def _publish_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _same_file(source, destination, None):
        return
    tmp = destination.with_name(destination.name + ".migrate_tmp")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(source, tmp)
    if destination.exists():
        # Never overwrite differing content — caller should have classified conflict.
        if not _same_file(source, destination, None):
            tmp.unlink(missing_ok=True)
            raise OSError(f"conflict at {destination}")
        tmp.unlink(missing_ok=True)
        return
    tmp.replace(destination)


def _update_relative(
    connection: sqlite3.Connection,
    table: str,
    row_id: int,
    relative: str,
    archive_root: str,
) -> None:
    if table == "readme":
        return
    if table == "page_assets":
        connection.execute(
            "UPDATE page_assets SET relative_path = ? WHERE id = ?",
            (relative, row_id),
        )
        return
    if table not in {"source_snapshots", "release_files", "offline_pages"}:
        raise ValueError(table)
    connection.execute(
        f"UPDATE {table} SET relative_path = ?, archive_root = ? WHERE id = ?",
        (relative, archive_root, row_id),
    )


def _pointers_ok(
    connection: sqlite3.Connection,
    layout: PathLayout,
    repository_id: int,
    v2_root: Path,
) -> bool:
    for table in ("source_snapshots", "offline_pages"):
        for row in connection.execute(
            f"SELECT relative_path, archive_root FROM {table} WHERE repository_id = ? AND relative_path IS NOT NULL",
            (repository_id,),
        ):
            root = Path(row["archive_root"]) if row["archive_root"] else layout.archive_root
            path = root / row["relative_path"]
            if not path.is_file():
                return False
            try:
                path.resolve().relative_to(v2_root.resolve())
            except ValueError:
                return False
    return True


def _iter_files(repo: RepoPlan):
    yield from repo.files
    if repo.readme_extra is not None:
        yield repo.readme_extra


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _print_report(report: MigrationReport) -> None:
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m techvault.archive.migrate_layout_v2",
        description="Migrate TechVault GitHub archives from Layout V1 to V2.",
    )
    parser.add_argument(
        "command",
        choices=("dry-run", "apply", "cleanup-v1"),
        help="dry-run plans only; apply copies and updates DB; cleanup-v1 removes V1 trees after confirm",
    )
    parser.add_argument("--database", type=Path, required=True, help="Path to techvault.sqlite")
    parser.add_argument(
        "--archive-root",
        type=Path,
        required=True,
        help="Archive root to scan (must match the tree you intend to migrate)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required for cleanup-v1",
    )
    args = parser.parse_args(argv)

    connection = connect(args.database)
    try:
        apply_migrations(connection, default_migrations_dir())
        layout = PathLayout(args.archive_root)
        if args.command == "dry-run":
            report = plan_migration(connection, layout)
        elif args.command == "apply":
            report = apply_migration(connection, layout)
        else:
            report = cleanup_v1(connection, layout, confirm=args.confirm)
        _print_report(report)
        return 1 if report.errors or report.conflicts else 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
