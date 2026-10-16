"""HTTP fetch for public web pages (Mode A)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx

from techvault.logging_setup import get_logger
from techvault.web_archive.failure_codes import FailureCode

logger = get_logger()

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


@dataclass(frozen=True)
class FetchOutcome:
    ok: bool
    final_url: str
    status_code: int
    html: str
    content_type: str = ""
    failure_code: str | None = None
    failure_message: str | None = None
    diagnostics: dict | None = None


def sanitize_url_for_log(url: str) -> str:
    """Strip common share/tracking query params before logging."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "(invalid-url)"
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def fetch_html(url: str, *, timeout: float = 30.0, client: httpx.Client | None = None) -> FetchOutcome:
    owns = client is None
    http = client or httpx.Client(headers=_BROWSER_HEADERS, follow_redirects=True, timeout=timeout)
    try:
        response = http.get(url)
    except httpx.TimeoutException:
        return FetchOutcome(False, url, 0, "", failure_code=FailureCode.TIMEOUT, failure_message="网页请求超时")
    except httpx.HTTPError as exc:
        logger.warning(
            "Web fetch failed code=NETWORK url=%s err=%s",
            sanitize_url_for_log(url),
            type(exc).__name__,
        )
        return FetchOutcome(False, url, 0, "", failure_code=FailureCode.NETWORK, failure_message="无法访问该网页")
    finally:
        if owns:
            http.close()

    final = str(response.url)
    content_type = response.headers.get("content-type", "")
    text = response.text or ""
    diag = {
        "request_url": sanitize_url_for_log(url),
        "final_url": sanitize_url_for_log(final),
        "status_code": response.status_code,
        "content_type": content_type.split(";")[0].strip(),
        "body_length": len(text),
        "has_zse_challenge": 'id="zh-zse-ck"' in text or "zh-zse-ck" in text,
        "set_cookie_present": "set-cookie" in {k.lower() for k in response.headers.keys()},
    }
    logger.info(
        "Web fetch diag status=%s len=%s ctype=%s zse=%s url=%s",
        diag["status_code"],
        diag["body_length"],
        diag["content_type"],
        diag["has_zse_challenge"],
        diag["final_url"],
    )

    if response.status_code == 404:
        return FetchOutcome(
            False, final, 404, text, content_type, FailureCode.NOT_FOUND, "链接失效或页面不存在", diag
        )
    if response.status_code == 401:
        return FetchOutcome(
            False, final, 401, text, content_type, FailureCode.NEEDS_LOGIN, "服务器要求身份认证", diag
        )
    # Zhihu anonymous bot wall is often 403 + zse challenge — NOT the same as login.
    if response.status_code in {403, 412, 429} or diag["has_zse_challenge"]:
        return FetchOutcome(
            False,
            final,
            response.status_code,
            text,
            content_type,
            FailureCode.ACCESS_RESTRICTED,
            "网页访问被限制（可能需要浏览器渲染），可尝试在浏览器中打开并保存。",
            diag,
        )
    if response.status_code >= 400:
        return FetchOutcome(
            False,
            final,
            response.status_code,
            text,
            content_type,
            FailureCode.HTTP_ERROR,
            f"服务器返回 {response.status_code}",
            diag,
        )
    if not text.strip():
        return FetchOutcome(
            False, final, response.status_code, "", content_type, FailureCode.EMPTY, "页面内容为空", diag
        )
    return FetchOutcome(True, final, response.status_code, text, content_type, diagnostics=diag)
