"""GitHub API data objects. HTTP transport stays in the client."""

from __future__ import annotations

from dataclasses import dataclass


class LookupOutcome:
    STABLE = "STABLE"
    REPO_NOT_VISIBLE = "REPO_NOT_VISIBLE"
    AUTH_INVALID = "AUTH_INVALID"
    FORBIDDEN = "FORBIDDEN"
    API_ERROR = "API_ERROR"
    NO_RELEASES = "NO_RELEASES"
    ONLY_PRERELEASE_OR_DRAFT = "ONLY_PRERELEASE_OR_DRAFT"
    NO_OFFICIAL_LATEST = "NO_OFFICIAL_LATEST"


@dataclass(frozen=True)
class RateLimitInfo:
    limit: int | None
    remaining: int | None
    reset: int | None
    used: int | None


@dataclass(frozen=True)
class RepositoryMetadata:
    id: int
    owner: str
    name: str
    full_name: str
    html_url: str
    description: str | None
    stargazers_count: int
    license: str | None
    default_branch: str | None
    topics: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RepositoryFetch:
    repository: RepositoryMetadata
    rate_limit: RateLimitInfo


@dataclass(frozen=True)
class ReleaseAsset:
    github_asset_id: int
    name: str
    size: int | None
    content_type: str | None
    state: str
    digest: str | None
    browser_download_url: str
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class ReleaseMetadata:
    github_release_id: int
    tag_name: str
    name: str | None
    body: str | None
    html_url: str
    published_at: str | None
    created_at: str | None
    prerelease: bool
    draft: bool
    tarball_url: str | None
    zipball_url: str | None
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class ReleaseListPage:
    releases: tuple[ReleaseMetadata, ...]
    next_url: str | None


@dataclass(frozen=True)
class ReleaseLookupResult:
    outcome: str
    repository: RepositoryMetadata | None
    release: ReleaseMetadata | None
    releases_checked: int | None
    error: Exception | None
