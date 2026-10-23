"""Shared README image pool: resolution, dedup, failure, and offline rewrite."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from techvault.app.archive_service import ArchiveService
from techvault.app.bootstrap import prepare_context
from techvault.archive.paths import PathLayout
from techvault.archive.storage import repository_dir_for
from techvault.readme_images.fetch import fetch_image, is_public_http_url
from techvault.readme_images.html_images import absolutize_images, render_markdown_body
from techvault.readme_images.media_path import file_matches_hash, resolve_media_file
from techvault.readme_images.pool import ImagePool
from techvault.readme_images.service import cache_repository_readme, present_github_readme
from techvault.readme_images.sniff import sanitize_svg
from techvault.readme_images.urls import resolve_readme_image_url

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
PNG_B = b"\x89PNG\r\n\x1a\n" + b"\x01" * 24
GIF = b"GIF89a" + b"\x00" * 16
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='1' height='1'/></svg>"
SVG_SCRIPT = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script><rect /></svg>"


@pytest.fixture
def public_net(monkeypatch):
    def allow(url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme.lower() not in {"http", "https"}:
            return False
        if host in {"127.0.0.1", "localhost", "169.254.169.254", "10.1.1.1"}:
            return False
        return True

    monkeypatch.setattr("techvault.readme_images.fetch.is_public_http_url", allow)


def test_private_and_local_targets_are_rejected() -> None:
    assert is_public_http_url("http://127.0.0.1/secret.png") is False
    assert is_public_http_url("http://10.1.1.1/a.png") is False
    assert is_public_http_url("http://169.254.169.254/latest/meta-data") is False
    assert is_public_http_url("file:///C:/Windows/win.ini") is False
    assert (
        resolve_readme_image_url(
            "javascript:alert(1)",
            owner="octo",
            repo="hello",
            branch="main",
            allow_relative=True,
        )
        is None
    )


def test_relative_html_and_github_blob_urls_resolve() -> None:
    markdown = """
![Logo](./assets/logo.png)

<img src="./images/demo.png" alt="demo">
<img srcset="https://cdn.example.com/wide.png 2x" alt="wide">

