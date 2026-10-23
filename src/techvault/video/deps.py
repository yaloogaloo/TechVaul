"""Detect yt-dlp, FFmpeg, and a JavaScript runtime. Never auto-upgrades them."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_CACHE: dict[str, ToolCheck] = {}


@dataclass(frozen=True)
class ToolCheck:
    name: str
    available: bool
    version: str
    path: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "available": self.available,
            "version": self.version,
            "path": self.path,
            "detail": self.detail,
        }


def dependency_report() -> dict[str, object]:
    ytdlp = _ytdlp()
    ejs = _ejs()
    ffmpeg = _binary("ffmpeg")
    ffprobe = _binary("ffprobe")
    node = _binary("node")
    deno = _binary("deno")
    runtime = "deno" if deno.available else "node" if node.available else ""
    core = ytdlp.available and ffmpeg.available and ffprobe.available
    return {
        "ready": core,
        "youtubeReady": core and ejs.available and bool(runtime),
        "ytdlp": ytdlp.as_dict(),
        "ejs": ejs.as_dict(),
        "ffmpeg": ffmpeg.as_dict(),
        "ffprobe": ffprobe.as_dict(),
        "node": node.as_dict(),
        "deno": deno.as_dict(),
        "jsRuntime": runtime,
        "message": _message(ytdlp, ejs, ffmpeg, ffprobe, runtime),
    }


def js_runtimes() -> dict[str, dict[str, str]]:
    report = dependency_report()
    runtime = str(report["jsRuntime"] or "")
    if runtime == "deno":
        path = str(report["deno"]["path"])
        return {"deno": {"path": path}} if path else {"deno": {}}
    if runtime == "node":
        path = str(report["node"]["path"])
        return {"node": {"path": path}} if path else {"node": {}}
    return {}


def tool_path(name: str) -> str | None:
    check = _binary(name)
    return check.path or None


def ffmpeg_directory() -> str | None:
    found = tool_path("ffmpeg")
    if not found:
        return None
    return str(Path(found).resolve().parent)


def clear_dependency_cache() -> None:
    _CACHE.clear()


def _ytdlp() -> ToolCheck:
    try:
        import yt_dlp
        from yt_dlp.version import __version__
    except ImportError:
        return ToolCheck("yt-dlp", False, "", "", "未安装 yt-dlp")
    return ToolCheck("yt-dlp", True, __version__, str(Path(yt_dlp.__file__).resolve()), "")


def _ejs() -> ToolCheck:
    try:
        import yt_dlp_ejs
    except ImportError:
        return ToolCheck("yt-dlp-ejs", False, "", "", "未安装 yt-dlp-ejs，YouTube 签名挑战无法计算")
    version = str(getattr(yt_dlp_ejs, "version", "") or "")
    return ToolCheck("yt-dlp-ejs", True, version, str(Path(yt_dlp_ejs.__file__).resolve()), "")


def _binary(name: str) -> ToolCheck:
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    found = _which(name)
    if not found:
        detail = {
            "ffmpeg": "未找到 ffmpeg。只安装 Python 包 ffmpeg 并不能合并音视频。",
            "ffprobe": "未找到 ffprobe，无法确认音视频是否完整。",
            "node": "未找到 Node.js。YouTube 需要 Deno 或显式启用的 Node。",
            "deno": "未找到 Deno。若 Node 可用，会改用 Node。",
        }.get(name, "未找到可执行文件")
        result = ToolCheck(name, False, "", "", detail)
        _CACHE[name] = result
        return result
    version = _version_line(found, name)
    result = ToolCheck(name, True, version, str(Path(found).resolve()), "")
    _CACHE[name] = result
    return result


def _which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    filename = f"{name}.exe" if os.name == "nt" else name
    for directory in _registered_path_dirs():
        candidate = directory / filename
        if candidate.is_file():
            return str(candidate)
    return None


def _registered_path_dirs() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    directories: list[Path] = []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        expanded = os.path.expandvars(str(raw))
        directories.extend(Path(part) for part in expanded.split(";") if part.strip())
    return directories


def _version_line(path: str, name: str) -> str:
    import subprocess

    try:
        completed = subprocess.run(
            [path, "--version" if name in {"node", "deno"} else "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    line = (completed.stdout or completed.stderr or "").splitlines()
    return line[0].strip() if line else ""


def _no_window() -> int:
    import subprocess

    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _message(ytdlp: ToolCheck, ejs: ToolCheck, ffmpeg: ToolCheck, ffprobe: ToolCheck, runtime: str) -> str:
    missing = []
    if not ytdlp.available:
        missing.append("yt-dlp")
    if not ffmpeg.available:
        missing.append("FFmpeg")
    if not ffprobe.available:
        missing.append("FFprobe")
    youtube_only = []
    if not ejs.available:
        youtube_only.append("yt-dlp-ejs")
    if not runtime:
        youtube_only.append("JavaScript 运行时（Deno 或 Node）")
    if missing:
        return "视频归档还不能开始：" + "、".join(missing) + " 不可用。"
    if youtube_only:
        return "YouTube 还不能解析：" + "、".join(youtube_only) + " 不可用。Bilibili 公开视频仍可下载。"
    return ""
