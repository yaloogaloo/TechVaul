"""Recognize YouTube and Bilibili inputs without calling a downloader."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from techvault.readme_images.fetch import is_public_http_url
from techvault.video.errors import VideoArchiveError

_BV = re.compile(r"^BV[1-9A-HJ-NP-Za-km-z]{10}$")
_YOUTUBE_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")
_SHORT = re.compile(r"^https?://(?:www\.)?b23\.tv/([0-9A-Za-z]+)/?$", re.IGNORECASE)
_HOSTISH = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)", re.IGNORECASE)
_MAX_REDIRECTS = 5


@dataclass(frozen=True)
class VideoIdentity:
    platform: str
    platform_video_id: str
    part_key: str
    part_index: int | None
    canonical_url: str
    display_name: str
    input_kind: str = "url"

    def as_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "platformVideoId": self.platform_video_id,
            "partKey": self.part_key,
            "partIndex": self.part_index,
            "canonicalUrl": self.canonical_url,
            "displayName": self.display_name,
            "inputKind": self.input_kind,
        }


def parse_video_input(value: str, *, client: httpx.Client | None = None) -> VideoIdentity:
    """Single entry for video identity. BV checks do not run against other text."""
    text = (value or "").strip()
    if not text:
        raise VideoArchiveError("EMPTY_INPUT", "请输入视频链接或 BV 号")
    if _BV.fullmatch(text):
        return replace(_bilibili_identity(text, None), input_kind="bv")
    normalized, kind = _normalize_video_input(text)
    expanded = _expand_short_link(normalized, client) if _SHORT.match(normalized) else normalized
    parts = urlsplit(expanded.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host in {"b23.tv"}:
        expanded = _expand_short_link(expanded, client)
        parts = urlsplit(expanded)
        host = (parts.hostname or "").lower().removeprefix("www.")
    if _is_bilibili_host(parts.hostname or ""):
        return replace(_parse_bilibili(parts), input_kind=kind)
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
        return replace(_parse_youtube(host, parts), input_kind=kind)
    raise VideoArchiveError("UNSUPPORTED_PLATFORM", "当前只支持 YouTube 和 Bilibili 的单个视频")


def _normalize_video_input(text: str) -> tuple[str, str]:
    if "://" in text:
        scheme = text.split("://", 1)[0].lower()
        if scheme not in {"http", "https"}:
            raise VideoArchiveError("UNSUPPORTED_PLATFORM", "只接受 http 或 https 视频链接")
        return text, "url"
    if text.startswith("//") and _HOSTISH.match(text[2:]):
        return "https:" + text, "schemeless"
    if _HOSTISH.match(text):
        return "https://" + text, "schemeless"
    if text.upper().startswith("BV"):
        raise VideoArchiveError("INVALID_BV", "这不是有效的 BV 号")
    raise VideoArchiveError("UNRECOGNIZED", "无法识别这个视频地址。请输入 YouTube、Bilibili 链接或 BV 号")


def _is_bilibili_host(host: str) -> bool:
    name = host.lower().removeprefix("www.")
    return name == "bilibili.com" or name.endswith(".bilibili.com")


def _parse_bilibili(parts) -> VideoIdentity:
    path = parts.path or ""
    lowered = path.lower()
    if any(token in lowered for token in ("/medialist", "/list/", "/fav", "/cheese/", "/bangumi/")):
        raise VideoArchiveError("PLAYLIST_UNSUPPORTED", "当前版本不批量下载收藏夹或视频列表")
    match = re.search(r"/video/(BV[0-9A-Za-z]+)", path, re.IGNORECASE)
    if match is None:
        raise VideoArchiveError("NOT_A_VIDEO", "这个 Bilibili 链接不是单个视频")
    token = match.group(1)
    if not _BV.fullmatch(token):
        raise VideoArchiveError("INVALID_BV", "链接中的 BV 号无效")
    page = _page_query(parts.query)
    return _bilibili_identity(token, page)


def _bilibili_identity(bvid: str, page: int | None) -> VideoIdentity:
    part_index = None if page in (None, 1) else page
    part_key = "" if part_index is None else str(part_index)
    query = "" if part_index is None else "?" + urlencode({"p": str(part_index)})
    return VideoIdentity(
        platform="bilibili",
        platform_video_id=bvid,
        part_key=part_key,
        part_index=part_index,
        canonical_url=f"https://www.bilibili.com/video/{bvid}{query}",
        display_name="Bilibili",
    )


def _page_query(query: str) -> int | None:
    values = parse_qs(query, keep_blank_values=False).get("p") or []
    if not values:
        return None
    raw = values[0].strip()
    if not raw.isdigit() or int(raw) < 1:
        raise VideoArchiveError("INVALID_PART", "分 P 编号无效")
    return int(raw)


def _parse_youtube(host: str, parts) -> VideoIdentity:
    path = parts.path or ""
    query = parse_qs(parts.query, keep_blank_values=False)
    if path.startswith("/playlist") or (host != "youtu.be" and "v" not in query and "/shorts/" not in path and "/watch" not in path):
        if "list" in query or path.startswith("/playlist") or path.startswith("/channel") or path.startswith("/c/") or path.startswith("/user/") or path.startswith("/@"):
            raise VideoArchiveError("PLAYLIST_UNSUPPORTED", "当前版本不批量下载播放列表或频道")
    video_id = ""
    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
    elif "/shorts/" in path:
        video_id = path.split("/shorts/", 1)[1].split("/")[0]
    elif "/live/" in path:
        video_id = path.split("/live/", 1)[1].split("/")[0]
    else:
        found = query.get("v") or []
        video_id = found[0] if found else ""
    video_id = video_id.strip()
    if not _YOUTUBE_ID.fullmatch(video_id):
        if "list" in query and "v" not in query:
            raise VideoArchiveError("PLAYLIST_UNSUPPORTED", "当前版本不批量下载播放列表或频道")
        raise VideoArchiveError("INVALID_YOUTUBE", "这不是有效的 YouTube 视频链接")
    return VideoIdentity(
        platform="youtube",
        platform_video_id=video_id,
        part_key="",
        part_index=None,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        display_name="YouTube",
    )


def _expand_short_link(url: str, client: httpx.Client | None) -> str:
    current = url.strip()
    owns = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=False, trust_env=False)
    try:
        for _ in range(_MAX_REDIRECTS):
            if not is_public_http_url(current):
                raise VideoArchiveError("UNSAFE_URL", "短链接目标不在允许的公网范围内")
            response = http.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location") or ""
                if not location:
                    raise VideoArchiveError("REDIRECT", "短链接没有返回目标地址")
                current = str(response.url.join(location))
                continue
            if response.status_code >= 400:
                raise VideoArchiveError("NOT_FOUND", "短链接无法打开")
            final = str(response.url)
            break
        else:
            raise VideoArchiveError("REDIRECT", "短链接重定向次数过多")
    except VideoArchiveError:
        raise
    except httpx.TimeoutException as exc:
        raise VideoArchiveError("TIMEOUT", "解析短链接超时") from exc
    except httpx.HTTPError as exc:
        raise VideoArchiveError("NETWORK", "解析短链接失败") from exc
    finally:
        if owns:
            http.close()
    parts = urlsplit(final)
    if not _is_bilibili_host(parts.hostname or ""):
        raise VideoArchiveError("NOT_A_VIDEO", "短链接没有指向 Bilibili 视频")
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, parts.query, ""))
