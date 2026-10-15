"""Download and localize article images for offline reading."""

from __future__ import annotations

import hashlib
import re
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from techvault.offline.links import _walk
from techvault.web_archive.paths import WebPathLayout

_ZHIHU_HOST_SUFFIXES = (
    "zhimg.com",
    "zhihu.com",
)
_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg|bmp)(?:$|\?)", re.I)


def is_downloadable_image(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if any(host == s or host.endswith("." + s) for s in _ZHIHU_HOST_SUFFIXES):
        return True
    if _EXT_RE.search(parsed.path):
        return True
    return False


def media_filename(url: str) -> str:
    prefix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    name = unquote(urlparse(url).path).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[<>:\"|?*]", "_", name)
    if name in {"", ".", ".."} or len(name) > 80:
        name = "image.bin"
    if "." not in name:
        name = f"{name}.img"
    return f"{prefix}_{name}"


def localize_article_images(
    html: str,
    layout: WebPathLayout,
    article_root: Path,
    *,
    job_id: str,
    client: httpx.Client | None = None,
    cancel_check=None,
) -> tuple[str, int, int]:
    """Return (html, total_images, missing_count)."""
    owns = client is None
    http = client or httpx.Client(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.zhihu.com/",
        },
        follow_redirects=True,
        timeout=30.0,
    )
    total = 0
    missing = 0
    counter = {"n": 0}

    def fetch(url: str) -> tuple[str, Path] | None:
        nonlocal missing
        if cancel_check is not None and cancel_check():
            return None
        counter["n"] += 1
        filename = media_filename(url)
        final = layout.media_file(article_root, filename)
        if final.is_file() and final.stat().st_size > 0:
            return f"media/{final.name}", final
        part = layout.tmp_dir(job_id) / f"{counter['n']}.part"
        part.parent.mkdir(parents=True, exist_ok=True)
        try:
            with http.stream("GET", url) as response:
                if response.status_code >= 400:
                    missing += 1
                    return None
                with part.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if cancel_check is not None and cancel_check():
                            return None
                        handle.write(chunk)
            final.parent.mkdir(parents=True, exist_ok=True)
            part.replace(final)
            return f"media/{final.name}", final
        except OSError:
            missing += 1
            part.unlink(missing_ok=True)
            return None
        except httpx.HTTPError:
            missing += 1
            part.unlink(missing_ok=True)
            return None

    def on_tag(tag: str, attrs: list[tuple[str, str | None]]):
        nonlocal total, missing
        if tag.lower() != "img":
            return attrs, None
        values = {key.lower(): value for key, value in attrs}
        src = values.get("src") or values.get("data-src") or values.get("data-original") or ""
        alt = values.get("alt") or ""
        if not src or src.startswith("data:"):
            return attrs, None
        total += 1
        if not is_downloadable_image(src):
            missing += 1
            return attrs, _placeholder(src, alt)
        saved = fetch(src)
        if saved is None:
            return attrs, _placeholder(src, alt)
        media_src, _path = saved
        alt_attr = f' alt="{escape(alt, quote=True)}"' if alt else ""
        return attrs, f'<img src="{escape(media_src, quote=True)}"{alt_attr}>'

    try:
        rewritten = _walk(html, on_tag)
    finally:
        if owns:
            http.close()
    return rewritten, total, missing


def _placeholder(url: str, alt: str) -> str:
    label = escape(alt or "图片未下载", quote=False)
    href = escape(url, quote=True)
    return f'<a href="{href}" rel="noopener noreferrer">[{label}]</a>'
