"""Structured capture failure / outcome codes (not all map to NEEDS_LOGIN)."""

from __future__ import annotations


class FailureCode:
    SUCCESS = "SUCCESS"
    NEEDS_LOGIN = "NEEDS_LOGIN"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    HTTP_ERROR = "HTTP_ERROR"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    EMPTY = "EMPTY"
    EXTRACTION_FAILED = "EXTRACT_FAILED"
    CONTENT_INCOMPLETE = "INCOMPLETE"
    UNSUPPORTED_PAGE = "UNSUPPORTED"
    QUESTION_HUB = "QUESTION_HUB"
    PAYWALL = "PAYWALL"
    CANCELLED = "CANCELLED"
    IO_ERROR = "IO_ERROR"
    INTEGRITY = "INTEGRITY"
    NO_ARCHIVE_ROOT = "NO_ARCHIVE_ROOT"
    INVALID_URL = "INVALID_URL"
    WORKER = "WORKER"


_USER_MESSAGES = {
    FailureCode.NEEDS_LOGIN: "当前页面确实需要登录，请在浏览器中登录后保存。",
    FailureCode.CAPTCHA_REQUIRED: "知乎要求进行人机验证，请在浏览器中完成。",
    FailureCode.ACCESS_RESTRICTED: "网页访问被限制（可能需要浏览器渲染）。",
    FailureCode.HTTP_ERROR: "网页请求失败，请检查网络连接。",
    FailureCode.NETWORK: "无法访问该网页，请检查网络连接。",
    FailureCode.TIMEOUT: "网页请求超时。",
    FailureCode.NOT_FOUND: "链接失效或页面不存在。",
    FailureCode.EMPTY: "页面内容为空。",
    FailureCode.EXTRACTION_FAILED: "页面可以访问，但未能识别文章正文。",
    FailureCode.CONTENT_INCOMPLETE: "正文疑似不完整，可在浏览器中展开后保存。",
    FailureCode.UNSUPPORTED_PAGE: "暂不支持该页面类型。",
    FailureCode.QUESTION_HUB: "请打开具体回答链接后再归档；暂不支持整页问题批量归档。",
    FailureCode.PAYWALL: "检测到付费或权限限制内容，不会尝试绕过。",
}


def user_message_for(code: str | None, fallback: str | None = None) -> str:
    if code and code in _USER_MESSAGES:
        return _USER_MESSAGES[code]
    return fallback or "网页归档失败"


def suggest_browser_capture(code: str | None) -> bool:
    return code in {
        FailureCode.ACCESS_RESTRICTED,
        FailureCode.CAPTCHA_REQUIRED,
        FailureCode.EXTRACTION_FAILED,
        FailureCode.CONTENT_INCOMPLETE,
        FailureCode.HTTP_ERROR,
        FailureCode.EMPTY,
        # Not for true NEEDS_LOGIN-only unless user opens browser to log in —
        # still useful to offer browser so they can log in there.
        FailureCode.NEEDS_LOGIN,
    }
