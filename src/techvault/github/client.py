"""Minimal GitHub REST client for repository metadata."""

from __future__ import annotations

import json
from urllib.parse import quote, urljoin, urlparse

import httpx

from techvault.errors import ErrorCode, GitHubHTTPError, InvalidUrlError
from techvault.github.models import (
    RateLimitInfo,
    ReleaseListPage,
    ReleaseMetadata,
    RepositoryFetch,
    RepositoryMetadata,
)
from techvault.github.pagination import PaginationLinkError, next_page_url
from techvault.github.payloads import parse_release_payload
from techvault.github.redirect import authorization_for_url
from techvault.logging_setup import get_logger

logger = get_logger()

API_BASE = "https://api.github.com"
API_VERSION = "2026-03-10"
USER_AGENT = "TechVault"
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token or None
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        accept: str | None = None,
        json_body: dict | None = None,
    ) -> httpx.Response:
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        current_method = method.upper()
        current_url = url
        for hop in range(_MAX_REDIRECTS + 1):
            headers = _base_headers()
            if accept:
                headers["Accept"] = accept
            if hop == 0 and body is not None:
                headers["Content-Type"] = "application/json"
            authorization = authorization_for_url(self._token, current_url)
            if authorization is not None:
                headers["Authorization"] = authorization
            try:
                response = self._client.request(
                    current_method,
                    current_url,
                    headers=headers,
                    content=body if hop == 0 else None,
                )
            except httpx.TimeoutException as exc:
                _log_transport(ErrorCode.TIMEOUT, current_url)
                raise GitHubHTTPError(ErrorCode.TIMEOUT, "GitHub request timed out") from exc
            except httpx.RequestError as exc:
                _log_transport(ErrorCode.NETWORK, current_url)
                raise GitHubHTTPError(ErrorCode.NETWORK, "GitHub network request failed") from exc

            if response.status_code not in _REDIRECT_STATUSES:
                return response
            if hop == _MAX_REDIRECTS:
                break
            location = response.headers.get("Location")
            if not location:
                raise GitHubHTTPError(ErrorCode.API_ERROR, "GitHub redirect missing Location")
            current_url = urljoin(current_url, location)
            if response.status_code in {301, 302, 303}:
                current_method = "GET"
        raise GitHubHTTPError(ErrorCode.API_ERROR, "GitHub redirect limit exceeded")

    def get_readme_markdown(self, owner: str, repo: str) -> str | None:
        """Return the README markdown source. None means GitHub has no README."""
        _reject_unsafe_component(owner, field="owner")
        _reject_unsafe_component(repo, field="repo")
        url = f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/readme"
        response = self.request("GET", url, accept="application/vnd.github.raw")
        try:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise classify_http_error(response)
            return response.text
        finally:
            response.close()

    def get_readme_html(self, owner: str, repo: str) -> str | None:
        """Return rendered README HTML. None means GitHub has no README."""
        _reject_unsafe_component(owner, field="owner")
        _reject_unsafe_component(repo, field="repo")
        url = f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/readme"
        response = self.request("GET", url, accept="application/vnd.github.html+json")
        try:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise classify_http_error(response)
            return response.text
        finally:
            response.close()

    def render_markdown(self, text: str, context: str) -> str:
        """Render GitHub Flavored Markdown. Empty text is not sent."""
        if text == "":
            raise ValueError("Empty markdown is not sent to GitHub")
        url = f"{API_BASE}/markdown"
        response = self.request(
            "POST",
            url,
            json_body={"text": text, "mode": "gfm", "context": context},
        )
        try:
            if response.status_code != 200:
                raise classify_http_error(response)
            return response.text
        finally:
            response.close()

    def get_repository(self, owner: str, repo: str) -> RepositoryFetch:
        _reject_unsafe_component(owner, field="owner")
        _reject_unsafe_component(repo, field="repo")
        url = f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        response = self.request("GET", url)
        rate_limit = parse_rate_limit(response.headers)
        if response.status_code != 200:
            error = classify_http_error(response, rate_limit)
            logger.warning(
                "GitHub repository request failed code=%s status=%s url=%s",
                error.code,
                response.status_code,
                url,
            )
            raise error
        payload = _decode_json(response, rate_limit)
        repository = _parse_repository(payload, rate_limit)
        logger.info("Fetched repository %s", repository.full_name)
        return RepositoryFetch(repository=repository, rate_limit=rate_limit)

    def get_latest_release(self, owner: str, repo: str) -> ReleaseMetadata | None:
        """Return the official latest release. HTTP 404 is absence, not a classification."""
        _reject_unsafe_component(owner, field="owner")
        _reject_unsafe_component(repo, field="repo")
        url = f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/releases/latest"
        response = self.request("GET", url)
        if response.status_code == 404:
            logger.info("GitHub official latest release returned 404 url=%s", url)
            return None
        rate_limit = parse_rate_limit(response.headers)
        if response.status_code != 200:
            error = classify_http_error(response, rate_limit)
            logger.warning(
                "GitHub latest release request failed code=%s status=%s url=%s",
                error.code,
                response.status_code,
                url,
            )
            raise error
        payload = _decode_json(response, rate_limit)
        return parse_release_payload(payload, rate_limit)

    def get_releases_page(self, url: str) -> ReleaseListPage:
        _require_api_url(url)
        response = self.request("GET", url)
        rate_limit = parse_rate_limit(response.headers)
        if response.status_code != 200:
            error = classify_http_error(response, rate_limit)
            logger.warning(
                "GitHub release list request failed code=%s status=%s url=%s",
                error.code,
                response.status_code,
                url,
            )
            raise error
        payload = _decode_json_value(response, rate_limit)
        if not isinstance(payload, list):
            raise GitHubHTTPError(
                ErrorCode.API_ERROR,
                "GitHub release list was not a JSON array",
                status_code=response.status_code,
                rate_limit=rate_limit,
            )
        try:
            following = next_page_url(response.headers.get("Link"), url)
        except PaginationLinkError as exc:
            raise GitHubHTTPError(
                ErrorCode.API_ERROR,
                "GitHub pagination Link header could not be parsed",
                status_code=response.status_code,
                rate_limit=rate_limit,
            ) from exc
        releases = tuple(parse_release_payload(item, rate_limit) for item in payload)
        return ReleaseListPage(releases=releases, next_url=following)


