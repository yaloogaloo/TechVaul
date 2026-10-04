import hashlib
import sqlite3
from threading import Event

import httpx
import respx

from techvault.archive.downloader import FailureCode, FileState
from techvault.archive.paths import PathLayout
from techvault.archive.release_archive import JobStatus, archive_release
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

TOKEN = "ghp_supersecrettokenvalue"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(data: bytes) -> str:
    return "sha256:" + _sha(data)


def _database(tmp_path):
    connection = connect(tmp_path / "techvault.sqlite")
    apply_migrations(connection, default_migrations_dir())
    return connection


def _repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        id=123,
        owner="octo",
        name="hello",
        full_name="octo/hello",
        html_url="https://github.com/octo/hello",
        description="A repository",
        stargazers_count=1,
        license="MIT",
        default_branch="main",
    )


def _asset(asset_id: int, name: str, body: bytes, *, state: str = "uploaded", digest: str | None = None) -> ReleaseAsset:
    return ReleaseAsset(
        github_asset_id=asset_id,
        name=name,
        size=len(body),
        content_type="application/octet-stream",
        state=state,
        digest=digest,
        browser_download_url=f"https://release-assets.githubusercontent.com/assets/{asset_id}",
        created_at=None,
        updated_at=None,
    )


def _release(release_id: int, assets: tuple[ReleaseAsset, ...]) -> ReleaseMetadata:
    return ReleaseMetadata(
        github_release_id=release_id,
        tag_name="v2",
        name="Hello",
        body="raw",
        html_url="https://github.com/octo/hello/releases/tag/v2",
        published_at="2026-02-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        prerelease=False,
        draft=False,
        tarball_url="https://api.github.com/repos/octo/hello/tarball/v2",
        zipball_url="https://api.github.com/repos/octo/hello/zipball/v2",
        assets=assets,
    )


def _save(connection, release: ReleaseMetadata) -> tuple[int, int]:
    save_release_lookup(
        connection,
        ReleaseLookupResult(LookupOutcome.STABLE, _repository(), release, None, None),
    )
    repository_id = connection.execute("SELECT id FROM repositories").fetchone()["id"]
    release_id = connection.execute(
        "SELECT id FROM releases WHERE github_release_id = ?",
        (release.github_release_id,),
    ).fetchone()["id"]
    return int(repository_id), int(release_id)


def _payloads(release: ReleaseMetadata, contents: dict[int, bytes] | None = None) -> dict[str, bytes]:
    payloads = {
        release.zipball_url: b"zip-source",
        release.tarball_url: b"tar-source",
    }
    known = contents or {}
    for asset in release.assets:
        if asset.github_asset_id in known:
            payloads[asset.browser_download_url] = known[asset.github_asset_id]
    return payloads


def _route(router: respx.MockRouter, url: str, body: bytes, *, status: int = 200, headers: dict | None = None) -> None:
    response_headers = {"Content-Length": str(len(body))}
    if headers:
        response_headers.update(headers)
    router.get(url).mock(return_value=httpx.Response(status, content=body, headers=response_headers))


def _archive(
    connection,
    layout: PathLayout,
    repository_id: int,
    release_id: int,
    payloads: dict[str, bytes],
    *,
    assert_all_called: bool = True,
    **kwargs,
):
    with respx.mock(assert_all_called=assert_all_called) as router:
        for url, body in payloads.items():
            _route(router, url, body)
        with GitHubClient(token=TOKEN) as client:
            status = archive_release(
                connection,
                client,
                layout,
                repository_id=repository_id,
                release_id=release_id,
                **kwargs,
            )
    return status


