# GitHub README 共享图片资源池

## 图片无法显示的原因

Slate 阅读 `README.md`，不是离线页里的 `media/`。流程是 markdown-it 渲染，再经 `sanitize_html`。相对路径只有在文件就在 README 旁边时才会变成 `file://`，否则 `src` 被清空。远程 `https` 能通过清理，但 `DesktopWindow` 把 `LocalContentCanAccessRemoteUrls` 设为 false，`techvault://app` 页面不能加载远程图。

知乎 / 小黑盒的离线媒体规则和 GitHub V2 目录没有改。原始 `README.md` 也不改写。

## 目录

权威索引是资源池自己的 `cache_index.sqlite`，不写入应用库，删除仓库也不会级联删除图片。

```
<data>/github_readme_images/
  cache_index.sqlite
  objects/<sha256[:2]>/<sha256>.<ext>
```

路径解析：

1. `TECHVAULT_README_IMAGE_ROOT`
2. 否则是数据库文件所在目录下的 `github_readme_images`
3. 数据库路径未知时用 `default_data_dir() / github_readme_images`

`default_data_dir()`：显式的 `TECHVAULT_DATA_DIR` 优先；源码目录使用仓库 `data/`；打包后使用 `%LOCALAPPDATA%\TechVault` 或 `~/TechVault`。若 `data/techvault.sqlite` 还不存在、而用户目录里已有库，启动仍打开那个已有库，不另建空库。图片池跟在实际打开的数据库旁边。`.gitignore` 已忽略 `/data/`。

共享池不在某个仓库的 archive 目录里。只复制一个仓库文件夹不会带上这些图片。本轮不自动清理对象。

## 内容身份与索引

对象身份是图片字节的 SHA-256。SVG 先去掉脚本、`foreignObject`、嵌入对象、事件属性和 `javascript:` / `data:` 链接，再对清理后的字节哈希。扩展名来自魔数，不信任 URL 后缀。

`cache_index.sql` 中的表：

- `objects`：哈希、扩展名、MIME、大小、创建时间、失效标记
- `urls`：规范化 URL、哈希、ETag、Last-Modified、最近验证时间、状态、失败原因
- `image_refs`：`repository_id`、`github_repo_id`、URL、哈希、README 相对路径
- `pool_stats.duplicate_hits`：内容去重命中次数

同一内容、不同 URL 只写一个文件，并增加 `duplicate_hits`。24 小时内且文件可用则跳过网络；之后的归档、更新和补全使用 `If-None-Match` / `If-Modified-Since`。304 只更新验证时间。200 且字节变化时写入新对象并改 URL 映射，旧对象保留。404/403 把 URL 标为失败，不删已有对象。文件缺失或服务时哈希不符则标失效，下次同步重新获取。

下载限制：仅 http/https，禁止用户信息、本机、内网、链路本地和保留地址，最多 5 次重定向并逐跳复查，不发送 `Authorization`，8 MiB 上限，超时 20 秒。并发 4，同一 URL 在途只下载一次，临时文件原子发布后再更新索引。单张失败不让整个 README 或归档任务失败，也不改成功指针。

## URL 解析与 HTML

在 markdown-it 渲染后的 HTML 上走解析器，不在原始 Markdown 上做全局替换。代码块保持文本。相对地址拼到 `https://raw.githubusercontent.com/{owner}/{repo}/{branch}/`。`github.com/.../blob|raw/...` 改写为 raw。`user-attachments` 留在 `github.com`。`javascript:`、`data:`、`file:` 拒绝。

每张图只取一个候选：`src`（不是 `data:`）优先，否则 `data-src` / `data-original`，否则 `srcset` 的第一项，否则 `<picture><source>`。

展示顺序：渲染、把图片变成绝对 URL 或占位、`sanitize_html`、再把已缓存的 https 图片换成 `techvault://app/media/<sha256>.<ext>`。未缓存显示「图片尚未缓存」，失败显示「图片未能加载」。打开详情不下载。

## WebEngine

仍使用现有 `techvault` scheme 的 `app` host，路径 `/media/<64hex>.<ext>`，避免跨源，也避免落到 SPA 的 `index.html`。按哈希读文件，核对 SHA-256，返回对应 MIME。SVG 在输出前再清理一次。不向 React 返回磁盘路径或 Base64。

## 归档与补全

`ArchiveService` 在离线页生成之后调用图片缓存。异常被吞掉，不改变归档结果，也不重新下载 Release 或源码包。

已有项目使用详情里的「补全 README 图片」，对应 `ArchiveService.backfill_readme_images`。它只解析当前 README 并更新图片映射，不改快照、Release、分类或历史目录。命令行需用户主动执行：

```
python -m techvault.readme_images --database <sqlite> --all
python -m techvault.readme_images --database <sqlite> --repository-id <id>
```

启动应用时不会扫描全部项目。

## 统计

`ImagePool.stats()` 返回对象数、字节数、引用数和 `duplicateHits`。没有缓存管理页。
