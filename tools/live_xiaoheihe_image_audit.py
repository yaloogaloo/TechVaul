"""Audit Xiaoheihe body images vs page chrome for link_id=f1ba7299a477.

Outputs docs/web_archive/xiaoheihe_image_audit.json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

URL = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
OUT = ROOT / "docs" / "web_archive" / "xiaoheihe_image_audit.json"

JS = r"""
(() => {
  const textOf = (el) => (el && (el.innerText || el.textContent) || '').trim();
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const h = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  window.scrollTo(0, Math.floor(h * 0.4));
  window.scrollTo(0, Math.floor(h * 0.8));
  window.scrollTo(0, h);
  window.scrollTo(0, 0);

  const bodyEl =
    q('.post__content') ||
    q('.hb-article') ||
    q('.hb-bbs-post .post__content') ||
    q('#post-static');

  const classify = (img) => {
    const src = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original') || '';
    const srcset = img.getAttribute('srcset') || img.getAttribute('data-srcset') || '';
    const cls = (img.className || '') + ' ' + ((img.parentElement && img.parentElement.className) || '');
    const inBody = !!(bodyEl && bodyEl.contains(img));
    const w = img.naturalWidth || img.width || 0;
    const hgt = img.naturalHeight || img.height || 0;
    let kind = 'other';
    if (/avatar|user-face|user_face|头像/i.test(cls + src)) kind = 'avatar';
    else if (/nav|icon|logo|emoji|badge/i.test(cls + src)) kind = 'chrome';
    else if (!inBody) kind = 'outside_body';
    else if (w > 0 && w < 48 && hgt > 0 && hgt < 48) kind = 'icon_small';
    else kind = 'body_content';
    return {
      kind,
      in_body: inBody,
      src: src.slice(0, 220),
      srcset: srcset.slice(0, 160),
      data_src: (img.getAttribute('data-src') || '').slice(0, 220),
      w, h: hgt,
      complete: !!img.complete,
    };
  };

  const all = qa('img').map(classify);
  const body = all.filter((x) => x.in_body);
  const bodyContent = body.filter((x) => x.kind === 'body_content');
  // Unique by path without query
  const uniq = (arr) => {
    const seen = new Set();
    const out = [];
    for (const x of arr) {
      const key = (x.src || x.data_src || '').split('?')[0];
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(x);
    }
    return out;
  };

  return JSON.stringify({
    url: location.href,
    title: textOf(q('.link-section-title')) || textOf(q('.section-title__content')) || document.title,
    page_img_count: all.length,
    body_img_count: body.length,
    body_content_img_count: bodyContent.length,
    body_content_unique: uniq(bodyContent).length,
    by_kind: all.reduce((acc, x) => { acc[x.kind] = (acc[x.kind] || 0) + 1; return acc; }, {}),
    body_content: uniq(bodyContent),
    body_all: body.slice(0, 40),
  });
})()
"""


def main() -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings

    tmp = Path(tempfile.mkdtemp(prefix="tv-xhh-img-"))
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    profile = QWebEngineProfile("xhh-img-audit", app)
    page = QWebEnginePage(profile, app)
    view = QWebEngineView()
    view.setPage(page)
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    view.resize(1200, 900)
    view.show()
    holder: dict = {}

    def finish(raw) -> None:
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            data = {"raw": str(raw)[:500]}
        holder["data"] = data
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
        QTimer.singleShot(200, app.quit)

    def on_load(ok: bool) -> None:
        # Wait for lazy images after scroll in JS.
        QTimer.singleShot(3500, lambda: page.runJavaScript(JS, finish))

    view.loadFinished.connect(on_load)
    view.load(QUrl(URL))
    QTimer.singleShot(90_000, app.quit)
    app.exec()
    return 0 if holder.get("data") else 2


if __name__ == "__main__":
    raise SystemExit(main())
