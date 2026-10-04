import httpx
import respx

from techvault.db.connection import connect
from techvault.db.migrate import apply_migrations, default_migrations_dir
from techvault.db.release_store import save_release_lookup
from techvault.github.client import GitHubClient
from techvault.github.models import LookupOutcome
from techvault.github.releases import lookup_official_release
from tests.test_release_lookup import (
    DIGEST,
    LATEST_URL,
    LIST_URL,
    REPO_URL,
    _release,
    _repository,
)


def _discover(tmp_path, release_id: int = 77):
    connection = connect(tmp_path / "techvault.sqlite")
    apply_migrations(connection, default_migrations_dir())
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json=_release(release_id, body="## Notes\n\nraw markdown"),
            )
        )
        with GitHubClient() as client:
            result = lookup_official_release(client, "octo", "hello")
    save_release_lookup(connection, result)
    return connection, result


def test_first_stable_lookup_leaves_current_release_null(tmp_path) -> None:
    connection, result = _discover(tmp_path)
    try:
        assert result.outcome == LookupOutcome.STABLE
        row = connection.execute("SELECT * FROM repositories").fetchone()
        assert row["current_release_id"] is None
        assert row["github_repo_id"] == 123
        assert row["owner"] == "octo"
        assert row["name"] == "hello"
        assert row["full_name"] == "octo/hello"
        assert row["html_url"] == "https://github.com/octo/hello"
        assert row["description"] == "A repository"
        assert row["stargazers_count"] == 42
        assert row["license_spdx"] == "MIT"
        assert row["default_branch"] == "main"
        stored = connection.execute("SELECT * FROM releases").fetchone()
        assert stored["lookup_outcome"] == LookupOutcome.STABLE
        assert stored["body"] == "## Notes\n\nraw markdown"
        assert stored["github_release_id"] == 77
        assets = connection.execute(
            "SELECT name, state, upstream_digest FROM release_files ORDER BY github_asset_id"
        ).fetchall()
        assert [tuple(item) for item in assets] == [
            ("app.exe", "uploaded", DIGEST),
            ("pending.bin", "open", "sha256:abc123"),
        ]
    finally:
        connection.close()


def test_new_stable_release_does_not_replace_current(tmp_path) -> None:
    connection, _ = _discover(tmp_path, release_id=100)
    try:
        current = connection.execute(
            "SELECT id FROM releases WHERE github_release_id = 100"
        ).fetchone()
        connection.execute(
            "UPDATE repositories SET current_release_id = ? WHERE github_repo_id = 123",
            (current["id"],),
        )
        with respx.mock() as router:
            router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
            router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release(456)))
            with GitHubClient() as client:
                result = lookup_official_release(client, "octo", "hello")
        assert result.outcome == LookupOutcome.STABLE
        assert result.release is not None
        assert result.release.github_release_id == 456
        save_release_lookup(connection, result)
        row = connection.execute(
            "SELECT current_release_id FROM repositories WHERE github_repo_id = 123"
        ).fetchone()
        assert row["current_release_id"] == current["id"]
        ids = [
            item[0]
            for item in connection.execute(
                "SELECT github_release_id FROM releases ORDER BY github_release_id"
            )
        ]
        assert ids == [100, 456]
    finally:
        connection.close()


def test_repeated_lookup_does_not_duplicate_release(tmp_path) -> None:
    connection, result = _discover(tmp_path)
    try:
        save_release_lookup(connection, result)
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM release_files").fetchone()[0] == 2
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] is None
    finally:
        connection.close()


def test_no_official_latest_does_not_store_a_list_release(tmp_path) -> None:
    connection = connect(tmp_path / "techvault.sqlite")
    apply_migrations(connection, default_migrations_dir())
    try:
        with respx.mock() as router:
            router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
            router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
            router.get(LIST_URL).mock(
                return_value=httpx.Response(
                    200,
                    json=[_release(50, prerelease=False, draft=False, assets=[])],
                )
            )
            with GitHubClient() as client:
                result = lookup_official_release(client, "octo", "hello")
        assert result.outcome == LookupOutcome.NO_OFFICIAL_LATEST
        assert result.release is None
        save_release_lookup(connection, result)
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0
        assert connection.execute("SELECT current_release_id FROM repositories").fetchone()[0] is None
    finally:
        connection.close()
