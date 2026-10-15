"""JSON helpers for WebChannel slots."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def ok(data: Any = None) -> str:
    return json.dumps({"ok": True, "data": _normalize(data)}, ensure_ascii=False)


def err(code: str, message: str, *, data: Any = None) -> str:
    payload = {"ok": False, "error": {"code": code, "message": message}}
    if data is not None:
        payload["data"] = _normalize(data)
    return json.dumps(payload, ensure_ascii=False)


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _normalize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return str(value)
