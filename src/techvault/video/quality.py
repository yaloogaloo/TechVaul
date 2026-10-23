"""Choose a real yt-dlp format. Heights and sizes come from the extractor."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FormatChoice:
    key: str
    label: str
    height: int | None
    format_spec: str
    container: str
    vcodec: str
    acodec: str
    filesize: int | None
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "height": self.height,
            "container": self.container,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
            "filesize": self.filesize,
            "note": self.note,
        }


def choices_for(info: dict) -> list[FormatChoice]:
    formats = [item for item in info.get("formats") or [] if isinstance(item, dict)]
    videos = [item for item in formats if _video_only(item)]
    audios = [item for item in formats if _audio_only(item)]
    progressive = [item for item in formats if _progressive(item)]
    available: list[FormatChoice] = []
    for key, label, limit in (
        ("auto", "自动推荐", 1080),
        ("best", "最佳可用", None),
        ("1080", "1080p", 1080),
        ("720", "720p", 720),
        ("480", "480p", 480),
    ):
        picked = _pick(videos, audios, progressive, limit, allow_below=key in {"auto", "best"})
        if picked is None and key in {"auto", "best"}:
            picked = _unknown_container(formats)
        if picked is None:
            continue
        if key in {"1080", "720", "480"} and (picked.height or 0) < int(key):
            continue
        note = ""
        if key == "auto" and picked.height and picked.height < 1080:
            note = f"最高可用 {picked.height}p"
        elif picked.height is None and key in {"auto", "best"}:
            note = "清晰度未在元数据中给出"
        available.append(
            FormatChoice(
                key=key,
                label=label,
                height=picked.height,
                format_spec=picked.spec,
                container=picked.container,
                vcodec=picked.vcodec,
                acodec=picked.acodec,
                filesize=picked.filesize,
                note=note,
            )
        )
    return available


def choice_by_key(info: dict, key: str) -> FormatChoice | None:
    for item in choices_for(info):
        if item.key == key:
            return item
    return None


@dataclass(frozen=True)
class _Picked:
    spec: str
    height: int | None
    container: str
    vcodec: str
    acodec: str
    filesize: int | None


def _pick(videos, audios, progressive, limit: int | None, *, allow_below: bool) -> _Picked | None:
    video = _best(videos, limit)
    audio = _best_audio(audios, prefer_m4a=bool(video and _ext(video) == "mp4"))
    if video is not None and audio is not None:
        height = _height(video)
        if limit is not None and height is not None and height > limit and not allow_below:
            return None
        container = "mp4" if _ext(video) == "mp4" and _ext(audio) in {"m4a", "mp4"} else "mkv"
        return _Picked(
            spec=f"{video.get('format_id')}+{audio.get('format_id')}",
            height=height,
            container=container,
            vcodec=str(video.get("vcodec") or ""),
            acodec=str(audio.get("acodec") or ""),
            filesize=_sum_size(video, audio),
        )
    single = _best(progressive, limit)
    if single is None:
        return None
    height = _height(single)
    if limit is not None and height is not None and height > limit and not allow_below:
        return None
    container = _ext(single) if _ext(single) in {"mp4", "webm", "mkv"} else "mkv"
    return _Picked(
        spec=str(single.get("format_id")),
        height=height,
        container=container,
        vcodec=str(single.get("vcodec") or ""),
        acodec=str(single.get("acodec") or ""),
        filesize=_size(single),
    )


def _best(formats: list[dict], limit: int | None) -> dict | None:
    ranked = []
    for item in formats:
        height = _height(item)
        if height is None:
            continue
        if limit is not None and height > limit:
            continue
        ranked.append(item)
    if not ranked and limit is not None:
        return None
    pool = ranked or [item for item in formats if _height(item) is not None]
    if not pool:
        return None
    return max(pool, key=lambda item: (_height(item) or 0, 1 if _ext(item) == "mp4" else 0, _tbr(item)))


def _best_audio(formats: list[dict], *, prefer_m4a: bool) -> dict | None:
    if not formats:
        return None
    return max(
        formats,
        key=lambda item: (
            1 if prefer_m4a and _ext(item) == "m4a" else 0,
            _tbr(item),
        ),
    )


def _unknown_container(formats: list[dict]) -> _Picked | None:
    candidates = [
        item
        for item in formats
        if _ext(item) in {"mp4", "webm", "mkv"}
        and not _video_only(item)
        and not _progressive(item)
        and item.get("format_id")
    ]
    if not candidates:
        return None
    item = max(candidates, key=lambda entry: (1 if _ext(entry) == "mp4" else 0, _tbr(entry)))
    container = _ext(item)
    return _Picked(
        spec=str(item.get("format_id")),
        height=None,
        container=container,
        vcodec="",
        acodec="",
        filesize=_size(item),
    )


def _video_only(item: dict) -> bool:
    return _codec(item.get("vcodec")) and not _codec(item.get("acodec")) and _height(item) is not None


def _audio_only(item: dict) -> bool:
    return _codec(item.get("acodec")) and not _codec(item.get("vcodec"))


def _progressive(item: dict) -> bool:
    return _codec(item.get("vcodec")) and _codec(item.get("acodec")) and _height(item) is not None


def _codec(value: object) -> bool:
    return isinstance(value, str) and value not in {"", "none"}


def _height(item: dict) -> int | None:
    value = item.get("height")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _ext(item: dict) -> str:
    value = item.get("ext")
    return value.lower() if isinstance(value, str) else ""


def _tbr(item: dict) -> float:
    value = item.get("tbr") or item.get("abr") or item.get("vbr") or 0
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _size(item: dict) -> int | None:
    for key in ("filesize", "filesize_approx"):
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return None


def _sum_size(video: dict, audio: dict) -> int | None:
    left = _size(video)
    right = _size(audio)
    if left is None or right is None:
        return None
    return left + right
