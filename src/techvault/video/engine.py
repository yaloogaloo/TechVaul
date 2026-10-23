"""Call yt-dlp. This module does not implement extractors or stream protocols."""

from __future__ import annotations

import errno
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError

from techvault.video.deps import ffmpeg_directory, js_runtimes
from techvault.video.errors import VideoArchiveError

ProgressCallback = Callable[[dict], None]


class YtDlpEngine:
    """One yt-dlp invocation. Cancellation stops this task's ffmpeg child only."""

    def probe(self, url: str, *, part_index: int | None = None, extra: dict | None = None) -> dict:
        options = self.build_options(staging=None, part_index=part_index, extra=extra)
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise _classify(exc) from exc
        if not isinstance(info, dict):
            raise VideoArchiveError("NO_METADATA", "yt-dlp 没有返回视频信息")
        return _one_entry(info, part_index)

    def download(
        self,
        url: str,
        *,
        staging: Path,
        format_spec: str,
        container: str,
        part_index: int | None,
        cancel_event: Event | None,
        on_progress: ProgressCallback | None = None,
        extra: dict | None = None,
    ) -> Path:
        staging.mkdir(parents=True, exist_ok=True)
        options = self.build_options(
            staging=staging,
            part_index=part_index,
            format_spec=format_spec,
            container=container,
            extra=extra,
        )
        event = cancel_event or Event()

        def hook(data: dict) -> None:
            if event.is_set():
                raise DownloadCancelled("cancelled")
            if on_progress is not None and data.get("status") == "downloading":
                on_progress(_public_progress(data))

        def post_hook(data: dict) -> None:
            if event.is_set():
                raise DownloadCancelled("cancelled")
            if on_progress is not None and data.get("status") in {"started", "processing", "finished"}:
                on_progress({"phase": "MERGING", "status": data.get("status") or ""})

        options["progress_hooks"] = [hook]
        options["postprocessor_hooks"] = [post_hook]
        try:
            with _cancellable_processes(event):
                with YoutubeDL(options) as ydl:
                    if event.is_set():
                        raise DownloadCancelled("cancelled")
                    info = ydl.extract_info(url, download=True)
        except DownloadCancelled as exc:
            raise VideoArchiveError("CANCELLED", "已取消下载") from exc
        except DownloadError as exc:
            raise _classify(exc) from exc
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise VideoArchiveError("DISK_FULL", "磁盘空间不足") from exc
            raise VideoArchiveError("IO_ERROR", "写入视频文件失败") from exc
        if not isinstance(info, dict):
            raise VideoArchiveError("DOWNLOAD_FAILED", "yt-dlp 没有完成下载")
        info = _one_entry(info, part_index)
        path = _downloaded_file(info, staging)
        if path is None:
            raise VideoArchiveError("DOWNLOAD_FAILED", "下载结束，但没有得到视频文件")
        return path

    def build_options(
        self,
        *,
        staging: Path | None,
        part_index: int | None,
        format_spec: str | None = None,
        container: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        runtimes = js_runtimes()
        options: dict = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": part_index is None,
            "ignoreerrors": False,
            "retries": 3,
            "fragment_retries": 3,
            "continuedl": True,
            "nopart": False,
            "overwrites": True,
            "windowsfilenames": True,
            "cachedir": False,
            "js_runtimes": runtimes or {"node": {}},
            "remote_components": [],
            "allowed_extractors": ["youtube", "Bilibili", "BiliBili", "BiliIntl"],
            "socket_timeout": 20,
        }
        ffmpeg = ffmpeg_directory()
        if ffmpeg:
            options["ffmpeg_location"] = ffmpeg
        if part_index is not None:
            options["playlist_items"] = str(part_index)
        if format_spec:
            options["format"] = format_spec
        if container in {"mp4", "webm", "mkv"}:
            options["merge_output_format"] = container
        if staging is not None:
            options["outtmpl"] = {"default": str(staging / "download.%(ext)s")}
            options["paths"] = {"home": str(staging), "temp": str(staging)}
        if extra:
            options.update(extra)
        return options


