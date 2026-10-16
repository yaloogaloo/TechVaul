# TechVault P2.1 — 「保存网页」一键归档闭环修复

日期：2026-10-15  
案例 URL：`https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493`

## 1. 用户点击「保存网页」失败的真正代码根因

P2 已实现 Chromium DOM 捕获，但主流程未接入：

| 入口 | 调用链 | 结果 |
|------|--------|------|
| **保存网页** | `WebArchiveWorkspace` → `TaskBridge.startWebArchive` → `WebArchiveWorker` → `archive_url`（仅 httpx） | 知乎 403/zse → `ACCESS_RESTRICTED` → 任务结束失败 |
| **在浏览器中打开** | `WebArchiveBridge.openBrowserCapture` → `WebCaptureWindow` → `publish_from_rendered_html` | 可成功，但需用户二次操作 |

`CaptureResult.suggest_browser=True` 仅驱动前端提示「可尝试在浏览器中打开」，**不会**自动打开 WebEngine。  
因此用户主按钮永远停在 HTTP 失败，左侧列表为空。  
开发样例 `docs/web_archive/live_case/` 与正式应用 SQLite / `archive_root` 完全独立，不能替代正式闭环证据。

## 2. HTTP → Chromium 补救流程（同一逻辑任务）

```text
startWebArchive(url)
  ├─ phase FETCHING_HTTP          WebArchiveWorker / archive_url
  ├─ HTTP 成功且正文合格           → PUBLISHING → COMPLETED（不启动浏览器）
  └─ HTTP 失败且 suggest_browser
        ├─ phase BROWSER_LOADING  打开 WebCaptureWindow(auto_capture=True)
        ├─ WAITING_FOR_CONTENT    轮询 DOM（非仅 loadFinished）
        ├─ CAPTURING_DOM          runJavaScript(DOM_EXTRACT_JS)
        ├─ LOCALIZING_MEDIA / PUBLISHING
        │     publish_from_rendered_html  （禁止 httpx 回源）
        └─ COMPLETED | PARTIAL | FAILED | CANCELLED
```

超时后不无限重试：提示用户在同一浏览器窗口点「保存当前页面」，任务保持 `active`。

## 3. 修改的实际文件

| 文件 | 变更 |
|------|------|
| `src/techvault/ui/bridge/task_bridge.py` | HTTP 完成后自动 `_begin_browser_fallback`；统一 phaseCode；合并失败文案 |
| `src/techvault/ui/web_capture_window.py` | `auto_capture` 轮询 DOM、自动保存、超时转人工确认 |
| `src/techvault/web_archive/dom_extract.py` | 共享 DOM 脚本与自动捕获就绪判定 |
| `src/techvault/web_archive/failure_codes.py` | ACCESS_RESTRICTED 文案（一键会自动补救） |
| `frontend/src/features/web/WebArchiveWorkspace.tsx` | 成功文案；失败后次级恢复提示 |
| `frontend/src/types/bridge.ts` | `phaseCode` / `httpFailureCode` / `browserFailureCode` |
| `tests/test_web_archive_one_click.py` | 任务生命周期与 SQLite 发布断言 |
| `tools/live_zhihu_one_click.py` | 正式 TaskBridge 一键 live 证据 |

## 4. 任务生命周期

`FETCHING_HTTP` → `BROWSER_LOADING` → `WAITING_FOR_CONTENT` → `CAPTURING_DOM` → `LOCALIZING_MEDIA` → `PUBLISHING` → `COMPLETED` / `PARTIAL` / `FAILED` / `CANCELLED`  
（另：`WAITING_FOR_USER` 超时等待人工保存）

取消：HTTP worker `cancel` 或浏览器 `cancel_capture` / 关闭窗口 → 同一任务 `CANCELLED`。

## 5. 数据库发布与列表刷新

- `publish_from_rendered_html` 写入配置的 `archive_root` + `web_articles` / `web_captures`
- `TaskBridge` 成功态携带 `articleId`
- React：`task.kind==='web' && running→success` → `loadList` + `setSelectedId(articleId)` + Toast

Live 结果（隔离库，非用户主库）：`article_id=1`，`list_count=1`，`sqlite_ok=true`。

## 6. 用户案例真实执行结果

命令：

```bash
.\.venv\Scripts\python.exe tools\live_zhihu_one_click.py
```

观测 phase：`FETCHING_HTTP` → `BROWSER_LOADING` → `CAPTURING_DOM` → `LOCALIZING_MEDIA` → `PUBLISHING` → `COMPLETED`

| 字段 | 值 |
|------|-----|
| pass_one_click | **true** |
| http_failure_code | ACCESS_RESTRICTED |
| capture_mode | BROWSER |
| article_id | 1 |
| title | 为什么有人会包养女大学生？ |
| article.html / .md / metadata | 存在 |
| plain_preview | 非空（约 1265 字） |

证据目录：`docs/web_archive/one_click_case/`

## 7. 截图

- `screenshots/one_click_workspace_1440x900.png` — 工作区（种子文章入列表）
- `screenshots/one_click_offline_960x720.png` — 本地 article.html 离线打开
- `screenshots/browser_capture_1280x800.png` — 浏览器捕获窗（P2）

## 8. 隔离归档目录树

```text
docs/web_archive/one_click_case/
  result.json
  offline_reopen.json
  archive/web/zhihu.com/为什么有人会包养女大学生？-9cd7a8fcf9/
    article.html
    article.md
    metadata.json
    snapshot.mhtml
```

## 9. 断网重启测试

`offline_reopen.json`：新鲜 SQLite 连接重读 → `reopen_plain_len=1265`，html/md 字节非零。  
正式产品路径：关闭应用 → 断网 → 重启 → 打开列表中该文（与 P1 离线阅读同一文件模型）。

## 10. 全量回归

见 `WEB_ARCHIVE_VALIDATION.md`（本轮更新后）。

## 验收

| 项目 | 状态 | 证据 |
|------|------|------|
| HTTP FALLBACK TO BROWSER | PASS | phase 链 + httpFailureCode |
| ONE CLICK CAPTURE | PASS | `tools/live_zhihu_one_click.py` |
| LIVE ZHIHU URL | PASS | result.json |
| SQLITE PUBLISH | PASS | article_id + list_count |
| REACT LIST REFRESH | PASS | articleId → 既有 useEffect；类型已扩展 |
| OFFLINE REOPEN | PASS | offline_reopen.json + 离线截图 |
| TASK LIFECYCLE | PASS | test_web_archive_one_click + live phases |
| EXISTING FEATURES | PASS | 全量 pytest |
| **OVERALL** | **PASS** | |
