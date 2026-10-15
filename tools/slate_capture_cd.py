"""Stage C+D screenshots: Knowledge + Settings + GitHub regression (isolated DB)."""

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
    tmp = Path(tempfile.mkdtemp(prefix="techvault-slate-cd-"))
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
        for _ in range(10):
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
        time.sleep(0.55)
        for _ in range(16):
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

    def sequence() -> None:
        try:
            time.sleep(2.2)
            for _ in range(50):
                app.processEvents()

            # GitHub regression
            grab("cd_github_regression_1440x900.png", 1440, 900)

            # Knowledge
            click_nav("知识")
            time.sleep(0.8)
            for _ in range(20):
                app.processEvents()
            grab("cd_knowledge_read_1686x896.png", 1686, 896)
            grab("cd_knowledge_read_1440x900.png", 1440, 900)
            grab("cd_knowledge_960x640.png", 960, 640)

            js(
                """
                (() => {
                  const more = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').includes('加载更多'));
                  if (more) more.click();
                  return !!more;
                })()
                """
            )
            time.sleep(0.6)
            for _ in range(20):
                app.processEvents()
            grab("cd_knowledge_pagination_50plus.png", 1440, 900)

            js(
                """
                (() => {
                  const btn = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').includes('编辑'));
                  if (btn) btn.click();
                  return !!btn;
                })()
                """
            )
            time.sleep(0.4)
            for _ in range(15):
                app.processEvents()
            grab("cd_knowledge_edit_1440x900.png", 1440, 900)

            js(
                """
                (() => {
                  const area = document.querySelector('textarea');
                  if (!area) return false;
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                  setter.call(area, area.value + '\\n\\n草稿修改 — dirty guard');
                  area.dispatchEvent(new Event('input', { bubbles: true }));
                  return true;
                })()
                """
            )
            click_nav("GitHub")
            time.sleep(0.5)
            for _ in range(15):
                app.processEvents()
            grab("cd_knowledge_dirty_guard.png", 1440, 900)
            js(
                """
                (() => {
                  const stay = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').includes('继续编辑'));
                  if (stay) stay.click();
                  return !!stay;
                })()
                """
            )
            time.sleep(0.3)
            js(
                """
                (() => {
                  const cancel = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').trim() === '取消');
                  if (cancel) cancel.click();
                  return !!cancel;
                })()
                """
            )
            js(
                """
                (() => {
                  const discard = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').includes('放弃'));
                  if (discard) discard.click();
                  return !!discard;
                })()
                """
            )

            # Long document: select first item with CMake if present
            js(
                """
                (() => {
                  const rows = [...document.querySelectorAll('button')];
                  const hit = rows.find(b => (b.innerText||'').includes('CMake'));
                  if (hit) hit.click();
                  return !!hit;
                })()
                """
            )
            time.sleep(0.4)
            grab("cd_knowledge_long_doc.png", 1440, 900)

            js(
                """
                (() => {
                  const input = document.querySelector('input[aria-label=\"搜索文档\"]');
                  if (!input) return false;
                  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  setter.call(input, '___empty_knowledge___');
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                  return true;
                })()
                """
            )
            time.sleep(0.5)
            for _ in range(20):
                app.processEvents()
            grab("cd_knowledge_empty.png", 1440, 900)

            # Settings — clear knowledge filters first so nav is responsive
            js(
                """
                (() => {
                  const clear = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').includes('清除筛选'));
                  if (clear) clear.click();
                  return true;
                })()
                """
            )
            click_nav("设置")
            for _ in range(40):
                app.processEvents()
                time.sleep(0.05)
            # Wait until archive path rendered (avoid loading flash)
            for _ in range(40):
                settled = {"ok": False}

                def _check(val):
                    settled["ok"] = isinstance(val, str) and ("archive" in val.lower() or ":\\" in val or "/" in val)

                window.view.page().runJavaScript(
                    "(() => { const v = document.querySelector('.value, [class*=value]'); return v ? v.innerText : ''; })()",
                    _check,
                )
                for _ in range(8):
                    app.processEvents()
                if settled["ok"]:
                    break
                time.sleep(0.1)
            grab("cd_settings_1440x900.png", 1440, 900)
            grab("cd_settings_960x640.png", 960, 640)

            js(
                """
                (() => {
                  const btn = [...document.querySelectorAll('button')].find(
                    b => (b.innerText||'').includes('配置 Token')
                      || (b.innerText||'').includes('更新 Token'));
                  if (btn) btn.click();
                  return !!btn;
                })()
                """
            )
            time.sleep(0.3)
            for _ in range(12):
                app.processEvents()
            grab("cd_settings_token_dialog.png", 1440, 900)

            screen = QGuiApplication.primaryScreen()
            meta = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "mode": "stage-cd-isolated",
                "platform": platform.platform(),
                "python": sys.version.split()[0],
                "database": str(db),
                "archive_root": str(archive),
                "logical_dpi": None if screen is None else screen.logicalDotsPerInch(),
                "device_pixel_ratio": None if screen is None else screen.devicePixelRatio(),
                "method": "QWidget.grab via tools/slate_capture_cd.py",
                "screenshots": paths,
                "dpi_150": "NOT VERIFIED",
            }
            (SHOT / "capture_meta_cd.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print("CAPTURE_OK", len(paths))
        except Exception as exc:
            print(f"CAPTURE_FAILED: {exc!r}", file=sys.stderr)
            app.exit(1)
            return
        finally:
            window.tasks.shutdown()
            context.close()
        app.exit(0)

    QTimer.singleShot(2000, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
