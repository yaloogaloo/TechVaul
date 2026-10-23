"""Download one image with size, redirect, and address limits.

The GitHub token is never attached. Callers must pass an httpx client that does
not add Authorization on its own.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from threading import Event
from urllib.parse import urljoin, urlsplit

import httpx

from techvault.readme_images.sniff import MAX_IMAGE_BYTES, sanitize_svg, sniff_image

_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.internal.",
}


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    data: bytes | None = None
    extension: str | None = None
    mime: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    failure_code: str | None = None
    failure_message: str | None = None


def fetch_image(
    client: httpx.Client,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    cancel_event: Event | None = None,
) -> FetchResult:
    headers = {
        "User-Agent": "TechVault",
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,image/svg+xml,image/*;q=0.8",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    current = url
    try:
        for _hop in range(_MAX_REDIRECTS + 1):
            if cancel_event is not None and cancel_event.is_set():
                return _fail("CANCELLED", "已取消")
            if not is_public_http_url(current):
                return _fail("UNSAFE_URL", "图片地址不在允许的公网范围内")
            response = client.get(current, headers=headers)
            try:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("Location")
                    if not location:
                        return _fail("REDIRECT", "图片重定向缺少 Location")
                    current = urljoin(current, location)
                    headers.pop("If-None-Match", None)
                    headers.pop("If-Modified-Since", None)
                    continue
                if response.status_code == 304:
                    return FetchResult(
                        ok=True,
                        not_modified=True,
                        etag=_header(response, "ETag") or etag,
                        last_modified=_header(response, "Last-Modified") or last_modified,
                    )
                if response.status_code == 404:
                    return _fail("NOT_FOUND", "图片不存在")
                if response.status_code in {401, 403}:
                    return _fail("FORBIDDEN", "图片无权访问")
                if response.status_code != 200:
                    return _fail("HTTP_ERROR", f"HTTP {response.status_code}")
                advertised = _content_length(response)
                if advertised is not None and advertised > MAX_IMAGE_BYTES:
                    return _fail("TOO_LARGE", "图片超过大小限制")
                try:
                    data = _read_limited(response, cancel_event)
                except _TooLarge:
                    return _fail("TOO_LARGE", "图片超过大小限制")
                if data is None:
                    return _fail("CANCELLED", "已取消")
                sniffed = sniff_image(data)
                if sniffed is None:
                    return _fail("BAD_TYPE", "响应不是受支持的图片")
                extension, mime = sniffed
                if extension == "svg":
                    sanitized = sanitize_svg(data)
                    if sanitized is None:
                        return _fail("BAD_TYPE", "SVG 无法安全保存")
                    data = sanitized
                return FetchResult(
                    ok=True,
                    data=data,
                    extension=extension,
                    mime=mime,
                    etag=_header(response, "ETag"),
                    last_modified=_header(response, "Last-Modified"),
                )
            finally:
                response.close()
        return _fail("REDIRECT", "图片重定向次数过多")
    except httpx.TimeoutException:
        return _fail("TIMEOUT", "图片下载超时")
    except httpx.RequestError:
        return _fail("NETWORK", "图片下载失败")


def is_public_http_url(url: str) -> bool:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"}:
        return False
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return _public_ip(literal)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not _public_ip(address):
            return False
    return True


def _public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("Content-Length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _read_limited(response: httpx.Response, cancel_event: Event | None) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        if cancel_event is not None and cancel_event.is_set():
            return None
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _header(response: httpx.Response, name: str) -> str | None:
    value = response.headers.get(name)
    if value is None:
        return None
    text = value.strip()
    return text or None


class _TooLarge(Exception):
    pass


def _fail(code: str, message: str) -> FetchResult:
    return FetchResult(ok=False, failure_code=code, failure_message=message)
