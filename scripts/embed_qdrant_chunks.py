#!/usr/bin/env python3
"""CLI: embed one retrieval JSON and upsert chunks to Qdrant."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from long_parser.embedding.embed_qdrant_chunks import main  # noqa: E402

if __name__ == "__main__":
    main()
