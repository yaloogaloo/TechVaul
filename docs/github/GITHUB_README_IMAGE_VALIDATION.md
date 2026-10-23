# GitHub 分类、计数与 README 图片验收

验收日期：2026-10-23。VoiceStudio 默认分支 `main`。

## VoiceStudio

根因见 `GITHUB_README_IMAGE_CACHE.md`。当前 README 解析出 12 个图片 URL，全部下载成功，失败 0。本轮没有发现已失效的远程图。对象 12 个，合计 7,867,481 字节。

| 来源 | 结果 |
|---|---|
| `raw.githubusercontent.com` 上的 logo、GIF、四张工作区 PNG | OK |
| shields.io 三枚徽章、Trendshift、Supermetry | OK，SVG |
| `github.com/user-attachments/assets/b474497d-...` | OK，按内容识别为 PNG |

展示 HTML 中的 `<img src>` 全部是 `techvault://app/media/...`。剩下的 `https://` 是正文链接，不是图片。

去重：再请求 `https://github.com/debpalash/VoiceStudio/raw/main/docs/logo.png`，与 raw logo 得到同一哈希 `c3f80e68559b9608966deeff759d5ad3282b5d1b07036186030a02bc53e5eec4`，`reused: true`，对象数仍是 12，`duplicateHits` 从 0 变为 1。见 `voicestudio_dedup.json`。

这次现场缓存调用的是 `ImagePool.ensure`，没有写 `image_refs`，所以该证据库的引用数是 0。正式补全和归档走 `cache_repository_readme`，会 `replace_refs`。

目录树：`docs/github/evidence/pool_tree.txt`。图片二进制在 `voicestudio_pool/`，已加入 `.gitignore`，不提交。上游 README 原文同样不提交。

## Qt WebEngine 显示

窗口为 1440×960 的 `DesktopWindow`，`LocalContentCanAccessRemoteUrls` 为 false。页面内 12 张图全部 `complete`，且 `naturalWidth` 大于 0（logo 512×512，GIF 1080×720，工作区图 1440×960，桌面图 2628×1950）。见 `image_probe.json`。

截图：

1. `01_category_and_count.png`：分类下拉为「AI 工具」，`已归档 2`，logo 与徽章已画出。
2. `02_category_filter.png`：顶栏「AI 工具」，`显示 1 / 2`，未分类的 FieldNotes 不在列表中。
3. `03_voicestudio_readme.png`：logo 与徽章。
4. `04_voicestudio_body.png`：正文中的 Voice cloning 工作区截图。

图片来自本地 scheme。没有关闭网卡后再冷启动；在该 WebEngine 配置下远程图片不能加载，已显示的图只能来自资源池。

## 测试与构建

- `tests/test_github_category_stats.py`：设置、未分类、筛选、重命名、删除、失败回滚、重启，以及多快照、多 Release、仅元数据、下载中、失败文件和搜索下的计数。
- `tests/test_readme_image_cache.py`：相对路径、HTML、srcset、blob、代码块、去重、304、内容变化、404/403、非法类型、超限、GIF、SVG 脚本剥离、缺文件重取、并发、占位、归档失败保护、补全不移动快照指针、重定向到回环地址。
- 前端：`npm run build` 成功。`dist/index.html` 0.46 kB，`dist/assets/index-B9Fgs85T.css` 29.45 kB，`dist/assets/index-HBK4eVaf.js` 290.41 kB。
- 全量 pytest：339 passed，76.25s。

进度 tick 仍不刷新整个 GitHub 列表。

## 未关闭问题

- 需要 GitHub Token 的私有图不会带 Token，会失败。
- `srcset` 只缓存选中的那一个候选。
- 打开详情时只检查文件大小；完整哈希在 scheme 响应时核对。损坏文件第一次可能 404，下次详情显示占位，直到补全。
- 魔数校验不是完整解码，截断但签名有效的 PNG 可能被保存。
- 含不安全片段的分支名回退到 `HEAD`。
- CommonMark 会把紧跟 HTML 块、中间没有空行的 Markdown 图片当作文字。
- 共享池的导出和垃圾回收未做。删除项目不删除共享图片。
- Windows 上 Chromium 可能打印缓存目录「拒绝访问」。这不影响退出码，也不是图片失败。

## 验收状态

| 项目 | 状态 |
|---|---|
| PROJECT CATEGORY ASSIGNMENT | PASS |
| CATEGORY PERSISTENCE | PASS |
| CATEGORY FILTER CONSISTENCY | PASS |
| ARCHIVED PROJECT COUNT | PASS |
| FILTERED PROJECT COUNT | PASS |
| README IMAGE URL RESOLUTION | PASS |
| CONTENT HASH DEDUPLICATION | PASS |
| SHARED IMAGE CACHE | PASS |
| LOCAL WEBENGINE IMAGE DISPLAY | PASS |
| OFFLINE README IMAGES | PASS |
| EXISTING README BACKFILL | PASS |
| VOICESTUDIO LIVE VALIDATION | PASS |
| GITHUB ARCHIVE REGRESSION | PASS |
| EXISTING FEATURES REGRESSION | PASS |
| SLATE VISUAL CONSISTENCY | PASS |
| OVERALL | PASS |
