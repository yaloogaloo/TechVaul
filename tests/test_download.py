import hashlib
from pathlib import Path
from threading import Event

import httpx
import respx

from techvault.archive.downloader import (
    CHUNK_BYTES,
    DigestStatus,
    DownloadRequest,
    FailureCode,
    FileState,
    download_release_file,
)
from techvault.github.client import GitHubClient

TOKEN = "ghp_supersecrettokenvalue"
API_FILE = "https://api.github.com/repos/octo/hello/releases/assets/1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request(tmp_path: Path, *, metadata_size: int | None = None, digest: str | None = None) -> DownloadRequest:
    return DownloadRequest(
        url=API_FILE,
        part_path=tmp_path / "_tmp" / "9.part",
        final_path=tmp_path / "repositories" / "octo" / "out.bin",
        metadata_size=metadata_size,
        upstream_digest=digest,
    )


def _download(
    tmp_path: Path,
    response: httpx.Response,
    request: DownloadRequest,
    *,
    assert_all_called: bool = True,
    **kwargs,
):
    with respx.mock(assert_all_called=assert_all_called) as router:
        router.get(API_FILE).mock(return_value=response)
        with GitHubClient(token=TOKEN) as client:
            result = download_release_file(client, request, **kwargs)
    return result


def test_streaming_download_uses_one_mebibyte_chunks_and_replaces_part(monkeypatch, tmp_path) -> None:
    source = Path("src/techvault/archive/downloader.py").read_text(encoding="utf-8")
    assert "response.content" not in source
    seen: list[tuple[int | None, int]] = []
    original = httpx.Response.iter_bytes

    def wrapped(self, chunk_size=None):
        count = 0
        for chunk in original(self, chunk_size=chunk_size):
            count += 1
            yield chunk
        seen.append((chunk_size, count))

    monkeypatch.setattr(httpx.Response, "iter_bytes", wrapped)
    body = b"a" * (CHUNK_BYTES + 1)
    request = _request(tmp_path, metadata_size=len(body))
    request.part_path.parent.mkdir(parents=True)
    request.part_path.write_bytes(b"STALE-PART")
    result = _download(
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        request,
    )
    assert result.state == FileState.COMPLETED
    assert (CHUNK_BYTES, 2) in seen
    assert request.final_path.read_bytes() == body
    assert not request.part_path.exists()
    assert b"STALE-PART" not in request.final_path.read_bytes()
    assert list(request.final_path.parent.rglob("*.part")) == []


def test_content_length_match_and_mismatch_and_absence(tmp_path) -> None:
    body = b"zip-bytes"
    matched = _download(
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        _request(tmp_path),
    )
    assert matched.state == FileState.COMPLETED
    assert matched.expected_size == len(body)
    assert matched.actual_size == len(body)
    assert matched.local_sha256 == _sha(body)
    assert matched.digest_status == DigestStatus.ABSENT

    mismatch = _download(
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Length": "99"}),
        _request(tmp_path / "mismatch"),
    )
    assert mismatch.state == FileState.FAILED
    assert mismatch.failure_code == FailureCode.SIZE_MISMATCH
    assert not (tmp_path / "mismatch" / "repositories" / "octo" / "out.bin").exists()

    missing = httpx.Response(200, content=body)
    del missing.headers["content-length"]
    absent = _download(tmp_path / "absent", missing, _request(tmp_path / "absent"))
    assert absent.state == FileState.COMPLETED
    assert absent.expected_size is None
    assert absent.actual_size == len(body)
    assert absent.local_sha256 == _sha(body)


def test_asset_metadata_size_and_http_size_are_both_checked(tmp_path) -> None:
    body = b"asset"
    metadata_mismatch = _download(
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        _request(tmp_path, metadata_size=len(body) + 5),
    )
    assert metadata_mismatch.failure_code == FailureCode.SIZE_MISMATCH
    assert not (tmp_path / "repositories" / "octo" / "out.bin").exists()

    both = _download(
        tmp_path / "both",
        httpx.Response(200, content=body, headers={"Content-Length": "99"}),
        _request(tmp_path / "both", metadata_size=len(body)),
    )
    assert both.failure_code == FailureCode.SIZE_MISMATCH
    assert not (tmp_path / "both" / "repositories" / "octo" / "out.bin").exists()


def test_reread_hash_must_match_stream_hash(tmp_path) -> None:
    body = b"stable-bytes"
    request = _request(tmp_path, metadata_size=len(body))

    def corrupt(path: Path) -> None:
        path.write_bytes(path.read_bytes() + b"x")

    result = _download(
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        request,
        after_stream=corrupt,
    )
    assert result.state == FileState.FAILED
    assert result.failure_code == FailureCode.CHECKSUM_FAILURE
    assert result.local_sha256 is None
    assert request.part_path.exists()
    assert not request.final_path.exists()


