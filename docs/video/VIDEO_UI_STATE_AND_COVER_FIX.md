# 视频工作区：状态隔离、标题约束与封面补全

本轮只修三件事：左侧标题最多两行、右侧预览与归档互斥、已归档视频在标题下显示本地封面。没有改 Slate 信息架构，没有加播放器，也没有改 yt-dlp 下载与合并流程。封面失败不会把已归档视频改回失败。

验收使用隔离目录 `tmp-video-ui`，没有写入 `data/techvault.sqlite`。

## 1. 为什么解析结果和已归档详情会叠在一起

`VideoWorkspace` 原来同时渲染两块：

- `preview` 有值就画待下载预览（封面、标题、预计大小）。
- `detail` 有值就在下面再画已归档详情。

点击左侧只更新 `selectedId` 和 `detail`，不清除 `preview`。所以先解析《巫师3》（`BV1q64f6PEhx`），再点《旧手机畅玩 Docker》（`BV1XTad6oEWe`）时，上半部分仍是 A，下半部分已是 B。输入框里留下的 BV 号也会让人以为当前下载对象还是 A。

## 2. 原来的 state 根因

相关状态是 `preview`、`detail`、`selectedId`，以及全局 `task`。

问题出在渲染条件和任务回调，不在数据库：

- 两套 JSX 没有互斥条件，只要两个 state 都非空就会一起出现。
- 列表点击不进入另一种模式，预览对象一直留着。
- `task.status === 'ready'` 时无条件写入 `preview`。用户已经点了别的视频，迟到的解析结果仍会盖回来。
- 下载按钮读的是最近一次 `preview`，和当前选中的归档记录不是同一个对象。

## 3. 互斥状态

右侧只有一种模式：`empty`、`preview`、`archived`。同一时刻只渲染其中一块。

| 操作 | 行为 |
|---|---|
| 点击「解析视频」 | 记下本次 `taskId`，允许接收这次结果，清掉上一次预览。成功后进入 `preview`。 |
| 点击左侧已归档视频 | `acceptProbe = false`，进入 `archived`，只加载这个 `video_id` 的记录。 |
| 归档模式下仍留着预览 | 工具栏画质和「下载视频」禁用。另给「返回解析结果」，点下去才切回 `preview`。 |
| 下载 | 只有 `preview` 模式可用，地址用当前预览的 `canonicalUrl`。 |
| 下载成功 | 清掉预览，进入 `archived`，选中返回的 `videoItemId`。 |
| 解析失败 | 清掉预览，显示这次的错误。不改已打开的归档记录。 |

迟到结果被丢掉的条件：`acceptProbe` 已关，或 `task.taskId` 不是当前这次解析。选中 id 用 ref 比对，后返回的详情若不是当前 id 就不写入。

本轮实机顺序是：解析 A 成功，再点 B，再点 C（Me at the zoo）。没有再注入一条故意延迟的解析回调，保护靠上述任务号和 `acceptProbe`。本轮也没有重新下载约 695 MB 的《巫师3》；下载成功后的切换写在同一条 `task.status === 'success'` 分支里。

## 4. 长标题

列表标题仍是数据库里的完整字符串，只在 CSS 里限制显示：

- 标题容器 `min-width: 0`、`overflow: hidden`。
- `display: -webkit-box`、`-webkit-line-clamp: 2`。
- `word-break: break-all`，避免无空格长英文把行撑开。
- 元素带 `title`，悬停可见全文。
- 缩略图固定 96px 宽、`aspect-ratio: 16 / 9`、`object-fit: cover`。

行高最多到两行标题，短标题保持一行。详情标题同样最多三行，`max-width: 720px`。

## 5. 封面来源

同一个 `thumbnail_relpath` 同时供列表和详情使用，地址是 `techvault://app/video-cover/...`，不把磁盘绝对路径交给前端，也不走 Base64。

优先级：

1. 下载发布时，若 yt-dlp 给出封面且 HTTP 内容是有效 JPEG/PNG/WebP，写入该视频目录的 `cover.jpg`（或实际后缀）。
2. 已有本地封面且文件头有效，直接复用，不再请求站点，也不再跑 FFmpeg。
3. 没有封面、下载失败、404，或本地文件损坏时，从已经落盘的视频提取第一帧。

封面失败只是没有图。视频文件大小、画质和 `COMPLETED` 状态保持不变。

## 6. FFmpeg 第一帧

`_extract_first_frame` 使用本机 `ffmpeg`，不重新编码视频：

```
ffmpeg -y -i <video> -frames:v 1 -q:v 3 .cover.extract.jpg
```

临时文件必须带 `.jpg`，否则 FFmpeg 无法选择输出格式。成功后原子替换为 `cover.jpg`。若第一帧命令失败，再用 `-ss 0.04` 重试一次，用来跳过容器开头无法解码的时间戳。超时 20 秒。黑场也按这一帧保存。

