"""Capture the video archive workspace from the isolated live-download database.

This script never opens D:\\project\\TechVault\\data\\techvault.sqlite.
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

DB = ROOT / "tmp-video-live" / "db.sqlite"
ARCHIVE = ROOT / "tmp-video-live" / "archive"
SHOT = ROOT / "docs" / "video" / "evidence"
os.environ["TECHVAULT_DATABASE"] = str(DB)
os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(ARCHIVE)
os.environ["TECHVAULT_DATA_DIR"] = str(ROOT / "tmp-video-live")
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing"
os.environ["QT_OPENGL"] = "software"


def main() -> int:
    if not DB.is_file():
        print("missing isolated video database", DB)
        return 1
    from PySide6.QtCore import Qt
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
    paths: list[str] = []

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)

    def grab(name: str, width: int, height: int) -> None:
        window.resize(width, height)
        window.show()
        pump(0.4)
        image = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if image.isNull() or not image.save(str(target), "PNG"):
            raise RuntimeError(f"save failed: {target}")
        paths.append(str(target))
        print("saved", target, image.width(), image.height(), flush=True)

    def js(code: str) -> None:
        window.view.page().runJavaScript(code)
        pump(0.3)

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
            pump(0.3)
        print("missing", needle, flush=True)
        return False

    def click_text(label: str) -> None:
        js(
            """
            (() => {
              const hit = [...document.querySelectorAll('button')].find((node) =>
                (node.innerText || '').replace(/\\s+/g, '').includes(%s)
              );
              if (hit) hit.click();
            })()
            """
            % json.dumps(label)
        )

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
                grab("00_load_failed.png", 1440, 900)
                finish(1)
                return
            click_text("视频")
            if not wait_for("视频归档", 10):
                grab("00_video_nav_failed.png", 1440, 900)
                finish(1)
                return
            if not wait_for("Me at the zoo", 10):
                grab("00_list_failed.png", 1440, 900)
                finish(1)
                return
            grab("01_video_home.png", 1440, 900)
            click_text("旧手机畅玩Docker")
            wait_for("打开本地视频", 8)
            grab("06_detail.png", 1440, 900)
            fill_and_probe("BV1XTad6oEWe")
            if not wait_for("将使用", 45):
                grab("02_bv_parse_failed.png", 1440, 900)
                finish(1)
                return
            grab("02_bv_parse.png", 1440, 900)
            js(
                """
                (() => {
                  const select = document.querySelector('select[aria-label="画质"]');
                  const option = [...select.options].find((item) => item.value === '720');
                  if (!option) return;
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
                  setter.call(select, '720');
                  select.dispatchEvent(new Event('change', { bubbles: true }));
                })()
                """
            )
            wait_for("720p", 5)
            grab("04_quality.png", 1440, 900)
            click_text("下载视频")
            if wait_for("仍然重新下载", 25):
                click_text("仍然重新下载")
            if wait_for("正在下载", 40):
                grab("05_progress.png", 1440, 900)
                click_text("取消")
                wait_for("已取消", 30)
            else:
                grab("05_progress_missed.png", 1440, 900)
            fill_and_probe("https://www.youtube.com/watch?v=jNQXAC9IVRw")
            if not wait_for("最高可用 240p", 45):
                grab("03_youtube_parse_failed.png", 1440, 900)
                finish(1)
                return
            grab("03_youtube_parse.png", 1440, 900)
            grab("08_960x640.png", 960, 640)
            window.resize(1440, 900)
            pump(0.3)
            click_text("打开本地视频")
            pump(1.2)
            grab("07_after_open_local.png", 1440, 900)
            print("captures", len(paths), flush=True)
            finish(0)
        except Exception as exc:
            print("capture failed", exc, flush=True)
            finish(1)

    from PySide6.QtCore import QTimer

    QTimer.singleShot(800, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
