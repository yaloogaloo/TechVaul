"""Zhihu-specific extraction with structural + JSON fallbacks (not brittle CSS-only)."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from techvault.web_archive.adapters.base import AdapterResult
from techvault.web_archive.urls import ParsedWebUrl

_LOGIN_MARKERS = (
    "登录后才能查看",
    "请登录后查看",
    "账号密码登录",
    "二维码登录",
    "Sign in to Zhihu",
)
_CAPTCHA_MARKERS = ("人机验证", "captcha", "安全验证")
_PAYWALL_MARKERS = ("付费 orth", "开通会员后", "试读已结束", "付费内容")
_INCOMPLETE_MARKERS = ("展开阅读全文", "显示全部", "阅读全文")


class ZhihuAdapter:
    site_key = "zhihu"

    def supports(self, parsed: ParsedWebUrl) -> bool:
        return parsed.site_key == "zhihu" and parsed.kind in {"answer", "article"}

    def extract(self, parsed: ParsedWebUrl, html: str, final_url: str) -> AdapterResult:
        soup = BeautifulSoup(html, "lxml")
        text_blob = soup.get_text(" ", strip=True)

        if _looks_like_login(soup, text_blob):
            return AdapterResult(
                ok=False,
                failure_code="NEEDS_LOGIN",
                failure_message="页面需要登录后才能查看完整内容",
                extractor="zhihu-gate",
            )
        if any(m.lower() in text_blob.lower() for m in _CAPTCHA_MARKERS):
            return AdapterResult(
                ok=False,
                failure_code="CAPTCHA",
                failure_message="页面出现验证码，无法自动归档",
                extractor="zhihu-gate",
            )
        if any(m in text_blob for m in _PAYWALL_MARKERS):
            return AdapterResult(
                ok=False,
                failure_code="PAYWALL",
                failure_message="检测到付费或权限限制内容，不会尝试绕过",
                extractor="zhihu-gate",
            )

        initial = _parse_initial_data(html)
        if parsed.kind == "article":
            result = _from_article_json(initial, parsed) or _from_article_dom(soup, parsed)
        else:
            result = _from_answer_json(initial, parsed, final_url) or _from_answer_dom(soup, parsed)

        if result is None or not result.ok:
            return result or AdapterResult(
                ok=False,
                failure_code="EXTRACT_FAILED",
                failure_message="未能提取知乎正文",
                extractor="zhihu",
            )

        warnings = list(result.warnings)
        if any(m in text_blob for m in _INCOMPLETE_MARKERS) and len(result.body_html) < 400:
            return AdapterResult(
                ok=False,
                failure_code="INCOMPLETE",
                failure_message="正文疑似未完整加载（存在展开全文提示）",
                extractor=result.extractor,
                title=result.title,
                author=result.author,
            )
        if len(re.sub(r"<[^>]+>", "", result.body_html).strip()) < 20:
            return AdapterResult(
                ok=False,
                failure_code="INCOMPLETE",
                failure_message="提取到的正文过短，可能未加载完成",
                extractor=result.extractor,
                title=result.title,
                author=result.author,
            )
        result.warnings = warnings
        return result


def _looks_like_login(soup: BeautifulSoup, text_blob: str) -> bool:
    title = (soup.title.string or "") if soup.title else ""
    if "登录" in title and "知乎" in title:
        return True
    return any(marker in text_blob for marker in _LOGIN_MARKERS)


def _parse_initial_data(html: str) -> dict | None:
    patterns = (
        r'<script id="js-initialData" type="text/json">(.*?)</script>',
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*</script>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            continue
        raw = match.group(1).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return None


def _from_article_json(data: dict | None, parsed: ParsedWebUrl) -> AdapterResult | None:
    if not data:
        return None
    entities = data.get("initialState", data).get("entities", data.get("entities", {}))
    articles = entities.get("articles") or {}
    article_id = parsed.canonical.rstrip("/").split("/")[-1]
    node = articles.get(str(article_id)) or next(iter(articles.values()), None)
    if not isinstance(node, dict):
        return None
    content = node.get("content") or node.get("contentHtml") or ""
    if not content.strip():
        return None
    title = unescape(str(node.get("title") or "知乎专栏文章"))
    author = None
    author_obj = node.get("author")
    if isinstance(author_obj, dict):
        author = author_obj.get("name") or author_obj.get("fullname_name")
    published = node.get("updated") or node.get("created") or node.get("publishedTime")
    if isinstance(published, (int, float)):
        published = None
    return AdapterResult(
        ok=True,
        title=title,
        author=author,
        published_at=str(published) if published else None,
        body_html=content,
        extractor="zhihu-json-article",
    )


def _from_answer_json(data: dict | None, parsed: ParsedWebUrl, final_url: str) -> AdapterResult | None:
    if not data:
        return None
    root = data.get("initialState", data)
    entities = root.get("entities", data.get("entities", {}))
    answers = entities.get("answers") or {}
    questions = entities.get("questions") or {}
    aid = _answer_id(parsed, final_url)
    node = answers.get(str(aid)) if aid else None
    if node is None and answers:
        node = next(iter(answers.values()), None)
    if not isinstance(node, dict):
        return None
    content = node.get("content") or node.get("editableContent") or ""
    if not content.strip():
        return None
    qid = str(node.get("question", {}).get("id") or "") if isinstance(node.get("question"), dict) else ""
    question = questions.get(qid) if qid else None
    q_title = ""
    if isinstance(question, dict):
        q_title = question.get("title") or ""
    title = unescape(q_title or "知乎回答")
    author = None
    author_obj = node.get("author")
    if isinstance(author_obj, dict):
        author = author_obj.get("name")
    published = node.get("updatedTime") or node.get("createdTime")
    return AdapterResult(
        ok=True,
        title=title,
        author=author,
        published_at=None,
        body_html=content,
        extractor="zhihu-json-answer",
        warnings=[] if not isinstance(published, (int, float)) else [],
    )


def _from_article_dom(soup: BeautifulSoup, parsed: ParsedWebUrl) -> AdapterResult | None:
    title_el = soup.select_one("h1.Post-Title, h1.ArticleTitle, article h1, h1")
    body_el = soup.select_one(".Post-RichText, .RichText, article .RichText, .Post-content")
    if body_el is None:
        return None
    author_el = soup.select_one(".AuthorInfo-name, .UserLink-link, meta[itemprop='author']")
    author = None
    if author_el:
        author = author_el.get("content") if author_el.name == "meta" else author_el.get_text(strip=True)
    title = title_el.get_text(strip=True) if title_el else "知乎专栏文章"
    return AdapterResult(
        ok=True,
        title=title,
        author=author,
        published_at=None,
        body_html=str(body_el),
        extractor="zhihu-dom-article",
    )


def _from_answer_dom(soup: BeautifulSoup, parsed: ParsedWebUrl) -> AdapterResult | None:
    title_el = soup.select_one("h1.QuestionHeader-title, .QuestionHeader-title, h1")
    # Prefer answer rich text; avoid stitching question + random answer incorrectly.
    body_el = soup.select_one(".AnswerItem .RichText, .AnswerCard .RichText, .RichContent-inner .RichText")
    if body_el is None:
        return None
    author_el = soup.select_one(".AnswerItem .AuthorInfo-name, .AnswerCard .UserLink-link")
    author = author_el.get_text(strip=True) if author_el else None
    title = title_el.get_text(strip=True) if title_el else "知乎回答"
    return AdapterResult(
        ok=True,
        title=title,
        author=author,
        published_at=None,
        body_html=str(body_el),
        extractor="zhihu-dom-answer",
    )


def _answer_id(parsed: ParsedWebUrl, final_url: str) -> str | None:
    for candidate in (parsed.canonical, final_url):
        parts = [p for p in urlparse(candidate).path.split("/") if p]
        if "answer" in parts:
            idx = parts.index("answer")
            if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                return parts[idx + 1]
    return None
