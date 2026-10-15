# TechVault Slate — 验收报告（阶段 A + B + C + D）

## 启动与构建

```bash
cd frontend
npm ci
npm run build

set TECHVAULT_DATABASE=%TEMP%\tv-slate\techvault.sqlite
set TECHVAULT_ARCHIVE_ROOT=%TEMP%\tv-slate\archive
.\.venv\Scripts\python.exe -m techvault
```

隔离截图：

```bash
.\.venv\Scripts\python.exe tools\slate_capture_business.py   # GitHub 业务
.\.venv\Scripts\python.exe tools\slate_capture_cd.py         # 知识 / 设置 / 回归
```

## 功能等价矩阵

对照 `docs/TECHVAULT_UI_UX_AUDIT.md` 能力编号。

### 知识 K01–K07

| ID | 能力 | 状态 | 证据 |
|----|------|------|------|
| K01 | 列表 / 阅读 Markdown | **PASS** | KnowledgeBridge + MarkdownReader；`cd_knowledge_read_*.png` |
| K02 | 新建 / 编辑 / 手动保存 | **PASS** | `saveDocument`；`cd_knowledge_edit_*.png`；`test_slate_bridges` |
| K03 | 删除（确认） | **PASS** | ConfirmDialog + `deleteDocument` |
| K04 | 搜索 / 类型 / 分类 / 标签 / 收藏筛选 | **PASS** | Bridge 透传 DocumentService；防抖搜索 |
| K05 | 收藏切换 | **PASS** | `setFavorite` |
| K06 | 复制正文 | **PASS** | `copyDocument` → 剪贴板 |
| K07 | 草稿保护（切文档/工作区/关窗） | **PASS** | DirtyGuard + `closeCheckRequested`；`cd_knowledge_dirty_guard.png`；`test_close_guard_*` |

分页（>50）：**PASS** — `hasMore` + 加载更多；`test_knowledge_search_filters_and_pagination`；`cd_knowledge_pagination_50plus.png`

### 设置 S01–S03

| ID | 能力 | 状态 | 证据 |
|----|------|------|------|
| S01 | 归档根目录读 / 选 / 存 | **PASS** | SettingsBridge + QFileDialog；`cd_settings_*.png` |
| S02 | Token 配置 / 清除 / 不回显 | **PASS** | keyring；`cd_settings_token_dialog.png`；`test_token_*` |
| S03 | 关于 / 版本 | **PASS** | `__version__`；设置页关于区 |

### GitHub G01–G09（回归）

| ID | 能力 | 状态 | 证据 |
|----|------|------|------|
| G01–G09 | 列表/详情/README/分类/添加/更新/任务/版本/双状态 | **PASS** | 既有 Bridge 未改业务语义；`cd_github_regression_1440x900.png`；全量测试 265 passed |

## 验收矩阵

| 验收项目 | 状态 |
|---|---|
| Slate Shell | **PASS** |
| GitHub UI Regression | **PASS** |
| Knowledge UI Implementation | **PASS** |
| Knowledge Business Parity | **PASS** |
| Draft Protection | **PASS** |
| Knowledge Pagination | **PASS** |
| Settings UI Implementation | **PASS** |
| Settings Business Parity | **PASS** |
| Cross-Workspace Navigation | **PASS** |
| Responsive Layout | **PASS**（960/1440/1686）；150% **NOT VERIFIED** |
| Legacy UI Removal | **PASS** |
| Data Safety | **PASS**（隔离临时库/keyring mock；无用户库写测） |
| Visual Product Acceptance | **PENDING PRODUCT REVIEW** |
| Overall | **PASS**（工程） / 视觉待产品审 |

## 截图（阶段 C+D）

目录：`docs/ux/slate_screenshots/`

| 文件 | 场景 |
|------|------|
| `cd_github_regression_1440x900.png` | GitHub 回归 |
| `cd_knowledge_read_1686x896.png` | 知识阅读宽屏 |
| `cd_knowledge_read_1440x900.png` | 知识阅读默认 |
| `cd_knowledge_edit_1440x900.png` | 知识编辑 |
| `cd_knowledge_long_doc.png` | 长文档 / 编辑态 |
| `cd_knowledge_empty.png` | 搜索无结果 |
| `cd_knowledge_pagination_50plus.png` | >50 列表 |
| `cd_knowledge_dirty_guard.png` | 未保存确认 |
| `cd_knowledge_960x640.png` | 知识最小窗 |
| `cd_settings_1440x900.png` | 设置默认 |
| `cd_settings_960x640.png` | 设置最小窗 |
| `cd_settings_token_dialog.png` | Token 输入（测试环境） |
| `capture_meta_cd.json` | DPI / 路径元数据 |

本机约 125% DPR（见 meta）。**150% NOT VERIFIED**。

## 测试

```bash
.\.venv\Scripts\python.exe -m pytest tests -q
# 265 passed
```

关键业务以 Bridge + Service + WebEngine 截图验证；不以 React Mock 作为完成证据。

## 未关闭问题

1. **视觉产品验收（知识/设置）**：PENDING PRODUCT REVIEW  
2. Windows 150% DPI 实机截图  
3. 知识分类「管理」独立对话框（可选增强；核心筛选/CRUD 已通）  
4. 中文 FTS 分词能力未扩展（沿用既有语义）

## 旧 UI 清理结果

| 项 | 结果 |
|----|------|
| MainWindow / *_page / theme / markdown_view | 已删除 |
| prototypes/ | 已删除 |
| ArchiveWorker / 业务服务 | 保留 |
| 生产入口 | 仅 DesktopWindow + Slate |
| 测试 | 已改挂 Bridge/Worker；无 MainWindow 引用 |