def test_open_asset_is_not_downloaded_and_blocks_current(tmp_path) -> None:
    uploaded = b"ready"
    release = _release(
        456,
        (
            _asset(1, "app.exe", uploaded, digest=_digest(uploaded)),
            _asset(2, "pending.bin", b"wait", state="open", digest=None),
        ),
    )
    connection = _database(tmp_path)
    try:
        repository_id, release_id = _save(connection, release)
        payloads = _payloads(release, {1: uploaded})
        open_url = release.assets[1].browser_download_url
        with respx.mock(assert_all_called=False) as router:
            for url, body in payloads.items():
                if url != open_url:
                    _route(router, url, body)
            open_route = router.get(open_url).mock(return_value=httpx.Response(200, content=b"wait"))
            with GitHubClient(token=TOKEN) as client:
                status = archive_release(
                    connection,
                    client,
                    PathLayout(tmp_path / "archive"),
                    repository_id=repository_id,
                    release_id=release_id,
                )
        assert open_route.called is False
        assert status == JobStatus.PARTIAL
        rows = {
            row["name"]: row
            for row in connection.execute("SELECT name, state, failure_code, github_state FROM release_files")
        }
        assert rows["pending.bin"]["state"] == FileState.FAILED
        assert rows["pending.bin"]["failure_code"] == FailureCode.ASSET_NOT_UPLOADED
        assert rows["pending.bin"]["github_state"] == "open"
        assert rows["app.exe"]["state"] == FileState.COMPLETED
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] is None
    finally:
        connection.close()


def test_partial_checksum_failure_preserves_old_current_and_completed_files(tmp_path) -> None:
    good_a = b"asset-a"
    bad_b = b"asset-b"
    good_c = b"asset-c"
    release = _release(
        456,
        (
            _asset(11, "My App.zip", good_a, digest=_digest(good_a)),
            _asset(12, "tool.7z", bad_b, digest=_digest(b"expected-other-bytes!!")),
            _asset(13, "lib.dll", good_c, digest=_digest(good_c)),
        ),
    )
    connection = _database(tmp_path)
    try:
        old = _release(100, ())
        _, old_release_id = _save(connection, old)
        connection.execute(
            "UPDATE repositories SET current_release_id = ? WHERE github_repo_id = 123",
            (old_release_id,),
        )
        repository_id, release_id = _save(connection, release)
        layout = PathLayout(tmp_path / "archive")
        payloads = _payloads(release, {11: good_a, 12: bad_b, 13: good_c})
        status = _archive(connection, layout, repository_id, release_id, payloads)
        assert status in {JobStatus.PARTIAL, JobStatus.FAILED}
        assert status == JobStatus.PARTIAL
        current = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        assert current == old_release_id
        rows = {row["name"]: row for row in connection.execute("SELECT * FROM release_files WHERE release_id = ?", (release_id,))}
        assert rows["source-code.zip"]["state"] == FileState.COMPLETED
        assert rows["source-code.tar.gz"]["state"] == FileState.COMPLETED
        assert rows["My App.zip"]["state"] == FileState.COMPLETED
        assert rows["lib.dll"]["state"] == FileState.COMPLETED
        assert rows["tool.7z"]["state"] == FileState.FAILED
        assert rows["tool.7z"]["failure_code"] == FailureCode.CHECKSUM_FAILURE
        success = layout.release_asset_file("octo", "hello", 123, 456, 11, "My App.zip")
        failed = layout.release_asset_file("octo", "hello", 123, 456, 12, "tool.7z")
        kept = layout.release_asset_file("octo", "hello", 123, 456, 13, "lib.dll")
        assert success.is_file() and kept.is_file()
        assert not failed.exists()
        assert success.parent == layout.release_assets_dir("octo", "hello", 123, 456)
        job_id = connection.execute("SELECT id FROM jobs").fetchone()[0]
        part = layout.part_file(str(job_id), int(rows["tool.7z"]["id"]))
        assert part.exists()
        assert not list(layout.release_dir("octo", "hello", 123, 456).rglob("*.part"))
    finally:
        connection.close()


