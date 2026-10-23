"""Video identity, format choice, and archive safety. Network downloads are explicit."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event

import httpx
import pytest

from techvault.app.bootstrap import prepare_context
from techvault.video.engine import YtDlpEngine
from techvault.video.errors import VideoArchiveError
from techvault.video.paths import VideoPathLayout, resolve_cover_file, safe_title
from techvault.video.quality import choices_for
from techvault.video.service import VideoArchiveService
from techvault.video.deps import tool_path
from techvault.video.urls import VideoIdentity, parse_video_input

USER_DB = Path(r"C:\Users\king\AppData\Local\TechVault\techvault.sqlite")
BV = "BV1XTad6oEWe"


def test_bilibili_bv_links_parts_and_youtube_aliases() -> None:
    direct = parse_video_input(BV)
    full = parse_video_input(f"https://www.bilibili.com/video/{BV}?spm_id_from=333")
    assert direct.canonical_url == full.canonical_url
    assert direct.part_key == ""
    page = parse_video_input(f"https://www.bilibili.com/video/{BV}?p=2")
    assert page.part_key == "2"
    assert page.canonical_url.endswith("?p=2")
    assert parse_video_input(f"https://www.bilibili.com/video/{BV}?p=1").part_key == ""
    watch = parse_video_input("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&si=track")
    short = parse_video_input("https://youtu.be/dQw4w9WgXcQ?feature=share")
    shorts = parse_video_input("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    mobile = parse_video_input("https://m.youtube.com/watch?v=dQw4w9WgXcQ")
    assert watch.platform_video_id == short.platform_video_id == shorts.platform_video_id == mobile.platform_video_id
    assert watch.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    mobile_bili = parse_video_input(f"https://m.bilibili.com/video/{BV}")
    assert mobile_bili.platform_video_id == BV
    with pytest.raises(VideoArchiveError) as caught:
        parse_video_input(f"https://evilbilibili.com/video/{BV}")
    assert caught.value.code == "UNSUPPORTED_PLATFORM"
    assert "list=" not in watch.canonical_url
    bare = parse_video_input("youtube.com/watch?reload=9&v=0Z-vhBvBmUY")
    assert bare.platform == "youtube"
    assert bare.input_kind == "schemeless"
    assert bare.platform_video_id == "0Z-vhBvBmUY"
    assert bare.canonical_url == "https://www.youtube.com/watch?v=0Z-vhBvBmUY"
    ordered = parse_video_input("https://www.youtube.com/watch?t=12&si=share&list=PL123&v=0Z-vhBvBmUY")
    assert ordered.canonical_url == bare.canonical_url
    assert parse_video_input(f"www.bilibili.com/video/{BV}?p=2").part_key == "2"
    assert parse_video_input("youtu.be/0Z-vhBvBmUY").platform_video_id == "0Z-vhBvBmUY"
    assert parse_video_input("youtube.com/shorts/0Z-vhBvBmUY").platform_video_id == "0Z-vhBvBmUY"


def test_invalid_and_playlist_inputs_are_rejected() -> None:
    for value, code in (
        ("BV123", "INVALID_BV"),
        ("not a video", "UNRECOGNIZED"),
        ("", "EMPTY_INPUT"),
        ("youtube.com/watch?v=short", "INVALID_YOUTUBE"),
        ("youtube.com/playlist?list=PL123", "PLAYLIST_UNSUPPORTED"),
        ("example.com/watch?v=0Z-vhBvBmUY", "UNSUPPORTED_PLATFORM"),
        ("https://example.com/watch", "UNSUPPORTED_PLATFORM"),
        ("https://www.youtube.com/playlist?list=PL123", "PLAYLIST_UNSUPPORTED"),
        ("https://www.youtube.com/@someone", "PLAYLIST_UNSUPPORTED"),
        ("https://www.bilibili.com/medialist/play/ml123", "PLAYLIST_UNSUPPORTED"),
    ):
        with pytest.raises(VideoArchiveError) as caught:
            parse_video_input(value)
        assert caught.value.code == code


def test_b23_redirect_is_limited_to_public_bilibili() -> None:
    class Response:
        def __init__(self, status: int, location: str, url: str) -> None:
            self.status_code = status
            self.headers = {"location": location}
            self.url = httpx.URL(url)

    class Client:
        def get(self, url: str) -> Response:
            if "bilibili.com/video" in url:
                return Response(200, "", url)
            return Response(302, f"https://www.bilibili.com/video/{BV}", url)

        def close(self) -> None:
            return None

    parsed = parse_video_input("https://b23.tv/abcDEF", client=Client())
    bare = parse_video_input("b23.tv/abcDEF", client=Client())
    assert parsed.platform_video_id == bare.platform_video_id == BV
    assert bare.input_kind == "schemeless"

    class Loopback:
        def get(self, url: str) -> Response:
            return Response(302, "http://127.0.0.1/secret", url)

        def close(self) -> None:
            return None

    with pytest.raises(VideoArchiveError) as caught:
        parse_video_input("https://b23.tv/abcDEF", client=Loopback())
    assert caught.value.code == "UNSAFE_URL"


def test_quality_uses_reported_heights_and_does_not_invent_1080() -> None:
    info = {
        "formats": [
            {"format_id": "v720", "height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "filesize": 100},
            {"format_id": "a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 20, "abr": 128},
            {"format_id": "v480", "height": 480, "ext": "webm", "vcodec": "vp9", "acodec": "none", "filesize": 40},
        ]
    }
    choices = {item.key: item for item in choices_for(info)}
    assert "1080" not in choices
    assert choices["auto"].height == 720
    assert choices["auto"].note == "最高可用 720p"
    assert choices["auto"].filesize == 120
    assert choices["720"].format_spec == "v720+a"
    assert choices["480"].height == 480
    assert choices["best"].height == 720
    unknown = {item.key: item for item in choices_for({"formats": [{"format_id": "mp4", "ext": "mp4"}]})}
    assert set(unknown) == {"auto", "best"}
    assert unknown["auto"].height is None
    assert unknown["auto"].note == "清晰度未在元数据中给出"


def test_titles_and_cover_paths_stay_inside_the_archive(tmp_path: Path) -> None:
    assert safe_title("CON") == "_CON"
    assert "/" not in safe_title('演示: "视频"/稿')
    layout = VideoPathLayout(tmp_path)
    directory = layout.item_dir("bilibili", BV, "2")
    assert directory == tmp_path / "videos" / "bilibili" / BV / "p2"
    assert resolve_cover_file(tmp_path, f"video-cover/bilibili/{BV}/p2/cover.jpg") == directory / "cover.jpg"
    assert resolve_cover_file(tmp_path, "video-cover/bilibili/../../secrets/cover.jpg") is None
    with pytest.raises(Exception):
        layout.item_dir("youtube", "../secret", "")


def test_duplicate_and_failed_replacement_keep_the_existing_file(tmp_path: Path, monkeypatch) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        archive = tmp_path / "archive"
        video = archive / "videos" / "bilibili" / BV / "demo.720p.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"original-video")
        context.connection.execute(
            """
            INSERT INTO video_items (
                platform, platform_video_id, part_key, canonical_url, title, uploader,
                created_at, updated_at
            ) VALUES ('bilibili', ?, '', ?, '演示', 'up', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (BV, f"https://www.bilibili.com/video/{BV}"),
        )
        item_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        relative = video.relative_to(archive).as_posix()
        context.connection.execute(
            """
            INSERT INTO video_downloads (
                video_item_id, task_id, status, requested_quality, actual_quality,
                relative_path, archive_root, file_size, has_video, started_at, finished_at
            ) VALUES (?, 'old', 'COMPLETED', '720', '720', ?, ?, 15, 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (item_id, relative, str(archive)),
        )
        download_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        context.connection.execute(
            "UPDATE video_items SET current_download_id = ? WHERE id = ?",
            (download_id, item_id),
        )
        service = VideoArchiveService(context.connection, engine=object())  # type: ignore[arg-type]
        with pytest.raises(VideoArchiveError) as caught:
            service.download(BV, "720", task_id="new", replace=False)
        assert caught.value.code == "ALREADY_ARCHIVED"
        assert video.read_bytes() == b"original-video"

        class Broken:
            def probe(self, *_args, **_kwargs):
                return {
                    "title": "演示",
                    "uploader": "up",
                    "duration": 12,
                    "formats": [
                        {"format_id": "v", "height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
                        {"format_id": "a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"},
                    ],
                }

            def download(self, *_args, **_kwargs):
                raise VideoArchiveError("DOWNLOAD_FAILED", "模拟失败")

        monkeypatch.setattr(
            "techvault.video.service.dependency_report",
            lambda: {
                "ready": True,
                "message": "",
                "ytdlp": {"available": True},
                "jsRuntime": "node",
            },
        )
        replaced = VideoArchiveService(context.connection, engine=Broken())
        with pytest.raises(VideoArchiveError):
            replaced.download(BV, "720", task_id="retry", replace=True)
        assert video.read_bytes() == b"original-video"
        current = context.connection.execute(
            "SELECT status FROM video_downloads WHERE id = ?",
            (download_id,),
        ).fetchone()
        assert current["status"] == "COMPLETED"
    finally:
        context.close()


def test_engine_options_do_not_enable_generic_or_playlists() -> None:
    options = YtDlpEngine().build_options(staging=None, part_index=None)
    assert options["noplaylist"] is True
    assert "generic" not in {item.lower() for item in options["allowed_extractors"]}
    assert "max_downloads" not in options
    part = YtDlpEngine().build_options(staging=None, part_index=2, format_spec="22", container="mp4")
    assert part["playlist_items"] == "2"
    assert part["noplaylist"] is False
    assert part["merge_output_format"] == "mp4"


def test_cancel_stops_before_extract(tmp_path: Path) -> None:
    event = Event()
    event.set()
    with pytest.raises(VideoArchiveError) as caught:
        YtDlpEngine().download(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            staging=tmp_path / "staging",
            format_spec="18",
            container="mp4",
            part_index=None,
            cancel_event=event,
        )
    assert caught.value.code == "CANCELLED"


def test_ytdlp_download_is_checked_before_it_is_published(tmp_path: Path, monkeypatch) -> None:
    ffmpeg = tool_path("ffmpeg")
    assert ffmpeg and tool_path("ffprobe"), "ffmpeg and ffprobe are required"
    sample_dir = tmp_path / "site"
    sample_dir.mkdir()
    sample = sample_dir / "clip.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=12",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(sample),
        ],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(sample_dir)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    media_url = f"http://127.0.0.1:{server.server_address[1]}/clip.mp4"

    class DirectEngine(YtDlpEngine):
        def build_options(self, **kwargs):
            extra = dict(kwargs.pop("extra") or {})
            extra["allowed_extractors"] = ["generic"]
            return super().build_options(extra=extra, **kwargs)

    monkeypatch.setattr(
        "techvault.video.service.parse_video_input",
        lambda _value, client=None: VideoIdentity("bilibili", BV, "", None, media_url, "Bilibili"),
    )
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        service = VideoArchiveService(context.connection, engine=DirectEngine())
        item = service.download(media_url, "auto", task_id="local-media", replace=False)
        published = Path(item["archiveRoot"]) / item["relativePath"]
        assert item["fileExists"] is True
        assert item["hasVideo"] is True
        assert item["hasAudio"] is True
        assert item["localStatus"] == "已归档"
        assert published.is_file() and published.stat().st_size > 1000
        assert published.suffix == ".mp4"
        metadata = (published.parent / "metadata.json").read_text(encoding="utf-8")
        assert "cookie" not in metadata.lower()
        assert "formats" not in metadata
        with pytest.raises(VideoArchiveError) as caught:
            service.download(media_url, "auto", task_id="again", replace=False)
        assert caught.value.code == "ALREADY_ARCHIVED"
        assert published.stat().st_size == Path(item["archiveRoot"], item["relativePath"]).stat().st_size
    finally:
        context.close()
        server.shutdown()


def _seed_archived_video(tmp_path: Path, video_name: str) -> tuple[object, Path, int]:
    ffmpeg = tool_path("ffmpeg")
    assert ffmpeg
    sample = tmp_path / "source.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=red:s=160x90:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(sample)],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    archive = tmp_path / "archive"
    video = archive / "videos" / "youtube" / video_name / "clip.240p.mp4"
    video.parent.mkdir(parents=True)
    shutil.copyfile(sample, video)
    now = "2026-01-01T00:00:00+00:00"
    context.connection.execute(
        """
        INSERT INTO video_items (
            platform, platform_video_id, part_key, canonical_url, title, uploader,
            duration_seconds, created_at, updated_at
        ) VALUES ('youtube', ?, '', ?, '封面样本', 'jawed', 1, ?, ?)
        """,
        (video_name, f"https://www.youtube.com/watch?v={video_name}", now, now),
    )
    item_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    relative = video.relative_to(archive).as_posix()
    context.connection.execute(
        """
        INSERT INTO video_downloads (
            video_item_id, task_id, status, requested_quality, actual_quality, height,
            relative_path, archive_root, file_size, has_video, started_at, finished_at
        ) VALUES (?, 'cover', 'COMPLETED', 'auto', 'auto', 90, ?, ?, ?, 1, ?, ?)
        """,
        (item_id, relative, str(archive), video.stat().st_size, now, now),
    )
    download_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    context.connection.execute(
        "UPDATE video_items SET current_download_id = ? WHERE id = ?",
        (download_id, item_id),
    )
    return context, video, item_id


def test_missing_cover_is_the_first_frame_and_does_not_change_the_video(tmp_path: Path) -> None:
    context, video, item_id = _seed_archived_video(tmp_path, "abcdefghijk")
    try:
        service = VideoArchiveService(context.connection)
        size = video.stat().st_size
        assert service.cover_is_ready(item_id) is False
        item = service.ensure_cover(item_id)
        cover = video.parent / "cover.jpg"
        assert item is not None and item["thumbnailUrl"].endswith("cover.jpg")
        assert cover.read_bytes().startswith(b"\xff\xd8\xff")
        assert video.stat().st_size == size
        kept = cover.read_bytes()
        service.ensure_cover(item_id)
        assert cover.read_bytes() == kept
        cover.write_bytes(b"broken-cover-bytes-that-are-definitely-not-an-image")
        service.ensure_cover(item_id)
        assert cover.read_bytes().startswith(b"\xff\xd8\xff")
        assert video.stat().st_size == size
        assert context.connection.execute("SELECT status FROM video_downloads WHERE id = 1").fetchone()[0] == "COMPLETED"
    finally:
        context.close()


def test_cover_backfill_does_nothing_without_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    context, video, item_id = _seed_archived_video(tmp_path, "nopreview1")
    original = tool_path

    def hidden(name: str) -> str | None:
        if name == "ffmpeg":
            return None
        return original(name)

    monkeypatch.setattr("techvault.video.service.tool_path", hidden)
    try:
        service = VideoArchiveService(context.connection)
        item = service.ensure_cover(item_id)
        assert item is not None
        assert item["thumbnailUrl"] == ""
        assert item["localStatus"] == "已归档"
        assert not (video.parent / "cover.jpg").exists()
        assert video.stat().st_size > 0
    finally:
        context.close()


def test_copy_source_uses_the_stored_url_and_does_not_open_it(tmp_path: Path, qtbot, monkeypatch) -> None:
    import json

    from PySide6.QtGui import QDesktopServices, QGuiApplication

    from techvault.ui.bridge.video_bridge import VideoBridge

    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    long_youtube = "https://www.youtube.com/watch?v=jNQXAC9IVRw&list=" + ("P" * 80) + "&t=12s"
    long_bilibili = "https://www.bilibili.com/video/BV1XTad6oEWe?spm_id_from=" + ("3" * 90)
    now = "2026-01-01T00:00:00+00:00"

    def insert(platform: str, video_id: str, url: str, title: str) -> int:
        context.connection.execute(
            """
            INSERT INTO video_items (
                platform, platform_video_id, part_key, canonical_url, title, uploader,
                duration_seconds, created_at, updated_at
            ) VALUES (?, ?, '', ?, ?, '', 1, ?, ?)
            """,
            (platform, video_id, url, title, now, now),
        )
        return int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    try:
        youtube_id = insert("youtube", "jNQXAC9IVRw", long_youtube, "zoo")
        bilibili_id = insert("bilibili", BV, long_bilibili, "docker")
        bridge = VideoBridge(context.connection)
        opened: list[str] = []
        monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
        app = QGuiApplication.instance()
        assert app is not None

        copied = json.loads(bridge.copySource(youtube_id))
        assert copied["ok"] is True
        assert app.clipboard().text() == long_youtube
        copied = json.loads(bridge.copySource(bilibili_id))
        assert copied["ok"] is True
        assert app.clipboard().text() == long_bilibili
        assert opened == []
        stored = context.connection.execute(
            "SELECT canonical_url FROM video_items WHERE id = ?",
            (bilibili_id,),
        ).fetchone()[0]
        assert stored == long_bilibili

        context.connection.execute("UPDATE video_items SET canonical_url = '' WHERE id = ?", (youtube_id,))
        rejected = json.loads(bridge.copySource(youtube_id))
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "UNSAFE_URL"
        assert app.clipboard().text() == long_bilibili
        missing = json.loads(bridge.copySource(99999))
        assert missing["ok"] is False
        assert missing["error"]["code"] == "NOT_FOUND"
    finally:
        context.close()


def test_copy_source_reports_clipboard_failure(tmp_path: Path, qtbot, monkeypatch) -> None:
    import json

    from techvault.ui.bridge.video_bridge import VideoBridge

    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    now = "2026-01-01T00:00:00+00:00"
    context.connection.execute(
        """
        INSERT INTO video_items (
            platform, platform_video_id, part_key, canonical_url, title, uploader,
            duration_seconds, created_at, updated_at
        ) VALUES ('youtube', 'abcdefghijk', '', 'https://www.youtube.com/watch?v=abcdefghijk', '短链', '', 1, ?, ?)
        """,
        (now, now),
    )
    item_id = int(context.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    monkeypatch.setattr("techvault.ui.bridge.video_bridge.QGuiApplication.clipboard", lambda: None)
    try:
        bridge = VideoBridge(context.connection)
        payload = json.loads(bridge.copySource(item_id))
        assert payload["ok"] is False
        assert payload["error"]["code"] == "CLIPBOARD"
        stored = context.connection.execute(
            "SELECT canonical_url FROM video_items WHERE id = ?",
            (item_id,),
        ).fetchone()[0]
        assert stored == "https://www.youtube.com/watch?v=abcdefghijk"
    finally:
        context.close()


def test_cover_download_404_does_not_invent_bytes() -> None:
    import respx

    from techvault.video.service import _download_image

    with respx.mock:
        respx.get("https://example.com/missing.jpg").mock(return_value=httpx.Response(404))
        assert _download_image("https://example.com/missing.jpg") is None


def test_adding_video_tables_keeps_an_existing_library(tmp_path: Path) -> None:
    target = tmp_path / "library.sqlite"
    assert USER_DB.is_file(), "expected the existing TechVault library to upgrade from a copy"
    shutil.copy2(USER_DB, target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(USER_DB) + suffix)
        if side.is_file():
            shutil.copy2(side, Path(str(target) + suffix))
    raw = sqlite3.connect(target)
    before = raw.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    documents = raw.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    articles = raw.execute("SELECT COUNT(*) FROM web_articles").fetchone()[0]
    raw.close()
    context = prepare_context(target)
    try:
        assert context.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == before
        assert context.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == documents
        assert context.connection.execute("SELECT COUNT(*) FROM web_articles").fetchone()[0] == articles
        assert before > 0
        context.connection.execute("SELECT id FROM video_items LIMIT 1")
    finally:
        context.close()