![Badge](https://img.shields.io/badge/demo-blue)

```
![skip](https://example.com/not-downloaded.png)
```

[plain link](https://example.com/file.png)

![blob](https://github.com/octo/hello/blob/main/docs/shot.png)
"""
    html = render_markdown_body(markdown)
    _prepared, outcomes = absolutize_images(
        html,
        owner="octo",
        repo="hello",
        branch="main",
        allow_relative=True,
    )
    urls = [url for state, url in outcomes if state == "url" and url]
    assert "https://raw.githubusercontent.com/octo/hello/main/assets/logo.png" in urls
    assert "https://raw.githubusercontent.com/octo/hello/main/images/demo.png" in urls
    assert "https://cdn.example.com/wide.png" in urls
    assert any(url.startswith("https://img.shields.io/") for url in urls)
    assert "https://raw.githubusercontent.com/octo/hello/main/docs/shot.png" in urls
    assert all("not-downloaded" not in url and not url.endswith("/file.png") for url in urls)


def test_content_dedup_revalidation_and_failures(tmp_path: Path, public_net) -> None:
    del public_net
    bodies = {
        "https://example.com/logo.png": (200, PNG, "image/png", '"one"'),
        "https://cdn.example.com/logo.png": (200, PNG, "image/png", '"two"'),
        "https://example.com/missing.png": (404, b"", "text/plain", None),
        "https://example.com/denied.png": (403, b"", "text/plain", None),
        "https://example.com/page.png": (200, b"<html>nope</html>", "text/html", None),
        "https://example.com/huge.png": (200, PNG, "image/png", None),
        "https://example.com/anim.gif": (200, GIF, "image/gif", None),
        "https://example.com/icon.svg": (200, SVG_SCRIPT, "image/svg+xml", None),
    }
    calls = {"n": 0}
    phase = {"changed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert "authorization" not in {key.lower() for key in request.headers}
        url = str(request.url).split("?")[0]
        if url.endswith("/huge.png"):
            return httpx.Response(200, headers={"Content-Length": "20000000", "Content-Type": "image/png"})
        status, body, mime, etag = bodies[url]
        headers = {"Content-Type": mime}
        if etag:
            headers["ETag"] = etag
        if (
            not phase["changed"]
            and request.headers.get("If-None-Match") == '"one"'
            and url.endswith("/logo.png")
            and "cdn" not in url
        ):
            return httpx.Response(304, headers={"ETag": '"one"'})
        return httpx.Response(status, content=body, headers=headers)

    pool = ImagePool(tmp_path / "images")
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        first = pool.ensure("https://example.com/logo.png", client=client, fresh_seconds=0)
        second = pool.ensure("https://cdn.example.com/logo.png", client=client, fresh_seconds=0)
        assert first.status == "OK" and second.status == "OK"
        assert first.sha256 == second.sha256
        assert pool.stats()["objects"] == 1
        assert pool.stats()["duplicateHits"] == 1
        assert pool.stats()["bytes"] == len(PNG)
        before = calls["n"]
        again = pool.ensure("https://example.com/logo.png", client=client, fresh_seconds=10_000)
        assert again.reused is True
        assert calls["n"] == before
        phase["changed"] = True
        bodies["https://example.com/logo.png"] = (200, PNG_B, "image/png", '"next"')
        changed = pool.ensure("https://example.com/logo.png", client=client, fresh_seconds=0)
        assert changed.sha256 != first.sha256
        assert pool.stats()["objects"] == 2
        missing = pool.ensure("https://example.com/missing.png", client=client)
        denied = pool.ensure("https://example.com/denied.png", client=client)
        bad = pool.ensure("https://example.com/page.png", client=client)
        huge = pool.ensure("https://example.com/huge.png", client=client)
        gif = pool.ensure("https://example.com/anim.gif", client=client)
        svg = pool.ensure("https://example.com/icon.svg", client=client)
        assert missing.failure_code == "NOT_FOUND"
        assert denied.failure_code == "FORBIDDEN"
        assert bad.failure_code == "BAD_TYPE"
        assert huge.failure_code == "TOO_LARGE"
        assert gif.status == "OK" and gif.extension == "gif"
        assert svg.status == "OK"
        stored = (tmp_path / "images" / "objects" / svg.sha256[:2] / f"{svg.sha256}.svg").read_bytes()
        assert b"<script" not in stored.lower()
        object_path = tmp_path / "images" / "objects" / first.sha256[:2] / f"{first.sha256}.png"
        object_path.unlink()
        assert pool.cache_state("https://cdn.example.com/logo.png")[0] == "failed"
        restored = pool.ensure("https://cdn.example.com/logo.png", client=client, fresh_seconds=0)
        assert restored.status == "OK"
        assert object_path.is_file()
    finally:
        client.close()
        pool.close()


def test_concurrent_download_writes_one_object(tmp_path: Path, public_net) -> None:
    del public_net
    calls = {"n": 0}
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            calls["n"] += 1
        threading.Event().wait(0.2)
        return httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})

    pool = ImagePool(tmp_path / "images")
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        results: list = []

        def work() -> None:
            results.append(pool.ensure("https://example.com/same.png", client=client, fresh_seconds=0))

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert calls["n"] == 1
        assert len({item.sha256 for item in results}) == 1
        assert pool.stats()["objects"] == 1
    finally:
        client.close()
        pool.close()


def test_present_uses_local_media_and_keeps_original_markdown(tmp_path: Path, public_net) -> None:
    del public_net
    root = tmp_path / "images"
    pool = ImagePool(root)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
            if "logo" in str(request.url)
            else httpx.Response(404)
        ),
        follow_redirects=False,
    )
    try:
        logo = "https://raw.githubusercontent.com/octo/hello/main/assets/logo.png"
        pool.ensure(logo, client=client)
        pool.ensure("https://example.com/missing.png", client=client)
    finally:
        client.close()
        pool.close()
    source = "![Logo](./assets/logo.png)\n\n![Gone](https://example.com/missing.png)\n"
    html, summary = present_github_readme(
        "markdown",
        source,
        owner="octo",
        repo="hello",
        branch="main",
        image_root=root,
    )
    assert "techvault://app/media/" in html
    assert "https://" not in html
    assert "图片未能加载" in html
    assert "<script" not in html.lower()
    assert summary["cached"] == 1
    assert summary["failed"] == 1
    assert source.startswith("![Logo]")
    media = html.split('src="', 1)[1].split('"', 1)[0]
    name = media.rsplit("/", 1)[-1]
    resolved = resolve_media_file(root, f"/media/{name}")
    assert resolved is not None
    path, sha = resolved
    assert file_matches_hash(path, sha)
    assert resolve_media_file(root, "/media/../secrets.png") is None
    assert resolve_media_file(root, "/media/" + ("a" * 64) + ".png") is None


def test_svg_script_is_not_served_as_markup() -> None:
    cleaned = sanitize_svg(SVG_SCRIPT)
    assert cleaned is not None
    assert b"<script" not in cleaned.lower()
    assert b"<svg" in cleaned.lower()
    assert sanitize_svg(SVG) is not None


def test_archive_caches_images_without_failing_or_rewriting_readme(tmp_path: Path, public_net) -> None:
    del public_net
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    raw = "https://raw.githubusercontent.com/octo/empty/main/logo.png"
    try:
        with respx.mock:
            respx.get("https://api.github.com/repos/octo/empty").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": 50,
                        "name": "empty",
                        "full_name": "octo/empty",
                        "html_url": "https://github.com/octo/empty",
                        "description": "sample",
                        "stargazers_count": 1,
                        "default_branch": "main",
                        "license": None,
                        "owner": {"login": "octo"},
                        "topics": [],
                    },
                )
            )
            respx.get("https://api.github.com/repos/octo/empty/releases/latest").mock(
                return_value=httpx.Response(404)
            )
            respx.get("https://api.github.com/repos/octo/empty/releases?per_page=100").mock(
                return_value=httpx.Response(200, json=[])
            )
            respx.get("https://api.github.com/repos/octo/empty/readme").mock(
                return_value=httpx.Response(200, text="![Logo](./logo.png)\n")
            )
            respx.get("https://api.github.com/repos/octo/empty/zipball/main").mock(
                return_value=httpx.Response(200, content=b"PK\x03\x04snapshot")
            )
            respx.get(raw).mock(return_value=httpx.Response(404))
            result = ArchiveService(context.connection).archive_repository("https://github.com/octo/empty")
        assert result.status == "COMPLETED"
        snapshot_id = context.connection.execute("SELECT current_snapshot_id FROM repositories").fetchone()[0]
        layout = PathLayout(tmp_path / "archive")
        repo_id = int(context.connection.execute("SELECT id FROM repositories").fetchone()["id"])
        readme = layout.readme_file_at(repository_dir_for(context.connection, layout, repository_id=repo_id))
        assert "![Logo](./logo.png)" in readme.read_text(encoding="utf-8")
        assert "techvault://" not in readme.read_text(encoding="utf-8")
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=PNG, headers={"Content-Type": "image/png"})
            ),
            follow_redirects=False,
        )
        try:
            report = cache_repository_readme(
                context.connection,
                repo_id,
                image_root=tmp_path / "pool",
                http_client=client,
                fresh_seconds=0,
            )
        finally:
            client.close()
        assert report.cached == 1
        assert report.failed == 0
        assert context.connection.execute("SELECT current_snapshot_id FROM repositories").fetchone()[0] == snapshot_id
        html, summary = present_github_readme(
            "markdown",
            readme.read_text(encoding="utf-8"),
            owner="octo",
            repo="empty",
            branch="main",
            image_root=tmp_path / "pool",
        )
        assert summary["cached"] == 1
        assert "techvault://app/media/" in html
        assert hashlib.sha256(PNG).hexdigest() in html
    finally:
        context.close()


def test_redirect_to_loopback_is_rejected(monkeypatch) -> None:
    def allow(url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if host == "example.com":
            return urlsplit(url).scheme.lower() in {"http", "https"}
        return is_public_http_url(url)

    monkeypatch.setattr("techvault.readme_images.fetch.is_public_http_url", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret.png"})
        raise AssertionError("loopback was requested")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        result = fetch_image(client, "https://example.com/logo.png")
    finally:
        client.close()
    assert result.ok is False
    assert result.failure_code == "UNSAFE_URL"
