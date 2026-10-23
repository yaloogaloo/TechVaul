# 视频归档实现

## 后端

- `src/techvault/video/urls.py`：BV 号、Bilibili 链接、短链和 YouTube 链接的规范身份。
- `src/techvault/video/quality.py`：从 extractor 的 `formats` 生成可选项。元数据没有高度时，只保留自动推荐和最佳可用，并注明清晰度未知。
- `src/techvault/video/engine.py`：`YoutubeDL` 的探测和下载。`noplaylist` 默认开启；指定分 P 时改用 `playlist_items`。
- `src/techvault/video/deps.py`：检测 yt-dlp、ejs、ffmpeg、ffprobe、node、deno。结果在进程内缓存。Node 使用 `--version`，FFmpeg 使用 `-version`。
- `src/techvault/video/paths.py`：正式目录、暂存目录、预览图和封面的路径约束。
- `src/techvault/video/service.py`：探测、下载、校验、发布、重复保护和删除。封面下载失败不会让已经检查通过的视频变成失败。
- `migrations/010_video_archive.sql`：视频表。
- `src/techvault/ui/video_worker.py`：在 QThread 里调用服务，并使用独立的数据库连接。
- `src/techvault/ui/bridge/task_bridge.py`：`startVideoProbe`、`startVideoDownload`。下载百分比只在 `DOWNLOADING` 且知道总大小时出现。合并、校验和发布保持不确定进度，避免把合并中的任务显示成已完成。
- `src/techvault/ui/bridge/video_bridge.py`：列表、详情、删除、用系统播放器打开、打开目录、打开原网页。
- `src/techvault/ui/web_scheme.py`：`techvault://app/video-preview/` 和 `techvault://app/video-cover/`。应用壳不允许远程图片，所以封面必须先落到本地再显示。

## 界面

导航增加「视频」。`VideoWorkspace` 沿用现有工具栏、列表、详情和底部 `TaskBar`。

操作顺序是：输入链接或 BV 号，解析，选择实际存在的画质，再下载。已归档且文件还在时，第一次点击不会重新下载，界面给出「仍然重新下载」。删除要再点一次「确认删除」。

GitHub 列表只在 GitHub 任务结束时刷新。视频进度不会带动 GitHub 全表刷新。视频列表也只在解析完成或任务终态时刷新。

取消结束后 `TaskBar` 会按原有规则收起，工作区补一条「已取消」提示。

## 本地播放

`openVideo` 在 Windows 上使用 `os.startfile`，把已发布的文件交给系统默认播放器。没有内置解码器。

## 测试数据位置

真实下载写在 `tmp-video-live/`，该目录已忽略。用户库 `data/techvault.sqlite` 在本轮结束后仍是 1,249,280 字节，最后写入时间是 2026-10-23 14:10:22，早于视频下载。测试通过 `TECHVAULT_DATA_DIR` 或临时库运行，不把样本写进用户库。

## Windows 与打包

当前机器的 FFmpeg 来自 winget 包 `Gyan.FFmpeg` 9.0.2 full build，启用了 GPL 和 version3。这个构建不能当成 LGPL 构建去再分发。安装包阶段需要单独决定 FFmpeg 的构建、版本和许可证文本，并决定是否随应用带上 Node 或 Deno。现在的程序只检测本机已经存在的可执行文件。

yt-dlp 使用 Unlicense。yt-dlp-ejs 随其自身许可证以 Python 依赖安装，不复制上游仓库源码进本仓库。
