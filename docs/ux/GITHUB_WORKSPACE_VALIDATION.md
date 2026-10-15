# GitHub 工作区原型 · 验证记录

## 1. 启动方式

在仓库根目录：

```bash
python prototypes/github_workspace/main.py
```

或使用项目虚拟环境：

```bash
.\.venv\Scripts\python.exe prototypes\github_workspace\main.py
```

截图批处理（写 `docs/ux/screenshots/`，不连网、不写用户库）：

```bash
.\.venv\Scripts\python.exe prototypes\github_workspace\main.py --capture
```

## 2. 运行环境（本轮截图）

来源：`docs/ux/screenshots/capture_meta.json`

| 项 | 值 |
|----|-----|
| OS | Windows 10.0.26200 |
| Python | 3.11.9 |
| PySide6 | 6.11.2 |
| Qt | 6.11.2 |
| logical DPI | 96 |
| devicePixelRatio | **1.25（≈ Windows 125% 缩放）** |
| 截图方式 | `QWidget.grab()` → PNG |
| 数据 | 固定合成 `data.build_catalog()` |
| 网络 / Token / 用户 DB | 未使用 |

**150% 缩放：NOT VERIFIED**（本机会话 DPR=1.25，未切换到 150% 再抓取）。

## 3. 截图清单

| 文件 | 场景 | 窗口 |
|------|------|------|
| `01_widescreen_normal.png` | 普通项目 README | 1686×896 |
| `02_default_1440x900.png` | 默认窗口 | 1440×900 |
| `03_minimum_960x640.png` | 最小窗口 | 960×640 |
| `04_long_readme_widescreen.png` | 长名 + 长 README + 更新失败双状态 | 1686×896 |
| `05_update_failed_dual_status.png` | 版本与文件 Tab + 失败语义 | 1440×900 |
| `06_task_running.png` | 独立任务面板进行中 | 1440×900 |
| `07_empty_state.png` | 无项目空状态 | 1440×900 |
| `08_archive_history_tab.png` | 归档记录 Tab | 1440×900 |

路径根：`docs/ux/screenshots/`

## 4. 交互检查

| 交互 | 结果 |
|------|------|
| 选择不同项目切换详情 | PASS |
| 搜索过滤 | PASS |
| 分类筛选 | PASS |
| 排序切换 | PASS |
| README / 版本与文件 / 归档记录 Tab | PASS |
| Splitter 分栏拖动 | PASS（手动；截图批处理未测极限） |
| 模拟任务状态切换 / 取消 | PASS（明示模拟） |
| 空状态切换 | PASS |
| 合成数据、无生产 DB/网络 | PASS |

## 5. 布局与可读性检查

| 场景 | 结果 | 说明 |
|------|------|------|
| 1686×896 普通 | PASS | 截图 01 |
| 1440×900 | PASS | 截图 02 |
| 960×640 | PASS（可用） | 三栏仍保留；偏紧但无重叠遮挡；未做单面板折叠（见已知问题） |
| 长名称/中英简介 | PASS | 列表省略 + tooltip；详情标题换行 |
| 长 README / 代码 / 宽表 | PASS | 纵滚；横滚策略已开；宽表在 README 下方需滚动到达 |
| 更新中 | PASS | 截图 06；任务面板独立 |
| 旧存档可用 + 最近失败 | PASS | 双状态芯片 + 列表失败标识；截图 04/05 |
| 空状态 | PASS | 截图 07 |
| README 非原始 HTML 标签 | PASS | 徽章经 sanitizer 显示为排版文本；说明性 inline code 中的标签字样除外 |
| 125% DPI | PASS | DPR=1.25 |
| 150% DPI | **NOT VERIFIED** | |

## 6. 生产隔离检查

| 项 | 结果 |
|----|------|
| 未修改 `src/techvault/**` | PASS（git 仅 `?? docs/` `?? prototypes/`） |
| 未改 migrations / Schema | PASS |
| 未触发真实下载 | PASS |
| 未读 Token / 用户库 | PASS |

## 7. 旧版 vs 新版差异摘要

1. **结构**：从单列堆叠改为「身份头 + Tabs + 独立任务栏」。  
2. **列表**：从字符截断纯文本改为结构化行与双状态。  
3. **状态**：本地归档与最近更新强制分离，失败不再被「已保存」一口吞掉。  
4. **README**：默认排版阅读；HTML 走生产 sanitizer，而非 `html=False` 转义直出。  
5. **任务**：取消与进度离开添加弹窗，进入常驻面板。

（旧版像素对照未在本轮启动生产 GUI，以免写入用户库；差异依据审计源码与本原型截图。）

## 8. 已知问题 / 未关闭

| ID | 说明 | 状态 |
|----|------|------|
| N1 | 960 宽仍为三栏，未实现「列表→详情」单面板切换 | NOT CLOSED（产品可接受则保留） |
| N2 | Windows 150% 缩放未实机截图 | NOT VERIFIED |
| N3 | 原型工具按钮（空状态 / 切换模拟）出现在主工具区，接入生产前应隐藏或移入调试菜单 | NOT CLOSED |
| N4 | 宽表需在 README 内滚动才能完全看见；未做「表格全宽弹出」 | 可接受 |
| N5 | 视觉尚待产品方基于截图签字 | **VISUAL REVIEW PENDING** |

## 9. 验收结论

| 标准 | 状态 |
|------|------|
| 独立入口可启动 | PASS |
| 主要交互可用 | PASS |
| 合成数据、无真实 DB/网络 | PASS |
| 生产代码与存档未改 | PASS |
| 普通窗与长文本无已知遮挡/裁切 | PASS（基于本轮截图） |
| 真实截图与环境记录 | PASS |
| 组件与接入边界文档 | PASS |
| 技术验收 | **TECHNICAL PASS** |
| 视觉设计最终认可 | **VISUAL REVIEW PENDING** |
| 全量结案（含视觉签字 + 150% + 窄屏策略产品确认） | **NOT CLOSED** |
