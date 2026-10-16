"""Xiaoheihe / Heybox BBS post extraction (DOM-first; SPA shells need Chromium)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from techvault.web_archive.adapters.base import AdapterResult
from techvault.web_archive.failure_codes import FailureCode
from techvault.web_archive.urls import ParsedWebUrl

_SOFT_LOGIN = ("登录", "注册", "Sign in")
_STRONG_LOGIN = ("登录后查看", "请登录后查看", "登录后才能查看")
_APP_ONLY = ("请在小黑盒 App 中打开", "打开小黑盒 App", "仅支持 App 内查看")


class XiaoheiheAdapter:
    site_key = "xiaoheihe"

    def supports(self, parsed: ParsedWebUrl) -> bool:
        return parsed.site_key == "xiaoheihe" and parsed.kind == "article"

    def extract(self, parsed: ParsedWebUrl, html: str, final_url: str) -> AdapterResult:
        soup = BeautifulSoup(html, "lxml")
        text_blob = soup.get_text(" ", strip=True)

        if any(m in text_blob for m in _APP_ONLY) and not _has_post_body(soup):
            return AdapterResult(
                ok=False,
                failure_code=FailureCode.ACCESS_RESTRICTED,
                failure_message="页面提示需在小黑盒 App 中打开",
                extractor="xiaoheihe-gate",
            )

        # Bare SPA shell from anonymous HTTP — not a login wall.
        if _is_spa_shell(soup, text_blob):
            return AdapterResult(
                ok=False,
                failure_code=FailureCode.CONTENT_INCOMPLETE,
                failure_message="页面需要浏览器渲染后才能读取帖子正文",
                extractor="xiaoheihe-spa",
            )

        title = _extract_title(soup)
        author = _extract_author(soup)
        body_el = _find_body(soup)
        body_html = ""
        if body_el is not None:
            # Drop comment blocks if nested.
            for junk in body_el.select(
                ".comment-item__content, .comment-item__content-container, "
                ".hb-bbs-link__header, .nav-content"
            ):
                junk.decompose()
            body_html = str(body_el)

        plain_len = len(re.sub(r"\s+", "", BeautifulSoup(body_html, "lxml").get_text(" ", strip=True)))
        if plain_len >= 20 or (body_el is not None and body_el.select("img") and plain_len >= 0):
            # Image-heavy posts: allow shorter text if images exist.
            if plain_len < 20 and not body_el.select("img"):
                pass
            else:
                warnings: list[str] = []
                if any(m in text_blob for m in _SOFT_LOGIN):
                    warnings.append("soft_login_ui")
                if plain_len < 20:
                    warnings.append("image_primary_post")
                return AdapterResult(
                    ok=True,
                    title=title or "未命名帖子",
                    author=author,
                    body_html=body_html,
                    extractor="xiaoheihe-dom",
                    warnings=warnings,
                )

        if any(m in text_blob for m in _STRONG_LOGIN) and plain_len < 20:
            return AdapterResult(
                ok=False,
                failure_code=FailureCode.NEEDS_LOGIN,
                failure_message="有明确证据表明目标正文需登录后才能访问",
                extractor="xiaoheihe-gate",
            )

        return AdapterResult(
            ok=False,
            failure_code=FailureCode.EXTRACTION_FAILED,
            failure_message="未能识别小黑盒帖子正文",
            extractor="xiaoheihe",
            title=title,
            author=author,
        )


def _is_spa_shell(soup: BeautifulSoup, text_blob: str) -> bool:
    if soup.select_one(".post__content, .hb-article, .hb-bbs-post, #post-static"):
        return False
    # Known heybox website shell: empty #app + vite bundles, short text.
    if soup.select_one("#app") and len(text_blob) < 80:
        return True
    if "heybox-project" in str(soup.head or "") and len(text_blob) < 120:
        return True
    return False


def _has_post_body(soup: BeautifulSoup) -> bool:
    return soup.select_one(".post__content, .hb-article, .hb-bbs-post") is not None


def _extract_title(soup: BeautifulSoup) -> str:
    for sel in (
        ".link-section-title",
        ".section-title__content",
        ".hb-bbs-post .link-section-title",
        "h1",
    ):
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if text and text not in {"小黑盒", "小黑盒 - 玩家高能聚集地"}:
                return text
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title and "小黑盒" not in title:
        return title
    return ""


def _extract_author(soup: BeautifulSoup) -> str | None:
    for sel in (
        ".hb-bbs-link__header [class*='name']",
        ".post__container [class*='username']",
        ".post__container [class*='user-name']",
        ".hb-bbs-post [class*='author']",
    ):
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if text and text not in {"关注", "返回", "登录"}:
                return text
    # Fallback: username often appears before title in header block.
    header = soup.select_one(".hb-bbs-link__header, .hb-cpt-page-header")
    if header:
        lines = [ln.strip() for ln in header.get_text("\n", strip=True).splitlines() if ln.strip()]
        for line in lines:
            if line in {"返回", "关注", "登录", "注册"}:
                continue
            if len(line) >= 2:
                return line
    return None


def _find_body(soup: BeautifulSoup):
    for sel in (
        ".post__content",
        ".hb-article",
        ".hb-bbs-post .post__content",
        ".hb-bbs-link__content .post__content",
        "#post-static",
    ):
        el = soup.select_one(sel)
        if el is not None:
            return el
    # Broader container as last resort (may include tags).
    return soup.select_one(".hb-bbs-post .post__container, .hb-bbs-post")
