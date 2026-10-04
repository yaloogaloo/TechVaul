import httpx
import respx

from techvault.github.client import GitHubClient
from techvault.github.redirect import authorization_for_url

TOKEN = "ghp_supersecrettokenvalue"
START = "https://api.github.com/repos/octo/hello"


def _assert_redirect(location: str, *, keep_authorization: bool) -> None:
    with respx.mock() as router:
        first = router.get(START).mock(
            return_value=httpx.Response(302, headers={"Location": location})
        )
        second = router.get(location).mock(return_value=httpx.Response(200, content=b"ok"))
        with GitHubClient(token=TOKEN) as client:
            response = client.request("GET", START)
    assert response.status_code == 200
    assert first.calls[0].request.headers["Authorization"] == f"Bearer {TOKEN}"
    second_headers = second.calls[0].request.headers
    if keep_authorization:
        assert second_headers["Authorization"] == f"Bearer {TOKEN}"
    else:
        assert "Authorization" not in second_headers
    assert authorization_for_url(TOKEN, location) == (
        f"Bearer {TOKEN}" if keep_authorization else None
    )


def test_redirect_keeps_authorization_on_api_github() -> None:
    _assert_redirect("https://api.github.com/repos/octo/hello-next", keep_authorization=True)


def test_redirect_strips_authorization_for_release_assets() -> None:
    _assert_redirect(
        "https://release-assets.githubusercontent.com/github-production-release-asset/1",
        keep_authorization=False,
    )


def test_redirect_strips_authorization_for_codeload() -> None:
    _assert_redirect(
        "https://codeload.github.com/octo/hello/legacy.tar.gz/refs/tags/v1",
        keep_authorization=False,
    )


def test_redirect_strips_authorization_for_arbitrary_host() -> None:
    _assert_redirect("https://example.com/files/app.exe", keep_authorization=False)
