"""Business-mode Slate screenshots against an isolated seeded database."""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SHOT = ROOT / "docs" / "ux" / "slate_screenshots"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="techvault-slate-biz-"))
    db = tmp / "techvault.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_DATABASE"] = str(db)
    os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(archive)
    os.environ.pop("TECHVAULT_SLATE_SHELL_DEMO", None)

    from tools.slate_seed import seed

    seed(db, archive)

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QGuiApplication
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
        for _ in range(8):
            app.processEvents()
        img = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if not img.save(str(target), "PNG"):
            raise RuntimeError(target)
        paths.append(str(target))
        print("saved", target)

    def js(code: str) -> None:
        window.view.page().runJavaScript(code)
        time.sleep(0.5)
        for _ in range(12):
            app.processEvents()

    def sequence() -> None:
        try:
            time.sleep(2.0)
            for _ in range(40):
                app.processEvents()

            grab("b_github_1686x896.png", 1686, 896)
            grab("b_github_1440x900.png", 1440, 900)
            grab("b_github_960x640.png", 960, 640)

            js(
                """
                (() => {
                  const buttons = [...document.querySelectorAll('button')];
                  const long = buttons.find(b => (b.innerText||'').includes('TypeScript'));
                  if (long) long.click();
                  return true;
                })()
                """
            )
            grab("b_long_name_failed_update.png", 1440, 900)

            js(
                """
                (() => {
                  const tabs = [...document.querySelectorAll('[role=tab]')];
                  const t = tabs.find(x => (x.innerText||'').includes('版本'));
                  if (t) t.click();
                  return true;
                })()
                """
            )
            grab("b_versions_tab.png", 1440, 900)

            js(
                """
                (() => {
                  const tabs = [...document.querySelectorAll('[role=tab]')];
                  const t = tabs.find(x => (x.innerText||'').includes('归档'));
                  if (t) t.click();
                  return true;
                })()
                """
            )
            grab("b_archive_tab.png", 1440, 900)

            js(
                """
                (() => {
                  const tabs = [...document.querySelectorAll('[role=tab]')];
                  const t = tabs.find(x => (x.innerText||'').includes('README'));
                  if (t) t.click();
                  return true;
                })()
                """
            )
            grab("b_long_readme.png", 1440, 900)

            # Empty library screenshot: clear list via filter that matches nothing
            js(
                """
                (() => {
                  const input = document.querySelector('input[aria-label=\"搜索项目\"]');
                  if (!input) return false;
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, '___no_such_repo___');
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                  return true;
                })()
                """
            )
            time.sleep(0.4)
            for _ in range(15):
                app.processEvents()
            grab("b_empty_filter.png", 1440, 900)

            # Real task: invalid URL through TaskBridge → FAILED result (real worker path)
            window.tasks.startArchive("not-a-github-url")
            time.sleep(0.8)
            for _ in range(20):
                app.processEvents()
            grab("b_task_result_invalid_url.png", 1440, 900)

            screen = QGuiApplication.primaryScreen()
            meta = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "mode": "business-isolated",
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "database": str(db),
                "archive_root": str(archive),
                "logical_dpi": None if screen is None else screen.logicalDotsPerInch(),
                "device_pixel_ratio": None if screen is None else screen.devicePixelRatio(),
                "method": "QWidget.grab via tools/slate_capture_business.py",
                "screenshots": paths,
                "note": "Seeded via LibraryService-compatible SQLite + README files; no user DB/Token.",
            }
            (SHOT / "capture_meta_business.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print("CAPTURE_OK")
        except Exception as exc:
            print(f"CAPTURE_FAILED: {exc!r}", file=sys.stderr)
            app.exit(1)
            return
        finally:
            window.tasks.shutdown()
            context.close()
        app.exit(0)

    QTimer.singleShot(1800, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
