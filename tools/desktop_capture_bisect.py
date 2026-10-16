"""Bisect AV after capture: nav vs snapshot beside DesktopWindow.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\desktop_capture_bisect.py nav
  .\\.venv\\Scripts\\python.exe tools\\desktop_capture_bisect.py snap
  .\\.venv\\Scripts\\python.exe tools\\desktop_capture_bisect.py both
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "tests" / "fixtures" / "web_archive"
OUT = ROOT / "docs" / "performance"
MODE = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
ROUNDS = 10


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, directory=None, **k):
        super().__init__(*a, directory=directory, **k)

    def log_message(self, *a):
        return


def main() -> int:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme

    tmp = Path(tempfile.mkdtemp(prefix=f"tv-bisect-{MODE}-"))
    db, archive = tmp / "db.sqlite", tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H, directory=str(FIXTURES)))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/local_stress_xiaoheihe.html"

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ctx = prepare_context(db, archive)
    win = DesktopWindow(ctx)
    win.show()
    bridge, web = win.tasks, win.webArchive
    h = {"i": 0, "waiting": False, "ok": 0, "loaded": False, "aid": None}

    def nav(label: str) -> None:
        win.view.page().runJavaScript(
            f"""
            (() => {{
              const rail = document.querySelector('nav[aria-label=\"主导航\"]');
              const buttons = [...(rail || document).querySelectorAll('button')];
              const hit = buttons.find(b => (b.innerText || '').replace(/\\s+/g, '').includes('{label}'));
              if (hit) hit.click();
            }})()
            """
        )
        for _ in range(20):
            app.processEvents()

    def on_task(raw: str) -> None:
        s = json.loads(raw)
        if not h["waiting"]:
            return
        if s.get("kind") == "web" and not s.get("active") and s.get("status") in {
            "success",
            "failed",
            "cancelled",
        }:
            h["waiting"] = False
            h["aid"] = s.get("articleId")
            if s.get("status") == "success":
                h["ok"] += 1
            print(f"[{MODE}] round {h['i']} {s.get('status')} aid={h['aid']}", flush=True)
            QTimer.singleShot(0, after)

    def wait_die(cb, n=0):
        app.processEvents()
        if bridge._dying_capture_alive() or bridge._capture_window_alive():  # noqa: SLF001
            if n < 400:
                QTimer.singleShot(50, lambda: wait_die(cb, n + 1))
                return
        cb()

    def after():
        wait_die(after_stable)

    def after_stable():
        if MODE in {"nav", "both"}:
            nav("GitHub")
            nav("知识")
            nav("网页")
            print(f"[{MODE}] nav done", flush=True)
        if MODE in {"snap", "both"} and h["aid"]:
            try:
                raw = web.openSnapshotPreview(json.dumps({"articleId": int(h["aid"])}))
                print(f"[{MODE}] snap open {raw[:80]}", flush=True)
                viewer = getattr(web, "_snapshot_window", None)
                dying = viewer
                if viewer is not None:
                    viewer.close()
                    viewer.deleteLater()

                def gone(n=0):
                    alive = False
                    if dying is not None:
                        try:
                            dying.objectName()
                            alive = True
                        except RuntimeError:
                            pass
                    if alive and n < 200:
                        QTimer.singleShot(50, lambda: gone(n + 1))
                        return
                    web._snapshot_window = None  # noqa: SLF001
                    print(f"[{MODE}] snap closed", flush=True)
                    nxt()

                QTimer.singleShot(0, lambda: gone(0))
                return
            except Exception as exc:
                print(f"[{MODE}] snap err {exc}", flush=True)
        nxt()

    def nxt():
        if h["i"] >= ROUNDS:
            out = {"mode": MODE, "ok": h["ok"], "rounds": ROUNDS}
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"desktop_bisect_{MODE}.json").write_text(
                json.dumps(out, indent=2), encoding="utf-8"
            )
            print(json.dumps(out), flush=True)
            win.close()
            ctx.close()
            srv.shutdown()
            QTimer.singleShot(200, app.quit)
            return
        if bridge._dying_capture_alive() or bridge._busy():  # noqa: SLF001
            QTimer.singleShot(50, nxt)
            return
        h["i"] += 1
        h["waiting"] = True
        bridge.startWebArchive(f"{url}?r={h['i']}")

    def on_load(ok: bool):
        if h["loaded"]:
            return
        h["loaded"] = True
        QTimer.singleShot(1200, nxt)

    bridge.taskChanged.connect(on_task)
    win.view.loadFinished.connect(on_load)
    QTimer.singleShot(8000, lambda: on_load(True) if not h["loaded"] else None)
    app.exec()
    return 0 if h["ok"] == ROUNDS else 1


if __name__ == "__main__":
    raise SystemExit(main())
