import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest
import respx

from techvault.archive.downloader import CHUNK_BYTES, FailureCode, FileState
from techvault.archive.paths import PathLayout
from techvault.db.connection import connect
from techvault.db.migrate import apply_migrations, default_migrations_dir
from techvault.db.release_store import save_release_lookup
from techvault.github.client import GitHubClient
from techvault.github.models import (
    LookupOutcome,
    ReleaseAsset,
    ReleaseLookupResult,
    ReleaseMetadata,
    RepositoryMetadata,
)
from techvault.offline.gate import HtmlIntegrityError, assert_offline_html
from techvault.offline.images import download_page_image, image_kind, media_filename
from techvault.offline.links import neutralize_unsafe_urls, rewrite_github_urls
from techvault.offline.pages import PageFailure, PageStatus, build_offline_pages
from techvault.offline.sanitize import sanitize_html

TOKEN = "ghp_supersecrettokenvalue"
README_URL = "https://api.github.com/repos/octo/hello/readme"
MARKDOWN_URL = "https://api.github.com/markdown"
REPO_URL = "https://github.com/octo/hello"


def _connection(tmp_path: Path):
    connection = connect(tmp_path / "techvault.sqlite")
    apply_migrations(connection, default_migrations_dir())
    return connection


def _repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        id=123,
        owner="octo",
        name="hello",
        full_name="octo/hello",
        html_url=REPO_URL,
        description="A repository",
        stargazers_count=1,
        license="MIT",
        default_branch="main",
    )


def _release(body: str = "", assets: tuple[ReleaseAsset, ...] = ()) -> ReleaseMetadata:
    return ReleaseMetadata(
        github_release_id=456,
        tag_name="v2.0.0",
        name="Hello release",
        body=body,
        html_url="https://github.com/octo/hello/releases/tag/v2.0.0",
        published_at="2026-02-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        prerelease=False,
        draft=False,
        tarball_url="https://api.github.com/repos/octo/hello/tarball/v2.0.0",
        zipball_url="https://api.github.com/repos/octo/hello/zipball/v2.0.0",
        assets=assets,
    )


def _save(connection, release: ReleaseMetadata | None = None) -> tuple[int, int | None]:
    if release is None:
        result = ReleaseLookupResult(LookupOutcome.NO_RELEASES, _repository(), None, 0, None)
    else:
        result = ReleaseLookupResult(LookupOutcome.STABLE, _repository(), release, None, None)
    save_release_lookup(connection, result)
    repository_id = int(connection.execute("SELECT id FROM repositories").fetchone()["id"])
    stored = connection.execute(
        "SELECT id FROM releases WHERE github_release_id = 456"
    ).fetchone()
    release_id = None if stored is None else int(stored["id"])
    if release_id is not None:
        connection.execute(
            "UPDATE repositories SET current_release_id = ? WHERE id = ?",
            (release_id, repository_id),
        )
    return repository_id, release_id


def _layout(tmp_path: Path) -> PathLayout:
    return PathLayout(tmp_path / "a")


def _index(layout: PathLayout) -> Path:
    return layout.page_index("octo", "hello", 123)


def _release_page(layout: PathLayout) -> Path:
    return layout.page_release("octo", "hello", 123)


def _build(
    connection,
    layout: PathLayout,
    repository_id: int,
    *,
    readme: str = "<p>Hi</p>",
    readme_status: int = 200,
    markdown: httpx.Response | str | None = None,
    images: dict[str, bytes] | None = None,
    image_status: dict[str, int] | None = None,
    assert_all_called: bool = True,
    **kwargs,
):
    with respx.mock(assert_all_called=assert_all_called) as router:
        if readme_status == 200:
            router.get(README_URL).mock(return_value=httpx.Response(readme_status, text=readme))
        else:
            router.get(README_URL).mock(return_value=httpx.Response(readme_status, json={"message": "no"}))
        if markdown is not None:
            response = markdown if isinstance(markdown, httpx.Response) else httpx.Response(200, text=markdown)
            router.post(MARKDOWN_URL).mock(return_value=response)
        for url, body in (images or {}).items():
            status = (image_status or {}).get(url, 200)
            router.get(url).mock(return_value=httpx.Response(status, content=body))
        with GitHubClient(token=TOKEN) as client:
            status = build_offline_pages(connection, client, layout, repository_id, **kwargs)
        captured = [
            {
                "method": call.request.method,
                "url": str(call.request.url),
                "headers": {key.lower(): value for key, value in call.request.headers.items()},
                "content": bytes(call.request.content),
            }
            for call in router.calls
        ]
        return status, captured


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.images: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        self.tags.append(name)
        values = {key.lower(): value or "" for key, value in attrs}
        if name == "img":
            self.images.append(values.get("src", ""))
        if name == "a":
            self.links.append(values.get("href", ""))


