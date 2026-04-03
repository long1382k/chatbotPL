#!/usr/bin/env python3
"""CLI: batch-parse .doc in data/input/type1 → artifacts/type1."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from long_parser.parser import main  # noqa: E402

if __name__ == "__main__":
    main()
