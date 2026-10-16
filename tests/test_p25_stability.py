"""P2.5: throttle flush, generic http canonical, getArticle payload vs on-disk files."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from PySide6.QtWidgets import QApplication

from techvault.app.bootstrap import prepare_context
from techvault.ui.bridge.task_bridge import TaskBridge
from techvault.ui.bridge.web_archive_bridge import WebArchiveBridge
from techvault.web_archive.service import WebArchiveService
from techvault.web_archive.urls import parse_web_url

FIXTURES = Path(__file__).parent / "fixtures" / "web_archive"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_generic_http_localhost_preserves_scheme() -> None:
    parsed = parse_web_url("http://127.0.0.1:8765/local_stress_xiaoheihe.html")
    assert parsed.site_key == "generic"
    assert parsed.canonical.startswith("http://127.0.0.1:8765/")


def test_throttle_flush_emits_latest_progress(qapp: QApplication) -> None:
    bridge = TaskBridge(Path("unused.sqlite"))
    emits: list[dict] = []
    bridge.taskChanged.connect(lambda raw: emits.append(json.loads(raw)))

    bridge._state = {**bridge._state, "active": True, "kind": "web", "phaseCode": "LOCALIZING_MEDIA", "percent": 10}
    bridge._last_progress_emit_at = 0.0
    bridge._emit_throttled(min_interval_s=0.5, force=True)
    assert len(emits) == 1

    # Burst within window — should schedule flush, not drop forever.
    bridge._state = {**bridge._state, "percent": 42, "fileLabel": "last.png"}
    bridge._emit_throttled(min_interval_s=0.5, force=False)
    bridge._emit_throttled(min_interval_s=0.5, force=False)
    assert bridge._pending_throttle_flush is True
    # Force timer fire.
    bridge._flush_throttled_emit()
    assert emits[-1]["percent"] == 42
    assert emits[-1]["fileLabel"] == "last.png"


def test_get_article_hot_path_keeps_disk_md_and_meta(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    html = (FIXTURES / "xiaoheihe_post_rendered.html").read_text(encoding="utf-8")
    url = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
    try:
        with respx.mock:
            respx.route(host="www.xiaoheihe.cn").mock(return_value=httpx.Response(403))
            result = service.archive_from_browser_dom(
                url=url,
                page_html=html,
                final_url=url,
                title="如何获得一个免费的数据库+后端",
                author="小稿的AI日记",
                body_html="<p>正文足够长用于离线阅读验证。" * 5 + "</p>",
            )
        assert result.article_id
        detail = service.get_article(int(result.article_id))
        assert detail is not None
        assert detail.body_html
        # Hot path may omit md/meta text in the model fields used for WebChannel.
        folder = Path(detail.archive_folder)
        assert (folder / "article.html").is_file()
        assert (folder / "article.md").is_file()
        assert (folder / "metadata.json").is_file()
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("title")

        bridge = WebArchiveBridge(service, tmp_path / "db.sqlite")
        payload = json.loads(bridge.getArticle(int(result.article_id)))
        data = payload["data"]
        assert data["bodyHtml"]
        # Intentionally slim on the wire; files remain on disk for export/openLocal.
        assert data.get("bodyMd") == ""
        assert data.get("metadataJson") == ""
    finally:
        context.close()
