"""Build repository and release pages without changing the archived release."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from techvault.archive.downloader import FileState
from techvault.archive.paths import PathLayout
from techvault.archive.storage import repository_dir_for
from techvault.errors import UnsafePathError
from techvault.fsutil import atomic_replace
from techvault.github.client import GitHubClient
from techvault.logging_setup import get_logger
from techvault.offline.gate import HtmlIntegrityError, assert_offline_html
from techvault.offline.images import download_page_image, localize_images, media_filename
from techvault.offline.links import neutralize_unsafe_urls, rewrite_github_urls
from techvault.offline.sanitize import sanitize_html

logger = get_logger()

REPOSITORY = "REPOSITORY"
RELEASE = "RELEASE"


class PageStatus:
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PageFailure:
    README = "README_UNAVAILABLE"
    MARKDOWN = "MARKDOWN_RENDER"
    INTEGRITY = "HTML_INTEGRITY"
    NETWORK = "NETWORK"


def build_offline_pages(
    connection: sqlite3.Connection,
    client: GitHubClient,
    layout: PathLayout,
    repository_id: int,
    *,
    observe=None,
    after_stream=None,
) -> str:
    repository = connection.execute(
        "SELECT * FROM repositories WHERE id = ?",
        (repository_id,),
    ).fetchone()
    if repository is None:
        raise ValueError("Repository does not exist")
    release = None
    if repository["current_release_id"] is not None:
        release = connection.execute(
            "SELECT * FROM releases WHERE id = ? AND repository_id = ?",
            (repository["current_release_id"], repository_id),
        ).fetchone()
    now = _now()
    page_ids = _ensure_pages(connection, repository_id, repository, release, now)
    _set_status(connection, list(page_ids.values()), PageStatus.BUILDING, None, None)
    if observe is not None:
        observe(PageStatus.BUILDING)

    readme_html, readme_failure = _readme_fragment(client, repository["owner"], repository["name"])
    if readme_failure is None and readme_html.strip():
        _store_readme_markdown(connection, client, layout, repository)
    body_html, markdown_failure = _release_fragment(client, repository, release)
    readme_html, readme_media = _prepare_fragment(
        connection,
        client,
        layout,
        repository,
        page_ids[REPOSITORY],
        readme_html,
        after_stream,
    )
    body_html, release_media = _prepare_fragment(
        connection,
        client,
        layout,
        repository,
        page_ids[RELEASE],
        body_html,
        after_stream,
    )
    built_at = _now()
    repo_root = repository_dir_for(connection, layout, repository_id=int(repository["id"]))
    index_ok = _publish(
        connection,
        layout,
        repository,
        page_ids[REPOSITORY],
        layout.page_index_at(repo_root),
        _repository_document(repository, release, readme_html, built_at),
        readme_media,
        readme_failure,
        built_at,
    )
    release_ok = _publish(
        connection,
        layout,
        repository,
        page_ids[RELEASE],
        layout.page_release_at(repo_root),
        _release_document(connection, layout, repository, release, body_html, built_at),
        release_media,
        markdown_failure,
        built_at,
    )
    if index_ok and release_ok:
        return PageStatus.COMPLETED
    return PageStatus.FAILED


def _prepare_fragment(
    connection: sqlite3.Connection,
    client: GitHubClient,
    layout: PathLayout,
    repository: sqlite3.Row,
    page_id: int,
    html: str,
    after_stream,
) -> tuple[str, dict[str, Path]]:
    neutralized, blocked = neutralize_unsafe_urls(html)
    for item in blocked:
        logger.warning(
            "Blocked unsafe URL scheme=%s context=<%s %s> url=%s",
            item.scheme,
            item.tag,
            item.attribute,
            item.url,
        )
    sanitized = sanitize_html(neutralized)
    rewritten = rewrite_github_urls(sanitized, repository["html_url"])

    def fetch(url: str) -> tuple[str, Path] | None:
        return _fetch_image(connection, client, layout, repository, page_id, url, after_stream)

    return localize_images(rewritten, fetch)


def _fetch_image(
    connection: sqlite3.Connection,
    client: GitHubClient,
    layout: PathLayout,
    repository: sqlite3.Row,
    page_id: int,
    url: str,
    after_stream,
) -> tuple[str, Path] | None:
    cursor = connection.execute(
        """
        INSERT INTO page_assets (offline_page_id, source_url, status)
        VALUES (?, ?, ?)
        """,
        (page_id, url, PageStatus.PENDING),
    )
    asset_id = int(cursor.lastrowid)
    try:
        repo_root = repository_dir_for(connection, layout, repository_id=int(repository["id"]))
        final = layout.page_media_file_at(repo_root, media_filename(url))
    except UnsafePathError as exc:
        _finish_asset(connection, asset_id, PageStatus.FAILED, None, None, None, "UNSAFE_PATH", exc.message)
        return None
    result = download_page_image(
        client,
        layout,
        job_id=f"page{page_id}",
        asset_id=asset_id,
        url=url,
        final_path=final,
        after_stream=after_stream,
    )
    if result.state != FileState.COMPLETED or result.local_sha256 is None:
        _finish_asset(
            connection,
            asset_id,
            PageStatus.FAILED,
            None,
            None,
            None,
            result.failure_code,
            result.failure_message,
        )
        return None
    relative = str(final.relative_to(layout.archive_root))
    media_src = f"media/{final.name}"
    _finish_asset(
        connection,
        asset_id,
        PageStatus.COMPLETED,
        relative,
        result.local_sha256,
        result.actual_size,
        None,
        None,
    )
    return media_src, final


def _finish_asset(
    connection: sqlite3.Connection,
    asset_id: int,
    status: str,
    relative: str | None,
    digest: str | None,
    actual_size: int | None,
    failure_code: str | None,
    message: str | None,
) -> None:
    connection.execute("BEGIN")
    try:
        connection.execute(
            """
            UPDATE page_assets
            SET relative_path = ?, sha256 = ?, actual_size = ?, status = ?,
                failure_code = ?, failure_message = ?
            WHERE id = ?
            """,
            (relative, digest, actual_size, status, failure_code, message, asset_id),
        )
        connection.execute("COMMIT")
    except sqlite3.Error:
        _rollback(connection)
        raise


def _store_readme_markdown(
    connection: sqlite3.Connection,
    client: GitHubClient,
    layout: PathLayout,
    repository: sqlite3.Row,
) -> None:
    try:
        text = client.get_readme_markdown(repository["owner"], repository["name"])
    except Exception as exc:
        logger.warning("README markdown was not saved code=%s", getattr(exc, "code", PageFailure.NETWORK))
        return
    if text is None or text.strip() == "":
        return
    repo_root = repository_dir_for(connection, layout, repository_id=int(repository["id"]))
    final_path = layout.readme_file_at(repo_root)
    part = layout.part_file(f"readme{int(repository['id'])}", int(repository["id"]))
    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    atomic_replace(part, final_path)


def _readme_fragment(client: GitHubClient, owner: str, repo: str) -> tuple[str, str | None]:
    try:
        html = client.get_readme_html(owner, repo)
    except Exception as exc:
        logger.warning("README request failed code=%s", getattr(exc, "code", PageFailure.NETWORK))
        return "<p>README unavailable</p>", PageFailure.README
    if html is None:
        logger.warning("README request failed code=%s", PageFailure.README)
        return "<p>README unavailable</p>", PageFailure.README
    if html.strip() == "":
        return "", None
    return html, None


def _release_fragment(client: GitHubClient, repository: sqlite3.Row, release: sqlite3.Row | None) -> tuple[str, str | None]:
    if release is None:
        return "", None
    body = release["body"] or ""
    if body.strip() == "":
        return "", None
    context = f"{repository['owner']}/{repository['name']}"
    try:
        return client.render_markdown(body, context), None
    except Exception as exc:
        logger.warning("Markdown rendering failed code=%s", getattr(exc, "code", PageFailure.MARKDOWN))
        return f"<pre>{escape(body, quote=True)}</pre>", PageFailure.MARKDOWN


def _publish(
    connection: sqlite3.Connection,
    layout: PathLayout,
    repository: sqlite3.Row,
    page_id: int,
    final_path: Path,
    html: str,
    media: dict[str, Path],
    failure_code: str | None,
    built_at: str,
) -> bool:
    try:
        assert_offline_html(html, media)
        for src, path in media.items():
            if not path.is_file():
                raise HtmlIntegrityError([f"missing media {src}"])
        _atomic_html(layout, page_id, final_path, html)
    except (HtmlIntegrityError, OSError) as exc:
        message = "; ".join(exc.violations) if isinstance(exc, HtmlIntegrityError) else "Offline page could not be written"
        logger.error("Offline page failed code=%s", PageFailure.INTEGRITY)
        _set_status(connection, [page_id], PageStatus.FAILED, PageFailure.INTEGRITY, message)
        return False
    if not final_path.is_file():
        _set_status(connection, [page_id], PageStatus.FAILED, PageFailure.INTEGRITY, "Offline page file is missing")
        return False
    relative = str(final_path.relative_to(layout.archive_root))
    _set_status(
        connection,
        [page_id],
        PageStatus.COMPLETED,
        failure_code,
        None,
        relative_path=relative,
        built_at=built_at,
        archive_root=str(layout.archive_root),
    )
    return True


def _atomic_html(layout: PathLayout, page_id: int, final_path: Path, html: str) -> None:
    part = layout.part_file(f"html{page_id}", page_id)
    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(html)
        handle.flush()
        os.fsync(handle.fileno())
    atomic_replace(part, final_path)


def _repository_document(
    repository: sqlite3.Row,
    release: sqlite3.Row | None,
    readme_html: str,
    built_at: str,
) -> str:
    if release is None:
        current = "<h2>Current release</h2>\n<p>该项目没有可用的正式 Release。</p>"
    else:
        label = release["tag_name"] or "Current release"
        current = (
            "<h2>Current release</h2>\n"
            f'<p><a href="release.html">{escape(label)}</a></p>'
        )
    description = repository["description"] or ""
    return _document(
        f"{repository['owner']}/{repository['name']}",
        f"""
