# TechVault P2.3 — 连续网页归档卡死根因

日期：2026-10-15  
用户案例：首次知乎回答成功 → 第二次知乎专栏卡住（「归档中…」/ `BROWSER_LOADING` / 进度 0/1）

- 回答：`https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493`
- 专栏：`https://zhuanlan.zhihu.com/p/2058207332111218352`

## 1. 用户截图对应的卡住阶段

| UI 表现 | 映射 |
|---------|------|
| 顶部「归档中…」 | `task.active === true` 且 `kind === 'web'` |
| 底部「直接获取受限，正在使用浏览器加载网页……」 | `phaseCode === BROWSER_LOADING` |
| 进度 0/1 | HTTP 阶段残留的 `countLabel`（浏览器阶段未清） |

任务已进入 HTTP→浏览器补救，但**长期停在 `BROWSER_LOADING`，未进入终态**（非 React 单独丢事件）。

## 2. 实际源码根因

多因素叠加；主因是 **浏览器自动捕获阶段超时只在 `loadFinished(true)` 时启动**：

```text
WebCaptureWindow._on_loaded(ok=False)  → 仅 _ensure_polling()，不启动 _timeout_timer
loadFinished 从未为 true / 很晚才到 → 永不进入 WAITING_FOR_USER / FAILED
主页面一直 active + BROWSER_LOADING → 「归档中…」无限显示
```

次要因：

1. **`navigate()` 在 `__init__` 内启动**，TaskBridge 尚未 `connect(phaseChanged)`，早期 `WAITING_FOR_CONTENT` 可能丢失，UI 假象停在 `BROWSER_LOADING`（内部可能已在轮询）。
2. **同一 Profile 对象名 + 窗口未 `WA_DeleteOnClose`**，连续任务时 Chromium profile 残留冲突风险。
3. **`cancel()` 在 `cancel_capture()` 已终态后仍覆盖为 `cancelling`**，破坏终态一致性。
4. 专栏 DOM 选择器偏回答优先；短正文 / 非 `.Post-RichText` 结构时就绪判定更严（已加强专栏容器选择，**不是**单独删掉正文校验）。

## 3. 是否与连续任务有关

**是（触发条件），但根因不限于「第二篇」。**

- 同一进程第二次浏览器窗口更易撞上 profile/生命周期问题。
- 卡死机制本身是：自动捕获阶段**没有有限超时出口**时，任意一次 `loadFinished(true)` 缺失都会无限 `active`。
- 隔离结论：专栏单次在本机可成功；回答→专栏连续在修复前符合用户现象；修复后连续两篇均 `COMPLETED`。

## 4. 是否与知乎专栏 DOM 有关

**部分有关，不是唯一根因。**

- URL 识别：`zhuanlan.zhihu.com/p/...` → `pageKind=article`（本就支持）。
- DOM：已优先 `.Post-RichTextContainer` / `.Post-RichText` / `article .RichText`，并按正文长度选最长容器。
- 本机 live：专栏在浏览器补救下可提取并 `COMPLETED`。
- 若正文长期不出现，正确行为是超时后 `WAITING_FOR_USER`（可见窗口 + 保存按钮），而不是永久 `BROWSER_LOADING`。

## 5. 是否存在超时后不退出的问题

**修复前：是。**

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| `loadFinished(false)` / 从未完成 | 无限轮询，无阶段超时 | `begin_load` 即启动超时；失败路径也保证计时器 |
| 超时且正文可用 | 仅转人工（且可能到不了） | 尝试自动捕获 |
| 超时且正文不可用 | 可能永远到不了 | 明确 `WAITING_FOR_USER` 并 `raise` 窗口 |
| 窗口已毁 | 可能空转 | `BROWSER_GONE` → `FAILED` |
| 用户取消 | 可能被 `cancelling` 覆盖 | 终态后不再覆盖 |

**不允许**用「加长固定 sleep」掩盖；超时用于状态机出口，不是盲目等待。

## 6. 修复文件与具体逻辑

| 文件 | 逻辑 |
|------|------|
| `web_capture_window.py` | `begin_load()` 延后导航；`begin_load` 即武装超时+轮询；`WA_DeleteOnClose`；每任务唯一 profile 名；超时决策捕获/人工；诊断日志；忽略保存后的迟到 `loadFinished`；取消与 MHTML 路径终态 |
| `task_bridge.py` | 每任务 `taskId`/`pageKind`；先 connect 再 `begin_load`；忽略陈旧回调；浏览器阶段清空 `countLabel`；`WAITING_FOR_USER`→`waiting_user`；`cancel` 不覆盖已终态；`deleteLater` 释放窗口 |
| `dom_extract.py` | 专栏/回答容器优先级；`not_ready_reason`；超时可用阈值 `payload_usable_on_timeout` |
| `WebArchiveWorkspace.tsx` / `TaskBar.tsx` / `bridge.ts` | `waiting_user` 与终态 toast；「等待确认…」；`taskId`/`pageKind` |
| `tests/test_web_archive_hang.py` | 连续/取消/超时/陈旧回调/10 次同桥接生命周期 |
| `tools/live_zhihu_hang_probe.py` | 真实回答→专栏连续证据 |

## 7. 10 次连续任务测试结果

同一 `QApplication` + 同一 `TaskBridge`（不重启进程）：

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_web_archive_hang.py -q
```

- `test_ten_consecutive_tasks_same_bridge_no_orphan_active`：**PASS**（10/10 均有终态，`active=false`，`taskId` 互异）
- 套件合计：`tests/test_web_archive_hang.py` + one_click + p2 + web_archive → **36 passed**

## 8. 真实知乎专栏归档结果

```bash
.\.venv\Scripts\python.exe tools\live_zhihu_hang_probe.py
```

证据：`docs/web_archive/hang_case/result.json`

| # | pageKind | status | articleId | taskId |
|---|----------|--------|-----------|--------|
| 1 | answer | success | 1 | 190299b000b0 |
| 2 | article | success | 2 | 65d22371aa52 |

`pass_sequential: true`（隔离 SQLite / archive_root，非用户生产库）。

## 9. React 与 Python 终态一致性证据

- Python：`web_task terminal ... react_active=false` + `taskChanged` JSON（`active=false`, `phaseCode=COMPLETED|FAILED|CANCELLED|PARTIAL`）
- React：`App.tsx` `taskChanged` → `setTask`；工作区在 `running|cancelling|waiting_user` → 终态时刷新列表
- 单测：`test_python_terminal_matches_react_payload_fields` 断言同一 payload 字段一致

## 10. 尚未关闭的问题

- 知乎 HTTP 仍常 `ACCESS_RESTRICTED`，公开文依赖浏览器补救（预期行为）。
- MHTML 快照失败不阻断正文（PARTIAL/提示）；快照稳定性随环境变化。
- `WAITING_FOR_USER` 仍保持 `active`（需用户点「保存当前页面」或取消）——有可见操作入口，符合产品语义。
- 未改 GitHub / 知识 / 设置模块；未重写 HTTP→Chromium 架构。

## 验收状态

| 项 | 状态 |
|----|------|
| ROOT CAUSE | **PASS** |
| ZHIHU COLUMN CAPTURE | **PASS**（live） |
| SEQUENTIAL CAPTURE | **PASS**（live + 10× pytest） |
| TIMEOUT HANDLING | **PASS** |
| CANCEL AND RETRY | **PASS** |
| TASK TERMINAL STATE | **PASS** |
| REACT STATE RECOVERY | **PASS** |
| EXISTING REGRESSION | **PASS**（36 web-archive related） |
| OVERALL | **PASS** |
