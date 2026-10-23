"""Identify image bytes and strip active content from SVG."""

from __future__ import annotations

import re

MAX_IMAGE_BYTES = 8 * 1024 * 1024

_SCRIPT = re.compile(
    r"<\s*(script|foreignObject|iframe|embed|object|handler)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_OPEN = re.compile(
    r"<\s*(script|foreignObject|iframe|embed|object|handler)\b[^>]*>.*",
    re.IGNORECASE | re.DOTALL,
)
_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE | re.DOTALL)
_ON_ATTR = re.compile(r"""\s+on[a-zA-Z]+\s*=\s*(['"]).*?\1""", re.DOTALL)
_JS_HREF = re.compile(
    r"""(?i)(\s(?:href|xlink:href)\s*=\s*)(['"])\s*(?:javascript|vbscript|data)\s*:[^'"]*\2"""
)


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """Return ``(extension, mime)`` from content, or None when it is not an image."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "ico", "image/x-icon"
    if data.startswith(b"BM"):
        return "bmp", "image/bmp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return "avif", "image/avif"
    svg = sanitize_svg(data)
    if svg is None:
        return None
    return "svg", "image/svg+xml"


def sanitize_svg(data: bytes) -> bytes | None:
    """Return SVG bytes with script and event handlers removed, or None."""
    if b"\x00" in data[:512]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "<svg" not in text.lower():
        return None
    cleaned = _DOCTYPE.sub("", text)
    cleaned = _SCRIPT.sub("", cleaned)
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _SCRIPT_OPEN.sub("", cleaned)
    cleaned = _ON_ATTR.sub("", cleaned)
    cleaned = _JS_HREF.sub("", cleaned)
    lowered = cleaned.lower()
    if "<svg" not in lowered or "<script" in lowered or "javascript:" in lowered:
        return None
    return cleaned.encode("utf-8")
