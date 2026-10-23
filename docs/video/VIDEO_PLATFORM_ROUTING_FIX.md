# 视频双平台输入解析修复

## 1. 根因

正式入口只有 `parse_video_input`（`src/techvault/video/urls.py`）。前端把输入原样交给 `TaskBridge.startVideoProbe`，没有第二套规则。

修复前的顺序是：

1. 整段文本必须完整匹配 BV 号。
2. 文本里没有 `://` 时，直接抛出 `INVALID_BV`：「这不是有效的 BV 号」。
3. 其后的 YouTube / Bilibili 域名判断不会执行。

`youtube.com/watch?reload=9&v=0Z-vhBvBmUY` 没有协议，也不是 BV 号，所以在域名识别之前就失败了。带 `https://` 的同一链接本来可以按 `v` 参数解析，和参数顺序无关。

YouTube 支持已经接在正式的 `VideoArchiveService` → `YtDlpEngine` 上，不是只存在于测试或命令行。错误出在 URL 进入引擎之前。

## 2. 修改

只改了 `src/techvault/video/urls.py` 和 `tests/test_video_archive.py`。

`parse_video_input` 仍是唯一入口。新的顺序：

1. 去掉首尾空白。
2. 只有整段就是合法 BV 号时才走 Bilibili 身份。
3. 已有 `http://` 或 `https://` 的文本按网址解析。
4. 没有协议、但主机名形式合法的文本补上 `https://`。`input_kind` 记为 `schemeless`。
5. 用 `urlsplit` / `parse_qs` 读取主机、路径和查询。YouTube 的视频 ID 取查询参数 `v`，不看参数顺序。
6. 按域名交给 Bilibili 或 YouTube 解析器，结果仍交给原来的 `YtDlpEngine`。
7. 看起来像 BV 但不合法的文本才返回「这不是有效的 BV 号」。其他无法识别的文本不再使用这句文案。

## 3. 用户示例

输入：

`youtube.com/watch?reload=9&v=0Z-vhBvBmUY`

结果：

| 字段 | 值 |
|---|---|
| platform | youtube |
| input_kind | schemeless |
| platform_video_id | `0Z-vhBvBmUY` |
| canonical_url | `https://www.youtube.com/watch?v=0Z-vhBvBmUY` |

yt-dlp 返回的真实信息：标题「对话孙宇晨：年轻人如何抓住AI时代的机会？」，作者 Alan Shao，时长 15749 秒。可用画质为自动推荐 1078p、最佳可用 1078p、720p、480p，没有编造 1080p。文件大小未知。正式工作区截图是 `docs/video/evidence/09_youtube_schemeless.png`。这支视频超过 4 小时，本轮没有下载它。

## 4. 同一路径上的真实下载

隔离目录 `tmp-video-route/archive`。用户库没有写入。

| 输入 | 画质 | ffprobe |
|---|---|---|
| `youtube.com/watch?reload=9&v=jNQXAC9IVRw` | 自动，实际 240p | h264 320×240 + aac，19.06 秒，635,505 字节 |
| `BV1XTad6oEWe` | 480p | h264 852×480 + aac，480 秒，18,848,926 字节 |

BV 号在 YouTube 解析之后再次解析，工作区显示 Bilibili、1080p 自动推荐和封面。截图是 `docs/video/evidence/10_bv_after_youtube.png`。无协议的 `www.bilibili.com/video/...?...p=2` 和 `b23.tv/...` 仍能得到原来的分 P 与 BV 身份。

## 5. 测试与构建

全量 pytest：**350 passed**。临时 `LOCALAPPDATA`，未设置 `TECHVAULT_DATA_DIR`。前端 `npm run build` 通过，产品包仍是 `index-BmQ10ZZF.js` 与 `index-DgE_O73y.css`。界面没有改解析规则。

## 6. 验收

| 项目 | 状态 |
|---|---|
| YOUTUBE INPUT RECOGNITION | PASS |
| SCHEMELESS URL | PASS |
| QUERY PARAMETER HANDLING | PASS |
| PLATFORM ROUTING | PASS |
| YT-DLP YOUTUBE EXTRACTION | PASS |
| BILIBILI BV REGRESSION | PASS |
| LIVE YOUTUBE DOWNLOAD | PASS |
| LIVE BILIBILI DOWNLOAD | PASS |
| EXISTING FEATURES REGRESSION | PASS |
| OVERALL | PASS |

## 7. 尚未关闭

- `0Z-vhBvBmUY` 只做了元数据解析，没有下载这支 4 小时视频。
- YouTube 封面仍可能因 `i.ytimg.com` 连接超时而缺失。这次工作区截图里没有这支视频的封面。
- 时长在界面上按「分:秒」显示，15749 秒显示为 262:29，没有换成小时。
- FFmpeg 与 Node 仍是本机检测，不随应用分发。
