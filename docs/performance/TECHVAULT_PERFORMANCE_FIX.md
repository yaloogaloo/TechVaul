# TechVault 性能修复（P2.4 阶段 A）

日期：2026-10-15

## 1. 修复对照

| 瓶颈 | 修改 | 前 | 后 |
|------|------|----|----|
| GitHub 进度触发全库刷新 | `GitHubWorkspace` 仅在任务终止时 refresh；`disconnect` | 每次 tick 打 list+detail | 终止态刷新一次 |
| Web 文章详情过大 | `get_article` 热路径只读 `article.html`；Bridge 省略 md/plain/meta | ~370 KB JSON | **~55 KB**（同文样本） |
| task 信号泄漏 | `App.tsx` / GitHub `disconnect` | 重复订阅 | 卸载清理 |
| 进度洪水 | `TaskBridge._emit_throttled`（~8–10 Hz，阶段切换强制） | 200 emit / 突发 | 节流后远少于输入次数 |
| 快照窗口残留 | `SnapshotViewerWindow` `WA_DeleteOnClose` | 依赖手动 GC | 关闭即销毁 |

## 2. 代码位置

- `frontend/src/features/github/GitHubWorkspace.tsx`
- `frontend/src/app/App.tsx`
- `src/techvault/ui/bridge/task_bridge.py`（`_emit_throttled`）
- `src/techvault/web_archive/service.py`（热路径读盘）
- `src/techvault/ui/bridge/web_archive_bridge.py`（瘦身 JSON）
- `src/techvault/ui/snapshot_viewer_window.py`

## 3. 复测

```bash
.\.venv\Scripts\python.exe tools\perf_baseline.py
```

- 空库冷启动仍约 **1.2 s**（未回归）。
- `get_article_payload_bytes`：**370818 → 54630**。
- 节流：连续 200 次 `_emit_throttled` 仅约 **1** 次实际 emit（最小间隔内合并）。

## 4. 未关闭

- 超大正文（数 MB HTML）仍会在切换时解析；可后续拆 `getArticleBody` 分页。
- 用户环境特定卡顿若无法在隔离库复现 → 记 **NOT VERIFIED**。
- 真实 Chromium 连续 10 次捕获的进程内存曲线：本轮以受控 DOM 交替（`sequential_stress.json`）+ 单次 live 小黑盒一键归档验证生命周期；完整 10× live Chromium 资源曲线仍建议在空闲时段复测。

## 5. 阶段 A 结论

| 项目 | 状态 |
|------|------|
| PERFORMANCE ROOT CAUSE | **PASS**（B1–B4 有代码+数据） |
| UI RESPONSIVENESS | **PASS**（去除进度全量刷新） |
| MAIN THREAD BLOCKING | **PASS**（热路径减负；无新增阻塞） |
| WEBENGINE RESOURCE LIFECYCLE | **PASS**（`WA_DeleteOnClose`；受控交替无卡死；完整 live 内存曲线可继续复测） |
