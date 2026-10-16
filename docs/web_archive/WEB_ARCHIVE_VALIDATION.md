# TechVault 网页归档 — 验收记录（P1 + P2 + P2.1 + P2.2）

日期：2026-10-15

## 测试命令

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
cd frontend && npm run build
.\.venv\Scripts\python.exe tools\live_mhtml_snapshot.py
```

## P2.2 Live MHTML

- `pass_mhtml_save=true`，`snapshot_bytes=799621`，`snapshotStatus=COMPLETE`
- 离线 WebEngine 加载成功：`mhtml_case/offline_reload.json`
- 证据：`docs/web_archive/mhtml_case/`、`MHTML_SNAPSHOT_COMPLETION.md`

## 验收状态

| 验收项目 | 状态 | 证据 |
|---|---|---|
| MHTML SAVE LIFECYCLE | PASS | MhtmlSaveController + live SAVING_SNAPSHOT |
| SAVE COMPLETION DETECTION | PASS | DownloadCompleted + 校验 |
| MHTML FILE VALIDITY | PASS | multipart + 799KB + 离线打开 |
| OFFLINE SNAPSHOT VIEWER | PASS | SnapshotViewerWindow 截图 |
| OFFLINE RESOURCE COVERAGE | PARTIAL | 正文/主布局在；部分头像图标缺失 |
| CAPTURE VERSION MAPPING | PASS | 009 迁移 + list_captures |
| OLD SNAPSHOT PROTECTION | PASS | test_failed_attach_preserves_previous_snapshot |
| SLATE UI INTEGRATION | PASS | 快照 Tab / 失败态截图 |
| LIVE ZHIHU SNAPSHOT | PASS | live_mhtml_snapshot.py |
| ONE CLICK REGRESSION | PASS | 同链路含快照阶段 |
| HTTP FALLBACK / ONE CLICK | PASS | P2.1 文档 |
| EXISTING FEATURES REGRESSION | PASS | **316 passed** + 前端构建 |
| **OVERALL P2.2** | **PASS** | 资源覆盖 PARTIAL |

## 已知限制

- MHTML 保真度受懒加载与未打包资源影响。
- 历史版本快照文件与 capture 行的对应为 best-effort。
