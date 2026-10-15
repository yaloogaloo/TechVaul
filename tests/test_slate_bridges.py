"""Slate Knowledge/Settings bridges against DocumentService and SettingsService."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from techvault.app.bootstrap import prepare_context
from techvault.ui.bridge.application_bridge import ApplicationBridge
from techvault.ui.bridge.knowledge_bridge import PAGE_SIZE, KnowledgeBridge
from techvault.ui.bridge.settings_bridge import SettingsBridge


@pytest.fixture
def context(tmp_path: Path):
    ctx = prepare_context(tmp_path / "techvault.sqlite", tmp_path / "archive")
    yield ctx
    ctx.close()


@pytest.fixture
def knowledge(context):
    return KnowledgeBridge(context.documents)


@pytest.fixture
def settings(context):
    return SettingsBridge(context.settings)


def _data(raw: str):
    payload = json.loads(raw)
    assert payload["ok"] is True, payload
    return payload["data"]


def _err(raw: str):
    payload = json.loads(raw)
    assert payload["ok"] is False, payload
    return payload["error"]


def test_bootstrap_marks_workspaces_ready() -> None:
    bridge = ApplicationBridge()
    data = _data(bridge.getBootstrap())
    assert data["knowledgeReady"] is True
    assert data["settingsReady"] is True
    assert "knowledge" in data["workspaces"]


def test_knowledge_create_list_get_update_delete(knowledge, context) -> None:
    cat = _data(knowledge.createCategory("Git"))
    saved = _data(
        knowledge.saveDocument(
            json.dumps(
                {
                    "title": "Git 远程",
                    "content": "git remote -v",
                    "docType": "COMMAND",
                    "categoryId": cat["id"],
                    "tags": ["git", "remote"],
                    "favorite": False,
                }
            )
        )
    )
    assert saved["id"] > 0
    assert "git remote" in saved["contentHtml"] or "git remote" in saved["content"]
    assert saved["docTypeLabel"] == "命令"

    page = _data(knowledge.listDocuments(json.dumps({"query": "", "offset": 0})))
    assert any(item["title"] == "Git 远程" for item in page["items"])
    assert page["hasMore"] is False

    detail = _data(knowledge.getDocument(saved["id"]))
    assert detail["content"] == "git remote -v"
    assert detail["tags"] == ["git", "remote"]

    updated = _data(
        knowledge.saveDocument(
            json.dumps(
                {
                    "id": saved["id"],
                    "title": "Git 远程地址",
                    "content": "git remote -v\n# note",
                    "docType": "COMMAND",
                    "categoryId": cat["id"],
                    "tags": ["git"],
                    "favorite": True,
                }
            )
        )
    )
    assert updated["title"] == "Git 远程地址"
    assert updated["isFavorite"] is True

    fav = _data(knowledge.setFavorite(saved["id"], False))
    assert fav["isFavorite"] is False

    _data(knowledge.deleteDocument(saved["id"]))
    assert _err(knowledge.getDocument(saved["id"]))["code"] == "NOT_FOUND"


def test_knowledge_search_filters_and_pagination(knowledge) -> None:
    for index in range(PAGE_SIZE + 3):
        _data(
            knowledge.saveDocument(
                json.dumps(
                    {
                        "title": f"文档 {index:04d}",
                        "content": f"body-{index} searchable",
                        "docType": "NOTE" if index % 2 == 0 else "TIP",
                        "tags": ["batch"] if index < 10 else ["other"],
                        "favorite": index == 0,
                    }
                )
            )
        )
    first = _data(knowledge.listDocuments(json.dumps({"offset": 0})))
    assert len(first["items"]) == PAGE_SIZE
    assert first["hasMore"] is True
    second = _data(knowledge.listDocuments(json.dumps({"offset": PAGE_SIZE})))
    assert len(second["items"]) == 3
    assert second["hasMore"] is False

    search = _data(knowledge.listDocuments(json.dumps({"query": "body-1", "offset": 0})))
    assert search["items"]
    assert all("1" in item["title"] or "1" in item["summary"] for item in search["items"])

    tips = _data(knowledge.listDocuments(json.dumps({"docType": "TIP", "offset": 0})))
    assert tips["items"]
    assert all(item["docType"] == "TIP" for item in tips["items"])

    tagged = _data(knowledge.listDocuments(json.dumps({"tags": "batch", "offset": 0})))
    assert tagged["items"]
    fav = _data(knowledge.listDocuments(json.dumps({"favoriteOnly": True, "offset": 0})))
    assert len(fav["items"]) == 1


def test_knowledge_copy_uses_clipboard(knowledge, qtbot) -> None:
    app = QApplication.instance()
    assert app is not None
    saved = _data(
        knowledge.saveDocument(
            json.dumps({"title": "Copy me", "content": "only-body", "docType": "NOTE"})
        )
    )
    _data(knowledge.copyDocument(saved["id"]))
    assert app.clipboard().text() == "only-body"


def test_knowledge_summary_strips_markdown(knowledge) -> None:
    saved = _data(
        knowledge.saveDocument(
            json.dumps(
                {
                    "title": "MD",
                    "content": "# Title\n\n**bold** and `code` with more text to truncate if needed",
                    "docType": "NOTE",
                }
            )
        )
    )
    page = _data(knowledge.listDocuments(json.dumps({"offset": 0})))
    item = next(i for i in page["items"] if i["id"] == saved["id"])
    assert "#" not in item["summary"]
    assert "**" not in item["summary"]


def test_settings_archive_root_and_token(settings, context, tmp_path: Path, monkeypatch) -> None:
    status = _data(settings.getStatus())
    assert status["ready"] is True
    assert status["archiveRoot"]
    assert "迁移" in status["migrationNote"] or "migrate" in status["migrationNote"].lower() or "不会" in status["migrationNote"]

    new_root = tmp_path / "new-archive"
    saved = _data(settings.setArchiveRoot(str(new_root)))
    assert Path(saved["archiveRoot"]) == new_root.resolve()
    assert context.settings.get_archive_root() == new_root.resolve()

    bad = _err(settings.setArchiveRoot("   "))
    assert bad["code"] == "INVALID_PATH"

    monkeypatch.setattr("techvault.app.settings.save_token", lambda token: None)
    monkeypatch.setattr("techvault.app.settings.has_token", lambda: True)
    monkeypatch.setattr("techvault.app.settings.delete_token", lambda: None)
    configured = _data(settings.saveToken("ghp_test_token_value"))
    assert configured["tokenStatus"] == "已配置"
    # Never echo the token back
    assert "ghp_test" not in json.dumps(configured)

    monkeypatch.setattr("techvault.app.settings.has_token", lambda: False)
    cleared = _data(settings.clearToken())
    assert cleared["tokenStatus"] == "未配置"


def test_settings_keyring_unavailable(settings, monkeypatch) -> None:
    from techvault.errors import KeyringUnavailableError

    monkeypatch.setattr(
        "techvault.app.settings.save_token",
        lambda token: (_ for _ in ()).throw(KeyringUnavailableError("no keyring")),
    )
    error = _err(settings.saveToken("x"))
    assert error["code"] == "KEYRING"


def test_close_guard_denies_until_answered() -> None:
    bridge = ApplicationBridge()
    assert bridge.request_close_permission() is True
    _data(bridge.registerCloseGuard())

    from PySide6.QtCore import QTimer

    answers: list[str] = []

    def answer():
        answers.append("deny")
        bridge.answerClose("deny")

    QTimer.singleShot(20, answer)
    assert bridge.request_close_permission() is False
    assert answers == ["deny"]

    def allow():
        bridge.answerClose("allow")

    QTimer.singleShot(20, allow)
    assert bridge.request_close_permission() is True
