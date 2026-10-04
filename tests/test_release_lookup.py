import httpx
import pytest
import respx

from techvault.errors import ErrorCode
from techvault.github.client import GitHubClient
from techvault.github.models import LookupOutcome
from techvault.github.releases import lookup_official_release

REPO_URL = "https://api.github.com/repos/octo/hello"
LATEST_URL = "https://api.github.com/repos/octo/hello/releases/latest"
LIST_URL = "https://api.github.com/repos/octo/hello/releases?per_page=100"
PAGE_2 = "https://api.github.com/repos/octo/hello/releases?per_page=100&page=2"
PAGE_3 = "https://api.github.com/repos/octo/hello/releases?per_page=100&page=3"
DIGEST = "sha256:f4bcbeef"


def _repository() -> dict[str, object]:
    return {
        "id": 123,
        "name": "hello",
        "full_name": "octo/hello",
        "html_url": "https://github.com/octo/hello",
        "description": "A repository",
        "stargazers_count": 42,
        "default_branch": "main",
        "license": {"spdx_id": "MIT"},
        "owner": {"login": "octo"},
    }


def _release(
    release_id: int,
    *,
    prerelease: bool = False,
    draft: bool = False,
    tag: str = "v1.2.3",
    body: str = "## Fixed\n\nUse the official archive.",
    assets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": release_id,
        "tag_name": tag,
        "name": "Hello",
        "body": body,
        "html_url": f"https://github.com/octo/hello/releases/tag/{tag}",
        "published_at": "2026-02-01T00:00:00Z",
        "created_at": "2026-01-15T00:00:00Z",
        "prerelease": prerelease,
        "draft": draft,
        "tarball_url": f"https://api.github.com/repos/octo/hello/tarball/{tag}",
        "zipball_url": f"https://api.github.com/repos/octo/hello/zipball/{tag}",
        "assets": assets
        if assets is not None
        else [
            {
                "id": 900,
                "name": "app.exe",
                "size": 12,
                "content_type": "application/octet-stream",
                "state": "uploaded",
                "digest": DIGEST,
                "browser_download_url": "https://github.com/octo/hello/releases/download/v1.2.3/app.exe",
                "created_at": "2026-02-01T00:00:01Z",
                "updated_at": "2026-02-01T00:00:02Z",
            },
            {
                "id": 901,
                "name": "pending.bin",
                "size": 4,
                "content_type": "application/octet-stream",
                "state": "open",
                "digest": "sha256:abc123",
                "browser_download_url": "https://github.com/octo/hello/releases/download/v1.2.3/pending.bin",
                "created_at": "2026-02-01T00:00:03Z",
                "updated_at": "2026-02-01T00:00:04Z",
            },
        ],
    }


def _lookup(router: respx.MockRouter):
    with GitHubClient(token="ghp_supersecrettokenvalue") as client:
        return lookup_official_release(client, "octo", "hello")


def test_repository_200_continues_to_latest() -> None:
    with respx.mock(assert_all_called=False) as router:
        repository = router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        latest = router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release(77)))
        result = _lookup(router)
    assert repository.called
    assert latest.called
    assert result.outcome == LookupOutcome.STABLE
    assert result.repository is not None
    assert result.repository.id == 123
    assert result.repository.license == "MIT"


def test_repository_404_does_not_request_latest() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        latest = router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release(1)))
        listing = router.get(LIST_URL).mock(return_value=httpx.Response(200, json=[]))
        result = _lookup(router)
    assert result.outcome == LookupOutcome.REPO_NOT_VISIBLE
    assert result.release is None
    assert result.repository is None
    assert latest.called is False
    assert listing.called is False


@pytest.mark.parametrize(
    ("status", "payload", "headers", "outcome"),
    [
        (401, {"message": "Bad credentials"}, {}, LookupOutcome.AUTH_INVALID),
        (
            403,
            {"message": "API rate limit exceeded"},
            {"X-RateLimit-Remaining": "0"},
            LookupOutcome.API_ERROR,
        ),
        (
            403,
            {"message": "Resource not accessible by integration"},
            {"X-RateLimit-Remaining": "10"},
            LookupOutcome.FORBIDDEN,
        ),
        (500, {"message": "boom"}, {}, LookupOutcome.API_ERROR),
    ],
)
def test_repository_http_errors_stop_before_latest(
    status: int,
    payload: dict[str, str],
    headers: dict[str, str],
    outcome: str,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(status, json=payload, headers=headers))
        latest = router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release(1)))
        result = _lookup(router)
    assert result.outcome == outcome
    assert result.release is None
    assert latest.called is False
    assert result.error is not None
    if status == 401:
        assert result.error.code == ErrorCode.AUTH_INVALID
    elif status == 403 and headers.get("X-RateLimit-Remaining") == "0":
        assert result.error.code == ErrorCode.RATE_LIMIT
    elif status == 403:
        assert result.error.code == ErrorCode.FORBIDDEN
    elif status == 500:
        assert result.error.code == ErrorCode.API_ERROR


