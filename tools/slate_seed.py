"""Seed an isolated TechVault DB + archive tree for Slate business screenshots."""

from __future__ import annotations

from pathlib import Path

from techvault.app.bootstrap import prepare_context
from techvault.archive.paths import PathLayout

NOW = "2026-10-14T12:00:00Z"

LONG_README = """# TechVault Seed README

面向业务集成截图的合成 README。

## Features

- Local archive of GitHub releases
- Offline README reading

### Install

```bash
pip install techvault
```

## Wide table

| Component | Role | Path | Notes |
|-----------|------|------|-------|
| SQLite | metadata | LOCALAPPDATA/TechVault | does not move with archive_root |
| PathLayout | paths | repositories/id | never mix snapshot and release ids |

Center badge HTML below should render as content, not raw tags:

<p align="center"><strong>build: passing</strong></p>
"""


def seed(database: Path, archive_root: Path) -> None:
    archive_root.mkdir(parents=True, exist_ok=True)
    context = prepare_context(database, archive_root=archive_root)
    conn = context.connection
    layout = PathLayout(archive_root)
    conn.execute(
        "INSERT INTO repository_categories (id, name, sort_order) VALUES (1, '工具链', 0), (2, '学习笔记', 1)"
    )
    # BongoCat — success
    _insert_repo(
        conn,
        layout,
        repo_id=1,
        github_repo_id=101,
        owner="vladelaina",
        name="BongoCat",
        description="桌面宠物 · SDL3 / OpenGL",
        category_id=1,
        release_tag="v1.14.0",
        job_status="COMPLETED",
        topics=("sdl3", "opengl", "desktop"),
    )
    # Long name — local ok, last job failed
    _insert_repo(
        conn,
        layout,
        repo_id=2,
        github_repo_id=102,
        owner="microsoft",
        name="TypeScript-Very-Long-Repository-Name-For-Layout-Testing",
        description=(
            "这是一段刻意加长的中英文混合简介：TypeScript language / 类型系统与编译器工具链，"
            "用于验证列表与详情头部。"
        ),
        category_id=1,
        release_tag="v5.6.2",
        job_status="FAILED",
        job_message="网络超时；本地旧存档仍可用",
        topics=("typescript", "compiler", "language", "devtools"),
        readme=LONG_README,
    )
    # Pending — none
    conn.execute(
        """
        INSERT INTO repositories (
            id, github_repo_id, owner, name, full_name, description, html_url,
            default_branch, private, archive_status, category_id, created_at, updated_at
        ) VALUES (
            3, 103, 'demo-org', 'pending-archive', 'demo-org/pending-archive',
            '尚未归档的项目。', 'https://github.com/demo-org/pending-archive',
            'main', 0, 'NOT_ARCHIVED', NULL, ?, ?
        )
        """,
        (NOW, NOW),
    )
    # Knowledge documents for Stage C screenshots (51+ for pagination)
    long_doc = """# CMake 构建优化指南

记录大型 C++ 项目中的依赖管理与编译效率优化实践。

## 要点

1. 使用 `ccache`
2. 拆分目标
3. 预编译头

```cmake
add_library(core STATIC ...)
```

| 选项 | 效果 |
|------|------|
| Ninja | 更快并行 |
| LTO | 体积更小 |
"""
    docs = context.documents
    cat = docs.create_category("技术笔记", sort_order=20)
    first = docs.create_document(
        "CMake 构建优化指南",
        long_doc,
        doc_type="TIP",
        category_id=cat.id,
        tags=["cmake", "cpp"],
    )
    docs.set_favorite(first.id, True)
    docs.create_document(
        "空摘要示例",
        "",
        doc_type="NOTE",
        category_id=cat.id,
    )
    for index in range(2, 55):
        docs.create_document(
            f"批量笔记 {index:03d}",
            f"正文内容 {index} — 用于验证加载更多与列表密度。\n\n## 小节\n\n段落 {index}.",
            doc_type="NOTE" if index % 2 == 0 else "COMMAND",
            category_id=cat.id,
            tags=["batch"] if index < 20 else ["archive"],
        )
    conn.commit()
    context.close()


