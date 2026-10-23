"""Resolve README image URLs onto public https targets.

Relative paths use the repository raw content base. They are never joined onto
a local archive path.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

_UNSAFE_SCHEMES = {"javascript", "vbscript", "data", "file", "blob"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return url.strip()
    port = parts.port
    netloc = host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    path = quote(unquote(parts.path), safe="/%")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def github_raw_url(url: str) -> str:
    """Rewrite GitHub blob/raw pages onto raw.githubusercontent.com when possible."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host != "github.com":
        return url.strip()
    segments = [segment for segment in parts.path.split("/") if segment != ""]
    if len(segments) < 5 or segments[2] not in {"blob", "raw"}:
        return url.strip()
    owner, repo, ref = segments[0], segments[1], segments[3]
    rest = "/".join(segments[4:])
    if not owner or not repo or not ref or not rest:
        return url.strip()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rest}"


def resolve_readme_image_url(
    raw: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    allow_relative: bool,
) -> str | None:
    """Return a canonical https/http URL, or None when the reference is unusable."""
    value = (raw or "").strip()
    if not value or value.startswith("#"):
        return None
    if value.startswith("//"):
        value = "https:" + value
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme in _UNSAFE_SCHEMES:
        return None
    if scheme in {"http", "https"}:
        return canonical_url(github_raw_url(value))
    if scheme or not allow_relative:
        return None
    if not _safe_component(owner) or not _safe_component(repo):
        return None
    ref = branch.strip() or "HEAD"
    if not _safe_ref(ref):
        ref = "HEAD"
    base = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/{quote(ref)}/"
    absolute = urljoin(base, value)
    resolved = urlsplit(absolute)
    if resolved.scheme.lower() not in {"http", "https"}:
        return None
    if (resolved.hostname or "").lower() != "raw.githubusercontent.com":
        return None
    return canonical_url(absolute)


def _safe_component(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return "/" not in value and "\\" not in value and ".." not in value


def _safe_ref(value: str) -> bool:
    if not value or value in {".", ".."} or "\\" in value:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))
