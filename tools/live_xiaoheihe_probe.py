"""Probe Xiaoheihe post via WebEngine DOM (HTTP is SPA shell only)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

URL = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
JS = r"""
(() => {
  const textOf = (el) => (el && (el.innerText || el.textContent) || '').trim();
  const htmlOf = (el) => (el && el.innerHTML) || '';
  const q = (s) => document.querySelector(s);
  const title =
    textOf(q('h1')) ||
    textOf(q('[class*="title"]')) ||
    textOf(q('.post-title')) ||
    (document.title || '').trim();
  const author =
    textOf(q('[class*="author"]')) ||
    textOf(q('[class*="user-name"]')) ||
    textOf(q('[class*="nickname"]')) ||
    '';
  const bodyEl =
    q('#post-static') ||
    q('[class*="post-content"]') ||
    q('[class*="article-content"]') ||
    q('[class*="bbs-content"]') ||
    q('article') ||
    q('[class*="rich-text"]') ||
    q('main');
  const imgs = Array.from(document.querySelectorAll('img')).slice(0, 20).map((img) => img.src || img.getAttribute('data-src') || '');
  return JSON.stringify({
    url: location.href,
    title,
    author,
    body_len: bodyEl ? textOf(bodyEl).length : 0,
    body_preview: bodyEl ? textOf(bodyEl).slice(0, 300) : '',
    body_html_len: bodyEl ? htmlOf(bodyEl).length : 0,
    has_post_static: !!q('#post-static'),
    img_count: document.querySelectorAll('img').length,
    imgs,
    text_len: textOf(document.body).length,
  });
})()
"""


def main() -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings

    tmp = Path(tempfile.mkdtemp(prefix="tv-xhh-probe-"))
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    profile = QWebEngineProfile("xhh-probe", app)
    page = QWebEnginePage(profile, app)
    view = QWebEngineView()
    view.setPage(page)
    view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    view.resize(1200, 800)
    view.show()
    holder: dict = {}

    def on_js(raw):
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            data = {"raw": str(raw)[:200]}
        holder["data"] = data
        out = ROOT / "docs" / "web_archive" / "xiaoheihe_probe.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        view.close()
        app.quit()

    def loaded(ok):
        QTimer.singleShot(8000, lambda: page.runJavaScript(JS, on_js))

    view.loadFinished.connect(loaded)
    view.setUrl(QUrl(URL))
    QTimer.singleShot(45000, lambda: (print("TIMEOUT"), app.quit()))
    app.exec()
    return 0 if holder.get("data", {}).get("body_len", 0) > 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
