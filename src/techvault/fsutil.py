"""Small filesystem helpers shared across archive publishers."""

from __future__ import annotations

import os
from pathlib import Path


def atomic_replace(part_path: Path | str, final_path: Path | str) -> None:
    """Publish a staged file onto its final path (os.replace is atomic on same volume)."""
    part = Path(part_path)
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part, final)
