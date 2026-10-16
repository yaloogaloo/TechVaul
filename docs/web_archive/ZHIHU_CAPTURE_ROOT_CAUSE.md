# 知乎公开文章「需要登录」误判 — 根因报告

日期：2026-10-15  
案例 URL：`https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493`

## 1. 用户失败链（修复前）

1. Slate「保存网页」→ `tasks.startWebArchive` → `WebArchiveWorker` → `archive_url`
2. `fetch_html()` 对规范化 URL 发起 httpx GET
3. 知乎返回 **HTTP 403**，正文约 584 字节，含 `<meta id="zh-zse-ck">`（zse 反爬挑战）
4. `fetch.py` 将 **403 映射为 `NEEDS_LOGIN`**，文案「需要登录后才能访问」
5. 流程终止；未尝试 Chromium；用户在普通浏览器可匿名阅读

## 2. 触发 NEEDS_LOGIN 的代码位置（修复前）

| 位置 | 行为 |
|------|------|
| `web_archive/fetch.py` | `status_code in {401, 403}` → `NEEDS_LOGIN` |
| `web_archive/adapters/zhihu.py` | `_looks_like_login()`：页面任意出现「账号密码登录 / 二维码登录」等 **软登录 UI** 即返回 `NEEDS_LOGIN`（在提取正文之前） |

第二点会在「有正文 + 顶栏登录按钮」的匿名壳页面上误杀；本案例在 HTTP 层已因 403 提前失败，多数用户首先撞上第一点。

## 3. HTTP 与 WebEngine 差异

| | httpx | Qt WebEngine（匿名 Profile） |
|--|-------|------------------------------|
| 状态 | 403 | 页面可加载 |
| 内容 | zse 挑战短 HTML | 完整回答 DOM |
| 登录 UI | 无正文 | `has_login_ui=true` 但正文可读 |
| 实测（本机 2026-10-15） | `ACCESS_RESTRICTED`（修复后） | `body_len=1245`，作者/标题可提取，**BROWSER 归档 COMPLETE** |

根因：**反爬 / JS 挑战，不是「必须登录」**。普通浏览器执行 JS 通过挑战；纯 HTTP 客户端不能。

## 4. 整改内容

1. **状态分类**：403/412/429/zse → `ACCESS_RESTRICTED`；仅 401 或强登录门控 → `NEEDS_LOGIN`
2. **知乎适配器**：先提取正文；软登录文案不再单独判定登录；强门控仅在无可用正文时生效
3. **诊断日志**：规范化 URL、状态码、长度、是否 zse（无 Cookie/Token）
4. **Chromium 捕获窗口**：`WebCaptureWindow` + 独立 Profile；「保存当前页面」走 `publish_from_rendered_html`，**禁止**再 httpx 拉正文
5. **Slate**：增加「在浏览器中打开」；自动失败且可补救时提示「使用浏览器归档」
6. **可选登录会话**：`%LOCALAPPDATA%/TechVault/browser_profiles/zhihu/`；清除会话 API；远程页无 WebChannel

## 5. 已修复的误判

- 403 ≠ 需要登录  
- 「登录 / 扫码登录」按钮 ≠ 需要登录  
- 真正需要登录：登录专用页标题或「登录后才能查看…」等强证据且无正文  

## 6. 匿名浏览器捕获

本机对用户案例：

- DOM 提取成功（`body_len=1245`，`has_login_ui=true`）
- `archive_from_browser_dom` → **COMPLETE**，`capture_mode=BROWSER`
- 产物：`article.html` / `article.md` / `metadata.json`（见 `docs/web_archive/live_case/`）

## 7. 何时真正需要用户登录

- 回答/专栏本身仅登录可见（强门控文案 + 无正文）
- 人机验证需在浏览器中完成（`CAPTCHA_REQUIRED`）
- 付费墙（`PAYWALL`，不绕过）

## 8. 仍无法处理 / 限制

- 纯 HTTP 仍无法越过知乎 zse；公开文默认走浏览器补救  
- MHTML：已请求 `page.save(MimeHtml)`，稳定性因环境而异 → 见验收表  
- SSL 握手噪声（部分资源）不影响主文档提取  
- 懒加载图片在未滚入视口时可能 PARTIAL  

## 9. 复现命令

```bash
# HTTP 诊断（应得到 ACCESS_RESTRICTED，不是 NEEDS_LOGIN）
.\.venv\Scripts\python.exe -c "from techvault.web_archive.fetch import fetch_html; o=fetch_html('https://www.zhihu.com/question/2028250289224922953/answer/2091704662068089493'); print(o.failure_code, o.status_code)"

# 匿名 WebEngine 探测
.\.venv\Scripts\python.exe tools\live_zhihu_browser_probe.py

# 自动化
.\.venv\Scripts\python.exe -m pytest tests/test_web_archive_p2.py tests/test_web_archive.py -q
```
