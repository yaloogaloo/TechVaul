"""Confirm schemeless YouTube and BV both parse in the video workspace.

Uses tmp-video-route only. Does not open the user database.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DB = ROOT / "tmp-video-route" / "db.sqlite"
ARCHIVE = ROOT / "tmp-video-route" / "archive"
SHOT = ROOT / "docs" / "video" / "evidence"
os.environ["TECHVAULT_DATABASE"] = str(DB)
os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(ARCHIVE)
os.environ["TECHVAULT_DATA_DIR"] = str(ROOT / "tmp-video-route")
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing"
os.environ["QT_OPENGL"] = "software"


def main() -> int:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    context = prepare_context(DB, archive_root=ARCHIVE)
    window = DesktopWindow(context)
    window.resize(1440, 900)
    window.show()

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)

    def grab(name: str) -> None:
        pump(0.3)
        image = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if image.isNull() or not image.save(str(target), "PNG"):
            raise RuntimeError(str(target))
        print("saved", target, flush=True)

    def js(code: str) -> None:
        window.view.page().runJavaScript(code)
        pump(0.25)

    def body_has(needle: str) -> bool:
        found = {"ok": False}

        def store(value) -> None:
            found["ok"] = bool(value)

        window.view.page().runJavaScript(
            "document.body ? document.body.innerText.includes(" + json.dumps(needle) + ") : false",
            store,
        )
        pump(0.2)
        return found["ok"]

    def wait_for(needle: str, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if body_has(needle):
                return True
            pump(0.25)
        print("missing", needle, flush=True)
        return False

    def fill_and_probe(value: str) -> None:
        js(
            """
            (() => {
              const input = document.querySelector('input[aria-label="视频地址"]');
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
              setter.call(input, %s);
              input.dispatchEvent(new Event('input', { bubbles: true }));
              const probe = [...document.querySelectorAll('button')].find((node) => node.innerText.trim() === '解析视频');
              if (probe) probe.click();
            })()
            """
            % json.dumps(value)
        )

    def finish(code: int) -> None:
        window.close()
        context.close()
        app.exit(code)

    def sequence() -> None:
        try:
            if not wait_for("GitHub", 25):
                grab("09_route_load_failed.png")
                finish(1)
                return
            js(
                """
                (() => {
                  const hit = [...document.querySelectorAll('button')].find((node) =>
                    (node.innerText || '').replace(/\\s+/g, '').includes('视频')
                  );
                  if (hit) hit.click();
                })()
                """
            )
            if not wait_for("视频归档", 10):
                grab("09_route_nav_failed.png")
                finish(1)
                return
            fill_and_probe("youtube.com/watch?reload=9&v=0Z-vhBvBmUY")
            if not wait_for("孙宇晨", 50) or body_has("这不是有效的 BV 号"):
                grab("09_youtube_route_failed.png")
                finish(1)
                return
            grab("09_youtube_schemeless.png")
            fill_and_probe("BV1XTad6oEWe")
            if not wait_for("1080p", 45):
                grab("10_bv_route_failed.png")
                finish(1)
                return
            grab("10_bv_after_youtube.png")
            finish(0)
        except Exception as exc:
            print("capture failed", exc, flush=True)
            finish(1)

    QTimer.singleShot(800, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
