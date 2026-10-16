"""Shared DOM extraction script for WebEngine capture (manual + auto).

Never re-fetches article HTML via httpx — callers publish from this payload.
"""

from __future__ import annotations

# Runs in page context. Returns JSON string.
DOM_EXTRACT_JS = r"""
(() => {
  const textOf = (el) => (el && (el.innerText || el.textContent) || '').trim();
  const htmlOf = (el) => (el && el.innerHTML) || '';
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));

  // Soft overlays should not block capture of already-rendered body.
  try {
    const closeBtn =
      q('.Modal-closeButton') ||
      q('button[aria-label="关闭"]') ||
      q('.Button.Modal-closeButton');
    if (closeBtn) closeBtn.click();
  } catch (e) {}
  try {
    const expand = Array.from(document.querySelectorAll('button, a, .Button')).find((el) =>
      /展开阅读全文|阅读全文/.test(textOf(el))
    );
    if (expand) expand.click();
  } catch (e) {}
  // Nudge lazy-loaded images (Heybox / Zhihu) into the viewport before snapshot.
  try {
    const h = Math.max(
      document.body ? document.body.scrollHeight : 0,
      document.documentElement ? document.documentElement.scrollHeight : 0
    );
    window.scrollTo(0, Math.floor(h * 0.35));
    window.scrollTo(0, Math.floor(h * 0.7));
    window.scrollTo(0, 0);
  } catch (e) {}

  const title =
    textOf(q('.link-section-title')) ||
    textOf(q('.section-title__content')) ||
    textOf(q('h1.QuestionHeader-title')) ||
    textOf(q('.QuestionHeader-title')) ||
    textOf(q('h1.Post-Title')) ||
    textOf(q('h1.ArticleTitle')) ||
    textOf(q('article h1')) ||
    textOf(q('h1')) ||
    (document.title || '').trim();
  const author =
    textOf(q('.AnswerItem .AuthorInfo-name')) ||
    textOf(q('.AnswerCard .AuthorInfo-name')) ||
    textOf(q('.Post-Header .AuthorInfo-name')) ||
    textOf(q('.AuthorInfo-name')) ||
    textOf(q('.UserLink-link')) ||
    textOf(q('.hb-bbs-link__header [class*="name"]')) ||
    (() => {
      const header = q('.hb-bbs-link__header') || q('.hb-cpt-page-header');
      if (!header) return '';
      const lines = textOf(header).split('\\n').map((s) => s.trim()).filter(Boolean);
      for (const line of lines) {
        if (['返回', '关注', '登录', '注册'].includes(line)) continue;
        if (line.length >= 2) return line;
      }
      return '';
    })();

  // Prefer answer / column / heybox post containers; avoid stray sidebar shells.
  const bodyCandidates = [
    q('.post__content'),
    q('.hb-article'),
    q('.hb-bbs-post .post__content'),
    q('#post-static'),
    q('.AnswerItem .RichText'),
    q('.AnswerCard .RichText'),
    q('.RichContent-inner .RichText'),
    q('.QuestionAnswer-content .RichText'),
    q('.Post-RichTextContainer .RichText'),
    q('.Post-RichText'),
    q('.Post-content .RichText'),
    q('article .RichText.ztext'),
    q('article .RichText'),
    q('.RichText.ztext.Post-RichText'),
    q('.RichText.ztext'),
    q('[itemprop="articleBody"]'),
  ].filter(Boolean);

  let bodyEl = null;
  let bestLen = 0;
  for (const el of bodyCandidates) {
    const n = textOf(el).length;
    if (n > bestLen) {
      bestLen = n;
      bodyEl = el;
    }
  }
  // Last resort: longest .RichText on the page (still skip tiny chrome).
  if (!bodyEl || bestLen < 40) {
    for (const el of qa('.RichText')) {
      const n = textOf(el).length;
      if (n > bestLen) {
        bestLen = n;
        bodyEl = el;
      }
    }
  }

  const body = htmlOf(bodyEl);
  const bodyText = bodyEl ? textOf(bodyEl) : '';
  const expanded = !!(
    document.body.innerText.includes('收起') ||
    !document.body.innerText.includes('展开阅读全文')
  );
  const hasLoginUi = /登录|扫码/.test(document.body.innerText || '');
  let notReady = '';
  if (!bodyEl) notReady = 'no_body_container';
  else if (bodyText.length < 80) notReady = 'body_too_short';
  else if (!body.trim()) notReady = 'body_html_empty';
  else if (title === '安全验证' || title === '请完成安全验证' || title === '登录') notReady = 'challenge_title';

  const pageType =
    /xiaoheihe\.cn/i.test(location.href) || q('.hb-bbs-post') || q('.post__content')
      ? 'xiaoheihe_post'
      : /zhuanlan\.zhihu\.com/i.test(location.href) || q('.Post-Title') || q('.Post-RichText')
        ? 'zhihu_article'
        : /\/answer\//i.test(location.href) || q('.AnswerItem') || q('.QuestionHeader-title')
          ? 'zhihu_answer'
          : 'generic';

  return JSON.stringify({
    title: title,
    author: author,
    body_html: body,
    body_len: bodyText.length,
    url: location.href,
    page_html: document.documentElement.outerHTML,
    has_login_ui: hasLoginUi,
    looks_expanded: expanded,
    has_body_container: !!bodyEl,
    not_ready_reason: notReady,
    page_type: pageType
  });
})()
"""

# Minimum plain-text length for auto-capture (login chrome alone is shorter).
AUTO_CAPTURE_MIN_BODY_LEN = 80
# On stage timeout, allow a shorter body through publish (PARTIAL may still apply later).
TIMEOUT_CAPTURE_MIN_BODY_LEN = 40


def payload_ready_for_auto_capture(payload: dict) -> bool:
    """True when rendered DOM looks like a real article body, not login/empty shell."""
    if not isinstance(payload, dict):
        return False
    body_len = int(payload.get("body_len") or 0)
    body_html = (payload.get("body_html") or "").strip()
    if body_len < AUTO_CAPTURE_MIN_BODY_LEN or not body_html:
        return False
    if not payload.get("has_body_container"):
        return False
    # Avoid saving pure challenge / empty shells mistaken as RichText.
    title = (payload.get("title") or "").strip()
    if title in {"安全验证", "请完成安全验证", "登录"}:
        return False
    return True


def payload_usable_on_timeout(payload: dict) -> bool:
    """Softer gate used when the stage timer expires — try publish if body exists."""
    if not isinstance(payload, dict):
        return False
    body_len = int(payload.get("body_len") or 0)
    body_html = (payload.get("body_html") or "").strip()
    if body_len < TIMEOUT_CAPTURE_MIN_BODY_LEN or not body_html:
        return False
    if not payload.get("has_body_container"):
        return False
    title = (payload.get("title") or "").strip()
    if title in {"安全验证", "请完成安全验证", "登录"}:
        return False
    return True


def not_ready_reason(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "invalid_payload"
    explicit = (payload.get("not_ready_reason") or "").strip()
    if explicit:
        return explicit
    if not payload.get("has_body_container"):
        return "no_body_container"
    if int(payload.get("body_len") or 0) < AUTO_CAPTURE_MIN_BODY_LEN:
        return "body_too_short"
    if not (payload.get("body_html") or "").strip():
        return "body_html_empty"
    title = (payload.get("title") or "").strip()
    if title in {"安全验证", "请完成安全验证", "登录"}:
        return "challenge_title"
    return "unknown"