def test_repository_timeout_is_api_error() -> None:
    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with respx.mock(assert_all_called=False) as router:
        router.get(REPO_URL).mock(side_effect=raise_timeout)
        latest = router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release(1)))
        result = _lookup(router)
    assert result.outcome == LookupOutcome.API_ERROR
    assert result.error is not None
    assert result.error.code == ErrorCode.TIMEOUT
    assert latest.called is False


def test_repository_invalid_json_is_api_error() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, content=b"{"))
        latest = router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=_release(1)))
        result = _lookup(router)
    assert result.outcome == LookupOutcome.API_ERROR
    assert result.error is not None
    assert result.error.code == ErrorCode.API_ERROR
    assert latest.called is False


def test_latest_200_is_stable_and_preserves_asset_fields() -> None:
    payload = _release(77, body="## Notes\n\nkeep this markdown")
    with respx.mock(assert_all_called=False) as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(200, json=payload))
        listing = router.get(url__regex=r".*/releases(\?.*)?$").mock(
            return_value=httpx.Response(200, json=[_release(1, prerelease=True, assets=[])])
        )
        result = _lookup(router)
    assert listing.called is False
    assert result.outcome == LookupOutcome.STABLE
    assert result.releases_checked is None
    assert result.release is not None
    assert result.release.github_release_id == 77
    assert result.release.tag_name == "v1.2.3"
    assert result.release.name == "Hello"
    assert result.release.body == "## Notes\n\nkeep this markdown"
    assert result.release.html_url == "https://github.com/octo/hello/releases/tag/v1.2.3"
    assert result.release.published_at == "2026-02-01T00:00:00Z"
    assert result.release.created_at == "2026-01-15T00:00:00Z"
    assert result.release.prerelease is False
    assert result.release.draft is False
    assert result.release.tarball_url.endswith("/tarball/v1.2.3")
    assert result.release.zipball_url.endswith("/zipball/v1.2.3")
    uploaded = result.release.assets[0]
    pending = result.release.assets[1]
    assert uploaded.digest == DIGEST
    assert uploaded.digest.startswith("sha256:")
    assert uploaded.state == "uploaded"
    assert pending.state == "open"
    assert pending.digest == "sha256:abc123"


def test_latest_404_with_empty_list_is_no_releases() -> None:
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        listing = router.get(LIST_URL).mock(return_value=httpx.Response(200, json=[]))
        result = _lookup(router)
    assert listing.called
    assert result.outcome == LookupOutcome.NO_RELEASES
    assert result.release is None
    assert result.releases_checked == 0


@pytest.mark.parametrize(
    "items",
    [
        [_release(1, prerelease=True, draft=False, assets=[])],
        [_release(1, prerelease=False, draft=True, assets=[])],
        [
            _release(1, prerelease=True, draft=False, assets=[]),
            _release(2, prerelease=False, draft=True, tag="v0", assets=[]),
        ],
    ],
)
def test_latest_404_only_prerelease_or_draft(items: list[dict[str, object]]) -> None:
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(LIST_URL).mock(return_value=httpx.Response(200, json=items))
        result = _lookup(router)
    assert result.outcome == LookupOutcome.ONLY_PRERELEASE_OR_DRAFT
    assert result.release is None
    assert result.releases_checked == len(items)


def test_latest_404_with_stable_release_is_not_selected() -> None:
    stable = _release(50, prerelease=False, draft=False, tag="v9.9.9", assets=[])
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(LIST_URL).mock(return_value=httpx.Response(200, json=[stable]))
        result = _lookup(router)
    assert result.outcome == LookupOutcome.NO_OFFICIAL_LATEST
    assert result.release is None
    assert result.releases_checked == 1


def test_latest_404_mixed_with_stable_is_not_selected() -> None:
    items = [
        _release(1, prerelease=True, draft=False, assets=[]),
        _release(2, prerelease=False, draft=True, tag="draft", assets=[]),
        _release(3, prerelease=False, draft=False, tag="v2", assets=[]),
    ]
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(LIST_URL).mock(return_value=httpx.Response(200, json=items))
        result = _lookup(router)
    assert result.outcome == LookupOutcome.NO_OFFICIAL_LATEST
    assert result.release is None
    assert result.releases_checked == 3


