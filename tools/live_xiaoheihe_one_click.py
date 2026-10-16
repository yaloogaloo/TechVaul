"""End-to-end one-click Xiaoheihe archive: TaskBridge → HTTP SPA → Chromium DOM.

Isolated SQLite + archive_root. Requires display + network.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\live_xiaoheihe_one_click.py
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

SHARE_URL = (
    "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share"
    "?link_id=f1ba7299a477&h_src=track&h_session_id=redacted"
)
CANONICAL = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
OUT = ROOT / "docs" / "web_archive" / "xiaoheihe_case"


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.bridge.task_bridge import TaskBridge
    from techvault.web_archive.service import WebArchiveService
    from techvault.web_archive.urls import parse_web_url

    parsed = parse_web_url(SHARE_URL)
    assert parsed.canonical == CANONICAL, parsed.canonical

    tmp = Path(tempfile.mkdtemp(prefix="tv-xhh-one-click-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    context = prepare_context(db, archive)
    bridge = TaskBridge(db)
    holder: dict = {"done": False, "state": None}

    def on_task(raw: str) -> None:
        state = json.loads(raw)
        holder["state"] = state
        print(
            f"[task] active={state.get('active')} phaseCode={state.get('phaseCode')} "
            f"status={state.get('status')} articleId={state.get('articleId')} "
            f"mode={state.get('captureMode')} snap={state.get('snapshotStatus')}",
            flush=True,
        )
        if state.get("kind") == "web" and not state.get("active") and state.get("status") in {
            "success",
            "failed",
            "cancelled",
        }:
            if not holder["done"]:
                holder["done"] = True
                QTimer.singleShot(500, finish)

    def finish() -> None:
        try:
            state = holder["state"] or {}
            from techvault.db.connection import connect

            conn = connect(db)
            service = WebArchiveService(conn)
            items = service.list_articles()
            article_id = state.get("articleId")
            detail = service.get_article(int(article_id)) if article_id else None
            folder = Path(detail.archive_folder) if detail and detail.archive_folder else None

            OUT.mkdir(parents=True, exist_ok=True)
            evidence = OUT / "archive"
            if evidence.exists():
                shutil.rmtree(evidence, ignore_errors=True)
            if folder and folder.is_dir():
                dest = evidence / "web" / "xiaoheihe.cn" / folder.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(folder, dest)

            media_count = 0
            if folder and (folder / "media").is_dir():
                media_count = sum(1 for _ in (folder / "media").rglob("*") if _.is_file())

            snap = folder / "snapshot.mhtml" if folder else None
            result = {
                "share_url_input": SHARE_URL.split("h_session_id=")[0] + "h_session_id=REDACTED",
                "normalized_canonical": parsed.canonical,
                "tmp": str(tmp),
                "task": {
                    k: state.get(k)
                    for k in (
                        "status",
                        "phaseCode",
                        "articleId",
                        "captureMode",
                        "httpFailureCode",
                        "snapshotStatus",
                        "snapshotBytes",
                        "message",
                        "title",
                    )
                },
                "list_count": len(items),
                "article_id": article_id,
                "title": detail.title if detail else None,
                "author": detail.author if detail else None,
                "site_key": detail.site_key if detail else None,
                "site_label": detail.site_label if detail else None,
                "canonical_url": detail.canonical_url if detail else None,
                "archive_folder": str(folder) if folder else None,
                "article_html_exists": bool(folder and (folder / "article.html").is_file()),
                "article_md_exists": bool(folder and (folder / "article.md").is_file()),
                "metadata_exists": bool(folder and (folder / "metadata.json").is_file()),
                "snapshot_mhtml_exists": bool(snap and snap.is_file()),
                "snapshot_bytes": snap.stat().st_size if snap and snap.is_file() else 0,
                "media_files": media_count,
                "body_html_len": len(detail.body_html) if detail else 0,
                "pass_one_click": state.get("status") == "success" and bool(article_id),
                "pass_reading_edition": bool(
                    folder and (folder / "article.html").is_file() and article_id
                ),
                "pass_mhtml": bool(snap and snap.is_file() and snap.stat().st_size > 1000),
            }
            (OUT / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            conn.close()
        except Exception as exc:
            print(f"finish error: {exc}", flush=True)
            (OUT / "result.json").write_text(
                json.dumps({"error": str(exc), "task": holder.get("state")}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        finally:
            try:
                context.close()
            except Exception:
                pass
            QTimer.singleShot(200, app.quit)

    def on_timeout() -> None:
        if holder["done"]:
            return
        print("TIMEOUT waiting for web archive task", flush=True)
        holder["done"] = True
        holder["state"] = holder.get("state") or {"status": "failed", "message": "timeout"}
        QTimer.singleShot(100, app.quit)

    bridge.taskChanged.connect(on_task)
    QTimer.singleShot(200, lambda: bridge.startWebArchive(SHARE_URL))
    # Xiaoheihe SPA + MHTML can take a while on cold Chromium.
    QTimer.singleShot(180_000, on_timeout)
    app.exec()
    if not holder.get("state"):
        return 2
    state = holder["state"] or {}
    return 0 if state.get("status") == "success" and state.get("articleId") else 1


if __name__ == "__main__":
    raise SystemExit(main())
