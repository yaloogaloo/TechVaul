# TechVault 网页归档 — 实现说明（P1 + P2）

日期：2026-10-15  
范围：知乎回答 / 专栏 + Readability + Slate 工作区；**P2** 修正登录误判与 Chromium DOM 捕获

## 1. 架构

```text
React WebArchiveWorkspace
  ├─ 保存网页 → tasks.startWebArchive → WebArchiveWorker → archive_url (httpx)
  └─ 在浏览器中打开 → webArchive.openBrowserCapture
         → WebCaptureWindow (独立 QWebEngineProfile，无业务 Bridge)
         → DOM runJavaScript → publish_from_rendered_html (BROWSER)
              → ZhihuAdapter | readability
              → sanitize + localize media（可复用会话已加载资源）
              → archive_root/web/<domain>/<title>-<id>/
              → SQLite web_articles / web_captures / FTS
              → 可选 snapshot.mhtml
```

与 GitHub V2、知识库表完全隔离。路径仅由 Python `WebPathLayout` 管理。  
浏览器会话：`browser_profiles/zhihu/`（配置层解析，不进 SQLite / React）。

## 2. 目录布局

```text
<archive_root>/web/<domain>/<readable-title>-<10hex>/
  article.html
  article.md
  metadata.json
  media/
  versions/<UTC>/   # 重新归档时暂存旧成功版本
```

临时下载：`<archive_root>/_tmp/web/<job_id>/`

## 3. 数据库

Migration：`migrations/008_web_archive.sql`

- `web_articles`：当前有效归档指针与元数据  
- `web_captures`：版本历史  
- `web_article_search`：FTS5（辅以 LIKE，改善中文检索）

## 4. 知乎支持（P1）

| 类型 | 状态 |
|------|------|
| `zhihu.com/question/.../answer/...` | 支持 |
| `zhuanlan.zhihu.com/p/...` | 支持 |
| `zhihu.com/question/...`（问题主页） | 识别并提示选择具体回答，不伪造成功 |
| 登录墙 / 验证码 / 付费提示 | 检测为失败或 NEEDS_LOGIN，不标完整成功 |

提取顺序：知乎 JSON（`js-initialData`）→ DOM 结构选择器 →（其他站点）readability-lxml。

## 5. 捕获模式

| 模式 | 状态 |
|------|------|
| A：URL 自动归档（httpx） | 已实现；知乎常遇 zse/403 → `ACCESS_RESTRICTED` |
| B：Chromium 当前 DOM 保存 | **已实现**（`WebCaptureWindow` → `publish_from_rendered_html`） |
| 可选知乎登录会话 | **已实现**（独立 Profile，非强制） |
| MHTML 快照 | **已正式化**：DownloadCompleted → 校验 → 原子发布；独立离线查看器 |

### 5.1 推荐策略（P2.1 一键闭环）

**「保存网页」单次点击：** 匿名 HTTP →（失败且可补救则同一任务内）匿名 WebEngine DOM 自动捕获 →（超时）同窗人工确认保存 →（确需认证）用户主动登录。

硬性规则：浏览器捕获**不得**再用 httpx 回源拉正文。

详见 [`ZHIHU_CAPTURE_ROOT_CAUSE.md`](./ZHIHU_CAPTURE_ROOT_CAUSE.md)、[`WEB_ARCHIVE_ONE_CLICK_FIX.md`](./WEB_ARCHIVE_ONE_CLICK_FIX.md)。

## 6. 第三方依赖与许可证

| 依赖 | 用途 | 许可证 |
|------|------|--------|
| [readability-lxml](https://github.com/buriy/python-readability) | 通用正文提取（Mozilla Readability 思路） | Apache-2.0 |
| beautifulsoup4 / lxml | HTML 解析 | MIT / BSD |
| nh3 | HTML 清理 | Apache-2.0 |
| httpx | 页面与图片拉取 | BSD |
| Qt WebEngine | Slate UI 容器（P2 将用于可见页面捕获） | LGPL/GPL（随 PySide6） |

**未引入：** ArchiveBox 全栈、SingleFile 源码捆绑（AGPL，待许可评估）。  
研究对照：zhihu-download / markdown-export — 仅作知乎字段与导出思路参考，未复制其代码。

## 7. 关键 / 修改文件（摘要）

- `src/techvault/web_archive/*`
- `src/techvault/ui/bridge/web_archive_bridge.py`
- `src/techvault/ui/web_archive_worker.py`
- `migrations/008_web_archive.sql`
- `frontend/src/features/web/*`
- NavigationRail / App / qtBridge / TaskBridge 扩展

## 8. 调用关系（P2）

```text
保存网页 → TaskBridge.startWebArchive → httpx → extract
失败且 suggest_browser → UI「使用浏览器归档」
在浏览器中打开 → WebArchiveBridge.openBrowserCapture
  → WebCaptureWindow (独立 QWebEngineProfile，无业务 WebChannel)
  → runJavaScript(DOM) → publish_from_rendered_html (BROWSER)
  → 可选 page.save(MHTML)
```

## 9. MHTML（P2.2）

详见 [`MHTML_SNAPSHOT_COMPLETION.md`](./MHTML_SNAPSHOT_COMPLETION.md)。

- 生命周期：`MhtmlSaveController`（`downloadRequested` + 完成态）
- 发布：`attach_mhtml_snapshot`（暂存 → 校验 → `os.replace`）
- UI：Slate「网页快照」Tab + `SnapshotViewerWindow`（拦截外网）
- 迁移：`009_web_snapshot.sql`

## 10. 未关闭

- 外部 SingleFile HTML 导入  
- 评论 / 多回答批量 / 收藏夹  
- MHTML 像素级 / 全资源保真（已标注 PARTIAL）  