<h1>{escape(repository['name'])}</h1>
<p>{escape(repository['owner'])}/{escape(repository['name'])}</p>
<p><a href="{escape(repository['html_url'], quote=True)}">{escape(repository['html_url'])}</a></p>
<p>{escape(description)}</p>
<h2>README</h2>
{readme_html}
{current}
<p>Archive status: {PageStatus.COMPLETED}</p>
<p>Archived at: {escape(built_at)}</p>
""",
    )


def _release_document(
    connection: sqlite3.Connection,
    layout: PathLayout,
    repository: sqlite3.Row,
    release: sqlite3.Row | None,
    body_html: str,
    built_at: str,
) -> str:
    if release is None:
        body = "<h1>Release</h1>\n<p>该项目没有可用的正式 Release。</p>"
    else:
        name = release["name"] or ""
        published = release["published_at"] or ""
        files = _file_list(connection, layout, repository, release)
        body = f"""
<h1>{escape(release['tag_name'] or '')}</h1>
<p>{escape(name)}</p>
<p>{escape(published)}</p>
<h2>Notes</h2>
{body_html}
<h2>Files</h2>
<ul>
{files}
</ul>
"""
    return _document(
        f"{repository['owner']}/{repository['name']} release",
        body + f'\n<p><a href="index.html">Repository page</a></p>\n<p>Archived at: {escape(built_at)}</p>\n',
    )


def _file_list(
    connection: sqlite3.Connection,
    layout: PathLayout,
    repository: sqlite3.Row,
    release: sqlite3.Row,
) -> str:
    rows = connection.execute(
        """
        SELECT f.kind, f.name, f.state, f.relative_path, f.archive_root
        FROM release_files f
        JOIN releases r ON r.id = f.release_id
        WHERE f.release_id = ?
          AND (r.artifact_attempt_id IS NULL OR f.attempt_id = r.artifact_attempt_id)
        ORDER BY CASE f.kind WHEN 'SOURCE_ZIP' THEN 0 WHEN 'SOURCE_TARBALL' THEN 1 ELSE 2 END, f.id
        """,
        (release["id"],),
    ).fetchall()
    repo_root = repository_dir_for(connection, layout, repository_id=int(repository["id"]))
    page = layout.page_release_at(repo_root)
    items: list[str] = []
    for row in rows:
        label = _file_label(row["kind"], row["name"])
        root = Path(row["archive_root"]) if row["archive_root"] else layout.archive_root
        href = _local_href(root, page, row["state"], row["relative_path"])
        if href is None:
            items.append(f"<li>{escape(label)}: Unavailable</li>")
        else:
            items.append(f'<li><a href="{escape(href, quote=True)}">{escape(label)}</a></li>')
    return "\n".join(items)


def _file_label(kind: str, name: str) -> str:
    if kind == "SOURCE_ZIP":
        return "Source ZIP"
    if kind == "SOURCE_TARBALL":
        return "Source TAR.GZ"
    return name


def _local_href(archive_root: Path, page: Path, state: str, relative_path: str | None) -> str | None:
    if state != FileState.COMPLETED or not relative_path:
        return None
    target = Path(os.path.normpath(str(archive_root / relative_path)))
    try:
        target.relative_to(archive_root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    href = Path(os.path.relpath(target, page.parent)).as_posix()
    resolved = Path(os.path.normpath(str(page.parent / href)))
    try:
        resolved.relative_to(archive_root)
    except ValueError:
        return None
    return href


def _document(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{escape(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _ensure_pages(
    connection: sqlite3.Connection,
    repository_id: int,
    repository: sqlite3.Row,
    release: sqlite3.Row | None,
    now: str,
) -> dict[str, int]:
    sources = {
        REPOSITORY: repository["html_url"],
        RELEASE: release["html_url"] if release is not None else repository["html_url"],
    }
    connection.execute("BEGIN")
    try:
        ids: dict[str, int] = {}
        for page_type, source in sources.items():
            row = connection.execute(
                """
                SELECT id FROM offline_pages
                WHERE repository_id = ? AND page_type = ?
                """,
                (repository_id, page_type),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO offline_pages (
                        repository_id, page_type, source_url, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (repository_id, page_type, source, PageStatus.PENDING, now, now),
                )
                ids[page_type] = int(cursor.lastrowid)
            else:
                connection.execute(
                    """
                    UPDATE offline_pages
                    SET source_url = ?, status = ?, failure_code = NULL, failure_message = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (source, PageStatus.PENDING, now, int(row["id"])),
                )
                ids[page_type] = int(row["id"])
        connection.execute("COMMIT")
    except sqlite3.Error:
        _rollback(connection)
        raise
    return ids


def _set_status(
    connection: sqlite3.Connection,
    page_ids: list[int],
    status: str,
    failure_code: str | None,
    message: str | None,
    *,
    relative_path: str | None = None,
    built_at: str | None = None,
    archive_root: str | None = None,
) -> None:
    now = _now()
    connection.execute("BEGIN")
    try:
        for page_id in page_ids:
            if relative_path is None and built_at is None:
                connection.execute(
                    """
                    UPDATE offline_pages
                    SET status = ?, failure_code = ?, failure_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, failure_code, message, now, page_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE offline_pages
                    SET status = ?, failure_code = ?, failure_message = ?, relative_path = ?,
                        built_at = ?, archive_root = COALESCE(?, archive_root), updated_at = ?
                    WHERE id = ?
                    """,
                    (status, failure_code, message, relative_path, built_at, archive_root, now, page_id),
                )
        connection.execute("COMMIT")
    except sqlite3.Error:
        _rollback(connection)
        raise


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
