#!/usr/bin/env python3
"""CLI: type1 JSON → retrieval JSON (wrapper; logic in long_parser.retrieval.type1_to_retrieval)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from long_parser.retrieval.type1_to_retrieval import main  # noqa: E402

if __name__ == "__main__":
    main()
