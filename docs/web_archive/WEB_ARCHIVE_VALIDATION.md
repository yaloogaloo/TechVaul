# TechVault 网页归档 — P1 验收记录

日期：2026-10-15

## 自动化测试

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_web_archive.py tests/ -q
```

结果：**284 passed**（含既有 GitHub / 知识 / 设置回归）。

覆盖要点：

1. 知乎回答 / 专栏 URL 识别  
2. 问题主页拒绝伪成功  
3. 登录页检测  
4. 通用 Readability  
5. 图片本地化与缺失 → PARTIAL  
6. 失效链接、取消、重复保存版本、删除隔离  
7. DB migration 008  

## 真实归档目录树（业务流生成）

```text
archive/web/
└── zhihu.com/
    └── 如何高效学习编程？-<short-id>/
        ├── article.html
        ├── article.md
        ├── metadata.json
        └── media/
            └── <hash>_v2-demo.png
```

样本副本：`docs/web_archive/sample_tree/sample_article/`

## UI 截图

由 `tools/slate_capture_web.py` 生成（隔离临时库）：

- `docs/web_archive/screenshots/web_workspace_1440x900.png`
- `docs/web_archive/screenshots/web_reader_1440x900.png`
- `docs/web_archive/screenshots/web_reader_960x640.png`
- `docs/web_archive/screenshots/web_info_1440x900.png`

## 离线阅读验收说明

归档成功后正文与图片写入本地 `article.html` / `media/`。  
`getArticle` 将相对 `media/` 改写为 `file://` URI，供 WebEngine 在断网条件下显示。  
测试中断言归档 HTML **不再依赖** `zhimg.com` 远程地址（已本地化时）。

**未对真实用户外网知乎账号执行登录抓取。** 外网真实链接验证：PENDING PRODUCT REVIEW（需用户提供可公开访问样本）。

## 验收状态

| 项目 | 状态 | 证据 |
|------|------|------|
| URL CAPTURE | PASS | `test_url_recognition` / `test_dead_link` |
| ZHIHU ARTICLE | PASS | `test_zhihu_answer_and_article_extract` + pipeline |
| ZHIHU ANSWER | PASS | 同上 |
| IMAGE LOCALIZATION | PASS | pipeline + partial 用例 |
| OFFLINE READING | PASS | `article.html` + local media + getArticle rewrite |
| DATABASE INTEGRATION | PASS | migration 008 + CRUD 测试 |
| SLATE UI | PASS | Web 工作区 + Bridge + 截图脚本 |
| EXISTING FEATURES REGRESSION | PASS | 284 passed |
| HIGH FIDELITY SNAPSHOT | NOT CLOSED | P2 |
| OVERALL P1 | PASS | 闭环可用；外网真实知乎待产品验收 |
| VISUAL ACCEPTANCE | PENDING PRODUCT REVIEW | |

## 已知限制

- 中文 FTS 依赖 LIKE 辅路，极短检索词可能偏宽。  
- 部分知乎反爬 / 强登录页在 Mode A 下会失败（预期），需 P2 浏览器捕获。  
- 「网页快照」标签为 P2 占位说明。  
