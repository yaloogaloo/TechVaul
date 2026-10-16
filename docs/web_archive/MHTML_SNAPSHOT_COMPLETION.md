# TechVault P2.2 — MHTML 高保真网页快照正式化

日期：2026-10-15  
案例：`https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493`

## 1. 原有实现缺口

| 缺口 | 源码位置（修复前） | 问题 |
|------|-------------------|------|
| 无完成检测 | `web_capture_window._try_save_mhtml` | 仅调用 `page.save()`，不等待 DownloadCompleted |
| 过早关窗 | auto_capture 成功后立即 `close()` | 下载未完成窗口已销毁 |
| 文件存在即成功 | 无校验 | 未检查大小 / multipart / 哈希 |
| 无版本字段 | `008_web_archive.sql` | 无 snapshot_status / relative_snapshot |
| 覆盖风险 | 直接写 `article/snapshot.mhtml` | 失败可能留下残缺文件 |
| UI 占位 | `WebArchiveWorkspace` 快照 Tab | 文案「属于 P2」 |

## 2. 保存生命周期（修复后）

```text
DOM 发布阅读版（COMPLETE/PARTIAL）
  → SAVING_SNAPSHOT
       MhtmlSaveController
         profile.downloadRequested
         accept() 仅 isSavePageDownload
         state: Requested → InProgress → Completed | Interrupted | Cancelled
  → 校验 staging 文件（大小 + MHTML 特征 + sha256）
  → attach_mhtml_snapshot（.part → os.replace → 更新 SQLite）
  → VERIFYING → COMPLETED
  → 然后才关闭 WebCaptureWindow
```

正文已写入后，快照失败**不会**回滚阅读版；任务仍为 success，文案为「正文已成功归档，但网页快照未完成」。

## 3. Qt DownloadRequest 处理

- API：PySide6 6.11.2 `QWebEnginePage.save` + `QWebEngineDownloadRequest`
- 仅处理 `isSavePageDownload()`，避免误收普通下载
- 强制 `MimeHtmlSaveFormat`，目录指向 `_tmp/web/<job>/snapshot.mhtml`
- Interrupted → 保留 `interruptReasonString()`
- 超时 90s → cancel + FAILED

实现：`src/techvault/ui/mhtml_saver.py`

## 4. 版本绑定

迁移 `migrations/009_web_snapshot.sql` 为 `web_articles` / `web_captures` 增加：

- `snapshot_status`（COMPLETE / FAILED / NOT_CAPTURED）
- `relative_snapshot`、`snapshot_bytes`、`snapshot_sha256`
- `snapshot_captured_at`、`snapshot_failure`

重新归档时 `_stash_version` 复制旧 `snapshot.mhtml` 到 `versions/<stamp>/`。  
新快照失败时 **保留** 旧 COMPLETE 指针（`preserve_previous=True`）。

## 5. 离线预览

`SnapshotViewerWindow`：

- 独立 `QWebEngineProfile`，无 WebChannel
- `UrlRequestInterceptor` 拦截 http/https
- `acceptNavigationRequest` 禁止静默跳转知乎；链接点击走系统浏览器
- Bridge：`openSnapshotPreview` / `openSnapshotFile` / `listCaptures`

## 6. 未经允许的网络请求

快照预览默认拦截外部请求；状态栏显示「已拦截 N 个外部请求」。  
Live 案例：`blocked=0`（资源已打包进 multipart），页面仍显示正文与主要布局。

## 7. 资源完整性判断

成功条件同时满足：

1. DownloadState = Completed  
2. 文件存在且 ≥ 256 字节  
3. 内容含 multipart / MIME / From:Saved 等特征  
4. 独立 WebEngine 离线 `loadFinished(ok)`  
5. 人工/截图确认标题与正文可见  

不把「文件后缀存在」或「save() 已调用」当作 PASS。

## 8. 失败时保护旧归档

- 先写 `snapshot.mhtml.part`，校验后再 `os.replace`
- 校验失败删除 part，不覆盖正式文件
- 若文章已有 COMPLETE 快照，失败只写 `snapshot_failure`，不把 status 改为 FAILED（指针保留）

## 9. 实际修改文件

| 文件 | 作用 |
|------|------|
| `migrations/009_web_snapshot.sql` | 快照元数据 |
| `src/techvault/web_archive/mhtml.py` | 校验 + 原子发布 |
| `src/techvault/ui/mhtml_saver.py` | DownloadRequest 生命周期 |
| `src/techvault/ui/web_capture_window.py` | 发布后等待快照再结束 |
| `src/techvault/ui/snapshot_viewer_window.py` | 离线查看器 |
| `src/techvault/ui/bridge/web_archive_bridge.py` | 预览 / 打开 / 版本列表 |
| `src/techvault/ui/bridge/task_bridge.py` | snapshotStatus 任务态 |
| `src/techvault/web_archive/service.py` | detail / list_captures |
| `frontend/.../WebArchiveWorkspace.tsx` | 快照 Tab |
| `tests/test_web_archive_mhtml.py` | 专项测试 |
| `tools/live_mhtml_snapshot.py` | Live 证据 |

## 10. Live 结果

```bash
.\.venv\Scripts\python.exe tools\live_mhtml_snapshot.py
```

| 项 | 值 |
|----|-----|
| HTTP | ACCESS_RESTRICTED → 浏览器补救 |
| 阅读版 | COMPLETE |
| 快照 | COMPLETE，799 621 字节 |
| 离线加载 | load_ok=true |
| 证据 | `docs/web_archive/mhtml_case/` |

截图：

- `screenshots/mhtml_offline_viewer_1280x800.png` — 断网快照窗口（可见知乎排版与正文）
- `screenshots/mhtml_slate_snapshot_tab_1440x900.png` — Slate「网页快照」Tab
- `screenshots/mhtml_slate_reading_1440x900.png` — 阅读正文 Tab

## 11. 仍未解决的保真限制

- 懒加载且未进入视口的图片可能缺失（头像等显示占位）
- 动态评论 / 未展开回答不会进入快照
- 部分 CSS/图标依赖未打包资源时显示不完整
- **不是**像素级还原；是「尽可能接近保存时刻的页面」
- 历史版本与 `versions/*/snapshot.mhtml` 的映射为 best-effort（按时间戳顺序）

## 验收

| 项目 | 状态 |
|------|------|
| MHTML SAVE LIFECYCLE | PASS |
| SAVE COMPLETION DETECTION | PASS |
| MHTML FILE VALIDITY | PASS |
| OFFLINE SNAPSHOT VIEWER | PASS |
| OFFLINE RESOURCE COVERAGE | PARTIAL（正文+主布局在；部分头像/图标缺失） |
| CAPTURE VERSION MAPPING | PASS |
| OLD SNAPSHOT PROTECTION | PASS（单元测试） |
| SLATE UI INTEGRATION | PASS |
| LIVE ZHIHU SNAPSHOT | PASS |
| ONE CLICK REGRESSION | PASS（同链路含 SAVING_SNAPSHOT） |
| EXISTING FEATURES REGRESSION | 见 VALIDATION 全量结果 |
| **OVERALL P2.2** | **PASS**（资源覆盖标注 PARTIAL） |
