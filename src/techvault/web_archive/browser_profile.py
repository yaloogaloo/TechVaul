"""Persistent QWebEngine profiles for anonymous vs optional Zhihu login sessions."""

from __future__ import annotations

import os
from pathlib import Path


def browser_profiles_root() -> Path:
    override = os.environ.get("TECHVAULT_BROWSER_PROFILES")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home()
    return root / "TechVault" / "browser_profiles"


def zhihu_profile_dir(*, anonymous: bool = False) -> Path:
    name = "zhihu_anonymous" if anonymous else "zhihu"
    path = browser_profiles_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def clear_zhihu_session() -> Path:
    """Remove persisted Zhihu login profile storage (cookies etc.)."""
    import shutil

    path = browser_profiles_root() / "zhihu"
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path
