"""Redaction helpers. Token material must not reach log files."""

from __future__ import annotations

import re

_BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+")
_BEARER_WORD = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_QUERY_SECRET = re.compile(
    r"(?i)((?:access_token|token|authorization)\s*[=:]\s*)[^&\s]+"
)
_USERINFO = re.compile(r"(?i)(https?://[^/\s:@]+):([^@\s/]+)@")
_GITHUB_TOKEN = re.compile(r"\b(?:ghp_|github_pat_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+")


def redact(text: str) -> str:
    redacted = _BEARER.sub(r"\1[REDACTED]", text)
    redacted = _BEARER_WORD.sub("Bearer [REDACTED]", redacted)
    redacted = _QUERY_SECRET.sub(r"\1[REDACTED]", redacted)
    redacted = _USERINFO.sub(r"\1:[REDACTED]@", redacted)
    redacted = _GITHUB_TOKEN.sub("[REDACTED]", redacted)
    return redacted
