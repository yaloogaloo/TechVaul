# TechVault 网页归档 — P1 实现说明

日期：2026-10-15  
范围：**P1 核心闭环**（知乎回答 / 专栏 + 通用 Readability + Slate 工作区）

## 1. 架构

```text
React WebArchiveWorkspace
  → webArchive Bridge / tasks.startWebArchive
       → WebArchiveWorker (QThread)
            → WebArchiveService.archive
                 → fetch (httpx)
                 → ZhihuAdapter | readability-lxml
                 → sanitize + localize media
                 → archive_root/web/<domain>/<title>-<id>/
                 → SQLite web_articles / web_captures / FTS
```

与 GitHub V2、知识库表完全隔离。路径仅由 Python `WebPathLayout` 管理。

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

| 模式 | P1 |
|------|----|
| A：URL 自动归档 | 已实现 |
| B：浏览器内打开并保存 | **P2** |
| MHTML / SingleFile 快照 | **P2**（未捆绑 AGPL 源码） |

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

## 8. 未关闭 / P2

- 高保真 MHTML / 外部 SingleFile 导入  
- 登录态浏览器捕获（隔离 Profile）  
- 评论、问题下多回答、批量 URL  
- 真实外网知乎链接需用户提供可访问样本后再做产品验收截图补充  