发布阶段在下载线程里调用。补全阶段用 `VideoCoverWorker`（`QThread`），不在 Qt GUI 线程跑 FFmpeg。找不到 ffmpeg 或提取失败时返回 false，不抛到归档状态上。

## 7. 已有视频补全

打开归档详情时，若文件在、但没有有效封面，前端调用 `ensureCover`。工作线程只读本地视频，写出旁边的 `cover.jpg`，再更新 `thumbnail_relpath`。

不会在启动时扫描全部视频，不会重新下载，也不会改 mp4。第二次打开同一条记录时封面已有效，不再提取。

实机例子：隔离库里的 `jNQXAC9IVRw`（Me at the zoo）原来只有 `Me at the zoo.240p.mp4`（635,505 字节）和 `metadata.json`。打开详情后生成 `cover.jpg`（18,261 字节），mp4 大小未变。列表缩略图和详情用的是同一张图。

## 8. 修改文件

- `frontend/src/features/video/VideoWorkspace.tsx`
- `frontend/src/features/video/VideoWorkspace.module.css`
- `src/techvault/video/service.py`
- `src/techvault/ui/bridge/video_bridge.py`
- `src/techvault/ui/video_worker.py`
- `tests/test_video_archive.py`
- `tools/slate_capture_video_ui.py`
- `.gitignore`（忽略 `tmp-video-ui`、`tmp-cover-debug`）

资源协议沿用 `web_scheme.py` 里已有的 `video-cover/`，没有另建图片库。

## 9. 实机截图

均来自隔离库上的 TechVault 窗口，不是静态 HTML。

| 文件 | 内容 |
|---|---|
| `docs/video/evidence/11_long_titles.png` | 10 字、约 40 字、100 字、中英混合、无空格长英文 |
| `docs/video/evidence/12_preview_witcher.png` | 只显示 `BV1q64f6PEhx` 的解析预览 |
| `docs/video/evidence/13_archived_docker.png` | 点选 `BV1XTad6oEWe` 后只显示该视频和它自己的封面 |
| `docs/video/evidence/14_first_frame.png` | Me at the zoo 的本地第一帧 |
| `docs/video/evidence/15_960x640.png` | 960×640 下封面按宽度缩放 |

`12` 的右侧是《巫师3》标题、B 站封面和「将使用 自动推荐（1080p mp4）· 695.5 MB」。`13` 里这段文字消失，标题、封面、18.0 MB、480p 都属于 Docker 那条记录。输入框仍留着 `BV1q64f6PEhx`，下载按钮已禁用，并出现「返回解析结果」。

## 10. 测试与未关闭项

- pytest：353 passed（约 93 秒）。临时 `LOCALAPPDATA`，未设置 `TECHVAULT_DATABASE`。
- 前端：`tsc -b && vite build` 通过。产物 `dist/assets/index-BUMx-AiR.js`、`dist/assets/index-BvHhKC9n.css`。
- 封面单测：缺封面时写出 JPEG 且视频字节不变；已有封面不重写；损坏封面会重做；没有 ffmpeg 时不生成封面且状态仍是已归档；封面 HTTP 404 不写入假图片。
- 实机解析了 `BV1q64f6PEhx`，没有下载它。B 站与 YouTube 的 URL 识别仍在原有测试里。

未关闭：

- 来源地址在窄栏里会从中间折行，没有横向溢出。
- 没有再跑一次完整下载来给「下载成功后只留归档详情」单独截图。
- 没有人为注入一条迟到的解析响应；切换后的丢弃逻辑在任务回调里。
- YouTube 的 `i.ytimg.com` 封面请求仍可能超时。归档后改走本地第一帧，不依赖那次远程请求。
- 时长仍显示为分:秒，没有小时位。一次只跑一个视频任务。FFmpeg 使用本机安装，不随应用打包。

## 验收矩阵

| 项目 | 状态 |
|---|---|
| VIDEO LIST TITLE CLAMP | PASS |
| FIXED ROW DENSITY | PASS |
| PREVIEW / ARCHIVED ISOLATION | PASS |
| SELECTED VIDEO DATA CONSISTENCY | PASS |
| ASYNC STALE RESPONSE PROTECTION | PASS |
| VIDEO DETAIL COVER | PASS |
| LOCAL COVER PERSISTENCE | PASS |
| FFMPEG FIRST FRAME FALLBACK | PASS |
| EXISTING VIDEO COVER BACKFILL | PASS |
| OFFLINE COVER DISPLAY | PASS |
| BILIBILI / YOUTUBE REGRESSION | PASS |
| SLATE VISUAL CONSISTENCY | PASS |
| OVERALL | PASS |
