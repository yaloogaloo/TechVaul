"""End-to-end Layout V2 acceptance: real archive flow builds the readable tree."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from techvault.app.archive_service import ArchiveService
from techvault.app.bootstrap import prepare_context
from techvault.app.library import LibraryService
from techvault.archive.release_archive import JobStatus

REPO = "https://api.github.com/repos/eternity4719/HowToLiveBetter"
LATEST = "https://api.github.com/repos/eternity4719/HowToLiveBetter/releases/latest"
README = "https://api.github.com/repos/eternity4719/HowToLiveBetter/readme"
SNAP = "https://api.github.com/repos/eternity4719/HowToLiveBetter/zipball/main"
ZIP = "https://codeload.github.com/eternity4719/HowToLiveBetter/zipball/v1.0.0"
TAR = "https://codeload.github.com/eternity4719/HowToLiveBetter/tarball/v1.0.0"
ASSET = "https://example.com/HowToLiveBetter.epub"


def _repo() -> dict:
    return {
        "id": 1359648985,
        "name": "HowToLiveBetter",
        "full_name": "eternity4719/HowToLiveBetter",
        "html_url": "https://github.com/eternity4719/HowToLiveBetter",
        "description": "demo",
        "stargazers_count": 1,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "eternity4719"},
        "topics": ["guide"],
    }


def _release() -> dict:
    return {
        "id": 391356253,
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "body": "notes",
        "html_url": "https://github.com/eternity4719/HowToLiveBetter/releases/tag/v1.0.0",
        "published_at": "2026-10-01T00:00:00Z",
        "created_at": "2026-09-01T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "tarball_url": TAR,
        "zipball_url": ZIP,
        "assets": [
            {
                "id": 600595924,
                "name": "HowToLiveBetter.epub",
                "size": 4,
                "state": "uploaded",
                "browser_download_url": ASSET,
                "content_type": "application/epub+zip",
            }
        ],
    }


def _tree(root: Path, prefix: str = "") -> list[str]:
    lines: list[str] = []
    if not root.is_dir():
        return lines
    entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    for index, entry in enumerate(entries):
        connector = "└── " if index == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if index == len(entries) - 1 else "│   "
            lines.extend(_tree(entry, prefix + extension))
    return lines


def test_archive_flow_builds_v2_directory_tree(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "archive"
    context = prepare_context(tmp_path / "db.sqlite", archive)
    service = ArchiveService(context.connection)
    try:
        with respx.mock:
            respx.get(REPO).mock(return_value=httpx.Response(200, json=_repo()))
            respx.get(LATEST).mock(return_value=httpx.Response(200, json=_release()))
            respx.get(README).mock(return_value=httpx.Response(200, text="<p>Hello</p>"))
            respx.get(SNAP).mock(return_value=httpx.Response(200, content=b"branch-zip"))
            respx.get(ZIP).mock(return_value=httpx.Response(200, content=b"release-zip"))
            respx.get(TAR).mock(return_value=httpx.Response(200, content=b"release-tar"))
            respx.get(ASSET).mock(return_value=httpx.Response(200, content=b"epub"))
            result = service.archive_repository(
                "https://github.com/eternity4719/HowToLiveBetter"
            )
        assert result.status == JobStatus.COMPLETED
        repo = archive / "github" / "eternity4719" / "HowToLiveBetter"
        assert repo.is_dir()
        assert (repo / "README.md").is_file()
        source_zips = list((repo / "source").glob("*.zip"))
        assert source_zips
        assert all(p.name.startswith("main-") for p in source_zips)
        release_dir = repo / "releases" / "v1.0.0"
        assert release_dir.is_dir()
        assert (release_dir / "release-source.zip").is_file()
        assert (release_dir / "HowToLiveBetter.epub").is_file()
        assert not (release_dir / "attempts").exists()
        assert (repo / "offline" / "index.html").is_file()
        assert not (archive / "repositories" / "1359648985").exists()

        detail = LibraryService(context.connection).get_repository(1)
        assert detail is not None
        assert Path(detail.archive_folder) == repo

        printed = "\n".join([str(archive / "github"), *_tree(archive / "github")])
        print("\n=== Layout V2 tree (from archive flow) ===")
        print(printed)
        captured = capsys.readouterr()
        assert "eternity4719" in captured.out
        assert "HowToLiveBetter" in captured.out
        assert "attempts" not in printed
    finally:
        context.close()
