"""Parse a GitHub repository root URL. Query strings and credentials are rejected."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from techvault.errors import InvalidUrlError

_ALLOWED_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")


@dataclass(frozen=True)
class GitHubRepositoryRef:
    owner: str
    repo: str
    canonical_url: str


def parse_github_repository_url(url: str) -> GitHubRepositoryRef:
    if not isinstance(url, str) or url.strip() == "":
        raise InvalidUrlError("GitHub repository URL is empty")
    if any(ord(char) < 32 for char in url):
        raise InvalidUrlError("GitHub repository URL contains control characters")

    lowered = url.lower()
    if "access_token" in lowered or "authorization" in lowered:
        raise InvalidUrlError("GitHub repository URL contains credential material")

    parsed = urlparse(url.strip())
    if parsed.username is not None or parsed.password is not None:
        raise InvalidUrlError("GitHub repository URL contains user info")
    if parsed.scheme != "https":
        raise InvalidUrlError("GitHub repository URL must use https")
    if parsed.hostname != "github.com":
        raise InvalidUrlError("GitHub repository URL host must be github.com")
    if parsed.query or parsed.fragment:
        raise InvalidUrlError("GitHub repository URL must not include a query or fragment")

    path = unquote(parsed.path)
    if path.endswith("/"):
        path = path[:-1]
    parts = [part for part in path.split("/") if part != ""]
    if len(parts) != 2:
        raise InvalidUrlError("GitHub repository URL must be https://github.com/{owner}/{repo}")

    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    _validate_name(owner, field="owner")
    _validate_name(repo, field="repo")
    return GitHubRepositoryRef(
        owner=owner,
        repo=repo,
        canonical_url=f"https://github.com/{owner}/{repo}",
    )


def _validate_name(value: str, *, field: str) -> None:
    if value in {"", ".", ".."} or "/" in value or "\\" in value:
        raise InvalidUrlError(f"GitHub repository {field} is not a single path segment")
    if any(char not in _ALLOWED_CHARS for char in value):
        raise InvalidUrlError(f"GitHub repository {field} contains unsupported characters")
