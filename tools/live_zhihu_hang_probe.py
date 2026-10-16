"""Isolation probe for consecutive Zhihu answer → column archive hang.

Uses one TaskBridge lifecycle + isolated SQLite/archive_root.
Requires display + network.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\live_zhihu_hang_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ANSWER = "https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493"
COLUMN = "https://zhuanlan.zhihu.com/p/2058207332111218352"
OUT = ROOT / "docs" / "web_archive" / "hang_case"


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.bridge.task_bridge import TaskBridge

    tmp = Path(tempfile.mkdtemp(prefix="tv-hang-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    context = prepare_context(db, archive)
    bridge = TaskBridge(db)
    evidence: dict = {"tmp": str(tmp), "runs": []}
    holder: dict = {"idx": 0, "urls": [ANSWER, COLUMN], "phases": [], "done_run": False}

    def on_task(raw: str) -> None:
        state = json.loads(raw)
        holder["phases"].append(
            {
                "taskId": state.get("taskId"),
                "phaseCode": state.get("phaseCode"),
                "status": state.get("status"),
                "active": state.get("active"),
                "pageKind": state.get("pageKind"),
            }
        )
        print(
            f"[task] id={state.get('taskId')} kind={state.get('pageKind')} "
            f"phase={state.get('phaseCode')} status={state.get('status')} active={state.get('active')}",
            flush=True,
        )
        if state.get("kind") == "web" and not state.get("active") and state.get("status") in {
            "success",
            "failed",
            "cancelled",
        }:
            if holder["done_run"]:
                return
            holder["done_run"] = True
            evidence["runs"].append(
                {
                    "url": holder["urls"][holder["idx"]],
                    "final": {
                        "taskId": state.get("taskId"),
                        "status": state.get("status"),
                        "phaseCode": state.get("phaseCode"),
                        "pageKind": state.get("pageKind"),
                        "articleId": state.get("articleId"),
                        "captureMode": state.get("captureMode"),
                        "httpFailureCode": state.get("httpFailureCode"),
                        "browserFailureCode": state.get("browserFailureCode"),
                        "message": state.get("message"),
                    },
                    "phases": list(holder["phases"]),
                }
            )
            holder["phases"] = []
            holder["idx"] += 1
            if holder["idx"] < len(holder["urls"]):
                holder["done_run"] = False
                QTimer.singleShot(800, start_next)
            else:
                QTimer.singleShot(400, finish)

    def start_next() -> None:
        url = holder["urls"][holder["idx"]]
        print(f"\n=== START {holder['idx']+1}/{len(holder['urls'])} {url} ===", flush=True)
        resp = json.loads(bridge.startWebArchive(url))
        if not resp.get("ok"):
            print("start failed", resp, flush=True)
            evidence["error"] = resp
            finish()

    def finish() -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        evidence["pass_sequential"] = (
            len(evidence["runs"]) == 2
            and all(r["final"]["status"] == "success" for r in evidence["runs"])
            and all(not r["final"].get("active") for r in evidence["runs"])
        )
        evidence["pass_no_orphan_active"] = bridge._state.get("active") is False
        (OUT / "result.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({
            "pass_sequential": evidence["pass_sequential"],
            "runs": [
                {
                    "pageKind": r["final"].get("pageKind"),
                    "status": r["final"].get("status"),
                    "articleId": r["final"].get("articleId"),
                    "taskId": r["final"].get("taskId"),
                }
                for r in evidence["runs"]
            ],
        }, ensure_ascii=False, indent=2), flush=True)
        context.close()
        app.quit()

    bridge.taskChanged.connect(on_task)
    QTimer.singleShot(200, start_next)
    QTimer.singleShot(240_000, lambda: (print("GLOBAL_TIMEOUT"), finish()))
    app.exec()
    return 0 if evidence.get("pass_sequential") else 1


if __name__ == "__main__":
    raise SystemExit(main())
