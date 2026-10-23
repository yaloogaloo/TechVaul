"""Save one video at a time. yt-dlp downloads; this module only records and publishes."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import httpx

from techvault.app.settings import SettingsService
from techvault.fsutil import atomic_replace
from techvault.readme_images.fetch import is_public_http_url
from techvault.video.deps import dependency_report, tool_path
from techvault.video.engine import YtDlpEngine
from techvault.video.errors import VideoArchiveError
from techvault.video.paths import VideoPathLayout, safe_title
from techvault.video.quality import choice_by_key, choices_for
from techvault.video.urls import VideoIdentity, parse_video_input

_MAX_COVER = 2_000_000


class VideoArchiveService:
    def __init__(self, connection: sqlite3.Connection, engine: YtDlpEngine | None = None) -> None:
        self._connection = connection
        self._engine = engine or YtDlpEngine()

    def dependencies(self) -> dict:
        report = dependency_report()
        root = self._archive_root_or_none()
        writable = False
        detail = ""
        if root is not None:
            try:
                root.mkdir(parents=True, exist_ok=True)
                probe = root / ".video-write-probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                writable = True
            except OSError:
                detail = "归档目录不可写"
        else:
            detail = "还没有设置归档目录"
        report["archiveWritable"] = writable
        if detail and not report["message"]:
            report["message"] = detail
        elif detail:
            report["message"] = str(report["message"]) + detail
        report["ready"] = bool(report["ready"] and writable)
        if "youtubeReady" in report:
            report["youtubeReady"] = bool(report["youtubeReady"] and writable)
        return report

    def list_items(self) -> list[dict]:
        rows = self._connection.execute(
            """
            SELECT i.*, d.status AS download_status, d.actual_quality, d.container, d.height,
                   d.relative_path, d.archive_root, d.file_size, d.finished_at, d.error_code,
                   d.error_message, d.has_video, d.has_audio
            FROM video_items i
            LEFT JOIN video_downloads d ON d.id = i.current_download_id
            ORDER BY i.updated_at DESC, i.id DESC
            """
        ).fetchall()
        return [self._item_payload(row) for row in rows]

    def get_item(self, item_id: int) -> dict | None:
        row = self._connection.execute(
            """
            SELECT i.*, d.status AS download_status, d.actual_quality, d.container, d.vcodec,
                   d.acodec, d.width, d.height, d.duration_seconds AS download_duration,
                   d.relative_path, d.archive_root, d.file_size, d.finished_at, d.started_at,
                   d.error_code, d.error_message, d.has_video, d.has_audio, d.requested_quality,
                   d.resumed
            FROM video_items i
            LEFT JOIN video_downloads d ON d.id = i.current_download_id
            WHERE i.id = ?
            """,
            (int(item_id),),
        ).fetchone()
        if row is None:
            return None
        return self._item_payload(row, detail=True)

    def probe(self, raw: str, *, preview_token: str) -> dict:
        identity = parse_video_input(raw)
        self._require_metadata_tools(identity.platform)
        info = self._engine.probe(identity.canonical_url, part_index=identity.part_index)
        title = str(info.get("title") or "").strip()
        if not title:
            raise VideoArchiveError("NO_METADATA", "没有解析到视频标题")
        options = choices_for(info)
        if not options:
            raise VideoArchiveError("NO_FORMAT", "没有可用的视频格式")
        cover = _save_cover(self._layout(), str(info.get("thumbnail") or ""), preview_token)
        existing = self._existing(identity)
        return {
            "identity": identity.as_dict(),
            "title": title,
            "uploader": str(info.get("uploader") or info.get("channel") or info.get("creator") or ""),
            "duration": _duration(info.get("duration")),
            "thumbnailUrl": cover,
            "qualities": [item.as_dict() for item in options],
            "existing": existing,
            "hasAudio": any(item.acodec for item in options),
        }

    def download(
        self,
        raw: str,
        quality: str,
        *,
        task_id: str,
        replace: bool,
        cancel_event: Event | None = None,
        on_progress=None,
        preview_token: str = "",
    ) -> dict:
        identity = parse_video_input(raw)
        existing = self._existing(identity)
        if existing and existing["fileExists"] and not replace:
            raise VideoArchiveError("ALREADY_ARCHIVED", "本地已有这个视频。要更换文件请明确重新下载")
        self._require_download_tools(identity.platform)
        report = lambda phase, **payload: on_progress({"phase": phase, **payload}) if on_progress else None
        report("FETCHING_METADATA")
        info = self._engine.probe(identity.canonical_url, part_index=identity.part_index)
        title = str(info.get("title") or "").strip() or identity.platform_video_id
        selected = choice_by_key(info, quality or "auto")
        if selected is None:
            raise VideoArchiveError("QUALITY_UNAVAILABLE", "所选画质不可用")
        layout = self._layout()
        staging = layout.staging_dir(identity.platform, identity.platform_video_id, identity.part_key)
        had_partial = any(staging.glob("*.part")) if staging.is_dir() else False
        item_id = self._upsert_item(identity, title, str(info.get("uploader") or ""), _duration(info.get("duration")))
        download_id = self._insert_download(item_id, task_id, selected.key)

        def forward(payload: dict) -> None:
            if on_progress:
                on_progress(payload)

        try:
            if cancel_event is not None and cancel_event.is_set():
                raise VideoArchiveError("CANCELLED", "已取消下载")
            path = self._engine.download(
                identity.canonical_url,
                staging=staging,
                format_spec=selected.format_spec,
                container=selected.container,
                part_index=identity.part_index,
                cancel_event=cancel_event,
                on_progress=forward,
            )
            report("VERIFYING")
            media = _ffprobe(path)
            if not media["hasVideo"]:
                raise VideoArchiveError("INVALID_MEDIA", "文件里没有视频流")
            if selected.acodec and not media["hasAudio"]:
                raise VideoArchiveError("INVALID_MEDIA", "源视频有音频，但文件里没有音频流")
            if media["duration"] <= 0:
                raise VideoArchiveError("INVALID_MEDIA", "无法确认视频时长")
            report("PUBLISHING")
            published = self._publish(layout, identity, title, selected.height, path, info, selected, media)
            self._finish_download(
                download_id,
                item_id,
                status="COMPLETED",
                selected=selected,
                media=media,
                relative=published["relative"],
                archive_root=str(layout.archive_root),
                cover_relative=published["cover_relative"],
                resumed=had_partial,
            )
            _cleanup_staging(staging)
        except VideoArchiveError as exc:
            self._fail_download(download_id, exc.code, exc.message)
            raise
        except Exception as exc:
            self._fail_download(download_id, "DOWNLOAD_FAILED", "下载没有完成")
            raise VideoArchiveError("DOWNLOAD_FAILED", "下载没有完成") from exc
        item = self.get_item(item_id)
        assert item is not None
        item["resumed"] = had_partial
        item["message"] = "已沿用暂存文件完成下载" if had_partial else "视频已保存"
        return item

    def delete_item(self, item_id: int) -> None:
        item = self.get_item(item_id)
        if item is None:
            raise VideoArchiveError("NOT_FOUND", "视频不存在")
        layout = self._layout()
        paths = self._connection.execute(
            "SELECT relative_path, archive_root FROM video_downloads WHERE video_item_id = ? AND relative_path IS NOT NULL",
            (int(item_id),),
        ).fetchall()
        for row in paths:
            _unlink_archived(layout, row["archive_root"], row["relative_path"])
        if item.get("thumbnailRelpath"):
            _unlink_archived(layout, item.get("archiveRoot") or str(layout.archive_root), item["thumbnailRelpath"])
        self._connection.execute("DELETE FROM video_items WHERE id = ?", (int(item_id),))
        self._connection.commit()

    def local_file(self, item_id: int) -> Path:
        item = self.get_item(item_id)
        if item is None or not item.get("relativePath"):
            raise VideoArchiveError("NOT_FOUND", "没有可打开的视频文件")
        path = self._layout().resolve_relative(item["relativePath"])
        if not path.is_file():
            raise VideoArchiveError("FILE_MISSING", "记录还在，但视频文件已经不存在")
        return path

    def local_dir(self, item_id: int) -> Path:
        return self.local_file(item_id).parent

    def cover_is_ready(self, item_id: int) -> bool:
        row = self._connection.execute(
            "SELECT thumbnail_relpath FROM video_items WHERE id = ?",
            (int(item_id),),
        ).fetchone()
        if row is None or not row["thumbnail_relpath"]:
            return False
        try:
            path = self._layout().resolve_relative(row["thumbnail_relpath"])
        except Exception:
            return False
        return _valid_image_file(path)

    def ensure_cover(self, item_id: int) -> dict | None:
        """Fill a missing cover from the local video. Does not re-download or rewrite the media file."""
        item = self.get_item(item_id)
        if item is None or self.cover_is_ready(item_id):
            return item
        if not item.get("fileExists") or not item.get("relativePath"):
            return item
        video = self._layout().resolve_relative(item["relativePath"])
        before = video.stat().st_size if video.is_file() else None
        target = video.parent / "cover.jpg"
        if before is None or not _extract_first_frame(video, target):
            return self.get_item(item_id)
        if video.stat().st_size != before:
            raise VideoArchiveError("COVER_FAILED", "提取封面时视频文件被改动")
        self._connection.execute(
            "UPDATE video_items SET thumbnail_relpath = ?, updated_at = ? WHERE id = ?",
            (self._layout().relative(target), _now(), int(item_id)),
        )
        return self.get_item(item_id)

    def _publish(self, layout, identity: VideoIdentity, title: str, height: int | None, source: Path, info: dict, selected, media: dict) -> dict:
        directory = layout.item_dir(identity.platform, identity.platform_video_id, identity.part_key)
        directory.mkdir(parents=True, exist_ok=True)
        height_label = f".{height}p" if height else ""
        filename = f"{safe_title(title)}{height_label}.{selected.container}"
        final = directory / filename
        temporary = directory / f".{filename}.publishing"
        if temporary.exists():
            temporary.unlink()
        shutil.copy2(source, temporary)
        atomic_replace(temporary, final)
        metadata = {
            "platform": identity.platform,
            "id": identity.platform_video_id,
            "part": identity.part_key,
            "title": title,
            "uploader": str(info.get("uploader") or ""),
            "duration": _duration(info.get("duration")),
            "webpage_url": identity.canonical_url,
            "requested_quality": selected.key,
            "height": height,
            "container": selected.container,
            "vcodec": media["vcodec"],
            "acodec": media["acodec"],
        }
        meta_path = directory / "metadata.json"
        meta_tmp = directory / ".metadata.json.publishing"
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        atomic_replace(meta_tmp, meta_path)
        try:
            cover = _write_cover(str(info.get("thumbnail") or ""), directory)
        except OSError:
            cover = None
        if cover is None and _extract_first_frame(final, directory / "cover.jpg"):
            cover = directory / "cover.jpg"
        return {
            "relative": layout.relative(final),
            "cover_relative": None if cover is None else layout.relative(cover),
        }

    def _existing(self, identity: VideoIdentity) -> dict | None:
        row = self._connection.execute(
            """
            SELECT i.id, d.status, d.relative_path, d.actual_quality, d.file_size
            FROM video_items i
            LEFT JOIN video_downloads d ON d.id = i.current_download_id
            WHERE i.platform = ? AND i.platform_video_id = ? AND i.part_key = ?
            """,
            (identity.platform, identity.platform_video_id, identity.part_key),
        ).fetchone()
        if row is None:
            return None
        path = row["relative_path"]
        exists = False
        if path:
            try:
                exists = self._layout().resolve_relative(path).is_file()
            except Exception:
                exists = False
        return {
            "id": int(row["id"]),
            "status": row["status"] or "",
            "quality": row["actual_quality"] or "",
            "fileExists": exists,
            "fileSize": row["file_size"],
        }

    def _upsert_item(self, identity: VideoIdentity, title: str, uploader: str, duration: int | None) -> int:
        now = _now()
        self._connection.execute(
            """
            INSERT INTO video_items (
                platform, platform_video_id, part_key, part_index, canonical_url,
                title, uploader, duration_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, platform_video_id, part_key) DO UPDATE SET
                title = excluded.title,
                uploader = excluded.uploader,
                duration_seconds = excluded.duration_seconds,
                canonical_url = excluded.canonical_url,
                updated_at = excluded.updated_at
            """,
            (
                identity.platform,
                identity.platform_video_id,
                identity.part_key,
                identity.part_index,
                identity.canonical_url,
                title,
                uploader,
                duration,
                now,
                now,
            ),
        )
        row = self._connection.execute(
            "SELECT id FROM video_items WHERE platform = ? AND platform_video_id = ? AND part_key = ?",
            (identity.platform, identity.platform_video_id, identity.part_key),
        ).fetchone()
        return int(row["id"])

    def _insert_download(self, item_id: int, task_id: str, quality: str) -> int:
        now = _now()
        self._connection.execute(
            """
            INSERT INTO video_downloads (
                video_item_id, task_id, status, requested_quality, started_at
            ) VALUES (?, ?, 'DOWNLOADING', ?, ?)
            """,
            (item_id, task_id, quality, now),
        )
        download_id = int(self._connection.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        self._connection.execute(
            """
            UPDATE video_items
            SET current_download_id = ?, updated_at = ?
            WHERE id = ? AND current_download_id IS NULL
            """,
            (download_id, now, item_id),
        )
        return download_id

    def _finish_download(self, download_id, item_id, *, status, selected, media, relative, archive_root, cover_relative, resumed) -> None:
        now = _now()
        self._connection.execute(
            """
            UPDATE video_downloads SET
                status = ?, actual_quality = ?, container = ?, vcodec = ?, acodec = ?,
                width = ?, height = ?, duration_seconds = ?, has_video = ?, has_audio = ?,
                relative_path = ?, archive_root = ?, file_size = ?, resumed = ?, finished_at = ?,
                error_code = NULL, error_message = NULL
            WHERE id = ?
            """,
            (
                status,
                selected.key,
                media["container"] or selected.container,
                media["vcodec"],
                media["acodec"],
                media["width"],
                media["height"],
                media["duration"],
                1 if media["hasVideo"] else 0,
                1 if media["hasAudio"] else 0,
                relative,
                archive_root,
                media["size"],
                1 if resumed else 0,
                now,
                download_id,
            ),
        )
        self._connection.execute(
            "UPDATE video_items SET current_download_id = ?, thumbnail_relpath = COALESCE(?, thumbnail_relpath), updated_at = ? WHERE id = ?",
            (download_id, cover_relative, now, item_id),
        )

    def _fail_download(self, download_id: int, code: str, message: str) -> None:
        status = "CANCELLED" if code == "CANCELLED" else "FAILED"
        self._connection.execute(
            """
            UPDATE video_downloads
            SET status = ?, error_code = ?, error_message = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, code, message[:300], _now(), download_id),
        )
        current = self._connection.execute(
            """
            SELECT i.current_download_id, d.status
            FROM video_downloads failed
            JOIN video_items i ON i.id = failed.video_item_id
            LEFT JOIN video_downloads d ON d.id = i.current_download_id
            WHERE failed.id = ?
            """,
            (download_id,),
        ).fetchone()
        if current is not None and current["status"] not in {"COMPLETED"}:
            self._connection.execute(
                "UPDATE video_items SET current_download_id = ? WHERE id = (SELECT video_item_id FROM video_downloads WHERE id = ?)",
                (download_id, download_id),
            )

    def _item_payload(self, row, *, detail: bool = False) -> dict:
        relative = row["relative_path"] if "relative_path" in row.keys() else None
        exists = False
        if relative:
            try:
                exists = VideoPathLayout(row["archive_root"] or self._layout().archive_root).resolve_relative(relative).is_file()
            except Exception:
                exists = False
        status = row["download_status"] or ""
        if status == "COMPLETED" and exists:
            local = "已归档"
        elif status == "COMPLETED" and not exists:
            local = "文件缺失"
        elif status == "FAILED":
            local = "下载失败"
        elif status == "CANCELLED":
            local = "已取消"
        elif status == "DOWNLOADING":
            local = "下载中"
        else:
            local = "未归档"
        payload = {
            "id": int(row["id"]),
            "platform": row["platform"],
            "platformLabel": "YouTube" if row["platform"] == "youtube" else "Bilibili",
            "platformVideoId": row["platform_video_id"],
            "partKey": row["part_key"],
            "title": row["title"],
            "uploader": row["uploader"] or "",
            "duration": row["duration_seconds"],
            "canonicalUrl": row["canonical_url"],
            "localStatus": local,
            "fileExists": exists,
            "quality": row["actual_quality"] or "",
            "container": row["container"] or "",
            "height": row["height"],
            "fileSize": row["file_size"],
            "finishedAt": row["finished_at"] or "",
            "thumbnailUrl": _cover_url(row),
            "relativePath": relative or "",
            "archiveRoot": row["archive_root"] or "",
            "errorCode": row["error_code"] if "error_code" in row.keys() else None,
            "errorMessage": row["error_message"] if "error_message" in row.keys() else "",
        }
        if detail:
            payload.update(
                {
                    "vcodec": row["vcodec"] if "vcodec" in row.keys() else "",
                    "acodec": row["acodec"] if "acodec" in row.keys() else "",
                    "width": row["width"] if "width" in row.keys() else None,
                    "hasVideo": bool(row["has_video"]) if "has_video" in row.keys() else exists,
                    "hasAudio": bool(row["has_audio"]) if "has_audio" in row.keys() else False,
                    "requestedQuality": row["requested_quality"] if "requested_quality" in row.keys() else "",
                }
            )
        return payload

    def _layout(self) -> VideoPathLayout:
        root = SettingsService(self._connection).get_archive_root()
        if root is None:
            raise VideoArchiveError("NO_ARCHIVE_ROOT", "还没有设置归档目录")
        return VideoPathLayout(root)

    def _archive_root_or_none(self) -> Path | None:
        root = SettingsService(self._connection).get_archive_root()
        return None if root is None else Path(root)

    def _require_metadata_tools(self, platform: str) -> None:
        report = dependency_report()
        if not report["ytdlp"]["available"]:
            raise VideoArchiveError("YTDLP_MISSING", report["message"] or "未安装 yt-dlp")
        if platform == "youtube" and (not report["ejs"]["available"] or not report.get("jsRuntime")):
            raise VideoArchiveError("JS_RUNTIME_MISSING", report["message"] or "YouTube 需要 yt-dlp-ejs 以及 Deno 或 Node.js")

    def _require_download_tools(self, platform: str) -> None:
        report = self.dependencies()
        if not report["ready"]:
            raise VideoArchiveError("DEPENDENCY_MISSING", report["message"] or "视频下载依赖不完整")
        if platform == "youtube" and not report.get("youtubeReady"):
            raise VideoArchiveError("JS_RUNTIME_MISSING", report["message"] or "YouTube 需要 yt-dlp-ejs 以及 Deno 或 Node.js")


