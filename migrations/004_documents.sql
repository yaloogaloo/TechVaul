ALTER TABLE documents ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'NOTE' CHECK (doc_type IN ('COMMAND', 'PROMPT', 'TIP', 'NOTE'));

CREATE VIRTUAL TABLE document_search USING fts5(
    title,
    body,
    content='documents',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE UNIQUE INDEX ux_tags_name_nocase ON tags(name COLLATE NOCASE);

CREATE UNIQUE INDEX ux_categories_name_nocase ON categories(name COLLATE NOCASE);
