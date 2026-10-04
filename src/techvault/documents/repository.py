"""SQL storage for documents, categories, tags, and the FTS5 index."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from techvault.documents.models import UNSET, Category, Document, DocumentNotFoundError, Tag
from techvault.errors import DatabaseError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.execute("BEGIN")
        return self._connection

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            self._connection.execute("COMMIT")
        else:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                return False
        return False


def transaction(connection: sqlite3.Connection) -> _Transaction:
    return _Transaction(connection)


class CategoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, name: str, sort_order: int) -> Category:
        try:
            with transaction(self._connection):
                cursor = self._connection.execute(
                    "INSERT INTO categories (name, sort_order) VALUES (?, ?)",
                    (name, sort_order),
                )
                category_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Category could not be saved") from exc
        stored = self.get(category_id)
        if stored is None:
            raise DatabaseError("Category could not be saved")
        return stored

    def rename(self, category_id: int, name: str) -> Category:
        try:
            with transaction(self._connection):
                if self.get(category_id) is None:
                    raise DocumentNotFoundError("Category does not exist")
                self._connection.execute(
                    "UPDATE categories SET name = ? WHERE id = ?",
                    (name, category_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Category could not be renamed") from exc
        stored = self.get(category_id)
        if stored is None:
            raise DocumentNotFoundError("Category does not exist")
        return stored

    def delete(self, category_id: int) -> None:
        with transaction(self._connection):
            if self.get(category_id) is None:
                raise DocumentNotFoundError("Category does not exist")
            self._connection.execute(
                "UPDATE documents SET category_id = NULL WHERE category_id = ?",
                (category_id,),
            )
            self._connection.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def get(self, category_id: int) -> Category | None:
        row = self._connection.execute(
            "SELECT id, name, sort_order FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()
        if row is None:
            return None
        return Category(int(row["id"]), row["name"], int(row["sort_order"]))

    def exists(self, category_id: int) -> bool:
        return self.get(category_id) is not None

    def list_all(self) -> list[Category]:
        rows = self._connection.execute(
            "SELECT id, name, sort_order FROM categories ORDER BY sort_order, name, id"
        ).fetchall()
        return [Category(int(row["id"]), row["name"], int(row["sort_order"])) for row in rows]


class TagRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, name: str) -> Tag:
        try:
            with transaction(self._connection):
                cursor = self._connection.execute("INSERT INTO tags (name) VALUES (?)", (name,))
                tag_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Tag could not be saved") from exc
        stored = self.get(tag_id)
        if stored is None:
            raise DatabaseError("Tag could not be saved")
        return stored

    def rename(self, tag_id: int, name: str) -> Tag:
        try:
            with transaction(self._connection):
                if self.get(tag_id) is None:
                    raise DocumentNotFoundError("Tag does not exist")
                self._connection.execute(
                    "UPDATE tags SET name = ? WHERE id = ?",
                    (name, tag_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Tag could not be renamed") from exc
        stored = self.get(tag_id)
        if stored is None:
            raise DocumentNotFoundError("Tag does not exist")
        return stored

    def delete(self, tag_id: int) -> None:
        with transaction(self._connection):
            if self.get(tag_id) is None:
                raise DocumentNotFoundError("Tag does not exist")
            self._connection.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

    def get(self, tag_id: int) -> Tag | None:
        row = self._connection.execute("SELECT id, name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if row is None:
            return None
        return Tag(int(row["id"]), row["name"])

    def get_by_name(self, name: str) -> Tag | None:
        row = self._connection.execute(
            "SELECT id, name FROM tags WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return Tag(int(row["id"]), row["name"])

    def attach(self, document_id: int, tag_id: int) -> None:
        with transaction(self._connection):
            self._require_document(document_id)
            if self.get(tag_id) is None:
                raise DocumentNotFoundError("Tag does not exist")
            self._connection.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                (document_id, tag_id),
            )

    def detach(self, document_id: int, tag_id: int) -> None:
        with transaction(self._connection):
            self._require_document(document_id)
            if self.get(tag_id) is None:
                raise DocumentNotFoundError("Tag does not exist")
            self._connection.execute(
                "DELETE FROM document_tags WHERE document_id = ? AND tag_id = ?",
                (document_id, tag_id),
            )

    def ids_for_names(self, names: list[str]) -> list[int]:
        found: list[int] = []
        for name in names:
            tag = self.get_by_name(name)
            if tag is not None:
                found.append(tag.id)
        return found

    def _require_document(self, document_id: int) -> None:
        row = self._connection.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise DocumentNotFoundError("Document does not exist")


class DocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._tags = TagRepository(connection)

    def create(
        self,
        *,
        title: str,
        content: str,
        doc_type: str,
        category_id: int | None,
        tag_names: list[str],
    ) -> Document:
        now = _now()
        with transaction(self._connection):
            self._require_category(category_id)
            cursor = self._connection.execute(
                """
                INSERT INTO documents (
                    title, body, doc_type, category_id, is_favorite, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (title, content, doc_type, category_id, now, now),
            )
            document_id = int(cursor.lastrowid)
            self._index_insert(document_id, title, content)
            self._replace_tags(document_id, tag_names)
        stored = self.get(document_id)
        if stored is None:
            raise DatabaseError("Document could not be saved")
        return stored

    def get(self, document_id: int) -> Document | None:
        row = self._connection.execute(
            """
            SELECT id, title, body, doc_type, category_id, is_favorite, created_at, updated_at
            FROM documents WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return self._with_tags([row])[0]

    def update(
        self,
        document_id: int,
        *,
        title: str,
        content: str,
        doc_type: str,
        category_id: int | None,
        tag_names: list[str] | None,
        previous_title: str,
        previous_content: str,
    ) -> Document:
        with transaction(self._connection):
            current = self._connection.execute(
                "SELECT id FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if current is None:
                raise DocumentNotFoundError("Document does not exist")
            self._require_category(category_id)
            self._connection.execute(
                """
                UPDATE documents
                SET title = ?, body = ?, doc_type = ?, category_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, content, doc_type, category_id, _now(), document_id),
            )
            if title != previous_title or content != previous_content:
                self._index_delete(document_id, previous_title, previous_content)
                self._index_insert(document_id, title, content)
            if tag_names is not None:
                self._replace_tags(document_id, tag_names)
        stored = self.get(document_id)
        if stored is None:
            raise DocumentNotFoundError("Document does not exist")
        return stored

    def delete(self, document_id: int) -> None:
        with transaction(self._connection):
            row = self._connection.execute(
                "SELECT title, body FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                raise DocumentNotFoundError("Document does not exist")
            self._index_delete(document_id, row["title"], row["body"])
            self._connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def set_favorite(self, document_id: int, favorite: bool) -> Document:
        with transaction(self._connection):
            if self.get(document_id) is None:
                raise DocumentNotFoundError("Document does not exist")
            self._connection.execute(
                "UPDATE documents SET is_favorite = ? WHERE id = ?",
                (1 if favorite else 0, document_id),
            )
        stored = self.get(document_id)
        if stored is None:
            raise DocumentNotFoundError("Document does not exist")
        return stored

    def list_documents(
        self,
        *,
        limit: int,
        offset: int,
        category_id: int | None,
        tag_ids: list[int] | None,
        match_all_tags: bool,
        favorite_only: bool,
        fts_query: str | None,
        doc_type: str | None = None,
    ) -> list[Document]:
        clauses = ["1 = 1"]
        parameters: list[object] = []
        source = "documents d"
        if fts_query is not None:
            source = "document_search JOIN documents d ON d.id = document_search.rowid"
            clauses.append("document_search MATCH ?")
            parameters.append(fts_query)
        if category_id is not None:
            clauses.append("d.category_id = ?")
            parameters.append(category_id)
        if favorite_only:
            clauses.append("d.is_favorite = 1")
        if doc_type is not None:
            clauses.append("d.doc_type = ?")
            parameters.append(doc_type)
        if tag_ids is not None:
            if not tag_ids:
                return []
            marks = ", ".join("?" for _ in tag_ids)
            if match_all_tags:
                clauses.append(
                    f"""
                    (
                        SELECT COUNT(DISTINCT tag_id) FROM document_tags
                        WHERE document_id = d.id AND tag_id IN ({marks})
                    ) = ?
                    """
                )
                parameters.extend(tag_ids)
                parameters.append(len(set(tag_ids)))
            else:
                clauses.append(
                    f"d.id IN (SELECT document_id FROM document_tags WHERE tag_id IN ({marks}))"
                )
                parameters.extend(tag_ids)
        parameters.extend([limit, offset])
        rows = self._connection.execute(
            f"""
            SELECT d.id, d.title, d.body, d.doc_type, d.category_id, d.is_favorite,
                   d.created_at, d.updated_at
            FROM {source}
            WHERE {' AND '.join(clauses)}
            ORDER BY d.updated_at DESC, d.id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return self._with_tags(rows)

    def _replace_tags(self, document_id: int, tag_names: list[str]) -> None:
        self._connection.execute("DELETE FROM document_tags WHERE document_id = ?", (document_id,))
        for name in tag_names:
            tag = self._tags.get_by_name(name)
            if tag is None:
                cursor = self._connection.execute("INSERT INTO tags (name) VALUES (?)", (name,))
                tag_id = int(cursor.lastrowid)
            else:
                tag_id = tag.id
            self._connection.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                (document_id, tag_id),
            )

    def _require_category(self, category_id: int | None) -> None:
        if category_id is None:
            return
        row = self._connection.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone()
        if row is None:
            raise DocumentNotFoundError("Category does not exist")

    def _index_insert(self, document_id: int, title: str, content: str) -> None:
        self._connection.execute(
            "INSERT INTO document_search(rowid, title, body) VALUES (?, ?, ?)",
            (document_id, title, content),
        )

    def _index_delete(self, document_id: int, title: str, content: str) -> None:
        self._connection.execute(
            """
            INSERT INTO document_search(document_search, rowid, title, body)
            VALUES ('delete', ?, ?, ?)
            """,
            (document_id, title, content),
        )

    def _with_tags(self, rows: list[sqlite3.Row]) -> list[Document]:
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        marks = ", ".join("?" for _ in ids)
        tag_rows = self._connection.execute(
            f"""
            SELECT dt.document_id, tags.id, tags.name
            FROM document_tags dt
            JOIN tags ON tags.id = dt.tag_id
            WHERE dt.document_id IN ({marks})
            ORDER BY tags.name COLLATE NOCASE, tags.id
            """,
            ids,
        ).fetchall()
        grouped: dict[int, list[Tag]] = {document_id: [] for document_id in ids}
        for row in tag_rows:
            grouped[int(row["document_id"])].append(Tag(int(row["id"]), row["name"]))
        return [
            Document(
                id=int(row["id"]),
                title=row["title"],
                content=row["body"],
                doc_type=row["doc_type"],
                category_id=None if row["category_id"] is None else int(row["category_id"]),
                is_favorite=bool(row["is_favorite"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                tags=tuple(grouped[int(row["id"])]),
            )
            for row in rows
        ]
