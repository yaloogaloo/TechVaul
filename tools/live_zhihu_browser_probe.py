"""Optional live WebEngine probe for a Zhihu answer (requires display + network).

Usage:
  .\\.venv\\Scripts\\python.exe tools\\live_zhihu_browser_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

URL = "https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493"


def main() -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.web_capture_window import WebCaptureWindow, _DOM_EXTRACT_JS

    tmp = Path(tempfile.mkdtemp(prefix="tv-live-zhihu-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")

    app = QApplication.instance() or QApplication(sys.argv)
    context = prepare_context(db, archive)
    window = WebCaptureWindow(db, URL, use_login_profile=False)
    window.show()
    result_holder: dict = {"done": False}

    def after_load(ok: bool) -> None:
        def extract() -> None:
            window.view.page().runJavaScript(_DOM_EXTRACT_JS, on_js)

        QTimer.singleShot(8000, extract)  # allow JS/zse to settle

    def on_js(raw) -> None:
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            data = {}
        out = tmp / "probe.json"
        summary = {
            "url": data.get("url"),
            "title": data.get("title"),
            "author": data.get("author"),
            "body_len": data.get("body_len"),
            "has_login_ui": data.get("has_login_ui"),
            "body_preview": (data.get("body_html") or "")[:240],
        }
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        result_holder["done"] = True
        window.close()
        context.close()
        app.quit()

    window.view.loadFinished.connect(after_load)
    # Hard timeout
    QTimer.singleShot(45000, lambda: (print("TIMEOUT"), app.quit()))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