def parse_rate_limit(headers: httpx.Headers) -> RateLimitInfo:
    return RateLimitInfo(
        limit=_optional_int(headers.get("X-RateLimit-Limit")),
        remaining=_optional_int(headers.get("X-RateLimit-Remaining")),
        reset=_optional_int(headers.get("X-RateLimit-Reset")),
        used=_optional_int(headers.get("X-RateLimit-Used")),
    )


def classify_http_error(
    response: httpx.Response,
    rate_limit: RateLimitInfo | None = None,
) -> GitHubHTTPError:
    info = rate_limit if rate_limit is not None else parse_rate_limit(response.headers)
    status = response.status_code
    if status == 401:
        code = ErrorCode.AUTH_INVALID
        message = "GitHub rejected the credentials"
    elif status == 429 or (status == 403 and _is_rate_limit(response)):
        code = ErrorCode.RATE_LIMIT
        message = "GitHub API rate limit exceeded"
    elif status == 403:
        code = ErrorCode.FORBIDDEN
        message = "GitHub refused the request"
    elif status == 404:
        code = ErrorCode.REPO_NOT_VISIBLE
        message = "Repository does not exist, or the current credential cannot access it"
    elif 500 <= status <= 599:
        code = ErrorCode.API_ERROR
        message = "GitHub API returned a server error"
    else:
        code = ErrorCode.API_ERROR
        message = "GitHub API request failed"
    return GitHubHTTPError(code, message, status_code=status, rate_limit=info)


def _base_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _is_rate_limit(response: httpx.Response) -> bool:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining == "0":
        return True
    message = _error_message(response).lower()
    return "rate limit" in message


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return ""
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str):
            return message
    return ""


def _decode_json(response: httpx.Response, rate_limit: RateLimitInfo) -> dict[str, object]:
    payload = _decode_json_value(response, rate_limit)
    if not isinstance(payload, dict):
        raise GitHubHTTPError(
            ErrorCode.API_ERROR,
            "GitHub response was not a JSON object",
            status_code=response.status_code,
            rate_limit=rate_limit,
        )
    return payload


def _decode_json_value(response: httpx.Response, rate_limit: RateLimitInfo) -> object:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise GitHubHTTPError(
            ErrorCode.API_ERROR,
            "GitHub response was not valid JSON",
            status_code=response.status_code,
            rate_limit=rate_limit,
        ) from exc


def _require_api_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.github.com":
        raise GitHubHTTPError(ErrorCode.API_ERROR, "Release list URL is not a GitHub API URL")


def _parse_repository(payload: dict[str, object], rate_limit: RateLimitInfo) -> RepositoryMetadata:
    try:
        owner = payload["owner"]
        if not isinstance(owner, dict) or not isinstance(owner.get("login"), str):
            raise TypeError("owner")
        license_value = payload.get("license")
        license_name: str | None
        if license_value is None:
            license_name = None
        elif isinstance(license_value, dict) and (
            license_value.get("spdx_id") is None or isinstance(license_value.get("spdx_id"), str)
        ):
            spdx = license_value.get("spdx_id")
            license_name = spdx if isinstance(spdx, str) else None
        else:
            raise TypeError("license")
        description = payload.get("description")
        default_branch = payload.get("default_branch")
        if description is not None and not isinstance(description, str):
            raise TypeError("description")
        if default_branch is not None and not isinstance(default_branch, str):
            raise TypeError("default_branch")
        topics_value = payload.get("topics")
        topics: tuple[str, ...] | None = None
        if isinstance(topics_value, list) and all(isinstance(item, str) for item in topics_value):
            topics = tuple(topics_value)
        repo_id = payload["id"]
        stars = payload["stargazers_count"]
        name = payload["name"]
        full_name = payload["full_name"]
        html_url = payload["html_url"]
        if not isinstance(repo_id, int) or isinstance(repo_id, bool):
            raise TypeError("id")
        if not isinstance(stars, int) or isinstance(stars, bool):
            raise TypeError("stargazers_count")
        if not all(isinstance(item, str) for item in (name, full_name, html_url)):
            raise TypeError("identity")
        return RepositoryMetadata(
            id=repo_id,
            owner=str(owner["login"]),
            name=name,
            full_name=full_name,
            html_url=html_url,
            description=description,
            stargazers_count=stars,
            license=license_name,
            default_branch=default_branch,
            topics=topics,
        )
    except (KeyError, TypeError) as exc:
        raise GitHubHTTPError(
            ErrorCode.API_ERROR,
            "GitHub repository payload is missing required fields",
            rate_limit=rate_limit,
        ) from exc


def _reject_unsafe_component(value: str, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or value == ""
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise InvalidUrlError(f"GitHub repository {field} is not safe to request")


def _log_transport(code: str, url: str) -> None:
    logger.warning("GitHub transport failure code=%s url=%s", code, url)
