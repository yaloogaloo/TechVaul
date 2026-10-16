"""Live one-click archive + MHTML completion + offline WebEngine reload.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\live_mhtml_snapshot.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

URL = "https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493"
OUT = ROOT / "docs" / "web_archive" / "mhtml_case"
SHOT = ROOT / "docs" / "web_archive" / "screenshots"


def main() -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.db.connection import connect
    from techvault.ui.bridge.task_bridge import TaskBridge
    from techvault.ui.snapshot_viewer_window import SnapshotViewerWindow
    from techvault.web_archive.service import WebArchiveService

    tmp = Path(tempfile.mkdtemp(prefix="tv-mhtml-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    context = prepare_context(db, archive)
    bridge = TaskBridge(db)
    holder: dict = {"done": False, "state": None, "err": None, "offline_ok": None}

    def on_task(raw: str) -> None:
        state = json.loads(raw)
        holder["state"] = state
        print(
            f"[task] {state.get('phaseCode')} status={state.get('status')} "
            f"snapshot={state.get('snapshotStatus')} articleId={state.get('articleId')}",
            flush=True,
        )
        if state.get("kind") == "web" and not state.get("active") and state.get("status") in {
            "success",
            "failed",
            "cancelled",
        }:
            if not holder["done"]:
                holder["done"] = True
                QTimer.singleShot(500, after_archive)

    def after_archive() -> None:
        try:
            state = holder["state"] or {}
            conn = connect(db)
            service = WebArchiveService(conn)
            aid = state.get("articleId")
            detail = service.get_article(int(aid)) if aid else None
            snap = Path(detail.snapshot_path) if detail and detail.snapshot_path else None
            if snap is None and detail and detail.archive_folder:
                candidate = Path(detail.archive_folder) / "snapshot.mhtml"
                if candidate.is_file():
                    snap = candidate

            OUT.mkdir(parents=True, exist_ok=True)
            SHOT.mkdir(parents=True, exist_ok=True)
            evidence = OUT / "archive"
            if evidence.exists():
                shutil.rmtree(evidence, ignore_errors=True)
            if detail and detail.archive_folder and Path(detail.archive_folder).is_dir():
                dest = evidence / "web" / "zhihu.com" / Path(detail.archive_folder).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(detail.archive_folder, dest)

            result = {
                "url": URL,
                "tmp": str(tmp),
                "task": state,
                "article_id": aid,
                "title": detail.title if detail else None,
                "snapshot_status": detail.snapshot_status if detail else None,
                "snapshot_bytes": detail.snapshot_bytes if detail else None,
                "snapshot_path": str(snap) if snap else None,
                "snapshot_file_exists": bool(snap and snap.is_file()),
                "reading_complete": bool(detail and detail.status in {"COMPLETE", "PARTIAL"}),
                "pass_mhtml_save": bool(
                    detail
                    and detail.snapshot_status == "COMPLETE"
                    and snap
                    and snap.is_file()
                    and snap.stat().st_size > 256
                ),
            }
            (OUT / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({k: result[k] for k in (
                "pass_mhtml_save", "snapshot_status", "snapshot_bytes", "article_id", "title"
            )}, ensure_ascii=False, indent=2), flush=True)

            if not result["pass_mhtml_save"] or snap is None:
                holder["err"] = "snapshot missing"
                conn.close()
                context.close()
                app.quit()
                return

            # Offline viewer: no network interceptor blocks http(s).
            viewer = SnapshotViewerWindow(snap, title=detail.title or "snapshot")
            viewer.show()

            def grab_offline() -> None:
                img = viewer.grab()
                path = SHOT / "mhtml_offline_viewer_1280x800.png"
                img.save(str(path), "PNG")
                # Probe title text from page
                def on_js(title):
                    holder["offline_ok"] = {
                        "load_ok": True,
                        "blocked": len(viewer._interceptor.blocked),  # noqa: SLF001
                        "page_title": title,
                        "screenshot": str(path),
                    }
                    (OUT / "offline_reload.json").write_text(
                        json.dumps(holder["offline_ok"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print("OFFLINE", holder["offline_ok"], flush=True)
                    viewer.close()
                    conn.close()
                    context.close()
                    app.quit()

                viewer.view.page().runJavaScript(
                    "document.title || (document.body && document.body.innerText.slice(0,80)) || ''",
                    on_js,
                )

            QTimer.singleShot(3500, grab_offline)
        except Exception as exc:
            holder["err"] = repr(exc)
            import traceback

            traceback.print_exc()
            context.close()
            app.quit()

    bridge.taskChanged.connect(on_task)
    bridge.startWebArchive(URL)
    QTimer.singleShot(180_000, lambda: (print("TIMEOUT"), app.quit()))
    app.exec()
    if holder.get("err"):
        return 1
    state = holder.get("state") or {}
    ok = (
        holder.get("done")
        and state.get("status") == "success"
        and (state.get("snapshotStatus") == "COMPLETE" or holder.get("offline_ok"))
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
