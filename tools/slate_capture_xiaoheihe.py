"""Slate screenshots for Xiaoheihe archive (isolated DB; real live case when present)."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SHOT = ROOT / "docs" / "web_archive" / "screenshots" / "xiaoheihe"
FIXTURES = ROOT / "tests" / "fixtures" / "web_archive"
LIVE = ROOT / "docs" / "web_archive" / "xiaoheihe_case" / "archive"


def seed(db: Path, archive: Path) -> None:
    import re

    from techvault.app.bootstrap import prepare_context
    from techvault.web_archive.service import WebArchiveService

    context = prepare_context(db, archive)
    service = WebArchiveService(context.connection)

    # Zhihu sample for mixed list.
    zhihu_url = "https://www.zhihu.com/question/1001/answer/2002"
    zhihu_html = (FIXTURES / "zhihu_answer.html").read_text(encoding="utf-8")
    with respx.mock:
        respx.get(zhihu_url).mock(return_value=httpx.Response(200, text=zhihu_html))
        respx.get("https://pic1.zhimg.com/v2-demo.png").mock(
            return_value=httpx.Response(200, content=b"PNG-DEMO")
        )
        z = service.archive(zhihu_url)
    if z.article_id is None:
        raise RuntimeError(z.message)

    url = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
    title = "如何获得一个免费的数据库+后端"
    author = "小稿的AI日记"
    page_html = (FIXTURES / "xiaoheihe_post_rendered.html").read_text(encoding="utf-8")
    body_html = (
        "<p>如果盒友想要搞一个带登录、还要存数据的小网站，没必要上来就考虑服务器。</p>"
        "<p>推荐一下 Supabase。一个免费的数据库，后端也顺便有了。</p>"
    )

    live_html = list(LIVE.rglob("article.html")) if LIVE.exists() else []
    live_meta = list(LIVE.rglob("metadata.json")) if LIVE.exists() else []
    if live_html and live_meta:
        meta = json.loads(live_meta[0].read_text(encoding="utf-8"))
        page = live_html[0].read_text(encoding="utf-8")
        url = meta.get("canonical_url") or url
        title = meta.get("title") or title
        author = meta.get("author") or author
        m = re.search(r"<article[^>]*>(.*)</article>", page, re.S | re.I)
        body_html = m.group(1) if m else page

    # Prefer remote-looking img URLs for publish so media localize can run;
    # when seeding from live case we overlay the real offline tree afterwards.
    if live_html:
        # Strip already-localized media/ paths for publish — use fixture body with remote demo img.
        body_html = (
            "<p>如果盒友想要搞一个带登录、还要存数据的小网站，没必要上来就考虑服务器。</p>"
            "<p>推荐一下 Supabase。一个免费的数据库，后端也顺便有了。</p>"
            '<p><img src="https://imgheybox.max-c.com/web/bbs/demo/thumb.png" alt="demo"></p>'
        )

    with respx.mock:
        respx.get(url__regex=r".*imgheybox.*").mock(
            return_value=httpx.Response(200, content=b"PNG-XHH")
        )
        result = service.archive_from_browser_dom(
            url=url,
            page_html=page_html,
            final_url=url,
            title=title,
            author=author,
            body_html=body_html,
        )
    if result.article_id is None:
        raise RuntimeError(result.message)

    detail = service.get_article(int(result.article_id))
    if detail and detail.archive_folder and live_html:
        dest_folder = Path(detail.archive_folder)
        src_folder = live_html[0].parent
        # Overlay authentic live reading edition + media + mhtml.
        for name in ("article.html", "article.md", "metadata.json"):
            src = src_folder / name
            if src.is_file():
                shutil.copy2(src, dest_folder / name)
        live_media = src_folder / "media"
        if live_media.is_dir():
            target_media = dest_folder / "media"
            if target_media.exists():
                shutil.rmtree(target_media)
            shutil.copytree(live_media, target_media)
        live_snap = src_folder / "snapshot.mhtml"
        if live_snap.is_file():
            shutil.copy2(live_snap, dest_folder / "snapshot.mhtml")
            context.connection.execute(
                """
                UPDATE web_articles
                SET status = 'COMPLETE',
                    media_total = ?,
                    media_missing = 0,
                    snapshot_status = 'COMPLETE',
                    relative_snapshot = 'snapshot.mhtml',
                    snapshot_bytes = ?,
                    snapshot_captured_at = COALESCE(snapshot_captured_at, captured_at),
                    snapshot_failure = NULL
                WHERE id = ?
                """,
                (
                    sum(1 for _ in (dest_folder / "media").rglob("*") if _.is_file())
                    if (dest_folder / "media").is_dir()
                    else 0,
                    live_snap.stat().st_size,
                    result.article_id,
                ),
            )
            context.connection.commit()
    context.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="techvault-xhh-shot-"))
    db = tmp / "techvault.sqlite"
    archive = tmp / "archive"
    os.environ["TECHVAULT_DATABASE"] = str(db)
    os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(archive)
    os.environ.pop("TECHVAULT_SLATE_SHELL_DEMO", None)

    seed(db, archive)

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    context = prepare_context(db, archive_root=archive)
    window = DesktopWindow(context)
    window.show()
    paths: list[str] = []

    def grab(name: str, w: int, h: int) -> None:
        window.resize(w, h)
        window.show()
        for _ in range(12):
            app.processEvents()
        img = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if not img.save(str(target), "PNG"):
            raise RuntimeError(str(target))
        paths.append(str(target))
        print("saved", target)

    def js(code: str) -> None:
        window.view.page().runJavaScript(code)
        time.sleep(0.7)
        for _ in range(24):
            app.processEvents()

    def click_nav(label: str) -> None:
        js(
            f"""
            (() => {{
              const rail = document.querySelector('nav[aria-label=\"主导航\"]');
              const buttons = [...(rail || document).querySelectorAll('button')];
              const hit = buttons.find(b => (b.innerText || '').replace(/\\s+/g, '').includes('{label}'));
              if (hit) {{ hit.click(); return true; }}
              return false;
            }})()
            """
        )

    def finish() -> None:
        meta = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "files": paths,
            "tmp": str(tmp),
        }
        (SHOT / "capture_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        window.close()
        context.close()
        app.quit()

    def sequence() -> None:
        time.sleep(1.4)
        for _ in range(40):
            app.processEvents()
        click_nav("网页")
        time.sleep(1.4)
        for _ in range(50):
            app.processEvents()

        # Share-link input surface (React controlled input needs native value setter).
        js(
            """
            (() => {
              const sel = document.querySelector('select');
              if (sel) {
                const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
                setter.call(sel, 'xiaoheihe');
                sel.dispatchEvent(new Event('change', { bubbles: true }));
              }
              const input = [...document.querySelectorAll('input')].find(el =>
                (el.placeholder || '').includes('链接') || (el.placeholder || '').includes('网页')
              );
              if (input) {
                const url = 'https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=f1ba7299a477';
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(input, url);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.focus();
              }
            })()
            """
        )
        time.sleep(0.8)
        for _ in range(20):
            app.processEvents()
        grab("xhh_share_input_1440x900.png", 1440, 900)

        # Mixed list (all sources).
        js(
            """
            (() => {
              const sel = document.querySelector('select');
              if (sel) {
                sel.value = 'all';
                sel.dispatchEvent(new Event('change', { bubbles: true }));
              }
            })()
            """
        )
        time.sleep(1.8)
        for _ in range(60):
            app.processEvents()
        grab("xhh_mixed_list_1440x900.png", 1440, 900)

        # Select Xiaoheihe row.
        js(
            """
            (() => {
              const rows = [...document.querySelectorAll('button')].filter(b =>
                (b.innerText || '').includes('数据库') || (b.innerText || '').includes('小黑盒')
              );
              if (rows[0]) rows[0].click();
            })()
            """
        )
        time.sleep(1.2)
        for _ in range(40):
            app.processEvents()
        grab("xhh_reader_1440x900.png", 1440, 900)
        grab("xhh_reader_960x640.png", 960, 640)

        # Archive info tab.
        js(
            """
            (() => {
              const tabs = [...document.querySelectorAll('button')].filter(b =>
                (b.innerText || '').includes('归档信息')
              );
              if (tabs[0]) tabs[0].click();
            })()
            """
        )
        time.sleep(0.6)
        for _ in range(20):
            app.processEvents()
        grab("xhh_info_1440x900.png", 1440, 900)

        # Open snapshot if button exists (may open separate window — still grab main).
        js(
            """
            (() => {
              const tabs = [...document.querySelectorAll('button')].filter(b =>
                (b.innerText || '').includes('网页快照') || (b.innerText || '').includes('快照')
              );
              if (tabs[0]) tabs[0].click();
            })()
            """
        )
        time.sleep(1.0)
        for _ in range(30):
            app.processEvents()
        grab("xhh_snapshot_tab_1440x900.png", 1440, 900)
        finish()

    QTimer.singleShot(2000, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
