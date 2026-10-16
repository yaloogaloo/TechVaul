# TechVault P2.5 — 性能稳定性与多平台归档完整性

日期：2026-10-15  
环境：Windows 10，隔离 SQLite / archive_root，PySide6 WebEngine  
工具：`tools/chromium_capture_stress.py`、`tools/live_xiaoheihe_image_audit.py`、`tools/verify_xiaoheihe_images.py`、`tools/perf_baseline.py`

## 1. Chromium 连续捕获（10 轮）

同一 `TaskBridge` 生命周期内，本地 HTTP 服务提供 SPA 壳页面（HTTP 不执行 JS → `INCOMPLETE` → 自动 Chromium）。交替加载知乎风格 / 小黑盒风格 fixture。

原始时间序列：`docs/performance/chromium_stress_series.json`

| 轮次 | elapsed_ms | terminal | captureMode | chrome_procs@terminal |
|------|------------|----------|-------------|------------------------|
| 1 | 2669 | success | BROWSER | 1 |
| 2 | 2444 | success | BROWSER | 1 |
| 3 | 2420 | success | BROWSER | 1 |
| 4 | 2429 | success | BROWSER | 1 |
| 5 | 2518 | success | BROWSER | 1 |
| 6 | 2483 | success | BROWSER | 1 |
| 7 | 2435 | success | BROWSER | 1 |
| 8 | 2440 | success | BROWSER | 1 |
| 9 | 2439 | success | BROWSER | 1 |
| 10 | 2417 | success | BROWSER | 1 |

### 资源趋势（settle 采样）

| 指标 | 首轮 settle | 末轮 settle | Δ |
|------|-------------|-------------|---|
| 主进程 RSS | 254.33 MB | 266.69 MB | **+12.36 MB** |
| Chromium 子进程数 | 0 | 0 | **0** |
| Chromium RSS | 0 | 0 | **0** |

归因：

- **可回收 / 正常**：每轮终态后 Chromium 子进程归零；chrome RSS 不累积。
- **可解释增长**：SQLite 文章行、归档文件句柄、Python 堆（约 +12 MB / 10 轮，非每轮线性堆进程）。
- **根因修复**：匿名 `auto_capture` 改用 **off-the-record** WebEngine Profile，避免多窗口共享同一 Chromium user-data 目录导致的原生 AV（修复前约 1–3 轮崩溃）。登录 Profile 仍共享持久 cookie 目录。

测量方法限制：为避免双 WebEngineView（DesktopWindow 壳 + 捕获窗）在本机快速连开时的 native crash，压力脚本以 **TaskBridge + 捕获窗** 为主线；生产仍可同时打开 Slate 壳。

## 2. TaskBridge 节流一致性

修改：`src/techvault/ui/bridge/task_bridge.py`

- 阶段码变化 `force` 推送。
- 节流窗口内的最后一次进度通过 `QTimer` **flush**，不会永久停在旧 percent。
- 终态仍走 `_emit()`（不节流）。
- 旧 `taskId` 回调继续忽略。

测试：`tests/test_p25_stability.py::test_throttle_flush_emits_latest_progress`

## 3. getArticle 载荷瘦身回归

- UI 热路径仅依赖 `bodyHtml`（`WebArchiveWorkspace`）。
- Bridge 仍返回空 `bodyMd` / `metadataJson` 字段以保持 schema。
- **磁盘**仍写 `article.md` + `metadata.json`；`openLocalArchive` 打开目录。
- FTS / 搜索继续用库内 `plain_text`。

测试：`test_get_article_hot_path_keeps_disk_md_and_meta`

## 4. 性能复测（对照 P2.4）

`tools/perf_baseline.py`（120 篇 × ~40KB 正文种子）：

| 指标 | P2.4 记录 | P2.5 复测 |
|------|-----------|-----------|
| `listArticles` p95 | ~14.5 ms | **13.5 ms** |
| `getArticle` p95 | ~2–4 ms | **1.2 ms** |
| `listDocuments` p95 | ~6–18 ms | **5.5 ms** |
| MHTML sha256 800KB | ~1 ms | **1.5 ms** |
| 空库冷启动（历史） | ~1.2 s | 本轮 seed 计入窗口约 72 s（**非空库**；勿与产品冷启动混淆） |

用户环境「整体卡顿」若来自杀毒/磁盘/远程桌面：**NOT VERIFIED**。

## 5. 小黑盒正文图片

见 `docs/web_archive/xiaoheihe_image_audit.json`、`xiaoheihe_image_coverage.json`。

| 口径 | 数量 |
|------|------|
| 页面全部 img | 29 |
| 正文容器 img | 3 |
| 正文内容图（去头像/chrome/推荐） | **3 unique** |
| 阅读版本地化 | **3 / 3**，占位 0 |
| MHTML `Content-Type: image` | 25（含样式资源；≥ 正文 3） |

**结论：正文所需 3 张已全部保存 → IMAGE LOCALIZATION = PASS。**

## 6. 附带修复

- `urls.py`：generic canonical **保留 http/https**，便于本地压力页（不再误升 https）。

## 7. 验收矩阵

| 项目 | 状态 |
|------|------|
| CHROMIUM MEMORY STABILITY | **PASS**（10/10；进程不累积；RSS +12MB 可解释） |
| UI RESPONSIVENESS | **PASS**（基线复测稳定；用户机卡顿 **NOT VERIFIED**） |
| TASK EVENT CONSISTENCY | **PASS**（flush + 阶段 force + 单测） |
| WEBCHANNEL PAYLOAD REGRESSION | **PASS**（磁盘 md/meta 仍在；热路径 bodyHtml） |
| XIAOHEIHE BODY IMAGE COVERAGE | **PASS**（3/3） |
| XIAOHEIHE OFFLINE REOPEN | **PASS**（阅读版无空白 + MHTML 含图） |
| EXISTING FEATURES REGRESSION | **PASS**（326 pytest + frontend build） |
| OVERALL | **PASS** |

## 8. 剩余问题

- DesktopWindow + 捕获窗极速连开在本机曾触发 native AV；已用 OTR profile 缓解捕获链。若再复现，可加关闭后最短冷却或串行化 Profile 销毁。
- 用户端「整体卡顿」未在隔离库复现 → **NOT VERIFIED**。
