# TechVault R1 — Deferred Backlog

Items confirmed in audit but **not** executed this round. Each entry states why and the trigger to reopen.

| ID | Item | Why deferred | Reopen when |
|---|---|---|---|
| AR-007 | Remove or wire `GitHubBridge.setRepositoryCategory` | Slate already manages categories via create/rename/delete; assigning repo category may be incomplete UX, not proven dead library path (`library.set_repository_category` used in tests) | Product wants category assignment in GitHub detail UI, or Slot confirmed unused after one release |
| AR-008 | Wire `listSites` / `getSessionStatus` / `clearZhihuSession` in Settings | Capture window already clears session locally; React Settings has no session panel yet | Settings UX for login profile status is scheduled |
| AR-010 | Split `TaskBridge` / `WebCaptureWindow` | High native AV / lifecycle risk; recent P2.5–P2.6 fixes concentrate here | Sustained bug density in this file **or** need for second concurrent task kind |
| AR-011 | Unify `taskChanged` subscribers; Web workspace list auto-refresh | Dual subscribe in App+GitHub is intentional; changing Web refresh needs UX agreement | User reports stale web list after archive without navigation |
| AR-016 | Extract shared media filename helper | Boundaries differ (Windows sanitization, extensions) | A third archive media publisher appears with identical rules |
| AR-017 | Formal TS Bridge client types per Slot | `QtObject` is `any`-ish; hand types cover payloads only | Recurring Slot/payload drift bugs |
| AR-018 | Additional fault-injection tests (SQLite mid-publish fail, late JS callback after destroy, MHTML disk full) | Existing hang/stability/mhtml coverage; avoid inventing brittle Qt tests without failing prod signal | First production incident matching gap |
| AR-019 | Remove `startRearchiveUrl` alias (calls `startArchive`) | Harmless clarity alias used by frontend | Frontend migrated to `startArchive` only |
| AR-020 | Ruff/mypy zero-warning campaign | Out of scope; static noise ≠ confirmed debt | Dedicated quality sprint |

## Forced stop reminders

Do not pull backlog items that require:

- Irreversible DB migrations without backup story
- Changing COMPLETE pointer semantics
- Broad TaskBridge rewrites without AV harness
- Merging Zhihu/Xiaoheihe extractors