def _ffprobe(path: Path) -> dict:
    binary = tool_path("ffprobe")
    if not binary:
        raise VideoArchiveError("FFPROBE_MISSING", "未找到 ffprobe，不能把未检查的文件标为已归档")
    completed = subprocess.run(
        [binary, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise VideoArchiveError("INVALID_MEDIA", "ffprobe 无法读取这个文件")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise VideoArchiveError("INVALID_MEDIA", "ffprobe 没有返回有效信息") from exc
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    fmt = payload.get("format") or {}
    try:
        duration = int(round(float(fmt.get("duration") or 0)))
    except (TypeError, ValueError):
        duration = 0
    size = path.stat().st_size if path.is_file() else 0
    names = str(fmt.get("format_name") or "")
    container = "mp4" if "mp4" in names else "webm" if "webm" in names else "mkv" if "matroska" in names else path.suffix.lower().lstrip(".")
    return {
        "hasVideo": video is not None,
        "hasAudio": audio is not None,
        "duration": duration,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "vcodec": video.get("codec_name") if video else "",
        "acodec": audio.get("codec_name") if audio else "",
        "container": container,
        "size": size,
    }


def _write_cover(url: str, directory: Path) -> Path | None:
    downloaded = _download_image(url)
    if downloaded is None:
        return None
    data, ext = downloaded
    target = directory / f"cover.{ext}"
    temporary = target.with_suffix(target.suffix + ".part")
    temporary.write_bytes(data)
    atomic_replace(temporary, target)
    return target


def _save_cover(layout: VideoPathLayout, url: str, token: str) -> str:
    downloaded = _download_image(url)
    if downloaded is None or not token:
        return ""
    data, kind = downloaded
    try:
        target = layout.preview_file(token, kind)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(data)
        atomic_replace(temporary, target)
    except (OSError, VideoArchiveError):
        return ""
    return f"techvault://app/video-preview/{token}.{kind}"


def _download_image(url: str) -> tuple[bytes, str] | None:
    if not url:
        return None
    current = url
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=False, trust_env=False) as client:
            for _ in range(4):
                if not is_public_http_url(current):
                    return None
                response = client.get(current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location") or ""
                    if not location:
                        return None
                    current = str(response.url.join(location))
                    continue
                if response.status_code != 200 or len(response.content) > _MAX_COVER:
                    return None
                kind = _image_ext(response.content)
                return None if kind is None else (response.content, kind)
    except httpx.HTTPError:
        return None
    return None


def _extract_first_frame(video: Path, target: Path) -> bool:
    binary = tool_path("ffmpeg")
    if not binary or not video.is_file():
        return False
    temporary = target.with_name(".cover.extract.jpg")
    commands = (
        [binary, "-y", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(temporary)],
        [binary, "-y", "-ss", "0.04", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(temporary)],
    )
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            temporary.unlink(missing_ok=True)
            continue
        if completed.returncode == 0 and _valid_image_file(temporary):
            atomic_replace(temporary, target)
            return True
        temporary.unlink(missing_ok=True)
    return False


def _valid_image_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 32:
        return False
    try:
        header = path.read_bytes()[:16]
    except OSError:
        return False
    return _image_ext(header) is not None


def _image_ext(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


def _cover_url(row) -> str:
    platform = row["platform"]
    video_id = row["platform_video_id"]
    part = row["part_key"] or ""
    suffix = f"/p{part}" if part else ""
    rel = row["thumbnail_relpath"] if "thumbnail_relpath" in row.keys() else None
    if not rel:
        return ""
    ext = Path(rel).suffix.lower().lstrip(".") or "jpg"
    if ext not in {"jpg", "jpeg", "png", "webp"}:
        return ""
    return f"techvault://app/video-cover/{platform}/{video_id}{suffix}/cover.{ext}"


def _unlink_archived(layout: VideoPathLayout, archive_root: str | None, relative: str | None) -> None:
    if not relative:
        return
    root = VideoPathLayout(archive_root) if archive_root else layout
    try:
        path = root.resolve_relative(relative)
    except Exception:
        return
    if path.is_file():
        path.unlink()
    parent = path.parent
    metadata = parent / "metadata.json"
    if metadata.is_file() and not any(parent.glob("*.mp4")) and not any(parent.glob("*.webm")) and not any(parent.glob("*.mkv")):
        metadata.unlink()
    covers = list(parent.glob("cover.*"))
    if not any(item.suffix.lower() in {".mp4", ".webm", ".mkv"} for item in parent.iterdir() if item.is_file()):
        for cover in covers:
            cover.unlink(missing_ok=True)


def _cleanup_staging(staging: Path) -> None:
    if staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)


def _duration(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
