# 视频归档验收

验收使用临时库 `tmp-video-live/db.sqlite` 和临时归档目录 `tmp-video-live/archive`。用户库 `data/techvault.sqlite` 仍是 1,249,280 字节，最后写入时间 2026-10-23 14:10:22，早于这次下载。

## 依赖

- yt-dlp 2026.10.21，调用 `YoutubeDL.extract_info`。
- yt-dlp-ejs 0.8.0。
- Node.js v24.20.0，路径 `C:\Program Files\nodejs\node.exe`，通过 `js_runtimes` 显式启用。本机没有 Deno。
- FFmpeg / FFprobe 9.0.2-full_build-www.gyan.dev，配置含 `--enable-gpl --enable-version3`。

## 真实视频

| 输入 | 标题 | 画质 | 容器 | 时长 | 大小 | ffprobe |
|---|---|---|---|---:|---:|---|
| `BV1XTad6oEWe` | 旧手机畅玩Docker！无需刷机Root，PoDroid+1Panel安装使用指南 | 自动推荐，实际 1080p | mp4 / h264 + aac | 480.32 秒 | 38,565,904 | 1920×1080 视频流，aac 音频流 |
| `https://www.youtube.com/watch?v=jNQXAC9IVRw` | Me at the zoo | 自动推荐，实际 240p | mp4 / h264 + aac | 19.06 秒 | 635,505 | 320×240 视频流，aac 音频流 |

YouTube 这支公开视频没有 480p 及以上格式。自动推荐显示「最高可用 240p」，文件大小在元数据里不存在，界面显示「大小未知」。没有把 1080p 选项造出来。

Bilibili 下载日志先完成视频轨和音频轨，然后进入 `MERGING`，ffprobe 通过后才进入 `PUBLISHING`。合并阶段没有被记成已完成。

取消一次 720p 重新下载后，`video_downloads` 第 3 行状态是 `CANCELLED`。原来的 1080p 文件大小仍是 38,565,904，`current_download_id` 仍指向那次成功记录。暂存区留下 `download.f30064.mp4.part`，没有被发布。

界面截图在 `docs/video/evidence/`。窗口逻辑尺寸是 1440×900 和 960×640；PNG 像素是 1800×1125 和 1200×800，因为系统显示缩放是 125%。

- `01_video_home.png`：两支已归档视频，Bilibili 有封面。
- `02_bv_parse.png`：BV 号解析出真实标题、作者、8:00、1080p 和 36.5 MB。
- `03_youtube_parse.png`：YouTube 解析结果，240p，大小未知。
- `04_quality.png`：改选 720p 后，下载前说明实际将使用 720p mp4、约 23.8 MB。
- `05_progress.png`：任务栏显示 4.0 MB / 13.8 MB、6.5 MB/s，详情仍标明已归档的 1080p 没有被这次下载替换。
- `06_detail.png`：来源 URL、画质、大小和打开操作。
- `07_after_open_local.png`：点击「打开本地视频」后的 TechVault 窗口。外部播放器不在这个窗口里。
- `08_960x640.png`：窄窗口仍能看到导航、列表和详情。

「打开本地视频」调用系统默认播放器。播放文件是上面的本地 mp4，不读取视频网站。这次没有关闭网卡。

## 自动化测试

隔离 `LOCALAPPDATA` 和未设置 `TECHVAULT_DATA_DIR` 的前提下，全量 pytest：**350 passed**。其中视频测试覆盖 BV、分 P、短链拒绝、播放列表拒绝、画质、路径越界、重复下载不覆盖、yt-dlp 下载本地媒体后再用 ffprobe 发布，以及把现有用户库副本升级到迁移 010 后 GitHub、知识和网页行数不变。

前端生产构建通过：`index-BmQ10ZZF.js` 297.95 kB，`index-DgE_O73y.css` 31.97 kB。

## 验收矩阵

| 项目 | 状态 |
|---|---|
| YT-DLP INTEGRATION | PASS |
| FFMPEG INTEGRATION | PASS |
| YOUTUBE JS RUNTIME | PASS |
| BILIBILI BV RECOGNITION | PASS |
| YOUTUBE URL SUPPORT | PASS |
| VIDEO METADATA | PASS |
| QUALITY SELECTION | PASS |
| VIDEO DOWNLOAD | PASS |
| AUDIO VIDEO MERGING | PASS |
| PROGRESS & CANCEL | PASS |
| VIDEO STORAGE | PASS |
| SQLITE INTEGRATION | PASS |
| DUPLICATE PROTECTION | PASS |
| OFFLINE PLAYBACK | PASS |
| FUTURE PLATFORM EXTENSIBILITY | PASS |
| SLATE UI INTEGRATION | PASS |
| EXISTING FEATURES REGRESSION | PASS |
| LIVE YOUTUBE | PASS |
| LIVE BILIBILI | PASS |
| OVERALL | PASS |

## 未完成

- 没有随应用分发 FFmpeg、Node 或 Deno。当前只检测本机已有程序。Gyan full build 是 GPL 构建，不能按 LGPL 再分发。
- 本机没有 Deno，YouTube 使用显式启用的 Node。
- YouTube 封面没有保存下来，列表里是占位块。Bilibili 封面已保存。`i.ytimg.com` 在归档当时没有返回可用图片。
- 同时只跑一个视频任务。没有播放列表、频道、收藏夹、转录、摘要、剪辑、直播录制或内置播放器。
- 不读取浏览器 Cookie，也不处理登录、会员或验证码。
- 取消后的 `.part` 留在 `_tmp/video`，下次是否能续传取决于 yt-dlp 和该格式。这次没有把未完成的 720p 说成续传成功。