def test_readme_html_is_fetched(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        status, _ = _build(connection, layout, repository_id, readme="<h1>Hello</h1><p>Body text</p>")
        page = _index(layout).read_text(encoding="utf-8")
        assert status == PageStatus.COMPLETED
        assert "Hello" in page
        assert "Body text" in page
        assert "octo/hello" in page
        assert REPO_URL in page
        assert "A repository" in page
    finally:
        connection.close()


def test_readme_sends_html_accept_header() -> None:
    with respx.mock() as router:
        route = router.get(README_URL).mock(return_value=httpx.Response(200, text="<p>Hi</p>"))
        with GitHubClient(token=TOKEN) as client:
            html = client.get_readme_html("octo", "hello")
    assert html == "<p>Hi</p>"
    headers = route.calls[0].request.headers
    assert headers["Accept"] == "application/vnd.github.html+json"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"


def test_readme_empty_body_is_not_unavailable(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        _build(connection, layout, repository_id, readme="  \n")
        page = _index(layout).read_text(encoding="utf-8")
        assert "README unavailable" not in page
        assert "SECRET README" not in page
    finally:
        connection.close()


def test_readme_http_failure_shows_unavailable(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        status, _ = _build(connection, layout, repository_id, readme_status=500)
        page = _index(layout).read_text(encoding="utf-8")
        stored = connection.execute(
            "SELECT status, failure_code FROM offline_pages WHERE page_type = 'REPOSITORY'"
        ).fetchone()
        assert status == PageStatus.COMPLETED
        assert stored["status"] == PageStatus.COMPLETED
        assert stored["failure_code"] == PageFailure.README
        assert "README unavailable" in page
        assert "SECRET README" not in page
    finally:
        connection.close()


def test_empty_release_body_does_not_call_markdown(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection, _release(body="   "))
        layout = _layout(tmp_path)
        _build(connection, layout, repository_id, readme="<p>Hi</p>")
        page = _release_page(layout).read_text(encoding="utf-8")
        assert "v2.0.0" in page
        assert "<pre>" not in page
    finally:
        connection.close()


def test_release_markdown_gfm_success(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        body = "## Shipped\n\nUse the archive."
        repository_id, _ = _save(connection, _release(body=body))
        layout = _layout(tmp_path)
        _, calls = _build(connection, layout, repository_id, markdown="<h2>Shipped</h2><p>Use the archive.</p>")
        page = _release_page(layout).read_text(encoding="utf-8")
        post = next(call for call in calls if call["method"] == "POST")
        payload = json.loads(post["content"])
        assert payload == {"text": body, "mode": "gfm", "context": "octo/hello"}
        assert post["headers"]["authorization"] == f"Bearer {TOKEN}"
        assert "Shipped" in page
        assert "Use the archive." in page
    finally:
        connection.close()


def test_markdown_failure_keeps_raw_body(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        body = "Keep this raw body"
        repository_id, release_id = _save(connection, _release(body=body))
        layout = _layout(tmp_path)
        _build(connection, layout, repository_id, markdown=httpx.Response(500, json={"message": "no"}))
        stored = connection.execute("SELECT body FROM releases WHERE id = ?", (release_id,)).fetchone()
        row = connection.execute(
            "SELECT status, failure_code FROM offline_pages WHERE page_type = 'RELEASE'"
        ).fetchone()
        assert stored["body"] == body
        assert row["status"] == PageStatus.COMPLETED
        assert row["failure_code"] == PageFailure.MARKDOWN
    finally:
        connection.close()


def test_markdown_failure_escapes_raw_html(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        body = "Before <script>alert(1)</script> after"
        repository_id, release_id = _save(connection, _release(body=body))
        layout = _layout(tmp_path)
        _build(connection, layout, repository_id, markdown=httpx.Response(500, json={"message": "no"}))
        page = _release_page(layout).read_text(encoding="utf-8")
        stored = connection.execute("SELECT body FROM releases WHERE id = ?", (release_id,)).fetchone()
        assert stored["body"] == body
        assert "<script>" not in page
        assert "&lt;script&gt;" in page
        assert "<pre>" in page
    finally:
        connection.close()


def test_sanitize_removes_script_and_content() -> None:
    assert "SECRET" not in sanitize_html("<p>ok</p><script>SECRET</script>")
    assert "<script" not in sanitize_html("<p>ok</p><script>SECRET</script>").lower()


def test_sanitize_removes_style_and_content() -> None:
    result = sanitize_html("<style>SECRET</style><p>ok</p>")
    assert "SECRET" not in result
    assert "<style" not in result.lower()


def test_sanitize_removes_iframe_and_content() -> None:
    result = sanitize_html("<iframe>SECRET</iframe><p>ok</p>")
    assert "SECRET" not in result
    assert "<iframe" not in result.lower()


def test_sanitize_removes_object_and_content() -> None:
    result = sanitize_html("<object>SECRET</object><p>ok</p>")
    assert "SECRET" not in result
    assert "<object" not in result.lower()


def test_sanitize_removes_embed_and_content() -> None:
    result = sanitize_html("<div><embed>SECRET</embed></div><p>ok</p>")
    assert "SECRET" not in result
    assert "<embed" not in result.lower()


def test_sanitize_removes_form_and_content() -> None:
    result = sanitize_html('<form action="https://example.com">SECRET</form><p>ok</p>')
    assert "SECRET" not in result
    assert "<form" not in result.lower()


def test_sanitize_removes_event_handlers() -> None:
    result = sanitize_html('<p onclick="alert(1)" onerror="x">ok</p>')
    assert "onclick" not in result
    assert "onerror" not in result
    assert "<p>ok</p>" in result


def test_neutralize_records_the_scheme_and_drops_the_query() -> None:
    html, blocked = neutralize_unsafe_urls(
        '<a href="javascript:alert(1)?token=secret">Click here</a>'
        '<img src="data:image/png;base64,aaaa?token=secret" alt="pic">'
        '<a href="https://github.com/octo/hello">source</a>'
    )
    assert "Click here" in html
    assert "source" in html
    assert "[Blocked unsafe image]" in html
    assert "javascript:" not in html.lower()
    assert "data:" not in html.lower()
    assert "token" not in html.lower()
    found = {(item.scheme, item.tag, item.attribute) for item in blocked}
    assert ("javascript", "a", "href") in found
    assert ("data", "img", "src") in found
    assert all("token" not in item.url for item in blocked)


def test_sanitize_removes_javascript_url() -> None:
    result = sanitize_html(
        '<a href="javascript:alert(1)">j</a><a href="vbscript:msg">v</a><a href="file:///c:/x">f</a>'
    )
    assert "javascript:" not in result.lower()
    assert "vbscript:" not in result.lower()
    assert "file:" not in result.lower()


def test_sanitize_removes_data_url() -> None:
    result = sanitize_html('<img src="data:image/png,xxxx" alt="pic"><a href="data:text/html,hi">d</a>')
    assert "data:" not in result.lower()


def test_sanitize_unwraps_disallowed_tags() -> None:
    result = sanitize_html("<div>Hello <span>there</span></div>")
    assert "<div" not in result
    assert "<span" not in result
    assert "Hello" in result
    assert "there" in result


def test_sanitize_keeps_allowed_tags() -> None:
    result = sanitize_html(
        "<h1>T</h1><p>P</p><ul><li>L</li></ul><strong>S</strong><em>E</em>"
        "<del>D</del><pre><code>C</code></pre><blockquote>B</blockquote><hr><br>"
    )
    for tag in ("h1", "p", "ul", "li", "strong", "em", "del", "pre", "code", "blockquote", "hr", "br"):
        assert f"<{tag}" in result


def test_sanitize_keeps_allowed_attributes() -> None:
    result = sanitize_html(
        '<table><tr><td colspan="2" rowspan="3">c</td></tr></table>'
        '<a href="https://example.com/a">e</a>'
        '<img src="https://raw.githubusercontent.com/o/r/a.png" alt="pic">'
    )
    assert 'colspan="2"' in result
    assert 'rowspan="3"' in result
    assert 'href="https://example.com/a"' in result
    assert 'src="https://raw.githubusercontent.com/o/r/a.png"' in result
    assert 'alt="pic"' in result


def test_sanitize_removes_style_and_class() -> None:
    result = sanitize_html('<p class="x" style="color:red" id="z">ok</p>')
    assert "class" not in result
    assert "style" not in result
    assert "id=" not in result
    assert "<p>ok</p>" in result


def test_rewrite_relative_link() -> None:
    result = rewrite_github_urls(
        '<a href="./docs/example.md">Docs</a><a href="../blob/main/file.txt">File</a>',
        REPO_URL,
    )
    assert 'href="https://github.com/octo/hello/docs/example.md"' in result
    assert 'href="https://github.com/octo/blob/main/file.txt"' in result


def test_rewrite_root_relative_link() -> None:
    result = rewrite_github_urls('<a href="/image.png">Image</a>', REPO_URL)
    assert 'href="https://github.com/octo/hello/image.png"' in result


def test_rewrite_absolute_github_link() -> None:
    url = "https://github.com/octo/hello/blob/main/README.md"
    result = rewrite_github_urls(f'<a href="{url}">Readme</a>', REPO_URL)
    assert f'href="{url}"' in result


def test_rewrite_external_link() -> None:
    result = rewrite_github_urls('<a href="https://example.com/a">Out</a>', REPO_URL)
    assert 'href="https://example.com/a"' in result


def test_rewrite_mailto_link() -> None:
    result = rewrite_github_urls('<a href="mailto:dev@example.com">Mail</a>', REPO_URL)
    assert 'href="mailto:dev@example.com"' in result


def test_image_kind_raw_host() -> None:
    assert image_kind("https://raw.githubusercontent.com/octo/hello/main/a.png") == "image"


def test_image_kind_camo_host() -> None:
    assert image_kind("https://camo.githubusercontent.com/abc/a.png") == "image"


def test_image_kind_private_user_images() -> None:
    assert image_kind("https://private-user-images.githubusercontent.com/abc/a.png") == "image"


def test_image_kind_user_attachments() -> None:
    assert image_kind("https://github.com/user-attachments/assets/abc.png") == "attachment"


def test_image_kind_owner_avatar() -> None:
    assert image_kind("https://github.com/images/avatar/octo.png") == "avatar"


def _expect_local_image(tmp_path: Path, url: str, body: bytes) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        _build(
            connection,
            layout,
            repository_id,
            readme=f'<p>Hi</p><img src="{url}" alt="pic">',
            images={url: body},
        )
        page = _index(layout).read_text(encoding="utf-8")
        parsed = _Tags()
        parsed.feed(page)
        assert parsed.images
        assert all(src.startswith("media/") for src in parsed.images)
        image = _index(layout).parent / parsed.images[0]
        assert image.is_file()
        assert image.read_bytes() == body
        row = connection.execute("SELECT sha256, actual_size, status FROM page_assets").fetchone()
        assert row["status"] == PageStatus.COMPLETED
        assert row["sha256"] == hashlib.sha256(body).hexdigest()
        assert row["actual_size"] == len(body)
        assert "https://" not in "".join(parsed.images)
    finally:
        connection.close()


def test_localizes_raw_githubusercontent_image(tmp_path: Path) -> None:
    _expect_local_image(tmp_path, "https://raw.githubusercontent.com/octo/hello/main/a.png", b"raw-bytes")


def test_localizes_camo_image(tmp_path: Path) -> None:
    _expect_local_image(tmp_path, "https://camo.githubusercontent.com/abc/a.png", b"camo-bytes")


def test_localizes_private_user_image(tmp_path: Path) -> None:
    _expect_local_image(
        tmp_path,
        "https://private-user-images.githubusercontent.com/abc/a.png",
        b"private-bytes",
    )


def test_localizes_user_attachment_image(tmp_path: Path) -> None:
    _expect_local_image(tmp_path, "https://github.com/user-attachments/assets/abc.png", b"attach-bytes")


def test_localizes_owner_avatar(tmp_path: Path) -> None:
    _expect_local_image(tmp_path, "https://github.com/images/avatar/octo.png", b"avatar-bytes")


def test_non_whitelisted_image_becomes_text_link(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        _build(
            connection,
            layout,
            repository_id,
            readme='<img src="https://example.com/secret.png" alt="ext">',
        )
        page = _index(layout).read_text(encoding="utf-8")
        parsed = _Tags()
        parsed.feed(page)
        assert parsed.images == []
        assert "https://example.com/secret.png" in parsed.links
        assert "[External image]" in page
        assert connection.execute("SELECT COUNT(*) FROM page_assets").fetchone()[0] == 0
    finally:
        connection.close()


def test_only_first_avatar_is_localized(tmp_path: Path) -> None:
    first = "https://github.com/images/avatar/one.png"
    second = "https://github.com/images/avatar/two.png"
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        _, calls = _build(
            connection,
            layout,
            repository_id,
            readme=f'<img src="{first}" alt="one"><img src="{second}" alt="two">',
            images={first: b"one"},
            assert_all_called=False,
        )
        page = _index(layout).read_text(encoding="utf-8")
        parsed = _Tags()
        parsed.feed(page)
        assert parsed.images == [f"media/{media_filename(first)}"]
        assert second in parsed.links
        assert "[External image]" in page
        requested = {call["url"] for call in calls}
        assert first in requested
        assert second not in requested
    finally:
        connection.close()


def test_duplicate_image_names_are_deterministic(tmp_path: Path) -> None:
    first = "https://raw.githubusercontent.com/octo/hello/main/icon.png"
    second = "https://raw.githubusercontent.com/octo/hello/other/icon.png"
    assert media_filename(first) == media_filename(first)
    assert media_filename(first) != media_filename(second)
    assert ".." not in media_filename(first)
    assert media_filename(first).endswith("_icon.png")
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        _build(
            connection,
            layout,
            repository_id,
            readme=f'<img src="{first}" alt="a"><img src="{second}" alt="b">',
            images={first: b"aaa", second: b"bbb"},
        )
        media = layout.page_media_dir("octo", "hello", 123)
        assert (media / media_filename(first)).read_bytes() == b"aaa"
        assert (media / media_filename(second)).read_bytes() == b"bbb"
    finally:
        connection.close()


def test_image_download_streams_in_one_mebibyte_chunks(monkeypatch, tmp_path: Path) -> None:
    seen: list[tuple[int | None, int]] = []
    original = httpx.Response.iter_bytes

    def wrapped(self, chunk_size=None):
        count = 0
        for chunk in original(self, chunk_size=chunk_size):
            count += 1
            yield chunk
        seen.append((chunk_size, count))

    monkeypatch.setattr(httpx.Response, "iter_bytes", wrapped)
    url = "https://raw.githubusercontent.com/octo/hello/main/big.png"
    body = b"a" * (CHUNK_BYTES + 1)
    layout = _layout(tmp_path)
    final = layout.page_media_file("octo", "hello", 123, media_filename(url))
    with respx.mock() as router:
        router.get(url).mock(return_value=httpx.Response(200, content=body))
        with GitHubClient(token=TOKEN) as client:
            result = download_page_image(
                client,
                layout,
                job_id="page1",
                asset_id=1,
                url=url,
                final_path=final,
            )
    assert result.state == FileState.COMPLETED
    assert (CHUNK_BYTES, 2) in seen
    assert final.read_bytes() == body


def test_image_download_double_sha256_matches_disk(tmp_path: Path) -> None:
    url = "https://raw.githubusercontent.com/octo/hello/main/a.png"
    body = b"image-bytes"
    layout = _layout(tmp_path)
    final = layout.page_media_file("octo", "hello", 123, media_filename(url))
    with respx.mock() as router:
        router.get(url).mock(return_value=httpx.Response(200, content=body))
        with GitHubClient() as client:
            result = download_page_image(
                client, layout, job_id="page1", asset_id=1, url=url, final_path=final
            )
    assert result.local_sha256 == hashlib.sha256(final.read_bytes()).hexdigest()
    assert result.actual_size == len(body)
    assert result.local_sha256 == hashlib.sha256(body).hexdigest()


def test_image_reread_mismatch_does_not_publish(tmp_path: Path) -> None:
    url = "https://raw.githubusercontent.com/octo/hello/main/a.png"
    layout = _layout(tmp_path)
    final = layout.page_media_file("octo", "hello", 123, media_filename(url))

    def corrupt(path: Path) -> None:
        path.write_bytes(path.read_bytes() + b"x")

    with respx.mock() as router:
        router.get(url).mock(return_value=httpx.Response(200, content=b"abc"))
        with GitHubClient() as client:
            result = download_page_image(
                client,
                layout,
                job_id="page1",
                asset_id=7,
                url=url,
                final_path=final,
                after_stream=corrupt,
            )
    assert result.failure_code == FailureCode.CHECKSUM_FAILURE
    assert not final.exists()
    assert layout.part_file("page1", 7).exists()


def test_image_http_failure_degrades_to_text_link(tmp_path: Path) -> None:
    url = "https://raw.githubusercontent.com/octo/hello/main/missing.png"
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        status, _ = _build(
            connection,
            layout,
            repository_id,
            readme=f'<img src="{url}" alt="gone">',
            images={url: b""},
            image_status={url: 404},
        )
        page = _index(layout).read_text(encoding="utf-8")
        parsed = _Tags()
        parsed.feed(page)
        row = connection.execute("SELECT status, failure_code FROM page_assets").fetchone()
        assert status == PageStatus.COMPLETED
        assert parsed.images == []
        assert url in parsed.links
        assert row["status"] == PageStatus.FAILED
        assert row["failure_code"] == FailureCode.NOT_FOUND
    finally:
        connection.close()


def test_image_download_strips_authorization(tmp_path: Path) -> None:
    url = "https://raw.githubusercontent.com/octo/hello/main/a.png"
    layout = _layout(tmp_path)
    final = layout.page_media_file("octo", "hello", 123, media_filename(url))
    with respx.mock() as router:
        route = router.get(url).mock(return_value=httpx.Response(200, content=b"abc"))
        with GitHubClient(token=TOKEN) as client:
            result = download_page_image(
                client, layout, job_id="page1", asset_id=1, url=url, final_path=final
            )
    assert result.state == FileState.COMPLETED
    assert "authorization" not in {name.lower() for name in route.calls[0].request.headers}


def test_gate_rejects_script_residue() -> None:
    with pytest.raises(HtmlIntegrityError):
        assert_offline_html("<html><body><script>x</script></body></html>", {})


def test_gate_rejects_javascript_residue() -> None:
    with pytest.raises(HtmlIntegrityError) as caught:
        assert_offline_html('<html><body><a href="javascript:alert(1)">x</a></body></html>', {})
    assert any(
        "dangerous URL scheme=javascript" in item and "attribute=href" in item
        for item in caught.value.violations
    )


def test_gate_allows_prose_that_is_not_a_url(tmp_path: Path) -> None:
    media = tmp_path / "a.png"
    media.write_bytes(b"png")
    assert_offline_html(
        '<html><body><p>NFT Data: listed</p><img src="media/a.png" alt="NFT Data:"></body></html>',
        {"media/a.png": media},
    )


def test_gate_rejects_external_js() -> None:
    with pytest.raises(HtmlIntegrityError):
        assert_offline_html('<html><body><script src="https://cdn.example/app.js"></script></body></html>', {})


def test_gate_rejects_external_css() -> None:
    with pytest.raises(HtmlIntegrityError):
        assert_offline_html(
            '<html><head><link rel="stylesheet" href="https://cdn.example/app.css"></head></html>',
            {},
        )


def test_gate_rejects_remote_img() -> None:
    with pytest.raises(HtmlIntegrityError):
        assert_offline_html('<html><body><img src="https://example.com/a.png"></body></html>', {})


def test_offline_page_passes_through_building(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        seen: list[str] = []

        def observe(status: str) -> None:
            rows = [row["status"] for row in connection.execute("SELECT status FROM offline_pages")]
            seen.append(status)
            seen.extend(rows)

        layout = _layout(tmp_path)
        _build(connection, layout, repository_id, observe=observe)
        assert seen[0] == PageStatus.BUILDING
        assert set(seen[1:]) == {PageStatus.BUILDING}
    finally:
        connection.close()


def test_offline_page_completed_status(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        status, _ = _build(connection, layout, repository_id)
        rows = connection.execute("SELECT page_type, status, relative_path FROM offline_pages").fetchall()
        assert status == PageStatus.COMPLETED
        assert {row["status"] for row in rows} == {PageStatus.COMPLETED}
        assert _index(layout).is_file()
        assert _release_page(layout).is_file()
        assert all(row["relative_path"] for row in rows)
    finally:
        connection.close()


def test_offline_page_failed_status_does_not_write_html(tmp_path: Path, monkeypatch) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection, _release(body="hello"))
        layout = _layout(tmp_path)

        def poisoned(_html: str) -> str:
            return "<script>alert(1)</script>"

        monkeypatch.setattr("techvault.offline.pages.sanitize_html", poisoned)
        status, _ = _build(connection, layout, repository_id, markdown="<p>hello</p>")
        rows = [row["status"] for row in connection.execute("SELECT status FROM offline_pages")]
        assert status == PageStatus.FAILED
        assert rows == [PageStatus.FAILED, PageStatus.FAILED]
        assert not _index(layout).exists()
        assert not _release_page(layout).exists()
    finally:
        connection.close()


def test_missing_current_release_has_no_fake_link(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        _build(connection, layout, repository_id, readme="<p>Hi</p>")
        index = _index(layout).read_text(encoding="utf-8")
        release = _release_page(layout).read_text(encoding="utf-8")
        assert "该项目没有可用的正式 Release。" in index
        assert 'href="release.html"' not in index
        assert "该项目没有可用的正式 Release。" in release
        assert "v2.0.0" not in release
    finally:
        connection.close()


def test_current_release_links_to_release_page(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, release_id = _save(connection, _release(body=""))
        before = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        layout = _layout(tmp_path)
        _build(connection, layout, repository_id)
        index = _index(layout).read_text(encoding="utf-8")
        after = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        assert 'href="release.html"' in index
        assert "v2.0.0" in index
        assert after == before == release_id
    finally:
        connection.close()


def _write_release_file(connection, layout: PathLayout, release_id: int, *, name: str, body: bytes, state: str) -> None:
    final = layout.release_asset_file("octo", "hello", 123, "v2", 50, name)
    relative = None
    digest = None
    if state == FileState.COMPLETED:
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(body)
        relative = str(final.relative_to(layout.archive_root))
        digest = hashlib.sha256(body).hexdigest()
    now = "2026-09-30T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO release_files (
            release_id, kind, github_asset_id, name, state, relative_path,
            local_sha256, actual_size, created_at, updated_at
        ) VALUES (?, 'asset', 50, ?, ?, ?, ?, ?, ?, ?)
        """,
        (release_id, name, state, relative, digest, len(body) if digest else None, now, now),
    )


def test_incomplete_release_file_is_unavailable(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, release_id = _save(connection, _release(body=""))
        layout = _layout(tmp_path)
        assert release_id is not None
        _write_release_file(connection, layout, release_id, name="missing.bin", body=b"nope", state=FileState.FAILED)
        _build(connection, layout, repository_id)
        page = _release_page(layout).read_text(encoding="utf-8")
        assert "missing.bin: Unavailable" in page
        assert "missing.bin</a>" not in page
    finally:
        connection.close()


def test_completed_release_file_is_linked(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, release_id = _save(connection, _release(body=""))
        layout = _layout(tmp_path)
        assert release_id is not None
        body = b"asset-bytes"
        _write_release_file(connection, layout, release_id, name="app.exe", body=body, state=FileState.COMPLETED)
        _build(connection, layout, repository_id)
        page = _release_page(layout).read_text(encoding="utf-8")
        parsed = _Tags()
        parsed.feed(page)
        linked = [href for href in parsed.links if "app.exe" in href]
        assert linked
        target = (_release_page(layout).parent / linked[0]).resolve()
        assert target.read_bytes() == body
    finally:
        connection.close()


def test_offline_failure_preserves_current_release_and_files(tmp_path: Path, monkeypatch) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, release_id = _save(connection, _release(body=""))
        layout = _layout(tmp_path)
        assert release_id is not None
        body = b"phase3-zip"
        final = layout.release_source_file("octo", "hello", 123, "v2", kind="zip")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(body)
        before = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        monkeypatch.setattr("techvault.offline.pages.sanitize_html", lambda _html: "<script>alert(1)</script>")
        status, _ = _build(connection, layout, repository_id)
        after = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        assert status == PageStatus.FAILED
        assert after == before == release_id
        assert final.read_bytes() == body
        assert not _index(layout).exists()
    finally:
        connection.close()


def test_offline_repository_archive_real_disk(tmp_path: Path) -> None:
    image_url = "https://raw.githubusercontent.com/octo/hello/main/diagram.png"
    image_body = b"diagram-bytes"
    zip_body = b"zip-archive"
    asset_body = b"exe-bytes"
    connection = _connection(tmp_path)
    try:
        asset = ReleaseAsset(
            github_asset_id=50,
            name="app.exe",
            size=len(asset_body),
            content_type="application/octet-stream",
            state="uploaded",
            digest=None,
            browser_download_url="https://example.invalid/app.exe",
            created_at=None,
            updated_at=None,
        )
        pending = ReleaseAsset(
            github_asset_id=51,
            name="pending.bin",
            size=4,
            content_type="application/octet-stream",
            state="open",
            digest=None,
            browser_download_url="https://example.invalid/pending.bin",
            created_at=None,
            updated_at=None,
        )
        repository_id, release_id = _save(connection, _release(body="## Shipped", assets=(asset, pending)))
        layout = _layout(tmp_path)
        assert release_id is not None
        archive = layout.release_source_file("octo", "hello", 123, "v2", kind="zip")
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(zip_body)
        now = "2026-09-30T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO release_files (
                release_id, kind, name, state, relative_path, local_sha256, actual_size,
                created_at, updated_at
            ) VALUES (?, 'SOURCE_ZIP', 'source-code.zip', 'COMPLETED', ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                str(archive.relative_to(layout.archive_root)),
                hashlib.sha256(zip_body).hexdigest(),
                len(zip_body),
                now,
                now,
            ),
        )
        asset_path = layout.release_asset_file("octo", "hello", 123, "v2", 50, "app.exe")
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(asset_body)
        connection.execute(
            """
            UPDATE release_files
            SET state = 'COMPLETED', relative_path = ?, local_sha256 = ?, actual_size = ?
            WHERE name = 'app.exe'
            """,
            (
                str(asset_path.relative_to(layout.archive_root)),
                hashlib.sha256(asset_body).hexdigest(),
                len(asset_body),
            ),
        )
        before = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        readme = (
            "<h1>Hello</h1><script>SECRET_SCRIPT</script><p>Read me</p>"
            '<a href="./docs/example.md">Docs</a>'
            f'<img src="{image_url}" alt="diagram">'
        )
        status, calls = _build(
            connection,
            layout,
            repository_id,
            readme=readme,
            markdown="<h2>Shipped</h2>",
            images={image_url: image_body},
        )
        image_call = next(call for call in calls if image_url in call["url"])
        assert "authorization" not in image_call["headers"]
        assert status == PageStatus.COMPLETED
        assert before == release_id
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] == release_id
        index = _index(layout)
        release = _release_page(layout)
        assert index.is_file() and release.is_file()
        assert layout.page_media_dir("octo", "hello", 123).is_dir()
        index_html = index.read_text(encoding="utf-8")
        release_html = release.read_text(encoding="utf-8")
        for html in (index_html, release_html):
            parsed = _Tags()
            parsed.feed(html)
            assert "script" not in parsed.tags
            assert "iframe" not in parsed.tags
            assert "style" not in parsed.tags
            assert "javascript:" not in html.lower()
            assert "data:" not in html.lower()
            for src in parsed.images:
                image = index.parent / src
                assert image.is_file()
                digest = hashlib.sha256(image.read_bytes()).hexdigest()
                row = connection.execute(
                    "SELECT sha256, actual_size, status FROM page_assets WHERE sha256 = ?",
                    (digest,),
                ).fetchone()
                assert row["status"] == PageStatus.COMPLETED
                assert row["actual_size"] == image.stat().st_size
        assert "SECRET_SCRIPT" not in index_html
        assert "Hello" in index_html
        assert "https://github.com/octo/hello/docs/example.md" in index_html
        assert 'href="release.html"' in index_html
        assert "Shipped" in release_html
        assert "Source ZIP" in release_html
        assert "app.exe" in release_html
        assert "pending.bin: Unavailable" in release_html
        assert archive.read_bytes() == zip_body
        assert asset_path.read_bytes() == asset_body
        assert list(layout.pages_dir("octo", "hello", 123).rglob("*.part")) == []
        assert {
            row["status"] for row in connection.execute("SELECT status FROM offline_pages")
        } == {PageStatus.COMPLETED}
    finally:
        connection.close()


def test_unsafe_urls_are_blocked_without_failing_the_readme(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        repository_id, _ = _save(connection)
        layout = _layout(tmp_path)
        readme = """
        <p>Normal paragraph.</p>
        <p>NFT Data: listed</p>
        <a href="https://github.com/octo/hello">source</a>
        <a href="docs/example.md">Docs</a>
        <img src="images/test.png" alt="pic">
        <a href="javascript:alert(1)">Click here</a>
        <a href="vbscript:msg">scripted</a>
        <a href="file:///C:/Users/secret.txt">local file</a>
        <img src="javascript:alert(1)" alt="boom">
        <img src="file:///C:/Users/secret.png" alt="disk">
        <script>alert(1)</script>
        """
        status, _ = _build(connection, layout, repository_id, readme=readme)
        assert status == PageStatus.COMPLETED
        page = _index(layout).read_text(encoding="utf-8")
        lowered = page.lower()
        parsed = _Tags()
        parsed.feed(page)
        assert "Normal paragraph." in page
        assert "NFT Data:" in page
        assert "Click here" in page
        assert "local file" in page
        assert 'href="https://github.com/octo/hello"' in page
        assert 'href="https://github.com/octo/hello/docs/example.md"' in page
        assert "https://github.com/octo/hello/images/test.png" in page
        assert "[Blocked unsafe image]" in page
        assert "<script" not in lowered
        assert "javascript:" not in lowered
        assert "vbscript:" not in lowered
        assert "file:" not in lowered
        assert str(Path.cwd()) not in page
        assert all(not href.lower().startswith(("javascript:", "vbscript:", "file:", "data:")) for href in parsed.links)
        assert all(not src.lower().startswith(("javascript:", "vbscript:", "file:", "data:")) for src in parsed.images)
        row = connection.execute(
            "SELECT status, failure_code FROM offline_pages WHERE page_type = 'REPOSITORY'"
        ).fetchone()
        assert row["status"] == PageStatus.COMPLETED
        assert row["failure_code"] is None
    finally:
        connection.close()
