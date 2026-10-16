"""TechVault performance baseline (isolated DB). Measures bridge/main-thread costs.

Usage:
  .\\.venv\\Scripts\\python.exe tools\\perf_baseline.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "docs" / "performance"
OUT.mkdir(parents=True, exist_ok=True)


def timed(fn, n: int = 5) -> dict:
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return {
        "n": n,
        "min_ms": round(samples[0], 2),
        "median_ms": round(statistics.median(samples), 2),
        "p95_ms": round(samples[max(0, int(len(samples) * 0.95) - 1)], 2),
        "max_ms": round(samples[-1], 2),
        "samples_ms": [round(x, 2) for x in samples],
    }


def seed_web(service, count: int, body_kb: int) -> None:
    from techvault.web_archive.capture import publish_from_rendered_html

    chunk = ("段落内容用于性能测试。" * 20) + "\n"
    body = "<p>" + chunk * max(1, (body_kb * 1024) // len(chunk)) + "</p>"
    for i in range(count):
        url = f"https://www.zhihu.com/question/{1000 + i}/answer/{2000 + i}"
        publish_from_rendered_html(
            service._connection,  # noqa: SLF001
            url=url,
            page_html=f"<html><body>{body}</body></html>",
            final_url=url,
            capture_mode="URL_AUTO",
            preextracted={"title": f"性能测试文章 {i:04d}", "author": "perf", "body_html": body},
        )


def seed_docs(documents, count: int) -> None:
    content = "# 标题\n\n" + ("这是长文档性能测试段落。\n\n" * 200)
    for i in range(count):
        documents.create_document(title=f"文档 {i:04d}", content=content, doc_type="NOTE")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.bridge.github_bridge import GitHubBridge
    from techvault.ui.bridge.knowledge_bridge import KnowledgeBridge
    from techvault.ui.bridge.task_bridge import TaskBridge
    from techvault.ui.bridge.web_archive_bridge import WebArchiveBridge
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.web_archive.mhtml import validate_mhtml_file
    from techvault.web_archive.service import WebArchiveService

    tmp = Path(tempfile.mkdtemp(prefix="tv-perf-"))
    db = tmp / "db.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_BROWSER_PROFILES"] = str(tmp / "profiles")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    t_boot0 = time.perf_counter()
    context = prepare_context(db, archive)
    web = WebArchiveService(context.connection)
    seed_web(web, count=120, body_kb=40)
    seed_docs(context.documents, count=120)
    win = DesktopWindow(context)
    win.resize(1440, 900)
    win.show()
    app.processEvents()
    cold_start_ms = round((time.perf_counter() - t_boot0) * 1000, 2)

    web_bridge = WebArchiveBridge(web, db, win, task_bridge=win.tasks)
    knowledge = KnowledgeBridge(context.documents, win)
    github = GitHubBridge(context.connection, db, win)

    results: dict = {
        "env": {
            "tmp": str(tmp),
            "web_articles": 120,
            "documents": 120,
            "article_body_approx_kb": 40,
        },
        "cold_start_to_window_ms": cold_start_ms,
    }

    # listArticles
    results["list_articles"] = timed(lambda: web_bridge.listArticles('{"query":"","offset":0}'), 8)

    # getArticle (large JSON over bridge path — includes body)
    first_id = web.list_articles(limit=1)[0].id
    results["get_article_full"] = timed(lambda: web_bridge.getArticle(first_id), 8)

    # list documents
    results["list_documents"] = timed(
        lambda: knowledge.listDocuments('{"query":"","offset":0}'), 8
    )
    doc_id = json.loads(knowledge.listDocuments('{"query":"","offset":0}'))["data"]["items"][0]["id"]
    results["get_document"] = timed(lambda: knowledge.getDocument(doc_id), 8)

    # workspace switch simulation (show/hide already done by React; measure processEvents)
    def switch_burst():
        for _ in range(20):
            app.processEvents()

    results["process_events_burst_20"] = timed(switch_burst, 5)

    # MHTML hash cost (synthetic ~800KB)
    mhtml = tmp / "sample.mhtml"
    payload = (
        b"From: <Saved by Blink>\nMIME-Version: 1.0\nContent-Type: multipart/related; boundary=--x\n\n"
        + b"--x\nContent-Type: text/html\n\n<html><body>"
        + (b"x" * 800_000)
        + b"</body></html>\n--x--"
    )
    mhtml.write_bytes(payload)
    results["mhtml_validate_sha256_800kb"] = timed(lambda: validate_mhtml_file(mhtml), 5)

    # task emit cost
    tb = TaskBridge(db)
    emits = {"n": 0}

    def on_task(_):
        emits["n"] += 1

    tb.taskChanged.connect(on_task)
    t0 = time.perf_counter()
    for i in range(200):
        tb._state = {**tb._state, "phase": f"p{i}", "active": True, "kind": "web"}  # noqa: SLF001
        tb._emit()  # noqa: SLF001
        app.processEvents()
    results["task_emit_200"] = {
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        "emits": emits["n"],
    }

    # File read of large article.html (main-thread style)
    detail = web.get_article(first_id)
    assert detail and detail.archive_folder
    html_path = Path(detail.archive_folder) / "article.html"

    def read_html():
        html_path.read_text(encoding="utf-8")

    results["read_article_html"] = timed(read_html, 8)

    # JSON size of getArticle payload
    payload = web_bridge.getArticle(first_id)
    results["get_article_payload_bytes"] = len(payload.encode("utf-8"))

    (OUT / "baseline_raw.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    win.close()
    context.close()
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
