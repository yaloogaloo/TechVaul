"""Generic Readability extraction + adapter orchestration."""

from __future__ import annotations

from readability import Document

from techvault.web_archive.adapters import ZhihuAdapter, try_adapters
from techvault.web_archive.adapters.base import AdapterResult
from techvault.web_archive.models import ExtractedArticle
from techvault.web_archive.sanitize_article import html_to_plain_text, sanitize_article_html
from techvault.web_archive.urls import ParsedWebUrl

_ADAPTERS = [ZhihuAdapter()]


def extract_article(parsed: ParsedWebUrl, html: str, final_url: str) -> AdapterResult | ExtractedArticle:
    if parsed.kind == "question":
        return AdapterResult(
            ok=False,
            failure_code="QUESTION_HUB",
            failure_message=parsed.message or "请选择具体回答链接",
            extractor="url-guard",
        )
    if parsed.kind == "unsupported":
        return AdapterResult(
            ok=False,
            failure_code="UNSUPPORTED",
            failure_message=parsed.message or "暂不支持该链接类型",
            extractor="url-guard",
        )

    adapted = try_adapters(_ADAPTERS, parsed, html, final_url)
    if adapted is not None:
        if not adapted.ok:
            return adapted
        body = sanitize_article_html(adapted.body_html)
        plain = html_to_plain_text(body)
        return ExtractedArticle(
            title=adapted.title or "未命名文章",
            author=adapted.author,
            published_at=adapted.published_at,
            body_html=body,
            plain_text=plain,
            site_key=parsed.site_key,
            site_label=parsed.site_label,
            canonical_url=parsed.canonical,
            source_url=parsed.original,
            extractor=adapted.extractor,
            warnings=list(adapted.warnings),
        )

    # Generic Mozilla Readability (readability-lxml)
    try:
        doc = Document(html)
        title = (doc.short_title() or doc.title() or "未命名文章").strip()
        summary = doc.summary(html_partial=True)
    except Exception:
        return AdapterResult(
            ok=False,
            failure_code="EXTRACT_FAILED",
            failure_message="通用正文提取失败",
            extractor="readability",
        )
    body = sanitize_article_html(summary)
    plain = html_to_plain_text(body)
    if len(plain) < 20:
        return AdapterResult(
            ok=False,
            failure_code="INCOMPLETE",
            failure_message="提取到的正文过短",
            extractor="readability",
            title=title,
        )
    return ExtractedArticle(
        title=title,
        author=None,
        published_at=None,
        body_html=body,
        plain_text=plain,
        site_key=parsed.site_key,
        site_label=parsed.site_label,
        canonical_url=parsed.canonical or final_url,
        source_url=parsed.original,
        extractor="readability-lxml",
    )
