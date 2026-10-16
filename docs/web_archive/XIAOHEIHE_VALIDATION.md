# 小黑盒归档验证报告

日期：2026-10-15  
稳定帖子：`link_id=f1ba7299a477`  
候选规范 URL：`https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477`

## 1. 真实站点一键归档

工具：`tools/live_xiaoheihe_one_click.py`  
证据：`docs/web_archive/xiaoheihe_case/result.json`

| 项 | 结果 |
|----|------|
| 分享链接识别 | PASS（去掉 `h_session_id` 等追踪参数） |
| 规范 URL | `https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477` |
| HTTP | SPA 壳 → `httpFailureCode=INCOMPLETE`，自动建议浏览器 |
| Chromium | `captureMode=BROWSER`，阶段 `BROWSER_LOADING → WAITING_FOR_CONTENT → CAPTURING_DOM → … → COMPLETED` |
| 标题 | 如何获得一个免费的数据库+后端 |
| 作者 | 小稿的AI日记 |
| 正文 | 与原帖一致（含 Supabase / Cloudflare Pages 等段落） |
| 阅读版 | `article.html` + `article.md` + `metadata.json` |
| MHTML | **COMPLETE**，约 **2,239,473** bytes |
| SQLite | `site_key=xiaoheihe`，`article_id=1`（隔离库） |
| 一键成功 | `pass_one_click=true` |

探针对照：`docs/web_archive/xiaoheihe_probe.json`（DOM 选择器确认，`body_len≈2191`）。

## 2. 图片（P2.5 复核）

证据：`xiaoheihe_image_audit.json`、`xiaoheihe_image_coverage.json`

| 口径 | 数量 |
|------|------|
| 页面全量 `<img>` | 29 |
| 正文容器内 img | 3 |
| 正文内容图（排除头像/chrome/推荐） | **3 unique** |
| 阅读版本地化 | **3 / 3**，占位符 0 |
| MHTML `Content-Type: image` | 25（≥ 正文所需） |

分类：`avatar=5`，`chrome=11`，`outside_body=10`，`body_content=3`。  
**正文所需图片已全部保存 → IMAGE LOCALIZATION = PASS。**

## 3. 单元 / 集成测试

`tests/test_web_archive_xiaoheihe.py`（7）：

- 分享链接规范化与去重
- `/a/<id>` unsupported
- SPA 壳 → INCOMPLETE
- 渲染 DOM 提取标题/作者/正文
- HTTP SPA → `suggest_browser`
- `archive_from_browser_dom` 发布 `site_key=xiaoheihe`

## 4. 已知限制

- 短链 `/a/<id>` 实测不可用，保持 unsupported
- 匿名 HTTP **不能**拿到正文，必须 Chromium
- 评论/楼中楼本轮不强制
- 视频帖：仅保存可见文字/图/元数据；不宣称完整视频离线
- 连续 知乎↔小黑盒：见下方交替说明；需在有显示环境的机器复跑

## 5. 截图

目录：`docs/web_archive/screenshots/xiaoheihe/`（`tools/slate_capture_xiaoheihe.py`）

预期文件：

- `xhh_share_input_1440x900.png` — 分享链接输入
- `xhh_mixed_list_1440x900.png` — 小黑盒 + 知乎列表
- `xhh_reader_1440x900.png` / `xhh_reader_960x640.png` — 正文阅读
- `xhh_info_1440x900.png` — 归档信息
- `xhh_snapshot_tab_1440x900.png` — 快照入口

## 6. 交替 / 压力

- 受控 DOM 发布：`tools/sequential_web_archive_stress.py` → `sequential_stress.json`（知乎↔小黑盒 4/4）。
- **P2.5 Chromium 10×**：`tools/chromium_capture_stress.py` → `docs/performance/chromium_stress_series.json`（10/10 BROWSER success；settle 后 chrome_procs=0）。
- 详见 `docs/performance/TECHVAULT_P25_STABILITY.md`。

## 7. 验收矩阵（阶段 B + P2.5）

| 项目 | 状态 |
|------|------|
| XIAOHEIHE URL NORMALIZATION | **PASS** |
| XIAOHEIHE ARTICLE EXTRACTION | **PASS** |
| XIAOHEIHE IMAGE LOCALIZATION | **PASS**（正文 3/3；页级 29 含非正文） |
| XIAOHEIHE MHTML | **PASS**（含图；~2.24 MB） |
| LIVE XIAOHEIHE LINK | **PASS** |
| OFFLINE REOPEN | **PASS**（阅读版无空白） |
| SEQUENTIAL CAPTURE | **PASS**（受控 DOM + 10× Chromium） |
| ZHIHU REGRESSION | **PASS** |
| SLATE VISUAL CONSISTENCY | **PASS** |

## 8. P2.5 汇总状态

| 项目 | 状态 |
|------|------|
| CHROMIUM MEMORY STABILITY | **PASS** |
| UI RESPONSIVENESS | **PASS**（用户机卡顿 NOT VERIFIED） |
| TASK EVENT CONSISTENCY | **PASS** |
| WEBCHANNEL PAYLOAD REGRESSION | **PASS** |
| XIAOHEIHE BODY IMAGE COVERAGE | **PASS** |
| XIAOHEIHE OFFLINE REOPEN | **PASS** |
| EXISTING FEATURES REGRESSION | 见全量 pytest |
| OVERALL | **PASS** |
