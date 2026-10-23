# 视频归档架构

TechVault 不解析视频流。YouTube 和 Bilibili 的地址、分片、签名和音视频合并都交给 yt-dlp 与 FFmpeg。本模块只识别输入、选择已返回的格式、管理任务，并把检查过的文件发布到归档目录。

## 调用关系

```text
VideoWorkspace
  → TaskBridge.startVideoProbe / startVideoDownload
  → VideoDownloadWorker
  → VideoArchiveService
  → YtDlpEngine
  → yt_dlp.YoutubeDL
  → FFmpeg / FFprobe
  → VideoPathLayout + video_items / video_downloads
```

列表、打开、删除走 `VideoBridge`，不占用下载线程。下载和解析占用现有的单一任务槽：已有 GitHub 或网页任务时，视频任务返回 `BUSY`。

## 锁定的下载器

| 组件 | 版本 | 用法 |
|---|---|---|
| yt-dlp | 2026.10.21 | `YoutubeDL.extract_info`、`progress_hooks`、`postprocessor_hooks` |
| yt-dlp-ejs | 0.8.0 | 随 Python 包导入，不在启动时联网升级 |
| Node.js | 24.20.0 | 显式写入 `js_runtimes={"node": {"path": ...}}` |
| Deno | 未安装 | 不额外安装。yt-dlp 的默认运行时是 Deno，因此这里必须显式启用 Node |
| FFmpeg / FFprobe | 9.0.2 full_build（Gyan） | `--enable-gpl --enable-version3`。只认真正的可执行文件 |

`pyproject.toml` 把 yt-dlp 和 yt-dlp-ejs 钉在上述版本。启动时只做检测，不执行升级。

依赖检测读当前进程 PATH，再读 Windows 用户和系统的 Path 注册表。这样刚由 winget 写入用户 Path、但当前进程还没刷新的 FFmpeg 也能被找到。只安装名为 `ffmpeg` 的 Python 包不会被当成 FFmpeg。

YouTube 解析需要 yt-dlp-ejs 和 Deno 或 Node。Bilibili 不要求 JavaScript 运行时。两条路径都要求 FFmpeg 和 FFprobe 才能把文件标成已归档。

## 输入识别

规则集中在 `techvault.video.urls`。没有按平台再写一套下载器。

- `BV1XTad6oEWe` 这种合法 BV 号变成 `https://www.bilibili.com/video/BV1XTad6oEWe`。字符集是 `BV` 加 10 位 Base58，任意字符串不会被当成 BV 号。
- 完整链接、`m.bilibili.com` 和 `p=2` 保留分 P。`p=1` 与不写 `p` 是同一个分集。指定 `p=2` 时 yt-dlp 使用 `playlist_items=2`，不会改去下 P1。
- `b23.tv` 最多跟随 5 次重定向，目标必须是公网地址，最终主机必须是 `bilibili.com` 或其子域。
- YouTube 的 `watch`、`youtu.be`、`shorts`、`m.youtube.com` 都收成 `https://www.youtube.com/watch?v=<11位ID>`，去掉分享参数。
- 纯播放列表、频道、收藏夹、合集直接拒绝。单个视频链接里附带的 `list` 参数被丢掉，只处理这一个视频。
- 生产调用的 `allowed_extractors` 只包含 `youtube`、`Bilibili`、`BiliBili`、`BiliIntl`。不启用 GenericExtractor。

## 画质

选项只从 yt-dlp 返回的格式里产生：自动推荐、最佳可用、1080p、720p、480p。自动推荐取不超过 1080p 的最高画面。指定档位如果实际高度不够，就不提供这个选项，并在自动推荐上写明实际高度，例如这支 YouTube 样本只有 240p。

视频轨是 mp4、音频轨是 m4a 时，用 yt-dlp 的 `merge_output_format=mp4` 做封装。否则保留 mkv。不做有损转码。两边大小都未知时，界面显示「大小未知」。

## 文件

```text
<archive_root>/videos/<platform>/<id>/[pN/]<标题>.<高度>p.<ext>
<archive_root>/videos/<platform>/<id>/cover.jpg
<archive_root>/videos/<platform>/<id>/metadata.json
<archive_root>/_tmp/video/<platform>/<id>/<part>/
```

正式目录用平台和视频 ID，不用任务 ID。标题做 Windows 文件名清理。下载先落在暂存目录，ffprobe 确认有视频流、时长大于 0，并且在源格式带音频时也有音频流，之后才原子发布。`.part` 不算已归档。失败和取消不替换已经成功的文件。

`metadata.json` 只保存平台、ID、标题、作者、时长、规范 URL、画质和编码。不写入 yt-dlp 的临时媒体地址、签名或 Cookie。

## 数据

迁移 `010_video_archive.sql` 新增 `video_items` 和 `video_downloads`。身份是 `platform + platform_video_id + part_key`。不复用 GitHub 或网页表。同一视频已有完成文件时，再次下载必须显式选择重新下载。新的失败记录不会把 `current_download_id` 从已完成记录上挪走。

## 取消

Python API 的 `DownloadCancelled` 用来停止当前 `YoutubeDL`。FFmpeg 后处理期间，只对这一次 `Popen` 的进程树执行 `taskkill /PID /T /F`，不扫描系统里的其他 FFmpeg。用户 URL 不经过 `shell=True`。取消后暂存区的 `.part` 留在 `_tmp/video`，不发布。

P1 同时只有一个视频任务。每个任务有自己的 `task_id`，旧任务回调不能改写新任务状态。

## 以后加平台

1. 确认 yt-dlp 已有对应 extractor。
2. 在 `urls.py` 增加识别和规范 URL。
3. 必要时给 `YtDlpEngine.build_options` 增加该平台的参数。
4. 继续用现有的任务、数据库、目录和视频工作区。

本轮没有加入 Vimeo 或其他平台，也没有做插件框架。
