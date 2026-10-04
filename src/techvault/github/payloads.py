"""Parse GitHub release JSON without choosing which release is latest."""

from __future__ import annotations

from techvault.errors import ErrorCode, GitHubHTTPError
from techvault.github.models import RateLimitInfo, ReleaseAsset, ReleaseMetadata


def parse_release_payload(payload: object, rate_limit: RateLimitInfo) -> ReleaseMetadata:
    if not isinstance(payload, dict):
        raise _invalid(rate_limit, "GitHub release payload was not an object")
    try:
        assets_value = payload.get("assets", [])
        if not isinstance(assets_value, list):
            raise TypeError("assets")
        assets = tuple(_parse_asset(item) for item in assets_value)
        return ReleaseMetadata(
            github_release_id=_required_int(payload, "id"),
            tag_name=_required_str(payload, "tag_name"),
            name=_optional_str(payload, "name"),
            body=_optional_str(payload, "body"),
            html_url=_required_str(payload, "html_url"),
            published_at=_optional_str(payload, "published_at"),
            created_at=_optional_str(payload, "created_at"),
            prerelease=_required_bool(payload, "prerelease"),
            draft=_required_bool(payload, "draft"),
            tarball_url=_optional_str(payload, "tarball_url"),
            zipball_url=_optional_str(payload, "zipball_url"),
            assets=assets,
        )
    except (KeyError, TypeError) as exc:
        raise _invalid(rate_limit, "GitHub release payload is missing required fields") from exc


def _parse_asset(payload: object) -> ReleaseAsset:
    if not isinstance(payload, dict):
        raise TypeError("asset")
    digest = payload.get("digest")
    if digest is not None and not isinstance(digest, str):
        raise TypeError("digest")
    return ReleaseAsset(
        github_asset_id=_required_int(payload, "id"),
        name=_required_str(payload, "name"),
        size=_optional_int(payload, "size"),
        content_type=_optional_str(payload, "content_type"),
        state=_required_str(payload, "state"),
        digest=digest,
        browser_download_url=_required_str(payload, "browser_download_url"),
        created_at=_optional_str(payload, "created_at"),
        updated_at=_optional_str(payload, "updated_at"),
    )


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(key)
    return value


def _optional_int(payload: dict[str, object], key: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(key)
    return value


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or value == "":
        raise TypeError(key)
    return value


def _optional_str(payload: dict[str, object], key: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise TypeError(key)
    return value


def _required_bool(payload: dict[str, object], key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise TypeError(key)
    return value


def _invalid(rate_limit: RateLimitInfo, message: str) -> GitHubHTTPError:
    return GitHubHTTPError(ErrorCode.API_ERROR, message, rate_limit=rate_limit)
