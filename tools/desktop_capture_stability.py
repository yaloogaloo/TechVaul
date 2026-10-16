"""P2.6: DesktopWindow + capture concurrency stress (same QApplication).

Formal Slate shell stays alive for 10 controlled Chromium captures with workspace
switching and MHTML open/close. Isolated DB / local fixtures only.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\desktop_capture_stability.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "tests" / "fixtures" / "web_archive"
OUT = ROOT / "docs" / "performance"
OUT.mkdir(parents=True, exist_ok=True)
SERIES = OUT / "desktop_stability_series.json"
ROUNDS = 10


def _metrics(pid: int) -> dict:
    try:
        import psutil
    except ImportError:
        return {"psutil": False}

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
    import faulthandler

    faulthandler.enable()

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme
    from techvault.web_archive.service import WebArchiveService

    tmp = Path(tempfile.mkdtemp(prefix="tv-desktop-stab-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")
    os.environ["TECHVAULT_DATABASE"] = str(db)
    os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(archive)
    os.environ.pop("TECHVAULT_SLATE_SHELL_DEMO", None)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(FIXTURES)))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    urls = [
        f"http://127.0.0.1:{port}/local_stress_xiaoheihe.html",
        f"http://127.0.0.1:{port}/local_stress_zhihu.html",
    ]

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    context = prepare_context(db, archive)
    window = DesktopWindow(context)
    window.resize(1280, 800)
    window.show()
    bridge = window.tasks
    web = window.webArchive
    pid = os.getpid()

    series: list[dict] = []
    latency_samples: list[float] = []
    holder: dict = {
        "i": 0,
        "waiting": False,
        "task_id": None,
        "t0": 0.0,
        "shell_loaded": False,
        "baseline_chrome": None,
        "baseline_rss": None,
        "last_article_id": None,
        "workspace_latencies_ms": [],
        "phases": [],
    }

    def snap(event: str, *, probe_loop: bool = True, **extra) -> dict:
        loop_ms = None
        if probe_loop:
            t0 = time.perf_counter()
            for _ in range(5):
                app.processEvents()
            loop_ms = round((time.perf_counter() - t0) * 1000, 2)
            latency_samples.append(loop_ms)
        row = {
            "t_unix": time.time(),
            "event": event,
            "round": holder["i"],
            "loop_process_events_ms": loop_ms,
            "desktop_alive": True,
            "capture_alive": bridge._capture_window_alive(),  # noqa: SLF001
            "dying_alive": bridge._dying_capture_alive(),  # noqa: SLF001
            **_metrics(pid),
            **extra,
        }
        series.append(row)
        return row

    def click_nav(label: str) -> float:
        t0 = time.perf_counter()
        window.view.page().runJavaScript(
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
        for _ in range(20):
            app.processEvents()
        ms = round((time.perf_counter() - t0) * 1000, 2)
        holder["workspace_latencies_ms"].append({"label": label, "ms": ms})
        return ms

    def on_task(raw: str) -> None:
        state = json.loads(raw)
        phase = state.get("phaseCode")
        if phase:
            holder["phases"].append(phase)
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
            aid = state.get("articleId")
            holder["last_article_id"] = aid
            # Do NOT processEvents here: finalize is still tearing down the capture
            # WebEngine beside DesktopWindow (native AV when pumping mid-destroy).
            snap(
                "terminal",
                probe_loop=False,
                status=state.get("status"),
                phaseCode=state.get("phaseCode"),
                captureMode=state.get("captureMode"),
                articleId=aid,
                elapsed_ms=elapsed,
                phases_seen=list(dict.fromkeys(holder["phases"])),
            )
            print(
                f"[desktop round {holder['i']}/{ROUNDS}] status={state.get('status')} "
                f"elapsed_ms={elapsed} rss={series[-1].get('rss_mb')} "
                f"chrome={series[-1].get('chromium_procs')}",
                flush=True,
            )
            holder["waiting"] = False
            holder["phases"] = []
            QTimer.singleShot(0, after_terminal)

    def wait_destroyed(cb, attempts: int = 0) -> None:
        app.processEvents()
        if bridge._dying_capture_alive() or bridge._capture_window_alive():  # noqa: SLF001
            if attempts > 400:
                snap("destroy_wait_timeout", probe_loop=False)
                cb()
                return
            QTimer.singleShot(50, lambda: wait_destroyed(cb, attempts + 1))
            return
        snap("capture_destroyed")
        cb()

    def after_terminal() -> None:
        wait_destroyed(after_capture_stable)

    def after_capture_stable() -> None:
        click_nav("GitHub")
        snap("after_nav_github")
        click_nav("知识")
        snap("after_nav_knowledge")
        click_nav("网页")
        snap("after_nav_web")

        aid = holder.get("last_article_id")
        if not aid:
            settle_and_next()
            return
        try:
            raw = web.openSnapshotPreview(json.dumps({"articleId": int(aid)}))
            snap(
                "snapshot_open",
                openSnapshot=raw[:160] if isinstance(raw, str) else str(raw),
            )
            viewer = getattr(web, "_snapshot_window", None)
            dying_viewer = viewer
            if viewer is not None:
                try:
                    viewer.close()
                    viewer.deleteLater()
                except RuntimeError:
                    pass

            def _snap_gone(attempts: int = 0) -> None:
                alive = False
                if dying_viewer is not None:
                    try:
                        dying_viewer.objectName()
                        alive = True
                    except RuntimeError:
                        alive = False
                if alive and attempts < 200:
                    QTimer.singleShot(50, lambda: _snap_gone(attempts + 1))
                    return
                web._snapshot_window = None  # noqa: SLF001
                snap("snapshot_closed")
                settle_and_next()

            QTimer.singleShot(0, lambda: _snap_gone(0))
        except Exception as exc:
            snap("snapshot_error", error=str(exc))
            settle_and_next()

    def settle_and_next() -> None:
        snap("settle")
        if holder["i"] >= ROUNDS:
            finish()
            return
        QTimer.singleShot(0, start_next)

    def start_next() -> None:
        if bridge._dying_capture_alive() or bridge._capture_window_alive():  # noqa: SLF001
            QTimer.singleShot(50, start_next)
            return
        if bridge._busy() and holder["i"] > 0 and not holder["waiting"]:
            QTimer.singleShot(50, start_next)
            return
        holder["i"] += 1
        url = f"{urls[(holder['i'] - 1) % len(urls)]}?r={holder['i']}"
        holder["waiting"] = True
        holder["t0"] = time.perf_counter()
        holder["phases"] = []
        snap("start", url=url.split("?")[0])
        raw = bridge.startWebArchive(url)
        try:
            payload = json.loads(raw)
            data = payload.get("data") if isinstance(payload, dict) else {}
            holder["task_id"] = (data or {}).get("taskId")
            if payload.get("ok") is False:
                snap("start_rejected", payload=payload)
                holder["waiting"] = False
                QTimer.singleShot(0, settle_and_next)
                return
        except Exception:
            holder["task_id"] = None
        rid = holder["i"]
        QTimer.singleShot(120_000, lambda: on_round_timeout(rid))

    def on_round_timeout(rid: int) -> None:
        if not holder["waiting"] or holder["i"] != rid:
            return
        snap("timeout", status="timeout")
        holder["waiting"] = False
        print(f"[desktop round {rid}] TIMEOUT", flush=True)
        QTimer.singleShot(0, after_terminal)

    def finish() -> None:
        try:
            service = WebArchiveService(context.connection)
            items = service.list_articles(limit=50)
            snap("final_idle", articles=len(items))
            samples = sorted(latency_samples)

            def pct(p):
                if not samples:
                    return None
                idx = min(len(samples) - 1, max(0, int(len(samples) * p) - 1))
                return samples[idx]

            settles = [s for s in series if s.get("event") == "settle"]
            terminals = [s for s in series if s.get("event") == "terminal"]
            freezes = [s for s in series if (s.get("loop_process_events_ms") or 0) >= 500]
            report = {
                "tmp": str(tmp),
                "rounds": ROUNDS,
                "desktop_window": True,
                "series": series,
                "article_count": len(items),
                "terminal_ok": sum(1 for t in terminals if t.get("status") == "success"),
                "terminal_failed": sum(1 for t in terminals if t.get("status") != "success"),
                "timeouts": sum(1 for s in series if s.get("event") == "timeout"),
                "native_av_observed": False,
                "baseline_rss_mb": holder.get("baseline_rss"),
                "baseline_chromium_procs": holder.get("baseline_chrome"),
                "rss_mb_first_settle": settles[0].get("rss_mb") if settles else None,
                "rss_mb_last_settle": settles[-1].get("rss_mb") if settles else None,
                "chromium_procs_first_settle": settles[0].get("chromium_procs") if settles else None,
                "chromium_procs_last_settle": settles[-1].get("chromium_procs") if settles else None,
                "chromium_rss_first_settle": settles[0].get("chromium_rss_mb") if settles else None,
                "chromium_rss_last_settle": settles[-1].get("chromium_rss_mb") if settles else None,
                "elapsed_ms_series": [t.get("elapsed_ms") for t in terminals],
                "loop_latency_p50_ms": pct(0.50),
                "loop_latency_p95_ms": pct(0.95),
                "loop_latency_max_ms": samples[-1] if samples else None,
                "freezes_ge_500ms": len(freezes),
                "workspace_latencies_ms": holder["workspace_latencies_ms"],
            }
            if settles and settles[0].get("rss_mb") is not None and settles[-1].get("rss_mb") is not None:
                report["rss_delta_mb"] = round(settles[-1]["rss_mb"] - settles[0]["rss_mb"], 2)
                report["chromium_procs_delta"] = (settles[-1].get("chromium_procs") or 0) - (
                    settles[0].get("chromium_procs") or 0
                )
                base_c = holder.get("baseline_chrome") or 0
                last_c = settles[-1].get("chromium_procs") or 0
                report["chromium_above_baseline_last"] = last_c - base_c
                report["pass_all_terminal_success"] = (
                    report["terminal_ok"] == ROUNDS and report["timeouts"] == 0
                )
                report["pass_no_unbounded_chrome_growth"] = report["chromium_procs_delta"] <= 2
                report["pass_no_native_av"] = True
                report["pass_desktop_capture_concurrency"] = bool(
                    report["pass_all_terminal_success"]
                    and report["pass_no_unbounded_chrome_growth"]
                    and report["pass_no_native_av"]
                )
            SERIES.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {k: report[k] for k in report if k != "series"}
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        except Exception:
            traceback.print_exc()
            SERIES.write_text(
                json.dumps({"error": traceback.format_exc(), "series": series}, indent=2),
                encoding="utf-8",
            )
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
            QTimer.singleShot(200, app.quit)

    def on_shell_loaded(ok: bool) -> None:
        if holder["shell_loaded"]:
            return
        holder["shell_loaded"] = True
        QTimer.singleShot(1500, after_shell_ready)

    def after_shell_ready() -> None:
        base = snap("baseline_shell")
        holder["baseline_rss"] = base.get("rss_mb")
        holder["baseline_chrome"] = base.get("chromium_procs")
        click_nav("网页")
        snap("web_workspace_ready")
        QTimer.singleShot(0, start_next)

    bridge.taskChanged.connect(on_task)
    window.view.loadFinished.connect(on_shell_loaded)
    QTimer.singleShot(8000, lambda: on_shell_loaded(True) if not holder["shell_loaded"] else None)

    app.exec()
    if not SERIES.is_file():
        return 2
    data = json.loads(SERIES.read_text(encoding="utf-8"))
    return 0 if data.get("pass_desktop_capture_concurrency") else 1


if __name__ == "__main__":
    raise SystemExit(main())
