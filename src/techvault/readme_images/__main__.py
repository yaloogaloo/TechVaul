"""Backfill README images for repositories that are already archived.

This does not run on application startup. Example::

    python -m techvault.readme_images --database data/techvault.sqlite --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from techvault.db.connection import connect
from techvault.readme_images.paths import resolve_readme_image_root
from techvault.readme_images.pool import ImagePool
from techvault.readme_images.service import cache_repository_readme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill GitHub README images into the shared pool")
    parser.add_argument("--database", required=True, help="Path to techvault.sqlite")
    parser.add_argument("--repository-id", type=int, action="append", dest="repository_ids")
    parser.add_argument("--all", action="store_true", help="Backfill every saved repository")
    parser.add_argument("--image-root", help="Override the shared image pool directory")
    args = parser.parse_args(argv)
    if not args.all and not args.repository_ids:
        parser.error("pass --all or at least one --repository-id")
    database = Path(args.database)
    connection = connect(database)
    try:
        if args.all:
            ids = [
                int(row["id"])
                for row in connection.execute("SELECT id FROM repositories ORDER BY id")
            ]
        else:
            ids = list(args.repository_ids or [])
        root = Path(args.image_root) if args.image_root else resolve_readme_image_root(database)
        for repository_id in ids:
            try:
                report = cache_repository_readme(connection, repository_id, image_root=root, revalidate=True)
            except Exception as exc:
                print(f"{repository_id}\tFAILED\t{exc}", file=sys.stderr)
                continue
            print(
                f"{repository_id}\t{report.message}\tcached={report.cached}\tfailed={report.failed}\treused={report.reused}"
            )
        index = root / "cache_index.sqlite"
        if index.is_file():
            pool = ImagePool(root)
            try:
                stats = pool.stats()
            finally:
                pool.close()
            print(
                "pool\tobjects={objects}\tbytes={bytes}\treferences={references}\tduplicateHits={duplicateHits}".format(
                    **stats
                )
            )
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
