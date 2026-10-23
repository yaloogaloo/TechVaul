# TechVault — Current Architecture (as implemented)

Fact sheet of the running system after R1 Batches 1–2. Not an aspirational redesign.

## 1. Desktop shell

```
techvault.ui.app.main
  → QApplication
  → bootstrap / SettingsService / DB migrate
  → DesktopWindow (QMainWindow + QWebEngineView)
       ├── loads Vite-built Slate (or dev URL)
       ├── QWebChannel objects: application, github, knowledge, settings, tasks, webArchive
       ├── close guard ↔ application.closeCheckRequested / answerClose
       └── child windows: WebCaptureWindow, SnapshotViewerWindow
```

- Slate UI: React + TypeScript + Vite under `frontend/`.
- Business logic stays in Python; React is view + WebChannel client (`services/qtBridge.ts`).

## 2. Bridge map

| Channel object | Python class | Primary services |
|---|---|---|
| `application` | `ApplicationBridge` | bootstrap, close guard |
| `github` | `GitHubBridge` | `app.library` list/detail/categories/open paths |
| `knowledge` | `KnowledgeBridge` | `documents.DocumentService` |
| `settings` | `SettingsBridge` | `SettingsService`, token keyring, archive root |
| `tasks` | `TaskBridge` | `ArchiveWorker`, `WebArchiveWorker`, capture window ownership |
| `webArchive` | `WebArchiveBridge` | `WebArchiveService`, open capture, MHTML viewer hooks |

Shared JSON envelope: `ui/bridge/_json.py` → `{ ok, data }` / `{ ok: false, error: { code, message } }`.

Result types on the frontend: `frontend/src/types/bridge.ts` (hand-maintained; no codegen).

## 3. Task lifecycle (actual)

`TaskBridge` is the single UI-facing task authority.

Observed fields in `_state` (GitHub + web): `active`, `ownerRepo`, `phase`, `phaseCode`, `progress`, `message`, `cancelable`, `taskId` (web), `pageKind` (web), terminal result fields via operation text helpers.

Protocol behaviors already enforced:

| Event | Behavior |
|---|---|
| Start | Reject if busy (`BUSY`) |
| Progress | Throttled emit + scheduled last flush |
| Success / fail / cancel | Terminal emit once (`_terminal_emitted`) |
| HTTP→browser remedy | Capture window owned by TaskBridge; stale callbacks gated |
| Cancel | Worker cancel event; must not overwrite a completed success |

Workers:

- `ArchiveWorker` — GitHub archive (QThread).
- `WebArchiveWorker` — HTTP web archive (QThread).
- Browser path runs on GUI thread via `WebCaptureWindow` (Qt WebEngine constraint).

## 4. Storage

| Concern | Mechanism |
|---|---|
| Metadata | SQLite (`db/connection`, migrations under `migrations/`) |
| GitHub files | `archive/paths.PathLayout` — Layout V2 under `archive_root/github/...`; V1 compatibility via `storage.repository_dir_for` / migrate tool |
| Web articles | `web_archive/paths.WebPathLayout` — article dirs, media, MHTML, versions |
| Secrets | `security/token_store` (keyring) — never logged (`redact`) |
| Profiles | `web_archive/browser_profile` — OTR anonymous vs persisted login dir |

**Publish rule:** write to staging (`.part` or tmp) → validate → `fsutil.atomic_replace` → update DB success pointers in service layer. Failures must leave prior COMPLETE content readable.

## 5. Web adapter architecture

```
parse_web_url (identity / kind / site_key)
    → HTTP fetch OR browser DOM
    → extract_article
         → try_adapters([ZhihuAdapter, XiaoheiheAdapter])
         → else readability-lxml
    → sanitize_article_html
    → localize media (web_archive.media)
    → DB row + files
    → optional MHTML attach (mhtml.py + MhtmlSaveController)
```

**Site adapters own:** URL support, site-specific DOM/JSON extract, readiness hints.  
**Engine owns:** fetch, browser capture, sanitize, media, publish, DB, tasks.

### Extension drill (no product change)

To add a fictional `example.com` adapter:

1. `web_archive/urls.py` — recognize host/path → `site_key`, `kind`, canonical URL.
2. `web_archive/adapters/example.py` — `supports` + `extract` → `AdapterResult`.
3. `web_archive/adapters/__init__.py` export.
4. `web_archive/extract.py` — append to `_ADAPTERS`.
5. Tests under `tests/test_web_archive_*.py` with HTML fixtures.
6. **Not required:** TaskBridge, DesktopWindow, React shells, downloader, MHTML core (unless new capture readiness JS).

Adapters must remain GUI-free (pure HTML in → `AdapterResult` out). Services are already unit-tested without windows (`WebArchiveService`, documents, library).

## 6. Chromium lifecycle (protected invariants)

- Anonymous capture: off-the-record profile.
- Login capture: dedicated persistent profile directory.
- Do not touch Qt GUI objects from worker threads.
- Stale `runJavaScript` callbacks must check window/page liveness.
- MHTML waits for explicit save outcome (`MhtmlSaveController`).
- Capture teardown uses real `deleteLater` / `prepare_teardown` (P2.6); no Chromium process kill.

## 7. Frontend state

| Workspace | Authority |
|---|---|
| `App.tsx` | Bridge connect, TaskBar state, close guard, nav |
| `GitHubWorkspace` | Repo list/detail; also listens `taskChanged` to refresh |
| `KnowledgeWorkspace` | Documents + DirtyGuard locally |
| `WebArchiveWorkspace` | Article list/detail; starts tasks via `tasks` / `webArchive` |
| `SettingsWorkspace` | Token + archive root |

Demo fixtures (`features/github/demoData.ts`) only when WebChannel absent.

## 8. Over-abstraction check

Current abstractions that **earn their keep:** PathLayout/WebPathLayout, SiteAdapter protocol, Bridge JSON envelope, TaskBridge throttle, fsutil.atomic_replace, redact/safe_host.

Rejected this round: unified ArchiveService, mega-Workspace React component, Slot codegen, merging offline vs web media stacks.
