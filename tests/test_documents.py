import sqlite3
from pathlib import Path

import pytest

from techvault.db.connection import connect
from techvault.db.migrate import apply_migrations, default_migrations_dir
from techvault.documents.models import DocumentNotFoundError, DocumentValidationError
from techvault.documents.service import DocumentService
from techvault.errors import DatabaseError


def _service(tmp_path: Path) -> tuple[sqlite3.Connection, DocumentService]:
    connection = connect(tmp_path / "techvault.sqlite")
    apply_migrations(connection, default_migrations_dir())
    return connection, DocumentService(connection)


def test_create_and_get_document(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Git status", "git status", doc_type="COMMAND")
        loaded = service.get_document(created.id)
        assert created.id == loaded.id
        assert loaded.title == "Git status"
        assert loaded.content == "git status"
        assert loaded.doc_type == "COMMAND"
        assert loaded.is_favorite is False
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    finally:
        connection.close()


def test_get_missing_document(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentNotFoundError):
            service.get_document(999)
    finally:
        connection.close()


def test_update_document_changes_updated_at(tmp_path: Path, monkeypatch) -> None:
    connection, service = _service(tmp_path)
    try:
        times = iter(["2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"])
        monkeypatch.setattr("techvault.documents.repository._now", lambda: next(times))
        created = service.create_document("Old", "old")
        updated = service.update_document(created.id, title="New", content="new")
        assert updated.title == "New"
        assert updated.content == "new"
        assert updated.updated_at == "2026-02-01T00:00:00+00:00"
        assert updated.created_at == "2026-01-01T00:00:00+00:00"
        assert updated.updated_at != created.updated_at
    finally:
        connection.close()


def test_read_does_not_change_updated_at(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Stable", "body")
        loaded = service.get_document(created.id)
        listed = service.list_documents()
        assert loaded.updated_at == created.updated_at
        assert listed[0].updated_at == created.updated_at
    finally:
        connection.close()


def test_delete_document(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Gone", "body", tags=["git"])
        service.delete_document(created.id)
        with pytest.raises(DocumentNotFoundError):
            service.get_document(created.id)
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        connection.close()


def test_delete_missing_document(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentNotFoundError):
            service.delete_document(4)
    finally:
        connection.close()


def test_empty_title_is_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentValidationError):
            service.create_document("", "body")
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        connection.close()


def test_whitespace_title_is_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentValidationError):
            service.create_document("   \n", "body")
    finally:
        connection.close()


def test_title_is_stored_trimmed_without_case_change(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("  Git Rebase  ", "body")
        assert created.title == "Git Rebase"
    finally:
        connection.close()


def test_empty_content_is_allowed(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Draft")
        assert created.content == ""
    finally:
        connection.close()


def test_empty_tag_is_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentValidationError):
            service.create_tag("")
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    finally:
        connection.close()


def test_whitespace_tag_is_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentValidationError):
            service.create_tag(" \t ")
    finally:
        connection.close()


def test_content_whitespace_is_preserved(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        content = "  git status\n\t-s\n"
        created = service.create_document("Status", content)
        assert service.get_document(created.id).content == content
        assert service.get_copy_text(created.id) == content
    finally:
        connection.close()


def test_unicode_content_roundtrip(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        content = "PowerShell：Get-ChildItem\nGit：git rebase -i\n命令 ✅"
        created = service.create_document("中文标题", content, doc_type="TIP")
        loaded = service.get_document(created.id)
        assert loaded.title == "中文标题"
        assert loaded.content == content
    finally:
        connection.close()


def test_document_type_must_be_known(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        for doc_type in ("COMMAND", "PROMPT", "TIP", "NOTE"):
            created = service.create_document(doc_type, doc_type=doc_type)
            assert created.doc_type == doc_type
        with pytest.raises(DocumentValidationError):
            service.create_document("Bad", doc_type="note")
        with pytest.raises(DocumentValidationError):
            service.create_document("Bad", doc_type="OTHER")
    finally:
        connection.close()


def test_create_rename_and_delete_category(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        category = service.create_category("  Command  ", sort_order=2)
        assert category.name == "Command"
        renamed = service.rename_category(category.id, "Development")
        assert renamed.name == "Development"
        assert renamed.sort_order == 2
        service.delete_category(category.id)
        assert connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
    finally:
        connection.close()


def test_document_survives_category_deletion(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        category = service.create_category("Operation")
        created = service.create_document("Steps", "keep me", category_id=category.id)
        before = created.updated_at
        service.delete_category(category.id)
        loaded = service.get_document(created.id)
        assert loaded.id == created.id
        assert loaded.title == "Steps"
        assert loaded.content == "keep me"
        assert loaded.category_id is None
        assert loaded.updated_at == before
        assert connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0
    finally:
        connection.close()


def test_unknown_category_is_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        with pytest.raises(DocumentNotFoundError):
            service.create_document("Orphan", category_id=8)
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        connection.close()


def test_create_rename_and_delete_tag(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        tag = service.create_tag("  git  ")
        assert tag.name == "git"
        renamed = service.rename_tag(tag.id, "Git")
        assert renamed.id == tag.id
        assert renamed.name == "Git"
        service.delete_tag(tag.id)
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
    finally:
        connection.close()


def test_duplicate_tag_name_is_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        service.create_tag("git")
        with pytest.raises(DatabaseError):
            service.create_tag("GIT")
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
    finally:
        connection.close()


def test_attach_and_detach_tag(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        document = service.create_document("Commands", "git status")
        tag = service.create_tag("git")
        service.attach_tag(document.id, tag.id)
        assert service.get_document(document.id).tags == (tag,)
        service.detach_tag(document.id, tag.id)
        assert service.get_document(document.id).tags == ()
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
    finally:
        connection.close()


def test_duplicate_attach_does_not_duplicate_relation(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        document = service.create_document("Commands", "git status")
        tag = service.create_tag("git")
        service.attach_tag(document.id, tag.id)
        service.attach_tag(document.id, tag.id)
        assert connection.execute("SELECT COUNT(*) FROM document_tags").fetchone()[0] == 1
    finally:
        connection.close()


def test_delete_tag_keeps_document_and_removes_link(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        document = service.create_document("Commands", "git status", tags=["git", "powershell"])
        git = next(tag for tag in document.tags if tag.name == "git")
        service.delete_tag(git.id)
        loaded = service.get_document(document.id)
        assert loaded.content == "git status"
        assert [tag.name for tag in loaded.tags] == ["powershell"]
        assert connection.execute("SELECT COUNT(*) FROM document_tags").fetchone()[0] == 1
    finally:
        connection.close()


def test_set_and_toggle_favorite(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Keep", "body")
        marked = service.set_favorite(created.id, True)
        assert marked.is_favorite is True
        assert marked.updated_at == created.updated_at
        cleared = service.set_favorite(created.id, False)
        assert cleared.is_favorite is False
        assert cleared.updated_at == created.updated_at
        toggled = service.toggle_favorite(created.id)
        assert toggled.is_favorite is True
        back = service.toggle_favorite(created.id)
        assert back.is_favorite is False
    finally:
        connection.close()


def test_favorite_filter_uses_database_flag(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        favorite = service.create_document("Pinned", "keep")
        service.create_document("Other", "skip")
        service.set_favorite(favorite.id, True)
        found = service.list_documents(favorite_only=True)
        assert [item.id for item in found] == [favorite.id]
    finally:
        connection.close()


def test_search_matches_title_case_insensitively(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Git rebase 常用命令", "notes")
        service.create_document("Unrelated", "nothing here")
        found = service.search_documents("GIT REBASE")
        assert [item.id for item in found] == [created.id]
    finally:
        connection.close()


def test_search_matches_content(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("History", "git rebase -i HEAD~3")
        found = service.search_documents("git rebase")
        assert [item.id for item in found] == [created.id]
    finally:
        connection.close()


def test_search_has_no_match(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        service.create_document("History", "git status")
        assert service.search_documents("terraform") == []
        assert service.search_documents("   ") == []
    finally:
        connection.close()


def test_search_unicode_and_commands(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document(
            "终端命令",
            "PowerShell：Get-ChildItem\nGit：git rebase -i\n命令 ✅",
        )
        assert service.search_documents("Get-ChildItem")[0].id == created.id
        assert service.search_documents("终端")[0].id == created.id
        assert service.search_documents("命令")[0].id == created.id
    finally:
        connection.close()


def test_search_requires_all_terms(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        both = service.create_document("Git rebase 常用命令", "git rebase -i HEAD~3")
        service.create_document("Status", "git status")
        found = service.search_documents("git rebase")
        assert [item.id for item in found] == [both.id]
    finally:
        connection.close()


def test_search_special_characters_do_not_raise(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        service.create_document("Quoted", 'git "rebase"')
        found = service.search_documents('git "rebase"')
        assert isinstance(found, list)
        assert service.search_documents("rebase")
    finally:
        connection.close()


def test_category_filter(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        command = service.create_category("Command")
        other = service.create_category("Other")
        first = service.create_document("A", "a", category_id=command.id)
        service.create_document("B", "b", category_id=other.id)
        found = service.list_documents(category_id=command.id)
        assert [item.id for item in found] == [first.id]
    finally:
        connection.close()


def test_tag_filter_or_and_and(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        git_only = service.create_document("Git", "status", tags=["git"])
        both = service.create_document("Both", "status", tags=["git", "powershell"])
        either = service.list_documents(tags=["git", "powershell"])
        assert {item.id for item in either} == {git_only.id, both.id}
        every = service.list_documents(tags=["git", "powershell"], match_all_tags=True)
        assert [item.id for item in every] == [both.id]
        assert service.list_documents(tags=["git", "missing"], match_all_tags=True) == []
    finally:
        connection.close()


def test_search_respects_favorite_and_category_filters(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        category = service.create_category("Command")
        pinned = service.create_document("Git rebase", "git rebase -i", category_id=category.id)
        service.create_document("Git rebase other", "git rebase -i")
        service.set_favorite(pinned.id, True)
        found = service.search_documents("git rebase", category_id=category.id, favorite_only=True)
        assert [item.id for item in found] == [pinned.id]
    finally:
        connection.close()


def test_pagination_limit_and_offset(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = [service.create_document(f"Doc {index}", "body") for index in range(3)]
        connection.execute("UPDATE documents SET updated_at = '2026-01-01T00:00:00+00:00'")
        page = service.list_documents(limit=1, offset=1)
        ordered = sorted(created, key=lambda item: item.id, reverse=True)
        assert [item.id for item in page] == [ordered[1].id]
    finally:
        connection.close()


def test_default_page_size_is_50(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        for index in range(55):
            service.create_document(f"Doc {index}", "body")
        page = service.list_documents()
        assert len(page) == 50
        assert page[0].id > page[-1].id
    finally:
        connection.close()


def test_equal_updated_at_orders_by_id_desc(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        first = service.create_document("First", "a")
        second = service.create_document("Second", "b")
        connection.execute("UPDATE documents SET updated_at = '2026-03-01T00:00:00+00:00'")
        listed = service.list_documents()
        assert [item.id for item in listed] == [second.id, first.id]
    finally:
        connection.close()


def test_create_with_tags_rolls_back_when_link_fails(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER abort_document_tag
            BEFORE INSERT ON document_tags
            BEGIN
                SELECT RAISE(ABORT, 'tag link failed');
            END
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            service.create_document("Partial", "body", tags=["git"])
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_tags").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_search").fetchone()[0] == 0
    finally:
        connection.close()


def test_update_with_tags_rolls_back_to_old_state(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Old title", "old-body", tags=["keep"])
        connection.execute(
            """
            CREATE TRIGGER abort_document_tag
            BEFORE INSERT ON document_tags
            BEGIN
                SELECT RAISE(ABORT, 'tag link failed');
            END
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            service.update_document(created.id, title="New title", content="new-body", tags=["other"])
        loaded = service.get_document(created.id)
        assert loaded.title == "Old title"
        assert loaded.content == "old-body"
        assert [tag.name for tag in loaded.tags] == ["keep"]
        assert [item.id for item in service.search_documents("old-body")] == [created.id]
        assert service.search_documents("new-body") == []
    finally:
        connection.close()


def test_delete_removes_links_but_keeps_tag_and_category(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        category = service.create_category("Development")
        other = service.create_document("Other", "stay", tags=["shared"])
        created = service.create_document("Gone", "remove", category_id=category.id, tags=["shared"])
        service.delete_document(created.id)
        assert service.get_document(other.id).tags[0].name == "shared"
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM document_tags WHERE document_id = ?",
            (created.id,),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_search_index_follows_create_update_and_delete(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Notes", "alpha-token-zzz")
        assert [item.id for item in service.search_documents("alpha-token-zzz")] == [created.id]
        service.update_document(created.id, content="beta-token-zzz")
        assert service.search_documents("alpha-token-zzz") == []
        assert [item.id for item in service.search_documents("beta-token-zzz")] == [created.id]
        service.delete_document(created.id)
        assert service.search_documents("beta-token-zzz") == []
    finally:
        connection.close()


def test_copy_text_is_content_only(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        created = service.create_document("Title stays out", "git status\n")
        assert service.get_copy_text(created.id) == "git status\n"
    finally:
        connection.close()


def test_invalid_limit_and_offset_are_rejected(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    try:
        service.create_document("One", "body")
        with pytest.raises(DocumentValidationError):
            service.list_documents(limit=0)
        with pytest.raises(DocumentValidationError):
            service.list_documents(offset=-1)
    finally:
        connection.close()


def test_migrations_apply_through_document_search(tmp_path: Path) -> None:
    connection, _service_ready = _service(tmp_path)
    try:
        versions = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(documents)")
        }
        assert "doc_type" in columns
        assert "body" in columns
        fts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'document_search'"
        ).fetchone()
        assert fts is not None
    finally:
        connection.close()


def test_document_knowledge_base_real_sqlite(tmp_path: Path) -> None:
    connection = connect(tmp_path / "techvault.sqlite")
    apply_migrations(connection, default_migrations_dir())
    service = DocumentService(connection)
    try:
        operation = service.create_category("Operation")
        prompt = service.create_category("AI Prompt")
        created = service.create_document(
            "  终端排错  ",
            "PowerShell：Get-ChildItem\nGit：git rebase -i HEAD~3\n命令 ✅",
            doc_type="COMMAND",
            category_id=operation.id,
            tags=["git", "powershell"],
        )
        assert created.title == "终端排错"
        assert service.search_documents("Get-ChildItem")[0].id == created.id
        assert service.search_documents("git rebase")[0].id == created.id
        updated_at = created.updated_at
        pinned = service.set_favorite(created.id, True)
        assert pinned.is_favorite is True
        assert pinned.updated_at == updated_at
        assert service.list_documents(favorite_only=True, category_id=operation.id)[0].id == created.id
        service.update_document(created.id, content="use git switch instead")
        assert service.search_documents("Get-ChildItem") == []
        assert service.search_documents("git switch")[0].content == "use git switch instead"
        service.delete_category(operation.id)
        survived = service.get_document(created.id)
        assert survived.category_id is None
        assert survived.content == "use git switch instead"
        git = next(tag for tag in survived.tags if tag.name == "git")
        service.delete_tag(git.id)
        assert [tag.name for tag in service.get_document(created.id).tags] == ["powershell"]
        service.delete_document(created.id)
        assert service.search_documents("git switch") == []
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM categories").fetchone()[0] == "AI Prompt"
        assert prompt.id
        assert service.get_copy_text(service.create_document("Copy", "  raw\n").id) == "  raw\n"
    finally:
        connection.close()
