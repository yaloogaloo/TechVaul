"""Capture list clamping, preview/archived isolation, and local covers.

Uses tmp-video-ui only. Does not open the user database.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

UI_ROOT = ROOT / "tmp-video-ui"
DB = UI_ROOT / "db.sqlite"
ARCHIVE = UI_ROOT / "archive"
SHOT = ROOT / "docs" / "video" / "evidence"
SOURCE = ROOT / "tmp-video-route" / "archive"
os.environ["TECHVAULT_DATABASE"] = str(DB)
os.environ["TECHVAULT_ARCHIVE_ROOT"] = str(ARCHIVE)
os.environ["TECHVAULT_DATA_DIR"] = str(UI_ROOT)
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing"
os.environ["QT_OPENGL"] = "software"


def seed() -> None:
    from techvault.app.bootstrap import prepare_context

    if ARCHIVE.exists():
        shutil.rmtree(ARCHIVE)
    if DB.exists():
        DB.unlink()
    shutil.copytree(SOURCE, ARCHIVE)
    context = prepare_context(DB, archive_root=ARCHIVE)
    try:
        bili = next(ARCHIVE.joinpath("videos", "bilibili", "BV1XTad6oEWe").glob("*.mp4"))
        zoo = next(ARCHIVE.joinpath("videos", "youtube", "jNQXAC9IVRw").glob("*.mp4"))
        now = "2026-10-23T08:00:00+00:00"
        rows = [
            ("bilibili", "BV1XTad6oEWe", "旧手机畅玩Docker！无需刷机Root，PoDroid+1Panel安装使用指南", "在下莫老师", 480, bili, 480, "videos/bilibili/BV1XTad6oEWe/cover.jpg"),
            ("youtube", "jNQXAC9IVRw", "Me at the zoo", "jawed", 19, zoo, 240, None),
            ("youtube", "shorttitle1", "十分钟入门", "示例", 12, zoo, 240, None),
            ("youtube", "mediumtitle", "这是一个大约四十字的中文视频标题用来检查列表两行省略是否稳定", "示例", 40, zoo, 240, None),
            (
                "youtube",
                "longtitle01",
                "中英混合 LongTitle 视频归档列表必须在两行内结束……" * 3 + "《标点》、Docker（无需刷机）！",
                "示例",
                100,
                zoo,
                240,
                None,
            ),
            ("youtube", "nospaces001", "A" * 100 + "NoSpacesEnglishTitle", "example", 8, zoo, 240, None),
        ]
        for platform, video_id, title, uploader, duration, media, height, cover in rows:
            context.connection.execute(
                """
                INSERT INTO video_items (
                    platform, platform_video_id, part_key, canonical_url, title, uploader,
                    duration_seconds, thumbnail_relpath, created_at, updated_at
                ) VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    video_id,
                    f"https://example.invalid/{video_id}" if platform == "youtube" and video_id not in {"jNQXAC9IVRw"} else (
                        "https://www.bilibili.com/video/BV1XTad6oEWe" if platform == "bilibili" else "https://www.youtube.com/watch?v=jNQXAC9IVRw"
                    ),
                    title,
                    uploader,
                    duration,
                    cover,
                    now,
                    now,
                ),
            )
            item_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            relative = media.relative_to(ARCHIVE).as_posix()
            context.connection.execute(
                """
                INSERT INTO video_downloads (
                    video_item_id, task_id, status, requested_quality, actual_quality, height,
                    container, relative_path, archive_root, file_size, has_video, has_audio,
                    started_at, finished_at
                ) VALUES (?, ?, 'COMPLETED', 'auto', 'auto', ?, 'mp4', ?, ?, ?, 1, 1, ?, ?)
                """,
                (item_id, video_id, height, relative, str(ARCHIVE), media.stat().st_size, now, now),
            )
            download_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            context.connection.execute(
                "UPDATE video_items SET current_download_id = ? WHERE id = ?",
                (download_id, item_id),
            )
    finally:
        context.close()


def main() -> int:
    seed()
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from techvault.app.bootstrap import prepare_context
    from techvault.ui.desktop_window import DesktopWindow
    from techvault.ui.web_scheme import register_scheme

    register_scheme()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    context = prepare_context(DB, archive_root=ARCHIVE)
    window = DesktopWindow(context)
    window.resize(1440, 900)
    window.show()

    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)

    def grab(name: str, width: int = 1440, height: int = 900) -> None:
        window.resize(width, height)
        pump(0.35)
        image = window.grab()
        SHOT.mkdir(parents=True, exist_ok=True)
        target = SHOT / name
        if image.isNull() or not image.save(str(target), "PNG"):
            raise RuntimeError(str(target))
        print("saved", target, flush=True)

    def js(code: str) -> None:
        window.view.page().runJavaScript(code)
        pump(0.25)

    def body_has(needle: str) -> bool:
        found = {"ok": False}

        def store(value) -> None:
            found["ok"] = bool(value)

        window.view.page().runJavaScript(
            "document.body ? document.body.innerText.includes(" + json.dumps(needle) + ") : false",
            store,
        )
        pump(0.2)
        return found["ok"]

    def wait_for(needle: str, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if body_has(needle):
                return True
            pump(0.25)
        print("missing", needle, flush=True)
        return False

    def click_text(label: str) -> None:
        js(
            """
            (() => {
              const hit = [...document.querySelectorAll('button')].find((node) =>
                (node.innerText || '').includes(%s)
              );
              if (hit) hit.click();
            })()
            """
            % json.dumps(label)
        )

    def fill_and_probe(value: str) -> None:
        js(
            """
            (() => {
              const input = document.querySelector('input[aria-label="视频地址"]');
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
              setter.call(input, %s);
              input.dispatchEvent(new Event('input', { bubbles: true }));
              const probe = [...document.querySelectorAll('button')].find((node) => node.innerText.trim() === '解析视频');
              if (probe) probe.click();
            })()
            """
            % json.dumps(value)
        )

    def finish(code: int) -> None:
        window.close()
        context.close()
        app.exit(code)

    def sequence() -> None:
        try:
            if not wait_for("GitHub", 25):
                grab("11_ui_load_failed.png")
                finish(1)
                return
            click_text("视频")
            if not wait_for("十分钟入门", 12):
                grab("11_list_failed.png")
                finish(1)
                return
            grab("11_long_titles.png")
            fill_and_probe("BV1q64f6PEhx")
            if not wait_for("将使用", 50):
                grab("12_preview_failed.png")
                finish(1)
                return
            grab("12_preview_witcher.png")
            click_text("旧手机畅玩Docker")
            pump(1.2)
            if body_has("将使用"):
                grab("13_isolation_failed.png")
                finish(1)
                return
            if not wait_for("打开本地视频", 8):
                grab("13_archived_failed.png")
                finish(1)
                return
            grab("13_archived_docker.png")
            click_text("Me at the zoo")
            deadline = time.time() + 20
            seen = False
            while time.time() < deadline:
                found = {"ok": False}

                def store(value, found=found) -> None:
                    found["ok"] = bool(value)

                window.view.page().runJavaScript(
                    "[...document.images].some((img) => (img.getAttribute('src') || '').includes('video-cover/youtube/jNQXAC9IVRw'))",
                    store,
                )
                pump(0.4)
                if found["ok"]:
                    seen = True
                    break
            if not seen:
                print("youtube cover image was not ready", flush=True)
            grab("14_first_frame.png")
            grab("15_960x640.png", 960, 640)
            finish(0)
        except Exception as exc:
            print("capture failed", exc, flush=True)
            finish(1)

    QTimer.singleShot(800, sequence)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
