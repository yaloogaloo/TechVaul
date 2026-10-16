# 小黑盒（Xiaoheihe / Heybox）适配器实现

日期：2026-10-15  
范围：P2.4 阶段 B — 复用现有 WebArchive 管道，不新建工作区 / 数据库。

## 1. URL 规则与规范化

| 输入形态 | 行为 |
|----------|------|
| `api.xiaoheihe.cn/v3/bbs/app/api/web/share?...link_id=...` | 提取 `link_id`，规范为 `https://www.xiaoheihe.cn/app/bbs/link/<link_id>` |
| `www.xiaoheihe.cn/app/bbs/link/<link_id>` | 直接识别为 article |
| `xiaoheihe.cn/a/<id>` | **unsupported**（实测 404，不擅自宣称可用） |
| 分享追踪参数 `h_src` / `h_session_id` / `h_camp` | **丢弃**，不写入 canonical，不进日志明文 |

实现：`src/techvault/web_archive/urls.py` → `_parse_xiaoheihe`  
示例稳定 ID：`f1ba7299a477` → `https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477`

同一帖子不同分享参数去重：canonical 相同。

## 2. XiaoheiheAdapter

文件：`src/techvault/web_archive/adapters/xiaoheihe.py`  
注册：`adapters/__init__.py`、`extract.py`（与 ZhihuAdapter 并列，互不混入）。

适配器职责：

- SPA 壳识别（空 `#app` / `heybox-project` + 短文本）→ `CONTENT_INCOMPLETE`（建议浏览器）
- App-only / 强登录闸门
- DOM 正文：`.post__content` / `.hb-article` / `#post-static`
- 标题：`.link-section-title` / `.section-title__content`
- 作者：`.hb-bbs-link__header [class*='name']` 等
- 图文为主帖：短正文 + 图片仍可 `ok=True`（`image_primary_post` warning）

## 3. 捕获路径

继续：`HTTP →（失败/不完整）→ Chromium → 当前 DOM → 本地化 → MHTML`

实测该帖：

1. HTTP 得到 SPA 壳（~3KB）→ `INCOMPLETE` + `suggest_browser`
2. TaskBridge 一键自动打开浏览器 Profile
3. `dom_extract.DOM_EXTRACT_JS` 识别 `xiaoheihe_post`，提取正文
4. `publish_from_rendered_html` 写入 `web/xiaoheihe.cn/<title>-<stable-id>/`
5. 复用 `MhtmlSaveController` 写 `snapshot.mhtml`

不引入第二套 MHTML / 任务引擎。

## 4. 本地目录

由现有 `WebPathLayout` 管理：

```
<archive_root>/web/xiaoheihe.cn/<readable-title>-<stable-id>/
  article.html
  article.md
  metadata.json
  snapshot.mhtml
  media/
```

证据目录：`docs/web_archive/xiaoheihe_case/archive/`

## 5. UI

- 网页归档工作区来源筛选增加「小黑盒」(`site_key=xiaoheihe`)
- 列表 `site_label` =「小黑盒」
- 复用阅读正文 / 网页快照 / 归档信息 / TaskBar

## 6. 媒体

- `media.is_downloadable_image` 增加 `max-c.com` / `xiaoheihe.cn` CDN 宿主
- DOM 提取前短暂滚动以触发懒加载
- 阅读版仅本地化**正文 DOM 内**图片；页面级头像/推荐流不计入正文 media（见 VALIDATION）