def test_complete_release_promotes_current_on_real_disk(tmp_path) -> None:
    files = {
        21: ("setup.exe", b"exe-bytes"),
        22: ("pack.7z", b"seven"),
        23: ("bundle.zip", b"zip"),
        24: ("src.tar.gz", b"tar-asset"),
        25: ("wheel.whl", b"wheel"),
        26: ("app.jar", b"jar"),
        27: ("native.dll", b"dll"),
        28: ("firmware.bin", b"bin"),
        29: ("NOEXT", b"plain"),
    }
    assets = tuple(
        _asset(asset_id, name, body, digest=_digest(body))
        for asset_id, (name, body) in files.items()
    )
    release = _release(456, assets)
    connection = _database(tmp_path)
    try:
        old = _release(100, ())
        _, old_release_id = _save(connection, old)
        connection.execute("UPDATE repositories SET current_release_id = ?", (old_release_id,))
        repository_id, release_id = _save(connection, release)
        archive_root = tmp_path / "archive"
        layout = PathLayout(archive_root)
        payloads = _payloads(release, {asset_id: body for asset_id, (_, body) in files.items()})
        status = _archive(connection, layout, repository_id, release_id, payloads)
        assert status == JobStatus.COMPLETED
        current = connection.execute("SELECT current_release_id FROM repositories").fetchone()[0]
        assert current == release_id
        assert current != old_release_id
        job = connection.execute("SELECT id, status, target_release_id FROM jobs ORDER BY id DESC").fetchone()
        assert job["status"] == JobStatus.COMPLETED
        assert job["target_release_id"] == release_id
        assert (archive_root / "_tmp").is_dir()
        assert (archive_root / "repositories" / "123" / "releases" / "456" / "source").is_dir()
        assert (archive_root / "repositories" / "123" / "releases" / "456" / "assets").is_dir()
        rows = connection.execute(
            "SELECT * FROM release_files WHERE release_id = ?",
            (release_id,),
        ).fetchall()
        assert rows
        assert all(row["state"] == FileState.COMPLETED for row in rows)
        for row in rows:
            final = archive_root / row["relative_path"]
            data = final.read_bytes()
            assert hashlib.sha256(data).hexdigest() == row["local_sha256"]
            assert len(data) == row["actual_size"]
            assert row["local_sha256"] == row["local_sha256"].lower()
            assert len(row["local_sha256"]) == 64
            assert not layout.part_file(str(job["id"]), int(row["id"])).exists()
        source_rows = {row["kind"]: row for row in rows}
        assert source_rows["SOURCE_ZIP"]["upstream_digest"] is None
        assert source_rows["SOURCE_TARBALL"]["upstream_digest"] is None
        assert source_rows["SOURCE_ZIP"]["digest_status"] == "ABSENT"
        zip_path = layout.release_source_file("octo", "hello", 123, 456, "source-code.zip")
        tar_path = layout.release_source_file("octo", "hello", 123, 456, "source-code.tar.gz")
        assert zip_path.read_bytes() == b"zip-source"
        assert tar_path.read_bytes() == b"tar-source"
    finally:
        connection.close()


def test_first_archive_failure_leaves_current_null(tmp_path) -> None:
    body = b"only"
    release = _release(456, (_asset(1, "app.exe", body, digest=_digest(b"nope-nope-nope-nope")),))
    connection = _database(tmp_path)
    try:
        repository_id, release_id = _save(connection, release)
        status = _archive(connection, PathLayout(tmp_path / "archive"), repository_id, release_id, _payloads(release, {1: body}))
        assert status == JobStatus.PARTIAL
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] is None
        failed = connection.execute(
            "SELECT state, failure_code FROM release_files WHERE name = 'app.exe'"
        ).fetchone()
        assert failed["state"] == FileState.FAILED
        assert failed["failure_code"] == FailureCode.CHECKSUM_FAILURE
    finally:
        connection.close()


