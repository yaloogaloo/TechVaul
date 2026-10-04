import pytest

from techvault.errors import ErrorCode, InvalidUrlError
from techvault.github.urls import parse_github_repository_url


def test_normal_repository_url() -> None:
    parsed = parse_github_repository_url("https://github.com/octo/hello")
    assert parsed.owner == "octo"
    assert parsed.repo == "hello"
    assert parsed.canonical_url == "https://github.com/octo/hello"


def test_trailing_slash() -> None:
    parsed = parse_github_repository_url("https://github.com/octo/hello/")
    assert parsed.owner == "octo"
    assert parsed.repo == "hello"
    assert parsed.canonical_url == "https://github.com/octo/hello"


def test_git_suffix() -> None:
    parsed = parse_github_repository_url("https://github.com/octo/hello.git")
    assert parsed.repo == "hello"
    assert parsed.canonical_url == "https://github.com/octo/hello"


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/octo/hello",
        "https://www.github.com/octo/hello",
        "https://github.com.evil.com/octo/hello",
    ],
)
def test_non_github_host_is_rejected(url: str) -> None:
    with pytest.raises(InvalidUrlError) as caught:
        parse_github_repository_url(url)
    assert caught.value.code == ErrorCode.INVALID_URL


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/octo/hello",
        "https://github.com/",
        "https://github.com/octo",
        "https://github.com/octo/hello/issues",
        "https://github.com/octo/hello/pulls",
        "https://github.com/octo/hello/tree/main",
        "https://github.com/octo/hello?tab=readme",
        "https://github.com/octo/hello?token=secret",
        "https://user:secret@github.com/octo/hello",
        "https://github.com/octo/hello#access_token=secret",
        "https://github.com/octo/hello?access_token=secret",
        "https://github.com/octo/hello?authorization=secret",
    ],
)
def test_non_repository_urls_are_rejected(url: str) -> None:
    with pytest.raises(InvalidUrlError) as caught:
        parse_github_repository_url(url)
    assert caught.value.code == ErrorCode.INVALID_URL
