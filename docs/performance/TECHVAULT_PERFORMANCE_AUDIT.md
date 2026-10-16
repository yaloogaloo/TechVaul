# TechVault 性能审计（P2.4 阶段 A）

日期：2026-10-15  
环境：Windows 10，隔离 SQLite / archive_root，PySide6 + 内置前端 dist  
工具：`tools/perf_baseline.py`、冷启动探针

## 1. 基线摘要

| 场景 | 结果 | 说明 |
|------|------|------|
| 空库冷启动 → loadFinished | **~1.2 s** | `prepare_context` ~100 ms + WebEngine 壳 |
| 含 120 篇大文种子后启动 | ~44 s | **种子写入计入测量**，非日常冷启动 |
| `listArticles`（120 条） | p95 **~14.5 ms** | 可接受 |
| `getArticle`（含正文） | p95 ~2–4 ms 读盘；**载荷 ~370 KB** | WebChannel 传输偏大 |
| `listDocuments` / `getDocument` | p95 ~6–18 ms | 可接受 |
| MHTML sha256（800 KB） | ~1 ms | 非主瓶颈 |
| `taskChanged` 突发 200 次 | 14 ms / 200 emits | 若叠加全量刷新则卡顿 |

原始数据：`docs/performance/baseline_raw.json`

## 2. 已确认瓶颈

### B1. GitHub 工作区：任务进度 → 全量刷新（高）

**位置：** `frontend/src/features/github/GitHubWorkspace.tsx`  
**行为：** `taskChanged` 每次回调执行 `refreshList()` + `refreshDetail()`。  
GitHub 下载进度事件极频繁，导致重复 SQLite + README HTML 读取 + React 重渲染。  
**证据：** 代码路径明确；进度事件频率与库刷新耦合。

### B2. WebChannel 文章详情载荷过大（中）

**位置：** `WebArchiveService.get_article` + `_detail_json`  
**行为：** 一次返回 `bodyHtml` + `bodyMd` + `plainText` + `metadataJson`。  
UI 仅使用 `bodyHtml`。样本载荷 **~370 KB**。  
切换文章时主线程 JSON 解析与 React 和解会放大卡顿感。

### B3. task 信号未断开 / 重复订阅（中）

**位置：** `App.tsx`、`GitHubWorkspace.tsx`  
**行为：** `connect` 无对称 `disconnect`；依赖变化可叠加回调。

### B4. 进度信号无节流（中）

**位置：** `TaskBridge._on_detail` / `_on_progress` / `_on_browser_phase`  
高频 `json.dumps` + Signal → React `setState`。

## 3. 未确认为主因 / NOT VERIFIED

- 用户机器「整体卡顿」若来自杀毒/磁盘/远程桌面：本机未复现为持续冻结。
- Chromium 进程泄漏：当前有 `WA_DeleteOnClose` + `deleteLater`；需连续捕获压力测验证（见 FIX）。
- SQLite FTS：列表查询 p95 < 15 ms，非主瓶颈。

## 4. 测量方法说明

- 隔离临时目录，不碰用户真实库。
- 不读取用户私有知识正文；性能测试文档为合成样本文本。
- 冷启动区分「空库」与「含种子」；不得把种子时间误报为产品启动时间。
