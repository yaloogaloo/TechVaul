# TechVault P2.6 — DesktopWindow + 捕获窗并行稳定性

日期：2026-10-15  
环境：Windows 10，隔离 SQLite / archive_root，正式 `DesktopWindow` + TaskBridge  
工具：`tools/desktop_capture_stability.py`、`tools/desktop_capture_minimal.py`、`tools/desktop_capture_bisect.py`  
原始序列：`docs/performance/desktop_stability_series.json`

## 1. 真实调用链

```
QApplication
 └─ DesktopWindow (techvault-slate-shell Profile + WebChannel)
      └─ TaskBridge.startWebArchive
           ├─ HTTP (本地 SPA 壳 → INCOMPLETE)
           └─ WebCaptureWindow (OTR Profile, auto_capture)
                └─ finalize → prepare_teardown → close/deleteLater
      └─ 工作区切换 (GitHub / 知识 / 网页)
      └─ WebArchiveBridge.openSnapshotPreview
           └─ SnapshotViewerWindow (每实例唯一 Profile 名)
```

同一 QApplication / 同一 DesktopWindow，不重启、不重建主窗口掩盖泄漏。

## 2. 历史 native AV：证据与归因

| 阶段 | 现象 |
|------|------|
| P2.5 | 仅 TaskBridge+捕获 10/10；标注 Desktop 并发未测 |
| P2.6 初测 | Desktop+捕获快速连开 → **Windows fatal exception: access violation (`0xC0000005`)**，约 1–4 轮 |
| faulthandler | 主线程停在 `app.exec()`；HTTP 服务线程在 `select` |

### 二分结果

| 场景 | 结果 |
|------|------|
| Desktop + 10× 捕获（无 nav/快照） | **10/10 PASS** |
| Desktop + 捕获 + 工作区切换 | **10/10 PASS** |
| Desktop + 捕获 + MHTML 开关 | **10/10 PASS** |
| Desktop + 捕获 + nav + 快照 | **10/10 PASS**（bisect） |
| 完整稳定性脚本在 **terminal 回调里 processEvents** | **AV 复现** |

### 已确认因素（有复现对比）

1. **在捕获窗 teardown 同一拍内泵 Qt 事件循环**（测试里对 `taskChanged` 终端态立刻 `processEvents`）→ AV。正式 UI 不会在该 tick 里同步泵循环，但属于危险模式。
2. **匿名 `loadProgress` lambda 未断开** → teardown 后仍可能回调；已改为具名 slot 并在 `prepare_teardown` 断开。
3. **Slate 壳使用 `defaultProfile()`** 与快速创建/销毁的捕获 Profile 并存风险 → 壳改为独立 `techvault-slate-shell` Profile。
4. **新捕获窗在旧窗 `destroyed` 前创建** → TaskBridge 排队 `_pending_browser_http`，等旧窗销毁。
5. 快照窗固定 Profile 名 → 改为每实例唯一名。

不能仅凭时间相关宣称「OTR 单独导致」；P2.5 OTR 修复了无 Desktop 场景，Desktop AV 需上述组合条件。

## 3. 实际修复文件

| 文件 | 变更 |
|------|------|
| `desktop_window.py` | 独立 shell Profile / Page，不用 defaultProfile |
| `web_capture_window.py` | `prepare_teardown`；具名 `_on_load_progress`；teardown 断开信号；回调尊重 `_tearing_down` |
| `task_bridge.py` | 关闭前 disconnect；`prepare_teardown`；`_dying_capture` 门闩后再开新捕获 |
| `snapshot_viewer_window.py` | 唯一 Profile 名 |
| `tests/test_web_archive_hang.py` | ScriptedCaptureWindow 真正 `deleteLater`，避免假死等待 |

## 4. 10 轮正式组合测试（复测通过）

`tools/desktop_capture_stability.py`：

| 轮次 | elapsed_ms | status | settle chrome_procs |
|------|------------|--------|---------------------|
| 1–10 | ~2270–2430 | success | **1**（= shell 基线） |

- `terminal_ok=10`，`timeouts=0`，`native_av_observed=false`
- 基线 chrome_procs=**1**；settle 始终 **1**（`chromium_procs_delta=0`，`chromium_above_baseline_last=0`）
- 主进程 RSS settle：297 → 314 MB（**+16.7 MB** / 10 轮，非子进程累积）
- 每轮含：归档 → 等捕获销毁 → GitHub/知识/网页切换 → 打开/关闭 MHTML

## 5. 主线程响应

| 指标 | 值 |
|------|-----|
| loop `processEvents` p50 | **0.27 ms** |
| p95 | **34.5 ms** |
| max | **37.7 ms** |
| ≥500 ms 冻结 | **0** |
| 工作区切换 | 约 **0.1–5 ms**（JS click + 少量 processEvents） |

区分：

- **Python 主线程阻塞**：本测未观测到 ≥500 ms 泵循环延迟。
- **Chromium 渲染**：未单独插桩；捕获耗时 ~2.3 s 含导航+DOM+MHTML，属任务耗时非 UI 冻住。
- **React/WebChannel**：终端态正常；用户主观「整体卡顿」**NOT VERIFIED**。

## 6. 资源趋势

- 正式 Desktop 常驻时 Chromium **不必归零**；以 shell 基线（1）比较。
- 捕获期间 terminal 采样可见 chrome=2（壳+捕获）；settle 回到 1。
- RSS 缓升可解释为归档文件/SQLite/缓存，**非** Chromium 进程数上升。

## 7. 回归

- 全量 pytest：**326 passed**
- 前端：`npm run build` 成功（本轮会话已验证）

## 8. 验收矩阵

| 项目 | 状态 |
|------|------|
| DESKTOP + CAPTURE CONCURRENCY | **PASS** |
| NATIVE AV REGRESSION | **PASS**（复现→修复→10/10 无 AV） |
| PROFILE LIFECYCLE | **PASS**（shell / OTR capture / 唯一 snapshot） |
| MAIN WINDOW RESPONSIVENESS | **PASS**（测得无用户卡顿 **NOT VERIFIED**） |
| CHROMIUM RESOURCE TREND | **PASS**（settle 回到基线） |
| TASK EVENT CONSISTENCY | **PASS** |
| EXISTING FEATURES REGRESSION | **PASS**（326） |
| OVERALL | **PASS** |

## 9. 未关闭风险

- 用户环境特定卡顿（杀毒/磁盘/远程桌面）仍 **NOT VERIFIED**。
- 极端情况下若业务代码在 `taskChanged` 同步路径里重入泵循环并操作 WebEngine，仍可能危险；应保持「终端态后不重入销毁中的捕获窗」。
- 登录 Profile 仍共享持久目录；本轮压力仅覆盖匿名 auto_capture。
