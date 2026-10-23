# TechVault R1 — Refactor Record

## Batch 1 — Dead code & low-risk duplication (AR-001 … AR-003)

**Why:** Evidence-complete, no business-behavior change beyond tag casefold dedupe (aligned with existing `parse_tag_input` contract).

### Changes

| AR | Action | Files |
|---|---|---|
| AR-001 | Deleted obsolete one-shot test patcher | `tools/_patch_v2_tests.py` (−68 lines) |
| AR-002 | Added `atomic_replace`; callers use shared publish | `src/techvault/fsutil.py` (new); `archive/downloader.py`; `offline/pages.py` (2 sites); `web_archive/mhtml.py`; `web_archive/media.py` |
| AR-003 | Knowledge bridge uses `parse_tag_input` | `ui/bridge/knowledge_bridge.py` |

### Old → new responsibilities

- **Before:** Each publisher duplicated `final.parent.mkdir` + `os.replace(part, final)`.
- **After:** `techvault.fsutil.atomic_replace` owns mkdir+replace; callers still own staging write + fsync + validation.
- **Tags:** Bridge no longer maintains a second split implementation; dedupe matches `tests/test_ui.py::test_parse_tag_input_dedupes`.

### Design trade-offs

- Did **not** merge `offline.images.media_filename` with `web_archive.media.media_filename` (different sanitization / extension rules — AR-009).
- Did **not** remove `startRearchive(int)` stub (AR-006) — documents the URL-only rearchive policy.

### Validation

- Targeted: `test_ui`, `test_download`, `test_offline_pages`, `test_web_archive_mhtml`, `test_documents` → **130 passed**.

### Net diff (Batch 1+2 measured)

`git diff --stat` (src/tools/tests): **≈ −63 net lines** (39 insertions / 102 deletions) plus new `fsutil.py` (~15 lines).

---

## Batch 2 — Log host helper + credential hygiene (AR-004, AR-005)

**Why:** Identical helpers in TaskBridge and WebCaptureWindow; `netloc` could put URL userinfo into logs.

### Changes

| AR | Action | Files |
|---|---|---|
| AR-004 | Single `redact.safe_host` | `redact.py`; `task_bridge.py`; `web_capture_window.py` |
| AR-005 | Prefer `hostname` (+ port); strip userinfo | `redact.py`; `tests/test_logging.py` |

### Public interfaces

- No Bridge Slot signature changes.
- Logging host format may drop `user:pass@` prefix (intentional hardening).

### Validation

- Unit: `test_safe_host_omits_credentials_and_path`.
- Full suite: see VALIDATION.md.

---

## Batches 3–5 — Deliberately limited

| Area | Decision | Reason |
|---|---|---|
| Task lifecycle (AR-010/014) | No structural split of TaskBridge | High AV/teardown risk; P2.5/P2.6 already stabilized throttle + terminal flush + capture `deleteLater` |
| Dual `taskChanged` (AR-011) | Document only | App TaskBar + GitHub list refresh both valid; unifying needs careful UX |
| Unused Slots (AR-007/008) | Keep | Library/tests/ops value; removing WebChannel surface mid-stability is low reward |
| Archive infra | Stopped after `atomic_replace` | Further abstraction rejected (no god ArchiveService) |
| Fault injection | Rely on existing hang/stability/mhtml tests | New scenarios listed in BACKLOG if gaps remain |

---

## Quantitative before / after

| 维度 | 优化前 (4e5fee7) | 优化后 (本轮工作树) |
|---|---|---|
| 生产 Python/TS 行数 | ~16 796 / 109 files | **16 791** / 109 files (−5; includes new `fsutil.py`) |
| 重复 atomic publish | 4 copy sites | 1 helper + 4 call sites |
| 重复 safe_host | 2 identical defs | 1 (`redact.safe_host`) |
| 无效工具脚本 | `_patch_v2_tests.py` present | deleted |
| parse_tag_input 生产引用 | 0 | knowledge_bridge (2 call sites) |
| Bridge Slot 数量 | 58 `@Slot` | 58 (no API removal) |
| TaskBridge 行数 | 712 | **705** |
| WebCaptureWindow 行数 | 816 | **808** |
| 全量 pytest | 326 passed | **327 passed** (+1 `safe_host` test) |
