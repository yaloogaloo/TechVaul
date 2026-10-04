"""Official latest-release lookup. The release list is never used to choose a target."""

from __future__ import annotations

from urllib.parse import quote

from techvault.errors import ErrorCode, GitHubHTTPError, TechVaultError
from techvault.github.client import API_BASE, GitHubClient
from techvault.github.models import LookupOutcome, ReleaseLookupResult, ReleaseMetadata
from techvault.logging_setup import get_logger

logger = get_logger()


def lookup_official_release(client: GitHubClient, owner: str, repo: str) -> ReleaseLookupResult:
    try:
        fetched = client.get_repository(owner, repo)
    except GitHubHTTPError as exc:
        outcome = _repository_outcome(exc)
        logger.warning("Repository lookup stopped code=%s outcome=%s", exc.code, outcome)
        return ReleaseLookupResult(
            outcome=outcome,
            repository=None,
            release=None,
            releases_checked=None,
            error=exc,
        )

    repository = fetched.repository
    try:
        latest = client.get_latest_release(owner, repo)
    except GitHubHTTPError as exc:
        outcome = _after_repository_outcome(exc)
        logger.warning("Latest release lookup failed code=%s outcome=%s", exc.code, outcome)
        return ReleaseLookupResult(
            outcome=outcome,
            repository=repository,
            release=None,
            releases_checked=None,
            error=exc,
        )

    if latest is not None:
        logger.info(
            "Official latest release accepted id=%s tag=%s",
            latest.github_release_id,
            latest.tag_name,
        )
        return ReleaseLookupResult(
            outcome=LookupOutcome.STABLE,
            repository=repository,
            release=latest,
            releases_checked=None,
            error=None,
        )

    try:
        visible = _read_all_visible_releases(client, owner, repo)
    except GitHubHTTPError as exc:
        outcome = _list_outcome(exc)
        logger.warning("Release list pagination failed code=%s outcome=%s", exc.code, outcome)
        return ReleaseLookupResult(
            outcome=outcome,
            repository=repository,
            release=None,
            releases_checked=None,
            error=exc,
        )

    outcome = _classify_visible_releases(visible)
    logger.info("Release list classified outcome=%s checked=%s", outcome, len(visible))
    return ReleaseLookupResult(
        outcome=outcome,
        repository=repository,
        release=None,
        releases_checked=len(visible),
        error=None,
    )


def _read_all_visible_releases(
    client: GitHubClient,
    owner: str,
    repo: str,
) -> tuple[ReleaseMetadata, ...]:
    url = (
        f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/releases"
        "?per_page=100"
    )
    collected: list[ReleaseMetadata] = []
    seen: set[str] = set()
    while True:
        if url in seen:
            raise GitHubHTTPError(ErrorCode.API_ERROR, "GitHub pagination repeated a page URL")
        seen.add(url)
        page = client.get_releases_page(url)
        collected.extend(page.releases)
        if page.next_url is None:
            return tuple(collected)
        url = page.next_url


def _classify_visible_releases(releases: tuple[ReleaseMetadata, ...]) -> str:
    if len(releases) == 0:
        return LookupOutcome.NO_RELEASES
    if all(item.prerelease or item.draft for item in releases):
        return LookupOutcome.ONLY_PRERELEASE_OR_DRAFT
    return LookupOutcome.NO_OFFICIAL_LATEST


def _repository_outcome(error: GitHubHTTPError) -> str:
    if error.status_code == 404 or error.code == ErrorCode.REPO_NOT_VISIBLE:
        return LookupOutcome.REPO_NOT_VISIBLE
    if error.status_code == 401 or error.code == ErrorCode.AUTH_INVALID:
        return LookupOutcome.AUTH_INVALID
    if error.code == ErrorCode.FORBIDDEN:
        return LookupOutcome.FORBIDDEN
    return LookupOutcome.API_ERROR


def _after_repository_outcome(error: GitHubHTTPError) -> str:
    if error.status_code == 401 or error.code == ErrorCode.AUTH_INVALID:
        return LookupOutcome.AUTH_INVALID
    if error.code == ErrorCode.FORBIDDEN:
        return LookupOutcome.FORBIDDEN
    return LookupOutcome.API_ERROR


def _list_outcome(error: TechVaultError) -> str:
    if error.code == ErrorCode.AUTH_INVALID:
        return LookupOutcome.AUTH_INVALID
    if error.code == ErrorCode.FORBIDDEN:
        return LookupOutcome.FORBIDDEN
    return LookupOutcome.API_ERROR