def test_single_page_without_next() -> None:
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        listing = router.get(LIST_URL).mock(
            return_value=httpx.Response(200, json=[_release(1, prerelease=True, assets=[])])
        )
        result = _lookup(router)
    assert listing.call_count == 1
    assert result.outcome == LookupOutcome.ONLY_PRERELEASE_OR_DRAFT
    assert result.releases_checked == 1


def test_two_pages_follow_next_url() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == LIST_URL:
            return httpx.Response(
                200,
                json=[_release(1, prerelease=True, assets=[])],
                headers={"Link": f'<{PAGE_2}>; rel="next"'},
            )
        if str(request.url) == PAGE_2:
            return httpx.Response(200, json=[_release(2, prerelease=True, tag="v0", assets=[])])
        raise AssertionError(str(request.url))

    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(url__regex=r"https://api\.github\.com/repos/octo/hello/releases\?").mock(
            side_effect=respond
        )
        result = _lookup(router)
    assert calls == [LIST_URL, PAGE_2]
    assert result.outcome == LookupOutcome.ONLY_PRERELEASE_OR_DRAFT
    assert result.releases_checked == 2
    assert result.release is None


def test_three_pages_and_stable_on_last_page_is_not_selected() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        current = str(request.url)
        calls.append(current)
        if current == LIST_URL:
            return httpx.Response(
                200,
                json=[_release(1, prerelease=True, assets=[])],
                headers={"Link": f'<{PAGE_2}>; rel="next"'},
            )
        if current == PAGE_2:
            return httpx.Response(
                200,
                json=[_release(2, prerelease=True, tag="v0", assets=[])],
                headers={"Link": f'<{PAGE_3}>; rel="next"'},
            )
        if current == PAGE_3:
            return httpx.Response(
                200,
                json=[_release(3, prerelease=False, draft=False, tag="v9", assets=[])],
            )
        raise AssertionError(current)

    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(url__regex=r"https://api\.github\.com/repos/octo/hello/releases\?").mock(
            side_effect=respond
        )
        result = _lookup(router)
    assert calls == [LIST_URL, PAGE_2, PAGE_3]
    assert result.outcome == LookupOutcome.NO_OFFICIAL_LATEST
    assert result.release is None
    assert result.releases_checked == 3


def test_prerelease_page_then_stable_page_is_not_official() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == LIST_URL:
            return httpx.Response(
                200,
                json=[_release(1, prerelease=True, assets=[])],
                headers={"Link": f'<{PAGE_2}>; rel="next"'},
            )
        return httpx.Response(
            200,
            json=[_release(9, prerelease=False, draft=False, tag="stable", assets=[])],
        )

    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(url__regex=r"https://api\.github\.com/repos/octo/hello/releases\?").mock(
            side_effect=respond
        )
        result = _lookup(router)
    assert result.outcome == LookupOutcome.NO_OFFICIAL_LATEST
    assert result.release is None
    assert result.releases_checked == 2


@pytest.mark.parametrize(
    "failure",
    ["timeout", "status-500", "status-429", "invalid-json"],
)
def test_pagination_failure_is_api_error(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == LIST_URL:
            return httpx.Response(
                200,
                json=[_release(1, prerelease=True, assets=[])],
                headers={"Link": f'<{PAGE_2}>; rel="next"'},
            )
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out")
        if failure == "status-500":
            return httpx.Response(500, json={"message": "nope"})
        if failure == "status-429":
            return httpx.Response(429, json={"message": "rate limit"})
        return httpx.Response(200, content=b"{")

    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(url__regex=r"https://api\.github\.com/repos/octo/hello/releases\?").mock(
            side_effect=respond
        )
        result = _lookup(router)
    assert result.outcome == LookupOutcome.API_ERROR
    assert result.release is None
    assert result.outcome != LookupOutcome.NO_RELEASES
    assert result.outcome != LookupOutcome.ONLY_PRERELEASE_OR_DRAFT
    assert result.error is not None


def test_unparseable_link_header_is_api_error() -> None:
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(200, json=_repository()))
        router.get(LATEST_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        router.get(LIST_URL).mock(
            return_value=httpx.Response(
                200,
                json=[_release(1, prerelease=True, assets=[])],
                headers={"Link": "not a link"},
            )
        )
        result = _lookup(router)
    assert result.outcome == LookupOutcome.API_ERROR
    assert result.release is None
    assert result.releases_checked is None
