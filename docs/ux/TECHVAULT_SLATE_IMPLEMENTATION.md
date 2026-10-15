# TechVault Slate — 实现报告（阶段 A + B + C + D）

日期：2026-10-15  
范围：正式桌面入口、GitHub / 知识 / 设置工作区真实业务接入、旧 Widgets 展示层清理  
**视觉产品验收（知识/设置）：PENDING PRODUCT REVIEW**

## 1. 新界面架构

```text
ui.app.main
  → register_scheme()  # 必须先于 QApplication
  → DesktopWindow (QWebEngineView)
       ├─ techvault://app/*  → frontend/dist
       └─ QWebChannel
            ├─ application  → bootstrap / closeCheck / answerClose
            ├─ github       → LibraryService (+ DesktopServices)
            ├─ tasks        → ArchiveWorker / ArchiveService
            ├─ knowledge    → DocumentService
            └─ settings     → SettingsService (+ 原生目录选择)
```

- 前端：`frontend/` React 18 + TypeScript + Vite + CSS Modules + Lucide
- 生产加载打包资源，不依赖 Vite dev server
- 正式入口：[`src/techvault/ui/app.py`](../../src/techvault/ui/app.py) → [`desktop_window.py`](../../src/techvault/ui/desktop_window.py)

## 2. 设计系统（TechVault Slate）

Token：[`frontend/src/design/tokens.css`](../../frontend/src/design/tokens.css)

- 背景：`--app-bg #0d1118` · `--nav-bg #131b26` · `--sidebar-bg #171f2b`
- 选中：`--surface-selected #304258` · 强调：`--accent #8ebce9`
- GitHub 工作区已获产品认可，本轮未重新设计；知识/设置复用同一套组件与密度

## 3. 阶段 C：知识工作区

### 组件

| 组件 | 路径 |
|------|------|
| KnowledgeWorkspace | `frontend/src/features/knowledge/KnowledgeWorkspace.tsx` |
| ConfirmDialog / Toast | `frontend/src/components/ConfirmDialog.tsx` · `Toast.tsx` |
| MarkdownReader（复用） | `frontend/src/components/MarkdownReader.tsx` |
| Dirty / 关闭协调 | `App.tsx` + `ApplicationBridge.closeCheckRequested` |

### Bridge ↔ 服务

| UI 操作 | Bridge | 服务 |
|---------|--------|------|
| 列表 / 搜索 / 筛选 / 分页 | `listDocuments(json)` | `list_documents` / `search_documents`（limit=50，offset，hasMore） |
| 详情 / HTML | `getDocument` | `get_document` + `compose_readme_html` |
| 新建 / 保存 | `saveDocument` | `create_document` / `update_document` + `set_favorite` |
| 删除 | `deleteDocument` | `delete_document` |
| 收藏 | `setFavorite` | `set_favorite` |
| 复制正文 | `copyDocument` | `get_copy_text` + 系统剪贴板 |
| 分类 CRUD | `create/rename/deleteCategory` | DocumentService 分类 API |

### 草稿保护

切换文档 / 新建 / 切换工作区 / 取消编辑 / 关闭主窗口：统一 ConfirmDialog（保存并继续 / 放弃 / 继续编辑）。  
`DesktopWindow.closeEvent` → `ApplicationBridge.request_close_permission()` 同步等待 React `answerClose`；超时否认关闭（不强制丢弃）。

### 分页

`PAGE_SIZE = 50`；请求 `limit+1` 探测 `hasMore`；UI「加载更多」。未取消 LIMIT。

全文搜索语义保持既有 SQLite FTS；中文分词能力未在本轮扩展。

## 4. 阶段 C：设置工作区

| UI | Bridge | 服务 |
|----|--------|------|
| 读取状态 | `getStatus` | `get_archive_root` / `token_status` / `__version__` |
| 选择目录 | `chooseArchiveRoot` | `QFileDialog.getExistingDirectory` |
| 保存路径 | `setArchiveRoot` | `set_archive_root`（**不迁移**既有存档） |
| 保存 / 清除 Token | `saveToken` / `clearToken` | keyring；**不回显**已存 Token |

