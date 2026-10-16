"""End-to-end one-click web archive: TaskBridge.startWebArchive → HTTP → Chromium.

Uses an isolated SQLite + archive_root (not the user's real library).
Requires display + network.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\live_zhihu_one_click.py
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
OUT = ROOT / "docs" / "web_archive" / "one_click_case"


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.bridge.task_bridge import TaskBridge
    from techvault.web_archive.service import WebArchiveService

    tmp = Path(tempfile.mkdtemp(prefix="tv-one-click-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")

    app = QApplication.instance() or QApplication(sys.argv)
    # Capture window may be the only window; do not quit before we write evidence.
    app.setQuitOnLastWindowClosed(False)
    context = prepare_context(db, archive)
    bridge = TaskBridge(db)
    holder: dict = {"done": False, "state": None, "err": None}

    def on_task(raw: str) -> None:
        state = json.loads(raw)
        holder["state"] = state
        print(
            f"[task] active={state.get('active')} phaseCode={state.get('phaseCode')} "
            f"status={state.get('status')} articleId={state.get('articleId')}",
            flush=True,
        )
        if state.get("kind") == "web" and not state.get("active") and state.get("status") in {
            "success",
            "failed",
            "cancelled",
        }:
            if not holder["done"]:
                holder["done"] = True
                QTimer.singleShot(400, finish)

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
                dest = evidence / "web" / "zhihu.com" / folder.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(folder, dest)

            result = {
                "url": URL,
                "tmp": str(tmp),
                "task": state,
                "list_count": len(items),
                "article_id": article_id,
                "title": detail.title if detail else None,
                "archive_folder": str(folder) if folder else None,
                "article_html_exists": bool(folder and (folder / "article.html").is_file()),
                "article_md_exists": bool(folder and (folder / "article.md").is_file()),
                "metadata_exists": bool(folder and (folder / "metadata.json").is_file()),
                "plain_preview": (detail.plain_text[:240] if detail else None),
                "sqlite_ok": bool(article_id and detail),
                "capture_mode": state.get("captureMode"),
                "http_failure_code": state.get("httpFailureCode"),
                "pass_one_click": state.get("status") == "success" and bool(article_id),
            }
            (OUT / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            offline = {
                "html_bytes": (
                    (folder / "article.html").stat().st_size
                    if folder and (folder / "article.html").is_file()
                    else 0
                ),
                "md_bytes": (
                    (folder / "article.md").stat().st_size
                    if folder and (folder / "article.md").is_file()
                    else 0
                ),
                "reopen_plain_len": len(detail.plain_text) if detail else 0,
                "reopen_via_fresh_connection": True,
            }
            (OUT / "offline_reopen.json").write_text(
                json.dumps(offline, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({k: result[k] for k in (
                "pass_one_click", "article_id", "title", "capture_mode",
                "http_failure_code", "list_count", "article_html_exists", "sqlite_ok",
            )}, ensure_ascii=False, indent=2), flush=True)
            conn.close()
        except Exception as exc:
            holder["err"] = repr(exc)
            print("FINISH_ERR", repr(exc), flush=True)
            import traceback

            traceback.print_exc()
        finally:
            context.close()
            app.quit()

    bridge.taskChanged.connect(on_task)
    resp = json.loads(bridge.startWebArchive(URL))
    if not resp.get("ok"):
        print("start failed", resp)
        return 1

    QTimer.singleShot(120_000, lambda: (print("TIMEOUT"), app.quit()))
    app.exec()
    if holder.get("err"):
        return 1
    return 0 if holder.get("done") and (holder.get("state") or {}).get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
