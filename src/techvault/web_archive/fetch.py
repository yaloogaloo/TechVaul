"""HTTP fetch for public web pages (Mode A)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from techvault.logging_setup import get_logger

logger = get_logger()

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


@dataclass(frozen=True)
class FetchOutcome:
    ok: bool
    final_url: str
    status_code: int
    html: str
    failure_code: str | None = None
    failure_message: str | None = None


def fetch_html(url: str, *, timeout: float = 30.0, client: httpx.Client | None = None) -> FetchOutcome:
    owns = client is None
    http = client or httpx.Client(headers=_BROWSER_HEADERS, follow_redirects=True, timeout=timeout)
    try:
        response = http.get(url)
    except httpx.TimeoutException:
        return FetchOutcome(False, url, 0, "", "TIMEOUT", "网页请求超时")
    except httpx.HTTPError as exc:
        logger.warning("Web fetch failed code=NETWORK url=%s err=%s", url, type(exc).__name__)
        return FetchOutcome(False, url, 0, "", "NETWORK", "无法访问该网页")
    finally:
        if owns:
            http.close()

    final = str(response.url)
    if response.status_code == 404:
        return FetchOutcome(False, final, 404, "", "NOT_FOUND", "链接失效或页面不存在")
    if response.status_code in {401, 403}:
        return FetchOutcome(False, final, response.status_code, "", "NEEDS_LOGIN", "需要登录后才能访问")
    if response.status_code >= 400:
        return FetchOutcome(
            False,
            final,
            response.status_code,
            "",
            "HTTP_ERROR",
            f"服务器返回 {response.status_code}",
        )
    text = response.text or ""
    if not text.strip():
        return FetchOutcome(False, final, response.status_code, "", "EMPTY", "页面内容为空")
    return FetchOutcome(True, final, response.status_code, text)