文案明确：已配置 ≠ 已验证有效。

## 5. 全应用统一（C3）

- 导航：GitHub ↔ 知识 ↔ 设置；未保存文档拦截
- TaskBar 提升到 App 级，三工作区可见真实归档任务
- Toast / ConfirmDialog / Loading / Empty 统一
- Ctrl+S 保存知识编辑；Esc 关闭可取消弹层

## 6. 阶段 D：旧 UI 清理

已删除（引用检查后）：

- `main_window.py`、`knowledge_page.py`、`archive_page.py`、`settings_page.py`
- `theme.py`、`markdown_view.py`
- `prototypes/`（历史 GitHub 原型）
- 前端占位 `KnowledgePlaceholder` / `SettingsPlaceholder`

保留：`ArchiveWorker`、业务服务、`content_render`、`tags.py`、`i18n.py`、Bridge、DesktopWindow。  
未改 SQLite Schema；未动用户数据路径默认逻辑。

测试已迁移到 Bridge / ArchiveWorker / LibraryService（`test_slate_bridges.py`、重写 `test_ui.py` / `test_e2e.py` 等）。全量：`265 passed`。

## 7. 新增 / 修改文件（摘要）

**新增：** Knowledge/Settings 前端、ConfirmDialog、Toast、`tools/slate_capture_cd.py`、`tests/test_slate_bridges.py`  
**重写：** `knowledge_bridge.py`、`settings_bridge.py`、`application_bridge.py`、`desktop_window.py`、`App.tsx`、若干测试  
**删除：** 旧 Widgets 页面与原型（见上）

## 8. 启动命令

```bash
cd frontend && npm ci && npm run build
cd ..
set TECHVAULT_DATABASE=%TEMP%\tv-slate\techvault.sqlite
set TECHVAULT_ARCHIVE_ROOT=%TEMP%\tv-slate\archive
.\.venv\Scripts\python.exe -m techvault
```

阶段 C+D 隔离截图：

```bash
.\.venv\Scripts\python.exe tools\slate_capture_cd.py
```

## 9. 网页归档工作区（P1）

导航新增「网页」；实现见 [`docs/web_archive/WEB_ARCHIVE_IMPLEMENTATION.md`](../web_archive/WEB_ARCHIVE_IMPLEMENTATION.md)。  
知乎回答/专栏 URL 自动归档、本地正文与图片、列表搜索删除；高保真快照为 P2。

## 10. GitHub Archive Layout V2（存储架构）

本轮重构物理归档目录，**未改** GitHub 工作区主布局 / 色板。

| 项 | 说明 |
|----|------|
| 权威路径 | `archive/paths.py`（V2 写入 + V1 读取辅助） |
| 稳定绑定 | `repositories.storage_relpath`（`007_storage_v2.sql`）+ `archive/storage.py` |
| 新布局 | `github/<owner>/<repo>/{README.md,source/,releases/<tag>/,offline/}`；临时 `_tmp/<job_id>/` |
| 打开本地 | Bridge `archiveFolder` → 绑定目录（V2 或未迁移 V1） |
| 迁移 | CLI：`python -m techvault.archive.migrate_layout_v2 {dry-run\|apply\|cleanup-v1}`（**不**自动执行） |
| 文档 | [`docs/archive/GITHUB_STORAGE_LAYOUT_V2.md`](../archive/GITHUB_STORAGE_LAYOUT_V2.md)、[`GITHUB_STORAGE_MIGRATION.md`](../archive/GITHUB_STORAGE_MIGRATION.md) |

设置页「整理旧版 GitHub 存档」UI 入口可后续接入同一 CLI；本轮以命令行为准。

## 11. 未覆盖 / 说明

- 知识分类管理入口：Bridge 已暴露 create/rename/delete；UI 以筛选下拉为主，未做独立「管理分类」对话框（可后续增强，不影响核心 CRUD）
- 中文 FTS 分词：沿用既有服务语义，未扩展
- 自动保存 / 文档版本历史：按需求未引入
- Windows 150% DPI：NOT VERIFIED
- 真实用户存档 Apply：**NOT EXECUTED**（需用户明确授权）
