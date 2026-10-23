# TechVault R1 — White-box Architecture Audit

**Baseline commit:** `4e5fee7` (`新UI&新增网页保存`)  
**Audit date:** 2026-10-16  
**Production LOC (pre-Batch1):** Python+TS ≈ **16 796** lines / **109** files (`src/techvault` + `frontend/src`)  
**Baseline tests:** **326 passed** (full pytest via `.venv`, re-executed this round — not taken from historical reports)

## 1. Production module inventory

| Layer | Path | Role | Approx LOC |
|---|---|---|---|
| Entry | `techvault.__main__` / `ui.app:main` | Desktop launch | small |
| App services | `app/` | Bootstrap, settings, archive orchestration, library, recovery | ~1226 |
| GitHub archive | `archive/` | Paths V1/V2, downloader, release/source snapshot, migrate | ~2637 |
| GitHub API | `github/` | Client, URLs, releases, pagination | ~852 |
| Offline pages | `offline/` | GitHub README/release HTML sanitize + image localize | ~1012 |
| Documents | `documents/` | Knowledge CRUD | ~753 |
| DB | `db/` | SQLite connect, migrate, release_store | ~427 |
| Web archive | `web_archive/` | Adapters, fetch, capture, media, MHTML, service | ~2914 |
| UI / Bridge | `ui/` | DesktopWindow, TaskBridge, workers, capture window | ~3481 |
| Frontend | `frontend/src/` | Slate React workspaces + WebChannel client | ~3283 |

**Non-production (do not treat as dead app code):** `tests/`, `tools/`, `docs/`, `archive/` user data tree, `.pytest_cache/`, diagnostic HTML dumps.

### Formal call chains (verified)

1. **Slate boot:** `ui.app.main` → `DesktopWindow` → QWebChannel registers `application|github|knowledge|settings|tasks|webArchive` → React `connectBridges()`.
2. **GitHub archive:** React `tasks.startArchive` → `TaskBridge` → `ArchiveWorker` → `app.archive_service` → downloader / source_snapshot / offline pages → SQLite + filesystem.
3. **Web archive (HTTP):** `tasks.startWebArchive` → `WebArchiveWorker` → `WebArchiveService.archive` → `capture.archive_url` → adapters / readability → media → DB.
4. **Web archive (browser):** `webArchive.openBrowserCapture` or TaskBridge HTTP→browser remedy → `WebCaptureWindow` (OTR or login profile) → DOM extract → `publish_from_rendered_html` → optional `MhtmlSaveController`.
5. **Knowledge:** `knowledge.*` slots → `DocumentService` → SQLite only (no archive_root files).

### Adapter registration (extensibility hot spot)

- Registry: `web_archive/extract.py` `_ADAPTERS = [ZhihuAdapter(), XiaoheiheAdapter()]`
- Dispatch: `adapters.base.try_adapters`
- URL identity: `web_archive/urls.parse_web_url`
- **Adding a site today:** new adapter module + `_ADAPTERS` entry + URL kind rules + tests. Core HTTP/browser/media/MHTML/DB stay untouched if adapter implements `SiteAdapter`.

---

## 2. Findings table

