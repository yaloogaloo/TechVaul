"""Stream a release file into _tmp, verify it, then move it into the archive."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Event

import httpx

from techvault.errors import ErrorCode, GitHubHTTPError
from techvault.github.client import GitHubClient

CHUNK_BYTES = 1024 * 1024
_SHA256_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class FileState:
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class FailureCode:
    SIZE_MISMATCH = "SIZE_MISMATCH"
    CHECKSUM_FAILURE = "CHECKSUM_FAILURE"
    DIGEST_UNSUPPORTED = "DIGEST_UNSUPPORTED"
    ASSET_NOT_UPLOADED = "ASSET_NOT_UPLOADED"
    AUTH = "AUTH"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMIT = "RATE_LIMIT"
    API_ERROR = "API_ERROR"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    UNSAFE_PATH = "UNSAFE_PATH"


class DigestStatus:
    ABSENT = "ABSENT"
    MATCHED = "MATCHED"


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    part_path: Path
    final_path: Path
    metadata_size: int | None
    upstream_digest: str | None


@dataclass(frozen=True)
class DownloadResult:
    state: str
    failure_code: str | None
    failure_message: str | None
    actual_size: int | None
    expected_size: int | None
    local_sha256: str | None
    digest_status: str | None


def download_release_file(
    client: GitHubClient,
    request: DownloadRequest,
    cancel_event: Event | None = None,
    after_stream=None,
    on_verifying=None,
    on_bytes=None,
) -> DownloadResult:
    request.part_path.parent.mkdir(parents=True, exist_ok=True)
    if request.part_path.exists():
        request.part_path.unlink()
    request.part_path.write_bytes(b"")
    if _cancelled(cancel_event):
        return _cancelled_result()

    digest_problem = _digest_problem(request.upstream_digest)
    if digest_problem is not None:
        return DownloadResult(
            state=FileState.FAILED,
            failure_code=FailureCode.DIGEST_UNSUPPORTED,
            failure_message=digest_problem,
            actual_size=None,
            expected_size=request.metadata_size,
            local_sha256=None,
            digest_status=None,
        )

    try:
        response = client.request("GET", request.url)
    except GitHubHTTPError as exc:
        code = exc.code if exc.code in {ErrorCode.TIMEOUT, ErrorCode.NETWORK, ErrorCode.API_ERROR} else exc.code
        return _failed(code, exc.message, request.metadata_size)

    try:
        if response.status_code == 206:
            return _failed(FailureCode.API_ERROR, "Partial content is not a complete file", request.metadata_size)
        if response.status_code != 200:
            return _failed(
                _http_failure_code(response),
                f"HTTP {response.status_code}",
                request.metadata_size,
            )
        content_length = _content_length(response)
        stream_hash = hashlib.sha256()
        actual_size = 0
        expected_now = request.metadata_size if request.metadata_size is not None else content_length
        _report_bytes(on_bytes, 0, expected_now, force=True)
        with request.part_path.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=CHUNK_BYTES):
                if _cancelled(cancel_event):
                    return _cancelled_result(actual_size)
                handle.write(chunk)
                stream_hash.update(chunk)
                actual_size += len(chunk)
                _report_bytes(on_bytes, actual_size, expected_now)
        _report_bytes(on_bytes, actual_size, expected_now, force=True)
        if _cancelled(cancel_event):
            return _cancelled_result(actual_size)
        if on_verifying is not None:
            on_verifying()
        if after_stream is not None:
            after_stream(request.part_path)
        reread_hash = _hash_file(request.part_path)
        stream_value = stream_hash.hexdigest()
        if stream_value != reread_hash:
            return _failed(
                FailureCode.CHECKSUM_FAILURE,
                "Stream SHA256 does not match the file reread",
                _resolved_expected(request.metadata_size, content_length),
                actual_size,
            )
        expected = _resolved_expected(request.metadata_size, content_length)
        if not _sizes_match(actual_size, request.metadata_size, content_length):
            return _failed(
                FailureCode.SIZE_MISMATCH,
                "Downloaded size does not match a known size",
                expected,
                actual_size,
            )
        digest_status = DigestStatus.ABSENT
        if request.upstream_digest is not None:
            upstream_hex = request.upstream_digest.split(":", 1)[1]
            if upstream_hex != reread_hash:
                return _failed(
                    FailureCode.CHECKSUM_FAILURE,
                    "Upstream digest does not match local SHA256",
                    expected,
                    actual_size,
                )
            digest_status = DigestStatus.MATCHED
        request.final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(request.part_path, request.final_path)
        return DownloadResult(
            state=FileState.COMPLETED,
            failure_code=None,
            failure_message=None,
            actual_size=actual_size,
            expected_size=expected,
            local_sha256=reread_hash,
            digest_status=digest_status,
        )
    except httpx.TimeoutException:
        return _failed(FailureCode.TIMEOUT, "Download timed out", request.metadata_size)
    except httpx.RequestError:
        return _failed(FailureCode.NETWORK, "Download network request failed", request.metadata_size)
    finally:
        response_to_close = locals().get("response")
        if response_to_close is not None:
            response_to_close.close()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _digest_problem(digest: str | None) -> str | None:
    if digest is None:
        return None
    if _SHA256_DIGEST.fullmatch(digest) is None:
        return "Upstream digest is not sha256:<64 lowercase hex>"
    return None


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("Content-Length")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _resolved_expected(metadata_size: int | None, content_length: int | None) -> int | None:
    if metadata_size is not None:
        return metadata_size
    return content_length


def _sizes_match(actual: int, metadata_size: int | None, content_length: int | None) -> bool:
    if metadata_size is not None and actual != metadata_size:
        return False
    if content_length is not None and actual != content_length:
        return False
    if (
        metadata_size is not None
        and content_length is not None
        and metadata_size != content_length
    ):
        return False
    return True


def _http_failure_code(response: httpx.Response) -> str:
    status = response.status_code
    if status == 401:
        return FailureCode.AUTH
    if status == 429 or (status == 403 and _is_rate_limit(response)):
        return FailureCode.RATE_LIMIT
    if status == 403:
        return FailureCode.FORBIDDEN
    if status == 404:
        return FailureCode.NOT_FOUND
    return FailureCode.API_ERROR


def _is_rate_limit(response: httpx.Response) -> bool:
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    try:
        payload = response.json()
    except ValueError:
        return False
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return "rate limit" in payload["message"].lower()
    return False


def _report_bytes(on_bytes, downloaded: int, expected: int | None, force: bool = False) -> None:
    if on_bytes is None:
        return
    on_bytes(downloaded, expected, force)


def _cancelled(cancel_event: Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _cancelled_result(actual_size: int | None = None) -> DownloadResult:
    return DownloadResult(
        state=FileState.CANCELLED,
        failure_code=None,
        failure_message="Download cancelled",
        actual_size=actual_size,
        expected_size=None,
        local_sha256=None,
        digest_status=None,
    )


def _failed(
    code: str,
    message: str,
    expected_size: int | None,
    actual_size: int | None = None,
) -> DownloadResult:
    return DownloadResult(
        state=FileState.FAILED,
        failure_code=code,
        failure_message=message,
        actual_size=actual_size,
        expected_size=expected_size,
        local_sha256=None,
        digest_status=None,
    )