def test_asset_digest_match_mismatch_malformed_and_absent(tmp_path) -> None:
    body = b"payload"
    good = "sha256:" + _sha(body)
    matched = _download(
        tmp_path,
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        _request(tmp_path, metadata_size=len(body), digest=good),
    )
    assert matched.state == FileState.COMPLETED
    assert matched.digest_status == DigestStatus.MATCHED
    assert matched.local_sha256 == _sha(body)
    assert matched.local_sha256 == good.split(":", 1)[1]
    assert matched.local_sha256 == matched.local_sha256.lower()
    assert len(matched.local_sha256) == 64

    mismatched = _download(
        tmp_path / "bad",
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        _request(tmp_path / "bad", metadata_size=len(body), digest="sha256:" + _sha(b"other")),
    )
    assert mismatched.failure_code == FailureCode.CHECKSUM_FAILURE
    assert not (tmp_path / "bad" / "repositories" / "octo" / "out.bin").exists()

    malformed = _download(
        tmp_path / "malformed",
        httpx.Response(200, content=body),
        _request(tmp_path / "malformed", metadata_size=len(body), digest="sha256:ABC"),
        assert_all_called=False,
    )
    assert malformed.failure_code == FailureCode.DIGEST_UNSUPPORTED
    assert not (tmp_path / "malformed" / "repositories" / "octo" / "out.bin").exists()

    absent = _download(
        tmp_path / "null",
        httpx.Response(200, content=body, headers={"Content-Length": str(len(body))}),
        _request(tmp_path / "null", metadata_size=len(body), digest=None),
    )
    assert absent.state == FileState.COMPLETED
    assert absent.digest_status == DigestStatus.ABSENT


def test_cancel_keeps_part_and_does_not_publish_final(tmp_path) -> None:
    request = _request(tmp_path, metadata_size=3)
    cancel = Event()

    def respond(request_sent: httpx.Request) -> httpx.Response:
        del request_sent
        cancel.set()
        return httpx.Response(200, content=b"abc", headers={"Content-Length": "3"})

    with respx.mock() as router:
        router.get(API_FILE).mock(side_effect=respond)
        with GitHubClient(token=TOKEN) as client:
            result = download_release_file(client, request, cancel_event=cancel)
    assert result.state == FileState.CANCELLED
    assert request.part_path.exists()
    assert not request.final_path.exists()


def test_http_errors_are_classified_for_downloads(tmp_path) -> None:
    cases = [
        (401, {}, FailureCode.AUTH),
        (403, {"X-RateLimit-Remaining": "0"}, FailureCode.RATE_LIMIT),
        (403, {}, FailureCode.FORBIDDEN),
        (404, {}, FailureCode.NOT_FOUND),
        (429, {}, FailureCode.RATE_LIMIT),
        (500, {}, FailureCode.API_ERROR),
        (206, {}, FailureCode.API_ERROR),
    ]
    for index, (status, headers, code) in enumerate(cases):
        root = tmp_path / str(index)
        result = _download(root, httpx.Response(status, json={"message": "no"}, headers=headers), _request(root))
        assert result.failure_code == code
        assert not (root / "repositories" / "octo" / "out.bin").exists()


def test_timeout_and_network_errors(tmp_path) -> None:
    with respx.mock() as router:
        router.get(API_FILE).mock(side_effect=httpx.ReadTimeout("slow"))
        with GitHubClient() as client:
            timed_out = download_release_file(client, _request(tmp_path))
    assert timed_out.failure_code == FailureCode.TIMEOUT

    with respx.mock() as router:
        router.get(API_FILE).mock(side_effect=httpx.ConnectError("down"))
        with GitHubClient() as client:
            failed = download_release_file(client, _request(tmp_path / "net"))
    assert failed.failure_code == FailureCode.NETWORK


def test_redirects_strip_authorization_except_on_api_host(tmp_path) -> None:
    token_header = f"Bearer {TOKEN}"
    targets = [
        "https://release-assets.githubusercontent.com/objects/file",
        "https://codeload.github.com/octo/hello/legacy.zip/v1",
        "https://example.invalid/archive.bin",
        "https://api.github.com/repos/octo/hello/zipball/v1",
    ]
    start = "https://api.github.com/repos/octo/hello/zipball/v1.2.3"
    for index, target in enumerate(targets):
        root = tmp_path / str(index)
        with respx.mock() as router:
            if target == start:
                router.get(start).mock(return_value=httpx.Response(200, content=b"ok"))
            else:
                router.get(start).mock(return_value=httpx.Response(302, headers={"Location": target}))
                router.get(target).mock(return_value=httpx.Response(200, content=b"ok"))
            request = DownloadRequest(
                url=start,
                part_path=root / "_tmp" / "1.part",
                final_path=root / "source-code.zip",
                metadata_size=2,
                upstream_digest=None,
            )
            with GitHubClient(token=TOKEN) as client:
                result = download_release_file(client, request)
            assert result.state == FileState.COMPLETED
            assert result.digest_status == DigestStatus.ABSENT
            assert request.final_path.read_bytes() == b"ok"
            assert router.calls[0].request.headers["Authorization"] == token_header
            if urlparse_host(target) == "api.github.com":
                assert router.calls[-1].request.headers["Authorization"] == token_header
            else:
                assert "authorization" not in {name.lower() for name in router.calls[-1].request.headers}


def urlparse_host(url: str) -> str | None:
    from urllib.parse import urlparse

    return urlparse(url).hostname