def test_cancel_preserves_completed_files_and_part(tmp_path) -> None:
    body = b"kept"
    release = _release(
        456,
        (
            _asset(1, "a.bin", body, digest=_digest(body)),
            _asset(2, "b.bin", body, digest=_digest(body)),
        ),
    )
    connection = _database(tmp_path)
    try:
        repository_id, release_id = _save(connection, release)
        layout = PathLayout(tmp_path / "archive")
        cancel = Event()
        calls = {"count": 0}

        def respond(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] >= 3:
                cancel.set()
            payload = b"zip-source" if request.url.path.endswith("/zipball/v2") else b"tar-source"
            if "release-assets" in request.url.host:
                payload = body
            return httpx.Response(200, content=payload, headers={"Content-Length": str(len(payload))})

        with respx.mock() as router:
            router.get(url__regex=r"https://.*").mock(side_effect=respond)
            with GitHubClient(token=TOKEN) as client:
                status = archive_release(
                    connection,
                    client,
                    layout,
                    repository_id=repository_id,
                    release_id=release_id,
                    cancel_event=cancel,
                )
        assert status == JobStatus.CANCELLED
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] is None
        rows = {row["name"]: row for row in connection.execute("SELECT * FROM release_files")}
        assert rows["source-code.zip"]["state"] == FileState.COMPLETED
        assert rows["source-code.tar.gz"]["state"] == FileState.COMPLETED
        assert rows["a.bin"]["state"] == FileState.CANCELLED
        zip_path = layout.release_source_file("octo", "hello", 123, 456, "source-code.zip")
        tar_path = layout.release_source_file("octo", "hello", 123, 456, "source-code.tar.gz")
        assert zip_path.is_file() and tar_path.is_file()
        assert not layout.release_asset_file("octo", "hello", 123, 456, 1, "a.bin").exists()
        job_id = connection.execute("SELECT id FROM jobs").fetchone()[0]
        assert layout.part_file(str(job_id), int(rows["a.bin"]["id"])).exists()
    finally:
        connection.close()


def test_promotion_transaction_rolls_back_current_when_job_update_fails(tmp_path) -> None:
    body = b"all-good"
    release = _release(456, (_asset(1, "app.exe", body, digest=_digest(body)),))
    connection = _database(tmp_path)
    try:
        _, old_release_id = _save(connection, _release(100, ()))
        connection.execute("UPDATE repositories SET current_release_id = ?", (old_release_id,))
        repository_id, release_id = _save(connection, release)
        connection.execute(
            """
            CREATE TRIGGER abort_completed_job
            BEFORE UPDATE OF status ON jobs
            WHEN NEW.status = 'COMPLETED'
            BEGIN
                SELECT RAISE(ABORT, 'promotion failed');
            END
            """
        )
        status = _archive(
            connection,
            PathLayout(tmp_path / "archive"),
            repository_id,
            release_id,
            _payloads(release, {1: body}),
        )
        assert status == JobStatus.FAILED
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] == old_release_id
        job = connection.execute("SELECT status FROM jobs").fetchone()
        assert job["status"] == JobStatus.FAILED
    finally:
        connection.close()


def test_unsafe_asset_name_fails_that_file_only(tmp_path) -> None:
    body = b"ok"
    release = _release(
        456,
        (
            _asset(1, "..", body, digest=_digest(body)),
            _asset(2, "CON", body, digest=_digest(body)),
        ),
    )
    connection = _database(tmp_path)
    try:
        repository_id, release_id = _save(connection, release)
        status = _archive(
            connection,
            PathLayout(tmp_path / "archive"),
            repository_id,
            release_id,
            _payloads(release, {1: body, 2: body}),
            assert_all_called=False,
        )
        assert status == JobStatus.PARTIAL
        rows = {row["name"]: row for row in connection.execute("SELECT name, state, failure_code FROM release_files")}
        assert rows[".."]["state"] == FileState.FAILED
        assert rows[".."]["failure_code"] == FailureCode.UNSAFE_PATH
        assert rows["CON"]["state"] == FileState.COMPLETED
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] is None
        layout = PathLayout(tmp_path / "archive")
        reserved = layout.release_asset_file("octo", "hello", 123, 456, 2, "CON")
        assert reserved.is_file()
        assert reserved.parent == layout.release_assets_dir("octo", "hello", 123, 456)
    finally:
        connection.close()
