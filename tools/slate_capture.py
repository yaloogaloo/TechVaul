"""Capture TechVault Slate desktop screenshots into docs/ux/slate_screenshots/.

Uses isolated TECHVAULT_DATABASE / TECHVAULT_ARCHIVE_ROOT only.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SHOT = ROOT / "docs" / "ux" / "slate_screenshots"


def main() -> int:
    mode = os.environ.get("TECHVAULT_SLATE_CAPTURE_MODE", "shell-demo")
    tmp = Path(tempfile.mkdtemp(prefix="techvault-slate-"))
    db = tmp / "techvault.sqlite"
    archive = tmp / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    os.environ["TECHVAULT_DATABASE"] = str(db)
    os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(archive)
    if mode == "shell-demo":
        os.environ["TECHVAULT_SLATE_SHELL_DEMO"] = "1"

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from techvault.ui.web_scheme import register_scheme

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from PySide6.QtGui import QGuiApplication

    context = prepare_context(db, archive_root=archive)
    window = DesktopWindow(context)
    window.show()

    paths: list[str] = []

    def grab(name: str, w: int, h: int) -> None:
        window.resize(w, h)
        window.show()
        for _ in range(5):
            app.processEvents()
        image = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if not image.save(str(target), "PNG"):
            raise RuntimeError(f"save failed: {target}")
        paths.append(str(target))
        print("saved", target)

    def sequence() -> None:
        try:
            # Wait for WebEngine first paint.
            deadline = datetime.now(timezone.utc).timestamp() + 8
            while datetime.now(timezone.utc).timestamp() < deadline:
                app.processEvents()
                if window.view.page().url().toString().endswith("index.html"):
                    break
            for _ in range(30):
                app.processEvents()
                QTimer.singleShot  # keep import used
            # Extra settle for React.
            import time

            time.sleep(1.2)
            for _ in range(20):
                app.processEvents()

            grab("a2_shell_1686x896.png", 1686, 896)
            grab("a2_shell_1440x900.png", 1440, 900)
            grab("a2_shell_960x640.png", 960, 640)

            # Exercise tabs / long repo via JS when page ready.
            page = window.view.page()
            page.runJavaScript(
                """
                (() => {
                  const buttons = [...document.querySelectorAll('button')];
                  const long = buttons.find(b => (b.innerText||'').includes('TypeScript'));
                  if (long) long.click();
                  return true;
                })()
                """
            )
            time.sleep(0.6)
            for _ in range(15):
                app.processEvents()
            grab("a2_shell_long_name_1440.png", 1440, 900)

            page.runJavaScript(
                """
                (() => {
                  const tabs = [...document.querySelectorAll('[role=tab]')];
                  const versions = tabs.find(t => (t.innerText||'').includes('版本'));
                  if (versions) versions.click();
                  return true;
                })()
                """
            )
            time.sleep(0.4)
            for _ in range(10):
                app.processEvents()
            grab("a2_shell_versions_tab.png", 1440, 900)

            screen = QGuiApplication.primaryScreen()
            meta = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "database": str(db),
                "archive_root": str(archive),
                "logical_dpi": None if screen is None else screen.logicalDotsPerInch(),
                "device_pixel_ratio": None if screen is None else screen.devicePixelRatio(),
                "method": "QWidget.grab via tools/slate_capture.py",
                "screenshots": paths,
                "note": "A2 shell-demo uses static React fixtures; not business parity evidence.",
            }
            (SHOT / "capture_meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print("CAPTURE_OK")
        except Exception as exc:
            print(f"CAPTURE_FAILED: {exc!r}", file=sys.stderr)
            app.exit(1)
            return
        finally:
            context.close()
        app.exit(0)

    QTimer.singleShot(1500, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
