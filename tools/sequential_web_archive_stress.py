"""Controlled sequential capture stress: Zhihu ↔ Xiaoheihe (fixtures, no live spam).

Verifies publish path does not hang and article count grows as expected.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURES = ROOT / "tests" / "fixtures" / "web_archive"
OUT = ROOT / "docs" / "web_archive" / "sequential_stress.json"


def main() -> int:
    from techvault.app.bootstrap import prepare_context
    from techvault.web_archive.models import ArchiveStatus
    from techvault.web_archive.service import WebArchiveService

    tmp = Path(tempfile.mkdtemp(prefix="tv-seq-"))
    context = prepare_context(tmp / "db.sqlite", tmp / "archive")
    service = WebArchiveService(context.connection)

    zhihu_html = (FIXTURES / "zhihu_answer.html").read_text(encoding="utf-8")
    xhh_html = (FIXTURES / "xiaoheihe_post_rendered.html").read_text(encoding="utf-8")
    sequence = [
        ("zhihu", "https://www.zhihu.com/question/1001/answer/2002", zhihu_html, "知乎样例", "答主"),
        (
            "xiaoheihe",
            "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477",
            xhh_html,
            "如何获得一个免费的数据库+后端",
            "小稿的AI日记",
        ),
        ("zhihu", "https://www.zhihu.com/question/1001/answer/2003", zhihu_html, "知乎样例2", "答主"),
        (
            "xiaoheihe",
            "https://www.xiaoheihe.cn/app/bbs/link/aabbccddeeff",
            xhh_html,
            "小黑盒样例2",
            "作者B",
        ),
    ]

    results = []
    try:
        with respx.mock:
            respx.route(host="pic1.zhimg.com").mock(
                return_value=httpx.Response(200, content=b"PNG")
            )
            respx.route(host="www.zhihu.com").mock(return_value=httpx.Response(403))
            respx.route(host="www.xiaoheihe.cn").mock(return_value=httpx.Response(403))
            for site, url, html, title, author in sequence:
                body = "<p>" + ("正文内容足够长用于归档验证。" * 4) + "</p>"
                r = service.archive_from_browser_dom(
                    url=url,
                    page_html=html,
                    final_url=url,
                    title=title,
                    author=author,
                    body_html=body,
                )
                results.append(
                    {
                        "site": site,
                        "status": r.status,
                        "article_id": r.article_id,
                        "ok": r.status == ArchiveStatus.COMPLETE and r.article_id is not None,
                    }
                )
        items = service.list_articles(limit=20)
        site_keys = sorted({i.site_key for i in items})
        report = {
            "tmp": str(tmp),
            "results": results,
            "list_count": len(items),
            "site_keys": site_keys,
            "all_ok": all(x["ok"] for x in results),
            "pass_sequential": all(x["ok"] for x in results)
            and len(items) >= 4
            and "zhihu" in site_keys
            and "xiaoheihe" in site_keys,
        }
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["pass_sequential"] else 1
    finally:
        context.close()


if __name__ == "__main__":
    raise SystemExit(main())
