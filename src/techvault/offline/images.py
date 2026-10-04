"""Download allow-listed images and turn every other image into a text link."""

from __future__ import annotations

import hashlib
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlparse

from techvault.archive.downloader import DownloadRequest, DownloadResult, download_release_file
from techvault.archive.paths import PathLayout
from techvault.errors import UnsafePathError
from techvault.github.client import GitHubClient
from techvault.offline.links import _walk

_IMAGE_HOSTS = {
    "raw.githubusercontent.com",
    "camo.githubusercontent.com",
    "private-user-images.githubusercontent.com",
}


def image_kind(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path).lower()
    if host in _IMAGE_HOSTS:
        return "image"
    if host == "github.com" and "/user-attachments/" in path:
        return "attachment"
    if host == "github.com" and "avatar" in path:
        return "avatar"
    return "external"


def media_filename(url: str) -> str:
    prefix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    name = unquote(urlparse(url).path).replace("\\", "/").rsplit("/", 1)[-1]
    if name in {"", ".", ".."}:
        name = "image"
    return f"{prefix}_{name}"


def download_page_image(
    client: GitHubClient,
    layout: PathLayout,
    *,
    job_id: str,
    asset_id: int,
    url: str,
    final_path: Path,
    after_stream=None,
) -> DownloadResult:
    return download_release_file(
        client,
        DownloadRequest(
            url=url,
            part_path=layout.part_file(job_id, asset_id),
            final_path=final_path,
            metadata_size=None,
            upstream_digest=None,
        ),
        after_stream=after_stream,
    )


def localize_images(html: str, fetch) -> tuple[str, dict[str, Path]]:
    """Replace image tags. fetch(url) returns a media src and path, or None."""
    state = {"avatars": 0}
    located: dict[str, Path] = {}

    def on_tag(tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "img":
            return attrs, None
        values = {key.lower(): value for key, value in attrs}
        src = values.get("src") or ""
        alt = values.get("alt") or ""
        kind = image_kind(src) if src else "external"
        if kind == "avatar":
            if state["avatars"] >= 1:
                return attrs, _external_link(src)
            state["avatars"] += 1
        if kind in {"image", "attachment", "avatar"} and src:
            saved = fetch(src)
            if saved is not None:
                media_src, path = saved
                located[media_src] = path
                alt_attr = f' alt="{escape(alt, quote=True)}"' if alt else ""
                return attrs, f'<img src="{escape(media_src, quote=True)}"{alt_attr}>'
        if src:
            return attrs, _external_link(src)
        return attrs, escape(alt) if alt else ""

    return _walk(html, on_tag), located


def _external_link(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https", "mailto"}:
        return "[External image]"
    return f'<a href="{escape(url, quote=True)}">[External image]</a>'
