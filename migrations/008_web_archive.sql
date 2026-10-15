-- Web Archive (independent of GitHub repositories / knowledge documents).

CREATE TABLE web_articles (
    id INTEGER PRIMARY KEY,
    canonical_url TEXT NOT NULL,
    source_url TEXT NOT NULL,
    site_key TEXT NOT NULL,
    site_label TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    published_at TEXT,
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL,
    capture_mode TEXT NOT NULL DEFAULT 'URL_AUTO',
    content_hash TEXT,
    storage_relpath TEXT,
    archive_root TEXT,
    relative_html TEXT,
    relative_md TEXT,
    relative_meta TEXT,
    plain_text TEXT,
    media_total INTEGER NOT NULL DEFAULT 0,
    media_missing INTEGER NOT NULL DEFAULT 0,
    category_id INTEGER NULL REFERENCES categories(id),
    is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
    failure_code TEXT,
    failure_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (canonical_url),
    CHECK (
        status IN (
            'COMPLETE',
            'PARTIAL',
            'FAILED',
            'NEEDS_LOGIN',
            'LINK_ONLY',
            'CANCELLED'
        )
    ),
    CHECK (capture_mode IN ('URL_AUTO', 'BROWSER', 'IMPORT'))
);

CREATE INDEX ix_web_articles_captured_at ON web_articles (captured_at DESC);
CREATE INDEX ix_web_articles_site_key ON web_articles (site_key);
CREATE INDEX ix_web_articles_favorite ON web_articles (is_favorite);
CREATE INDEX ix_web_articles_category ON web_articles (category_id);

CREATE TABLE web_captures (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES web_articles(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    content_hash TEXT,
    relative_html TEXT,
    relative_md TEXT,
    relative_meta TEXT,
    media_total INTEGER NOT NULL DEFAULT 0,
    media_missing INTEGER NOT NULL DEFAULT 0,
    failure_code TEXT,
    failure_message TEXT,
    UNIQUE (article_id, version),
    CHECK (
        status IN (
            'COMPLETE',
            'PARTIAL',
            'FAILED',
            'NEEDS_LOGIN',
            'LINK_ONLY',
            'CANCELLED'
        )
    )
);

CREATE VIRTUAL TABLE web_article_search USING fts5(
    title,
    author,
    plain_text,
    content='web_articles',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