def _insert_repo(
    conn,
    layout: PathLayout,
    *,
    repo_id: int,
    github_repo_id: int,
    owner: str,
    name: str,
    description: str,
    category_id: int,
    release_tag: str,
    job_status: str,
    topics: tuple[str, ...],
    job_message: str = "",
    readme: str = "# Hello\n\nSeed README.\n",
) -> None:
    full = f"{owner}/{name}"
    conn.execute(
        """
        INSERT INTO repositories (
            id, github_repo_id, owner, name, full_name, description, html_url,
            default_branch, private, archive_status, category_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'main', 0, 'ARCHIVED', ?, ?, ?)
        """,
        (
            repo_id,
            github_repo_id,
            owner,
            name,
            full,
            description,
            f"https://github.com/{full}",
            category_id,
            NOW,
            NOW,
        ),
    )
    for topic in topics:
        conn.execute(
            "INSERT INTO repository_topics (repository_id, name) VALUES (?, ?)",
            (repo_id, topic),
        )
    snap = conn.execute(
        """
        INSERT INTO source_snapshots (
            repository_id, ref, default_branch, commit_sha, archived_at, size,
            local_sha256, relative_path, state, created_at, updated_at, archive_root
        ) VALUES (?, 'main', 'main', NULL, ?, 1200000, NULL, ?, 'COMPLETED', ?, ?, ?)
        """,
        (
            repo_id,
            NOW,
            f"repositories/{github_repo_id}/source/1/source-code.zip",
            NOW,
            NOW,
            str(layout.archive_root),
        ),
    )
    snapshot_id = int(snap.lastrowid)
    rel = conn.execute(
        """
        INSERT INTO releases (
            github_release_id, repository_id, tag_name, name, html_url,
            published_at, prerelease, draft, lookup_outcome, created_at_local, updated_at_local
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'STABLE', ?, ?)
        """,
        (
            github_repo_id * 10,
            repo_id,
            release_tag,
            "Stable",
            f"https://github.com/{full}/releases/tag/{release_tag}",
            NOW,
            NOW,
            NOW,
        ),
    )
    release_id = int(rel.lastrowid)
    conn.execute(
        """
        INSERT INTO release_files (
            release_id, kind, name, state, created_at, updated_at, archive_root
        ) VALUES (?, 'SOURCE_ZIP', 'source-code.zip', 'COMPLETED', ?, ?, ?)
        """,
        (release_id, NOW, NOW, str(layout.archive_root)),
    )
    conn.execute(
        "UPDATE repositories SET current_snapshot_id = ?, current_release_id = ? WHERE id = ?",
        (snapshot_id, release_id, repo_id),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            repository_id, target_release_id, status, created_at, started_at, finished_at,
            archive_kind, target_snapshot_id, failure_message
        ) VALUES (?, ?, ?, ?, ?, ?, 'SOURCE', ?, ?)
        """,
        (repo_id, release_id, job_status, NOW, NOW, NOW, snapshot_id, job_message or None),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            repository_id, target_release_id, status, created_at, started_at, finished_at,
            archive_kind, failure_message
        ) VALUES (?, ?, ?, ?, ?, ?, 'RELEASE', ?)
        """,
        (repo_id, release_id, job_status, NOW, NOW, NOW, job_message or None),
    )
    conn.execute(
        """
        INSERT INTO offline_pages (
            repository_id, page_type, status, relative_path,
            created_at, updated_at, built_at, archive_root
        ) VALUES (?, 'REPOSITORY', 'COMPLETED', ?, ?, ?, ?, ?)
        """,
        (
            repo_id,
            f"repositories/{github_repo_id}/pages/index.html",
            NOW,
            NOW,
            NOW,
            str(layout.archive_root),
        ),
    )
    readme_path = layout.readme_file(owner, name, github_repo_id)
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.write_text(readme, encoding="utf-8")
    folder = layout.repository_dir(owner, name, github_repo_id)
    (folder / "source" / str(snapshot_id)).mkdir(parents=True, exist_ok=True)
    (folder / "source" / str(snapshot_id) / "source-code.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)


if __name__ == "__main__":
    import sys

    seed(Path(sys.argv[1]), Path(sys.argv[2]))
    print("SEED_OK")
