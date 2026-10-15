# GitHub 工作区原型 · 设计说明

状态：垂直切片原型（`prototypes/github_workspace/`）  
基线：对照 `docs/TECHVAULT_UI_UX_AUDIT.md` 的信息架构建议  
范围：仅 GitHub 项目工作区；不接入生产 `ArchivePage`、数据库或网络

## 1. 设计目标

把「分类筛选 → 项目扫描 → 内容阅读 → 后台任务」拆成清晰层级，解决审计中的：

- 元数据与 README 抢同一滚动区、固定高度裁切
- 列表纯文本堆叠导致难扫读
- README 原始 HTML 标签直出
- 下载进度与详情内容耦合
- 「已保存」与「最近更新失败」语义混淆

本原型用**新的控件树**实现，不是生产 `archive_page.py` 的 QSS 换肤。

## 2. 信息架构

```text
┌─ 一级导航（知识 / GitHub / 设置）────────────────────────────┐
│  标题 · 搜索 · 排序 · 主操作                                    │
│  ┌分类┐ ┌──项目列表──┐ ┌──────────详情──────────┐              │
│  │可调│ │结构化行    │ │身份头 + 双状态 + 操作   │              │
│  │筛选│ │本地/更新态 │ │Tabs: README|版本|归档  │              │
│  └────┘ └────────────┘ └────────────────────────┘              │
│  ┌─────────── 独立任务面板（Owner/Repo · 阶段 · 进度 · 取消）─┐ │
└────────────────────────────────────────────────────────────────┘
```

### 与旧版对照（信息架构，非仅颜色）

| 维度 | 旧版 `ArchivePage` | 本原型 |
|------|-------------------|--------|
| 列表行 | 多行纯文本拼接 + 字符硬截断 | 标题 / owner / 简介 / 双状态徽章结构化行；`QFontMetrics` 省略 |
| 详情元数据 | `QTextBrowser` `maxHeight=148` 与 README 并列滚 | 布局控件展示身份；长名换行；Topics 限量 + tooltip |
| 内容组织 | 元数据+README+资产+进度纵向堆叠 | 三个真实可切换标签页 |
| 任务状态 | 进度挂在详情底部；取消藏在添加弹窗 | 独立任务面板；取消常驻；明示「模拟」 |
| 状态语义 | 单一「已保存」易遮蔽失败 | **本地归档**与**最近更新**并排展示 |
| README | `html=False` 导致标签原文 | Markdown（禁 raw HTML）+ 生产 `sanitize_html` 处理合成 HTML 片段 |

## 3. 组件规范

| 组件 | 职责 | 位置 |
|------|------|------|
| `PrototypeMainWindow` | 壳、导航、状态栏 | `workspace.py` |
| `GitHubWorkspacePage` | 搜索/分类/列表/详情/任务组装 | `workspace.py` |
| `ProjectList` / `ProjectRow` | 结构化列表与字体度量省略 | `widgets/project_list.py` |
| `ProjectDetail` | 身份头、双状态、操作、Tabs | `widgets/detail.py` |
| `ReadmeView` | 安全排版阅读；允许横滚 | `widgets/readme_view.py` |
| `DualStatusRow` | 本地归档 vs 最近更新 | `widgets/status.py` |
| `TaskPanel` | 独立任务与模拟状态机 | `widgets/task_panel.py` |
| `compose_readme_html` | 离线安全合成 HTML | `render.py` |
| `theme` | Token / QSS / 字号间距 | `theme.py` |

### 设计 Token（可复用到知识/设置页）

- 表面：`#0E1014` / `#141820` / `#1A1F2A`
- 强调：`#6E93B5`（延续产品冷灰蓝，避免紫渐变套路）
- 状态：success `#6FBF8F` · warning `#C9A14A` · error `#C97A70` · info `#6E9FBF`
- 间距：4 / 8 / 12 / 16 / 24 / 32
- 正文字号约 13–14px；详情标题 20px；元信息 11px
- 主按钮 `role=primary`；次要 `quiet`；危险色仅用于失败文案，不只靠颜色

### 列表与滚动

- 分类 / 列表 / README 可并列纵滚；**无外层再套一层**包住全部详情
- 宽表格/长代码：README 允许横向滚动（相对生产 `ScrollBarAlwaysOff` 的改进）
- 分栏：`QSplitter`，默认约 140 / 340 / 720，不可 collapse

### 状态文案

**本地归档：** 未归档 / 已归档 / 部分可用  
**最近更新：** 未执行 / 更新中 / 成功 / 失败（及原型用的取消中）

允许同时出现：「本地已归档」+「最近更新失败（旧版本仍可用）」。

## 4. README 安全策略（原型 → 生产）

原型路径：

1. `markdown-it` `html=False` 渲染 Markdown（脚本等不会作为 HTML 执行）
2. 合成 HTML 片段先规范化，再调用生产 `techvault.offline.sanitize.sanitize_html`（nh3 白名单）
3. `QTextBrowser` 展示结果；外链仅 `http/https`

生产接入建议（本轮不改生产代码）：

1. 优先展示经 sanitizer + `pages/media` 映射的离线 HTML
2. 回退 `readme/README.md` 时继续 `html=False`，或走同一 sanitizer 管道，**禁止**无清理 `html=True`
3. 保留 `offline.gate` 写盘完整性门；应用内阅读与写盘共用允许标签表

## 5. 后续接入边界

| 可迁入生产 | 本轮明确不做 |
|------------|--------------|
| 双状态模型、任务面板布局、列表行结构、Tabs 信息架构、theme token | 改 `archive_page.py` 前须 **VISUAL REVIEW** 通过 |
| `compose` 思路接入 LibraryService README 选择 | 不改 Schema / ArchiveService / 下载协议 |
| 知识页/设置页套用同一 token | 本轮不重构这两页 |

模拟任务**不得**在接入时被误接到真实 `ArchiveWorker` 而不加合作取消与流式下载修复（见审计 UI-02 / UI-07）。