def _one_entry(info: dict, part_index: int | None) -> dict:
    if info.get("_type") not in {"playlist", "multi_video"}:
        return info
    entries = [item for item in info.get("entries") or [] if isinstance(item, dict)]
    if part_index is not None:
        for item in entries:
            playlist_index = item.get("playlist_index") or item.get("n_entries")
            if playlist_index == part_index or item.get("playlist_autonumber") == part_index:
                return item
        if len(entries) == 1:
            return entries[0]
        raise VideoArchiveError("PART_NOT_FOUND", f"没有找到第 {part_index} P")
    if len(entries) == 1:
        return entries[0]
    raise VideoArchiveError("PLAYLIST_UNSUPPORTED", "当前版本不批量下载播放列表")


def _downloaded_file(info: dict, staging: Path) -> Path | None:
    candidates: list[Path] = []
    for item in info.get("requested_downloads") or []:
        if isinstance(item, dict) and item.get("filepath"):
            candidates.append(Path(item["filepath"]))
    for path in staging.glob("download.*"):
        if path.suffix.lower() not in {".part", ".ytdl", ".temp"} and path.is_file():
            candidates.append(path)
    files = [path for path in candidates if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_size)


def _public_progress(data: dict) -> dict:
    downloaded = data.get("downloaded_bytes")
    total = data.get("total_bytes") or data.get("total_bytes_estimate")
    return {
        "phase": "DOWNLOADING",
        "downloaded": int(downloaded) if isinstance(downloaded, (int, float)) else None,
        "total": int(total) if isinstance(total, (int, float)) else None,
        "speed": data.get("speed") if isinstance(data.get("speed"), (int, float)) else None,
    }


def _classify(exc: Exception) -> VideoArchiveError:
    text = str(exc)
    lowered = text.lower()
    if isinstance(exc, ExtractorError) and getattr(exc, "expected", False) is False and "video unavailable" not in lowered:
        pass
    if any(token in lowered for token in ("sign in", "login required", "please log in", "members only", "premium")):
        if "geo" not in lowered and "not available in your country" not in lowered:
            return VideoArchiveError("LOGIN_REQUIRED", "这个视频需要登录或会员权限，当前不会读取浏览器 Cookie")
    if any(token in lowered for token in ("not available in your country", "geo restricted", "blocked it in your country")):
        return VideoArchiveError("GEO_RESTRICTED", "这个视频在当前地区不可用")
    if any(token in lowered for token in ("private video", "video unavailable", "has been removed", "was deleted", "不存在")):
        return VideoArchiveError("UNAVAILABLE", "视频不可用或已被删除")
    if "confirm you’re not a bot" in lowered or "confirm you're not a bot" in lowered or "sign in to confirm" in lowered:
        return VideoArchiveError("CHALLENGE", "YouTube 要求验证，当前版本不绕过验证码")
    if "ffmpeg" in lowered and "not installed" in lowered:
        return VideoArchiveError("FFMPEG_MISSING", "需要 FFmpeg 才能合并音视频")
    if "javascript runtime" in lowered or "no supported javascript runtime" in lowered:
        return VideoArchiveError("JS_RUNTIME_MISSING", "YouTube 需要可用的 Deno 或 Node.js")
    if "no space" in lowered or "disk full" in lowered:
        return VideoArchiveError("DISK_FULL", "磁盘空间不足")
    return VideoArchiveError("DOWNLOAD_FAILED", "下载没有完成")


@contextmanager
def _cancellable_processes(cancel_event: Event):
    import yt_dlp.postprocessor.ffmpeg as ffmpeg_mod

    original = ffmpeg_mod.Popen

    class StoppablePopen(original):
        @classmethod
        def run(cls, *args, timeout=None, input=None, **kwargs):  # noqa: A002
            if cancel_event.is_set():
                raise DownloadCancelled("cancelled")
            if input is not None and kwargs.get("stdin") is None:
                kwargs["stdin"] = subprocess.PIPE
            proc = cls(*args, **kwargs)
            sent_input = False
            try:
                while True:
                    if cancel_event.is_set():
                        _kill_tree(proc.pid)
                        raise DownloadCancelled("cancelled")
                    try:
                        stdout, stderr = proc.communicate(timeout=0.2, input=None if sent_input else input)
                        sent_input = True
                        text_mode = bool(kwargs.get("text") or kwargs.get("universal_newlines"))
                        blank = "" if text_mode else b""
                        return stdout or blank, stderr or blank, proc.returncode
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if proc.poll() is None:
                    _kill_tree(proc.pid)

    ffmpeg_mod.Popen = StoppablePopen
    try:
        yield
    finally:
        ffmpeg_mod.Popen = original


def _kill_tree(pid: int | None) -> None:
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return
