"""Minimal DesktopWindow + 10 captures (no nav/snapshot) to isolate native AV."""

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
ROUNDS = 10


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, directory=None, **k):
        super().__init__(*a, directory=directory, **k)

    def log_message(self, *a):
        return


def main() -> int:
    import faulthandler

    faulthandler.enable()
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme

    tmp = Path(tempfile.mkdtemp(prefix="tv-desk-min-"))
    db, archive = tmp / "db.sqlite", tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(H, directory=str(FIXTURES)))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    urls = [
        f"http://127.0.0.1:{port}/local_stress_xiaoheihe.html",
        f"http://127.0.0.1:{port}/local_stress_zhihu.html",
    ]

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ctx = prepare_context(db, archive)
    win = DesktopWindow(ctx)
    win.show()
    bridge = win.tasks
    h = {"i": 0, "waiting": False, "ok": 0, "loaded": False}
    log: list[dict] = []

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
            if s.get("status") == "success":
                h["ok"] += 1
            log.append({"round": h["i"], "status": s.get("status"), "mode": s.get("captureMode")})
            print(f"round {h['i']} {s.get('status')}", flush=True)
            QTimer.singleShot(0, wait_die)

    def wait_die(n=0):
        app.processEvents()
        if bridge._dying_capture_alive() or bridge._capture_window_alive():  # noqa: SLF001
            if n < 400:
                QTimer.singleShot(50, lambda: wait_die(n + 1))
                return
        QTimer.singleShot(0, nxt)

    def nxt():
        if h["i"] >= ROUNDS:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "desktop_minimal_series.json").write_text(
                json.dumps({"ok": h["ok"], "rounds": ROUNDS, "log": log}, indent=2),
                encoding="utf-8",
            )
            print(json.dumps({"ok": h["ok"], "rounds": ROUNDS}), flush=True)
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
        bridge.startWebArchive(f"{urls[(h['i']-1)%2]}?r={h['i']}")

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
