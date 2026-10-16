"""10× controlled Chromium captures in one TaskBridge lifecycle (local fixtures).

Serves SPA-shell pages that only render full DOM under Chromium (HTTP stays incomplete).
Writes docs/performance/chromium_stress_series.json + TECHVAULT_P25_STABILITY.md data input.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\chromium_capture_stress.py
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
OUT.mkdir(parents=True, exist_ok=True)
SERIES = OUT / "chromium_stress_series.json"

ROUNDS = 10


def _process_metrics(pid: int) -> dict:
    try:
        import psutil
    except ImportError:
        return {"psutil": False, "rss_mb": None, "chromium_procs": None, "chromium_rss_mb": None}

    proc = psutil.Process(pid)
    rss = proc.memory_info().rss
    chromium = []
    for child in proc.children(recursive=True):
        try:
            name = (child.name() or "").lower()
            if "qtwebengine" in name or "chrom" in name or "webengine" in name:
                chromium.append(child)
        except (psutil.Error, OSError):
            continue
    chrome_rss = 0
    for c in chromium:
        try:
            chrome_rss += c.memory_info().rss
        except (psutil.Error, OSError):
            pass
    return {
        "psutil": True,
        "rss_mb": round(rss / (1024 * 1024), 2),
        "chromium_procs": len(chromium),
        "chromium_rss_mb": round(chrome_rss / (1024 * 1024), 2),
    }


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format, *args):  # noqa: A003
        return


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.bridge.task_bridge import TaskBridge
    from techvault.web_archive.service import WebArchiveService

    tmp = Path(tempfile.mkdtemp(prefix="tv-chromium-stress-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")
    os.environ["TECHVAULT_DATABASE"] = str(db)
    os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(archive)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(FIXTURES)))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    urls = [
        f"http://127.0.0.1:{port}/local_stress_xiaoheihe.html",
        f"http://127.0.0.1:{port}/local_stress_zhihu.html",
    ]

    from PySide6.QtCore import Qt

    # Single WebEngine lineage (capture windows only). Pairing a persistent DesktopWindow
    # WebEngineView with rapid capture open/close triggered native AVs on this host;
    # production still uses both, but stress isolates TaskBridge + capture lifecycle.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    context = prepare_context(db, archive)
    bridge = TaskBridge(db)
    window = None

    pid = os.getpid()
    series: list[dict] = []
    holder: dict = {
        "i": 0,
        "waiting": False,
        "task_id": None,
        "t0": 0.0,
        "last_phase": None,
        "phases": [],
        "ui_probe_ms": [],
    }

    def ui_probe() -> float:
        t0 = time.perf_counter()
        for _ in range(5):
            app.processEvents()
        return round((time.perf_counter() - t0) * 1000, 2)

    def snapshot(extra: dict | None = None) -> dict:
        row = {
            "t_unix": time.time(),
            "round": holder["i"],
            **_process_metrics(pid),
            "ui_process_events_ms": ui_probe(),
        }
        if extra:
            row.update(extra)
        series.append(row)
        return row

    def on_task(raw: str) -> None:
        state = json.loads(raw)
        phase = state.get("phaseCode")
        if phase and phase != holder["last_phase"]:
            holder["phases"].append(phase)
            holder["last_phase"] = phase
        if not holder["waiting"]:
            return
        if state.get("taskId") and holder["task_id"] and state.get("taskId") != holder["task_id"]:
            return
        if state.get("kind") == "web" and not state.get("active") and state.get("status") in {
            "success",
            "failed",
            "cancelled",
        }:
            elapsed = round((time.perf_counter() - holder["t0"]) * 1000, 1)
            snap = snapshot(
                {
                    "event": "terminal",
                    "status": state.get("status"),
                    "phaseCode": state.get("phaseCode"),
                    "captureMode": state.get("captureMode"),
                    "articleId": state.get("articleId"),
                    "taskId": state.get("taskId"),
                    "elapsed_ms": elapsed,
                    "phases_seen": list(holder["phases"]),
                }
            )
            print(
                f"[round {holder['i']}/{ROUNDS}] status={state.get('status')} "
                f"mode={state.get('captureMode')} elapsed_ms={elapsed} "
                f"rss={snap.get('rss_mb')} chrome_procs={snap.get('chromium_procs')} "
                f"chrome_rss={snap.get('chromium_rss_mb')}",
                flush=True,
            )
            holder["waiting"] = False
            holder["phases"] = []
            holder["last_phase"] = None
            # Allow WebCaptureWindow.deleteLater + Chromium teardown before next round.
            QTimer.singleShot(2500, after_settle)

    def after_settle() -> None:
        # Drain deleteLater / GPU process teardown.
        for _ in range(40):
            app.processEvents()
            time.sleep(0.05)
        # Wait until TaskBridge releases capture window ref.
        deadline = time.time() + 8
        while time.time() < deadline:
            if getattr(bridge, "_capture_window", None) is None:
                break
            app.processEvents()
            time.sleep(0.1)
        snapshot({"event": "settle"})
        if holder["i"] >= ROUNDS:
            finish()
            return
        QTimer.singleShot(1200, start_next)

    def start_next() -> None:
        holder["i"] += 1
        url = urls[(holder["i"] - 1) % len(urls)]
        # Unique query to avoid path collisions across rounds.
        url = f"{url}?r={holder['i']}"
        holder["waiting"] = True
        holder["t0"] = time.perf_counter()
        holder["phases"] = []
        holder["last_phase"] = None
        snapshot({"event": "start", "url": url.split("?")[0]})
        raw = bridge.startWebArchive(url)
        try:
            payload = json.loads(raw)
            data = payload.get("data") if isinstance(payload, dict) else None
            holder["task_id"] = (data or {}).get("taskId") if isinstance(data, dict) else None
        except Exception:
            holder["task_id"] = None
        # Safety timeout per round.
        QTimer.singleShot(120_000, lambda: timeout_round(holder["i"]))

    def timeout_round(round_id: int) -> None:
        if not holder["waiting"] or holder["i"] != round_id:
            return
        snapshot({"event": "timeout", "status": "timeout"})
        holder["waiting"] = False
        print(f"[round {round_id}] TIMEOUT", flush=True)
        QTimer.singleShot(500, after_settle)

    def finish() -> None:
        try:
            service = WebArchiveService(context.connection)
            items = service.list_articles(limit=50)
            # Idle memory after all rounds.
            for _ in range(3):
                app.processEvents()
                time.sleep(0.4)
            snapshot({"event": "final_idle", "articles": len(items)})
            report = {
                "tmp": str(tmp),
                "rounds": ROUNDS,
                "series": series,
                "article_count": len(items),
                "terminal_ok": sum(1 for s in series if s.get("event") == "terminal" and s.get("status") == "success"),
                "terminal_failed": sum(
                    1 for s in series if s.get("event") == "terminal" and s.get("status") != "success"
                ),
                "timeouts": sum(1 for s in series if s.get("event") == "timeout"),
            }
            # Trend: first vs last settle rss / chromium
            settles = [s for s in series if s.get("event") == "settle"]
            terminals = [s for s in series if s.get("event") == "terminal"]
            if settles:
                report["rss_mb_first_settle"] = settles[0].get("rss_mb")
                report["rss_mb_last_settle"] = settles[-1].get("rss_mb")
                report["chromium_procs_first_settle"] = settles[0].get("chromium_procs")
                report["chromium_procs_last_settle"] = settles[-1].get("chromium_procs")
                report["chromium_rss_first_settle"] = settles[0].get("chromium_rss_mb")
                report["chromium_rss_last_settle"] = settles[-1].get("chromium_rss_mb")
            if terminals:
                report["elapsed_ms_series"] = [t.get("elapsed_ms") for t in terminals]
                report["capture_modes"] = [t.get("captureMode") for t in terminals]
            # Growth attribution heuristic
            if settles and settles[0].get("rss_mb") is not None and settles[-1].get("rss_mb") is not None:
                delta = settles[-1]["rss_mb"] - settles[0]["rss_mb"]
                chrome_delta = (settles[-1].get("chromium_rss_mb") or 0) - (settles[0].get("chromium_rss_mb") or 0)
                procs_delta = (settles[-1].get("chromium_procs") or 0) - (settles[0].get("chromium_procs") or 0)
                report["rss_delta_mb"] = round(delta, 2)
                report["chromium_rss_delta_mb"] = round(chrome_delta, 2)
                report["chromium_procs_delta"] = procs_delta
                # Unbounded if procs keep climbing every round.
                report["pass_no_unbounded_proc_growth"] = procs_delta <= 2
                report["pass_all_terminal_success"] = report["terminal_ok"] == ROUNDS and report["timeouts"] == 0
            SERIES.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({k: report[k] for k in report if k != "series"}, ensure_ascii=False, indent=2))
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                if window is not None:
                    window.close()
                context.close()
            except Exception:
                pass
            QTimer.singleShot(200, app.quit)

    bridge.taskChanged.connect(on_task)
    snapshot({"event": "baseline"})
    QTimer.singleShot(1500, start_next)
    app.exec()
    data = json.loads(SERIES.read_text(encoding="utf-8")) if SERIES.is_file() else {}
    ok = bool(data.get("pass_all_terminal_success")) and bool(data.get("pass_no_unbounded_proc_growth", True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
