"""Project entry point for the TechVault desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from techvault.ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
