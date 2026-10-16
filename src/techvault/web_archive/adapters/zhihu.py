"""Zhihu-specific extraction with structural + JSON fallbacks (not brittle CSS-only)."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from techvault.web_archive.adapters.base import AdapterResult
from techvault.web_archive.failure_codes import FailureCode
from techvault.web_archive.urls import ParsedWebUrl

# Soft markers appear on nearly every anonymous Zhihu shell — NEVER alone imply login.
_SOFT_LOGIN_UI = (
    "登录",
    "注册",
    "扫码登录",
    "账号密码登录",
    "二维码登录",
    "Sign in",
)
# Strong markers: body is gated and there is no readable article.
_STRONG_LOGIN_GATE = (
    "登录后才能查看",
    "请登录后查看完整内容",
    "登录后查看剩余",
    "登录后即可查看",
    "只有登录用户才能查看",
    "Sign in to Zhihu to continue",
)
_CAPTCHA_MARKERS = ("人机验证", "安全验证", "请完成安全验证")
_PAYWALL_MARKERS = ("开通会员后", "试读已结束", "付费内容", "付费咨询")
_INCOMPLETE_MARKERS = ("展开阅读全文", "显示全部", "阅读全文")


class ZhihuAdapter:
    site_key = "zhihu"

    def supports(self, parsed: ParsedWebUrl) -> bool:
        return parsed.site_key == "zhihu" and parsed.kind in {"answer", "article"}

    def extract(self, parsed: ParsedWebUrl, html: str, final_url: str) -> AdapterResult:
        soup = BeautifulSoup(html, "lxml")
        text_blob = soup.get_text(" ", strip=True)

        # Captcha / paywall before extraction — these are not "login".
        if _has_captcha(soup, text_blob):
            return AdapterResult(
                ok=False,
                failure_code=FailureCode.CAPTCHA_REQUIRED,
                failure_message="页面出现人机验证，请在浏览器中完成",
                extractor="zhihu-gate",
            )
        if any(m in text_blob for m in _PAYWALL_MARKERS) and not _has_rich_body_hint(soup):
            return AdapterResult(
                ok=False,
                failure_code=FailureCode.PAYWALL,
                failure_message="检测到付费或权限限制内容，不会尝试绕过",
                extractor="zhihu-gate",
            )

        initial = _parse_initial_data(html)
        if parsed.kind == "article":
            result = _from_article_json(initial, parsed) or _from_article_dom(soup, parsed)
        else:
            result = _from_answer_json(initial, parsed, final_url) or _from_answer_dom(soup, parsed)

        # Prefer successful extraction even when soft login UI is present.
        if result is not None and result.ok and _body_plain_len(result.body_html) >= 20:
            warnings = list(result.warnings)
            if any(m in text_blob for m in _INCOMPLETE_MARKERS) and len(result.body_html) < 400:
                return AdapterResult(
                    ok=False,
                    failure_code=FailureCode.CONTENT_INCOMPLETE,
                    failure_message="正文疑似未完整加载（存在展开全文提示）",
                    extractor=result.extractor,
                    title=result.title,
                    author=result.author,
                    body_html=result.body_html,
                )
            result.warnings = warnings
            return result

        # No usable body — only then consider strong login gate.
        if _strong_login_without_body(soup, text_blob):
            evidence = _login_evidence(soup, text_blob)
            return AdapterResult(
                ok=False,
                failure_code=FailureCode.NEEDS_LOGIN,
                failure_message="有明确证据表明目标正文需登录后才能访问",
                extractor="zhihu-gate",
                warnings=[f"login_evidence={evidence}"],
            )

        if result is not None and not result.ok:
            return result
        return AdapterResult(
            ok=False,
            failure_code=FailureCode.EXTRACTION_FAILED,
            failure_message="未能提取知乎正文",
            extractor="zhihu",
        )


def _has_captcha(soup: BeautifulSoup, text_blob: str) -> bool:
    if soup.select_one("#zh-zse-ck, .Captcha, [class*='Captcha']"):
        # zse cookie challenge alone is access restriction handled at fetch; here for HTML bodies.
        if 'id="zh-zse-ck"' in str(soup) and len(text_blob) < 200:
            return False  # leave to ACCESS_RESTRICTED at HTTP layer
    lowered = text_blob.lower()
    return any(m.lower() in lowered for m in _CAPTCHA_MARKERS)


def _has_rich_body_hint(soup: BeautifulSoup) -> bool:
    return soup.select_one(".RichText, .Post-RichText, .AnswerItem, .Post-content") is not None


def _strong_login_without_body(soup: BeautifulSoup, text_blob: str) -> bool:
    if _has_rich_body_hint(soup) and _body_plain_len(str(soup.select_one(".RichText") or "")) >= 40:
        return False
    title = (soup.title.string or "") if soup.title else ""
    if re.fullmatch(r".*登录\s*[-–|].*知乎.*", title.strip()) or title.strip() in {"登录 - 知乎", "登录｜知乎"}:
        return True
    return any(marker in text_blob for marker in _STRONG_LOGIN_GATE)


def _login_evidence(soup: BeautifulSoup, text_blob: str) -> str:
    hits = [m for m in _STRONG_LOGIN_GATE if m in text_blob]
    title = (soup.title.string or "") if soup.title else ""
    if "登录" in title:
        hits.append(f"title={title.strip()[:40]}")
    return ",".join(hits) or "login-page-structure"


def _body_plain_len(html: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html or "").strip())


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
        # Prefer exact id; do not silently grab unrelated answer on multi-answer shells.
        if aid:
            return None
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
    return AdapterResult(
        ok=True,
        title=title,
        author=author,
        published_at=None,
        body_html=content,
        extractor="zhihu-json-answer",
    )


def _from_article_dom(soup: BeautifulSoup, parsed: ParsedWebUrl) -> AdapterResult | None:
    title_el = soup.select_one("h1.Post-Title, h1.ArticleTitle, article h1, h1")
    body_el = soup.select_one(".Post-RichText, .RichText.ztext, article .RichText, .Post-content .RichText")
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
    body_el = soup.select_one(
        ".AnswerItem .RichText, .AnswerCard .RichText, .RichContent-inner .RichText, "
        ".QuestionAnswer-content .RichText"
    )
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
