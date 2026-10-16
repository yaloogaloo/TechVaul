"""URL recognition and canonicalization for web archives."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlunparse


@dataclass(frozen=True)
class ParsedWebUrl:
    original: str
    canonical: str
    site_key: str
    site_label: str
    kind: str  # answer | article | question | generic | unsupported
    message: str = ""


def parse_web_url(raw: str) -> ParsedWebUrl:
    text = (raw or "").strip()
    if not text:
        return ParsedWebUrl("", "", "unknown", "未知", "unsupported", "请输入网页链接")
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return ParsedWebUrl(raw, "", "unknown", "未知", "unsupported", "链接格式无效")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ParsedWebUrl(raw, "", "unknown", "未知", "unsupported", "链接格式无效")

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"

    if host in {"zhihu.com", "www.zhihu.com"} or host.endswith(".zhihu.com"):
        return _parse_zhihu(text, host, path, parsed)
    if host in {"xiaoheihe.cn", "www.xiaoheihe.cn", "api.xiaoheihe.cn"} or host.endswith(
        ".xiaoheihe.cn"
    ):
        return _parse_xiaoheihe(text, host, path, parsed)
    # Preserve caller scheme (do not force https) so local http:// stress fixtures work.
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    canonical = urlunparse((scheme, host, path.rstrip("/") or "/", "", "", ""))
    return ParsedWebUrl(raw, canonical, "generic", host, "generic")


def _parse_zhihu(original: str, host: str, path: str, parsed) -> ParsedWebUrl:
    # Share / redirect style: zhihu.com/question/123 or zhuanlan
    if host == "zhuanlan.zhihu.com":
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "p" and parts[1].isdigit():
            canonical = f"https://zhuanlan.zhihu.com/p/{parts[1]}"
            return ParsedWebUrl(original, canonical, "zhihu", "知乎", "article")
        return ParsedWebUrl(original, "", "zhihu", "知乎", "unsupported", "无法识别的知乎专栏链接")

    if host in {"zhihu.com", "www.zhihu.com"}:
        parts = [p for p in path.split("/") if p]
        # /question/<qid>/answer/<aid>
        if len(parts) >= 4 and parts[0] == "question" and parts[2] == "answer":
            qid, aid = parts[1], parts[3]
            if qid.isdigit() and aid.isdigit():
                canonical = f"https://www.zhihu.com/question/{qid}/answer/{aid}"
                return ParsedWebUrl(original, canonical, "zhihu", "知乎", "answer")
        # Question hub only
        if len(parts) >= 2 and parts[0] == "question" and parts[1].isdigit():
            if len(parts) == 2 or (len(parts) == 3 and parts[2] == ""):
                return ParsedWebUrl(
                    original,
                    f"https://www.zhihu.com/question/{parts[1]}",
                    "zhihu",
                    "知乎",
                    "question",
                    "请打开具体回答链接后再归档；暂不支持整页问题批量归档。",
                )
        # Mobile share: /answer/<aid> sometimes
        if len(parts) >= 2 and parts[0] == "answer" and parts[1].isdigit():
            return ParsedWebUrl(
                original,
                f"https://www.zhihu.com/answer/{parts[1]}",
                "zhihu",
                "知乎",
                "answer",
            )
        # target redirect query
        qs = parse_qs(parsed.query)
        target = (qs.get("target") or qs.get("url") or [None])[0]
        if target:
            return parse_web_url(target)

    return ParsedWebUrl(original, "", "zhihu", "知乎", "unsupported", "暂不支持该知乎页面类型")


def _parse_xiaoheihe(original: str, host: str, path: str, parsed) -> ParsedWebUrl:
    """Normalize Heybox / Xiaoheihe share + community post URLs to a stable link_id page."""
    qs = parse_qs(parsed.query)
    link_id = (qs.get("link_id") or [None])[0]
    parts = [p for p in path.split("/") if p]

    # api.xiaoheihe.cn/.../share?link_id=...
    if not link_id and host.startswith("api."):
        link_id = (qs.get("link_id") or [None])[0]

    # /app/bbs/link/<link_id>
    if not link_id and len(parts) >= 4 and parts[0] == "app" and parts[1] == "bbs" and parts[2] == "link":
        link_id = parts[3]

    # /bbs/link/<link_id>
    if not link_id and len(parts) >= 3 and parts[0] == "bbs" and parts[1] == "link":
        link_id = parts[2]

    # Short /a/<id> is NOT assumed valid (404 observed); leave unsupported unless proven.
    if not link_id and len(parts) >= 2 and parts[0] == "a":
        return ParsedWebUrl(
            original,
            "",
            "xiaoheihe",
            "小黑盒",
            "unsupported",
            "短链 /a/<id> 需先打开官方分享页以确认帖子身份",
        )

    if link_id and _looks_like_heybox_link_id(link_id):
        canonical = f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
        return ParsedWebUrl(original, canonical, "xiaoheihe", "小黑盒", "article")

    return ParsedWebUrl(
        original,
        "",
        "xiaoheihe",
        "小黑盒",
        "unsupported",
        "无法识别小黑盒帖子链接（需要 link_id）",
    )


def _looks_like_heybox_link_id(value: str) -> bool:
    text = (value or "").strip()
    if len(text) < 6 or len(text) > 64:
        return False
    return all(ch.isalnum() or ch in {"_", "-"} for ch in text)
