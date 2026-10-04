import httpx
import pytest
import respx

from techvault.errors import ErrorCode, GitHubHTTPError
from techvault.github.client import GitHubClient, parse_rate_limit

TOKEN = "ghp_supersecrettokenvalue"
REPO_URL = "https://api.github.com/repos/octo/hello"
RATE_HEADERS = {
    "X-RateLimit-Limit": "5000",
    "X-RateLimit-Remaining": "4990",
    "X-RateLimit-Reset": "1710000000",
    "X-RateLimit-Used": "10",
}


def _repository_payload() -> dict[str, object]:
    return {
        "id": 123,
        "name": "hello",
        "full_name": "octo/hello",
        "html_url": "https://github.com/octo/hello",
        "description": "A repository",
        "stargazers_count": 42,
        "default_branch": "main",
        "license": {"key": "mit", "name": "MIT License", "spdx_id": "MIT"},
        "owner": {"login": "octo", "id": 7},
    }


def test_repository_200_parses_metadata_and_sends_auth() -> None:
    with respx.mock() as router:
        route = router.get(REPO_URL).mock(
            return_value=httpx.Response(200, json=_repository_payload(), headers=RATE_HEADERS)
        )
        with GitHubClient(token=TOKEN) as client:
            fetched = client.get_repository("octo", "hello")
    request_headers = route.calls[0].request.headers
    assert request_headers["Authorization"] == f"Bearer {TOKEN}"
    assert request_headers["Accept"] == "application/vnd.github+json"
    assert request_headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert request_headers["User-Agent"] == "TechVault"
    assert fetched.repository.id == 123
    assert fetched.repository.owner == "octo"
    assert fetched.repository.name == "hello"
    assert fetched.repository.full_name == "octo/hello"
    assert fetched.repository.html_url == "https://github.com/octo/hello"
    assert fetched.repository.description == "A repository"
    assert fetched.repository.stargazers_count == 42
    assert fetched.repository.license == "MIT"
    assert fetched.repository.default_branch == "main"
    assert fetched.rate_limit.limit == 5000
    assert fetched.rate_limit.remaining == 4990
    assert fetched.rate_limit.reset == 1710000000
    assert fetched.rate_limit.used == 10


@pytest.mark.parametrize(
    ("status", "payload", "headers", "code"),
    [
        (401, {"message": "Bad credentials"}, RATE_HEADERS, ErrorCode.AUTH_INVALID),
        (
            403,
            {"message": "API rate limit exceeded for 127.0.0.1"},
            {**RATE_HEADERS, "X-RateLimit-Remaining": "0"},
            ErrorCode.RATE_LIMIT,
        ),
        (
            403,
            {"message": "You have exceeded a secondary rate limit"},
            {**RATE_HEADERS, "X-RateLimit-Remaining": "20"},
            ErrorCode.RATE_LIMIT,
        ),
        (
            403,
            {"message": "Resource not accessible by integration"},
            RATE_HEADERS,
            ErrorCode.FORBIDDEN,
        ),
        (404, {"message": "Not Found"}, RATE_HEADERS, ErrorCode.REPO_NOT_VISIBLE),
        (429, {"message": "rate limit"}, RATE_HEADERS, ErrorCode.RATE_LIMIT),
        (500, {"message": "Server Error"}, RATE_HEADERS, ErrorCode.API_ERROR),
    ],
)
def test_http_status_classification(
    status: int,
    payload: dict[str, str],
    headers: dict[str, str],
    code: str,
) -> None:
    with respx.mock() as router:
        router.get(REPO_URL).mock(return_value=httpx.Response(status, json=payload, headers=headers))
        with GitHubClient(token=TOKEN) as client:
            with pytest.raises(GitHubHTTPError) as caught:
                client.get_repository("octo", "hello")
    assert caught.value.code == code
    assert caught.value.status_code == status
    assert caught.value.code != "REPO_NOT_FOUND"
    assert caught.value.rate_limit is not None
    assert caught.value.rate_limit.limit == 5000


def test_timeout() -> None:
    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with respx.mock() as router:
        router.get(REPO_URL).mock(side_effect=raise_timeout)
        with GitHubClient(token=TOKEN) as client:
            with pytest.raises(GitHubHTTPError) as caught:
                client.get_repository("octo", "hello")
    assert caught.value.code == ErrorCode.TIMEOUT


def test_network_error() -> None:
    def raise_network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed")

    with respx.mock() as router:
        router.get(REPO_URL).mock(side_effect=raise_network)
        with GitHubClient(token=TOKEN) as client:
            with pytest.raises(GitHubHTTPError) as caught:
                client.get_repository("octo", "hello")
    assert caught.value.code == ErrorCode.NETWORK


def test_invalid_json() -> None:
    with respx.mock() as router:
        router.get(REPO_URL).mock(
            return_value=httpx.Response(200, content=b"{", headers={"Content-Type": "application/json"})
        )
        with GitHubClient(token=TOKEN) as client:
            with pytest.raises(GitHubHTTPError) as caught:
                client.get_repository("octo", "hello")
    assert caught.value.code == ErrorCode.API_ERROR


def test_rate_limit_header_parsing() -> None:
    headers = httpx.Headers(
        {
            "x-ratelimit-limit": "60",
            "x-ratelimit-remaining": "59",
            "x-ratelimit-reset": "1710001111",
            "x-ratelimit-used": "1",
        }
    )
    info = parse_rate_limit(headers)
    assert info.limit == 60
    assert info.remaining == 59
    assert info.reset == 1710001111
    assert info.used == 1
    assert parse_rate_limit(httpx.Headers()).limit is None
