"""Capture Web Archive workspace screenshots (isolated DB + seeded article)."""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SHOT = ROOT / "docs" / "web_archive" / "screenshots"
FIXTURES = ROOT / "tests" / "fixtures" / "web_archive"


def seed_article(db: Path, archive: Path) -> None:
    from techvault.app.bootstrap import prepare_context
    from techvault.web_archive.service import WebArchiveService

    context = prepare_context(db, archive)
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/1001/answer/2002"
    html = (FIXTURES / "zhihu_answer.html").read_text(encoding="utf-8")
    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, text=html))
        respx.get("https://pic1.zhimg.com/v2-demo.png").mock(
            return_value=httpx.Response(200, content=b"PNG-DEMO")
        )
        result = service.archive(url)
    if result.article_id is None:
        raise RuntimeError(result.message)
    context.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="techvault-web-shot-"))
    db = tmp / "techvault.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_DATABASE"] = str(db)
    os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(archive)
    os.environ.pop("TECHVAULT_SLATE_SHELL_DEMO", None)

    seed_article(db, archive)

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    context = prepare_context(db, archive_root=archive)
    window = DesktopWindow(context)
    window.show()
    paths: list[str] = []

    def grab(name: str, w: int, h: int) -> None:
        window.resize(w, h)
        window.show()
        for _ in range(12):
            app.processEvents()
        img = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if not img.save(str(target), "PNG"):
            raise RuntimeError(str(target))
        paths.append(str(target))
        print("saved", target)

    def js(code: str) -> None:
        window.view.page().runJavaScript(code)
        time.sleep(0.6)
        for _ in range(20):
            app.processEvents()

    def click_nav(label: str) -> None:
        js(
            f"""
            (() => {{
              const rail = document.querySelector('nav[aria-label=\"主导航\"]');
              const buttons = [...(rail || document).querySelectorAll('button')];
              const hit = buttons.find(b => (b.innerText || '').replace(/\\s+/g, '').includes('{label}'));
              if (hit) {{ hit.click(); return true; }}
              return false;
            }})()
            """
        )

    def click_first_row() -> None:
        js(
            """
            (() => {
              const rows = [...document.querySelectorAll('button')].filter(b =>
                (b.innerText || '').includes('学习') || (b.innerText || '').includes('知乎')
              );
              if (rows[0]) { rows[0].click(); return true; }
              return false;
            })()
            """
        )

    def finish() -> None:
        meta = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "files": paths,
            "tmp": str(tmp),
        }
        (SHOT / "capture_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        window.close()
        context.close()
        app.quit()

    def sequence() -> None:
        time.sleep(1.2)
        for _ in range(30):
            app.processEvents()
        click_nav("网页")
        time.sleep(1.2)
        for _ in range(40):
            app.processEvents()
        # Wait for auto-selected detail to render
        time.sleep(1.0)
        for _ in range(30):
            app.processEvents()
        grab("web_workspace_1440x900.png", 1440, 900)
        click_first_row()
        time.sleep(1.0)
        for _ in range(30):
            app.processEvents()
        grab("web_reader_1440x900.png", 1440, 900)
        grab("web_reader_960x640.png", 960, 640)
        js(
            """
            (() => {
              const tabs = [...document.querySelectorAll('button')].filter(b =>
                (b.innerText || '').includes('归档信息')
              );
              if (tabs[0]) tabs[0].click();
            })()
            """
        )
        time.sleep(0.5)
        for _ in range(16):
            app.processEvents()
        grab("web_info_1440x900.png", 1440, 900)
        finish()

    QTimer.singleShot(1800, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