| ID | 类型 | 位置 | 证据 | 影响 | 整改方式 | 风险 | 验证 | 状态 |
|---|---|---|---|---|---|---|---|---|
| AR-001 | 死代码 | `tools/_patch_v2_tests.py` | One-shot string rewriter for V2 path tests; not imported by app, CLI, or pytest collection | Dev clutter; risk of accidental re-run mutating tests | 删除 | 低 | Full pytest | **FIXED** (Batch 1) |
| AR-002 | 重复 | `offline/pages.py` L254–255, L332–333; `archive/downloader.py`; `web_archive/mhtml.py`; `web_archive/media.py` — identical `mkdir` + `os.replace` publish | Four publishers share same atomic publish pattern | Drift in publish semantics | 提取 `fsutil.atomic_replace` | 低 | download / offline / mhtml tests | **FIXED** (Batch 1) |
| AR-003 | 重复 | `ui/tags.parse_tag_input` vs `knowledge_bridge` inline `split(",")` (list+save) | `parse_tag_input` only used in `tests/test_ui.py`; bridge duplicated weaker split without casefold dedupe | Tag filter/save inconsistency | Bridge 调用 `parse_tag_input` | 低 | `test_ui` / documents | **FIXED** (Batch 1) |
| AR-004 | 重复 | `task_bridge._safe_host` ↔ `web_capture_window._safe_host` (identical) | Both truncated `urlparse(...).netloc` for logs | Duplicate + netloc leaked userinfo | 合并到 `redact.safe_host`（改用 hostname） | 低 | `test_logging` + web/capture smoke | **FIXED** (Batch 2) |
| AR-005 | 健壮性 | `_safe_host` used `netloc` | `https://User:Pass@host/` logged userinfo | Credential fragments in logs | hostname-based `safe_host` | 低 | unit test | **FIXED** (Batch 2) |
| AR-006 | 死代码/冗余 API | `TaskBridge.startRearchive(int)` | Always returns `USE_URL`; frontend only calls `startRearchiveUrl` | Harmless stub | **保留** as explicit contract (redirect to URL flow) | — | Slot still reachable via WebChannel | **NOT APPLICABLE** |
| AR-007 | 冗余 API | `GitHubBridge.setRepositoryCategory` | No frontend / test callers; library method exists | Unused public Slot | 保留至 Settings/分类 UX 明确；列入 backlog | 中 | — | **CONFIRMED** (not deleted) |
| AR-008 | 冗余 API | `WebArchiveBridge.listSites`, `clearZhihuSession` | No Slate callers; `getSessionStatus` also unused in UI | Dead UI surface, useful for ops/debug | 保留 Slot；UI 接线列入 backlog | 中 | — | **CONFIRMED** |
| AR-009 | 重复（表面） | `offline/images.media_filename` vs `web_archive/media.media_filename` | Same SHA16 prefix idea; web version adds Windows sanitization + ext rules | **不同边界** | **保留两套** — 禁止合并 | — | — | **NOT APPLICABLE** |
| AR-010 | 坏味道 | `ui/bridge/task_bridge.py` ~712 LOC; `web_capture_window.py` ~816 LOC | Large orchestration classes owning throttle, browser remedy, MHTML, teardown | Maintainability | 文档化契约；**本轮不拆**（避免 AV 回归） | 高 | P2.5/P2.6 lifecycle tests | **CONFIRMED** / deferred |
| AR-011 | 耦合 | `App.tsx` + `GitHubWorkspace.tsx` both `taskChanged.connect` | Dual subscribers; App drives TaskBar, GitHub refreshes list | Acceptable; Web workspace does not subscribe (may miss list refresh until navigation) | 评估统一 task bus（backlog） | 中 | Manual workspace switch | **CONFIRMED** |
| AR-012 | 坏味道 | Frontend `demoData.ts` used when bridges null | Intentional Vite browser preview | Not dead | 保留 | — | — | **NOT APPLICABLE** |
| AR-013 | 扩展性 | Site adapters registered only in `extract._ADAPTERS` | Zhihu + Xiaoheihe prove pattern; URL kinds in `urls.py` | New site needs 2–4 files | Document checklist; no third site this round | 低 | Code drill in ARCHITECTURE.md | **PASS** (documented) |
| AR-014 | 性能/健壮性 | TaskBridge progress throttle + terminal flush | Existing P2 design | Must not regress | 保留；回归测试 | 高 | task/web tests | **PASS** (unchanged) |
| AR-015 | 数据一致性 | Atomic publish + DB pointer patterns across GitHub/web | Staging `.part` then replace; failure must not clobber success pointer | Core archive integrity | Batch 1 shared helper only; no Schema change | 高 | archive/web tests | **PASS** (helper only) |

---

## 3. Explicit non-goals / retained complexity

- Do **not** merge Zhihu and Xiaoheihe DOM/login logic.
- Do **not** merge GitHub offline image stack with web article media stack.
- Do **not** create a god `ArchiveService` with `if kind == ...`.
- Do **not** rewrite Slate UI or replace Qt WebEngine.
- Do **not** delete SQLite migrations or V1 path compatibility (`archive/storage`, migrate_layout_v2).
- Chromium lifecycle fixes from P2.5/P2.6 stay; no process-kill “cleanup”.

## 4. Priority for implementation batches

1. **Batch 1:** AR-001, AR-002, AR-003  
2. **Batch 2:** AR-004, AR-005 (+ Bridge unused API documented)  
3. **Batch 3–5:** AR-010/011/014 observation-only unless regression found  
4. **Backlog:** AR-007, AR-008, AR-011 deeper unify
