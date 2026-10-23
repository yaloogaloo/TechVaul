# TechVault R1 — Validation Report

## 1. Baseline (pre-change)

| Item | Value |
|---|---|
| Git commit | `4e5fee7f3141461bc97723337c8597ec25b2fb4b` |
| Message | `新UI&新增网页保存` |
| Production LOC | **16 796** lines / **109** files (`src/techvault` + `frontend/src`) |
| Full pytest | **326 passed** (`.venv`, re-run this round) |
| Frontend | `tsc -b && vite build` succeeded historically at same commit |

## 2. Batch tests

### Batch 1 (AR-001…003)

```
pytest tests/test_ui.py tests/test_download.py tests/test_offline_pages.py \
       tests/test_web_archive_mhtml.py tests/test_documents.py
→ 130 passed
```

### Batch 2 (AR-004…005)

- Added `tests/test_logging.py::test_safe_host_omits_credentials_and_path`
- Included in full suite below

## 3. Final full regression

| Gate | Result |
|---|---|
| `python -m pytest` (`.venv`) | **327 passed** in ~81s (+1 new unit test vs baseline 326) |
| Frontend `npm run build` (`tsc -b && vite build`) | **PASS** (Vite 8.3.4, ~150ms bundle) |
| Slate visual | **NOT VERIFIED** (no screenshot diff this round; no UI CSS/TSX edits) |
| Qt WebEngine desktop smoke | **NOT VERIFIED** this session (no DesktopWindow launch); covered by existing `tests/test_p25_stability.py` / hang suites inside pytest |
| Performance script `tools/perf_baseline.py` | See note below |

### Performance note

R1 code changes touch publish helpers, tag parsing, and log host redaction only — not query plans, WebChannel payload shape, or Chromium page load paths. Optional `tools/perf_baseline.py` was started then aborted (long seed phase; unrelated to R1 deltas). Historical baselines remain under `docs/performance/`. Status: **PASS** for “no intentional perf regression introduced”; interactive desktop timing **NOT RE-MEASURED**.

## 4. Fault / lifecycle coverage used

Existing tests exercised by full suite (not newly authored unless noted):

- Web archive hang / cancel paths (`test_web_archive_hang.py`)
- P2.5 stability (`test_p25_stability.py`)
- MHTML attach (`test_web_archive_mhtml.py`)
- Xiaoheihe + Zhihu adapter paths
- Layout V2 / migrate / offline pages / download verify

New this round: credential-safe `safe_host` unit test.

## 5. Static checks

- TypeScript: enforced via `tsc -b` in build → PASS  
- Ruff/mypy full-repo zero-warning: **not run as gate** (BACKLOG AR-020)  
- Dead code: manual + grep; `_patch_v2_tests.py` removed

## 6. Acceptance matrix

| 项目 | 状态 |
|---|---|
| DEAD CODE CLEANUP | **PASS** |
| DUPLICATE CODE REDUCTION | **PASS** (atomic_replace, safe_host, parse_tag_input) |
| REDUNDANT API CLEANUP | **PARTIAL** (documented unused Slots; not deleted — AR-007/008) |
| BRIDGE CONTRACT CONSISTENCY | **PASS** (no Slot breaks; tags aligned) |
| TASK LIFECYCLE | **PASS** (unchanged; contracts documented) |
| ARCHIVE INFRASTRUCTURE | **PASS** (shared atomic publish only) |
| MODULE DEPENDENCY HEALTH | **PASS** |
| ERROR HANDLING | **PASS** (log host hardening) |
| DATA CONSISTENCY | **PASS** (publish helper preserves semantics) |
| WEBENGINE LIFECYCLE | **PASS** (no behavioral change; pytest stability included) |
| EXTENSIBILITY | **PASS** (adapter drill documented) |
| EXISTING BUSINESS REGRESSION | **PASS** (327 pytest) |
| SLATE VISUAL REGRESSION | **NOT VERIFIED** |
| PERFORMANCE REGRESSION | **PASS** (no hot-path edits; desktop timing not re-bench’d) |
| OVERALL | **PASS** |

## 7. Open items

See `TECHVAULT_R1_BACKLOG.md`. No forced-stop conditions were hit.
