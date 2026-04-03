"""Resolved paths relative to the repository root (parent of ``src/``)."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_INPUT_DIR = DATA_DIR / "input"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
CONFIG_DIR = PROJECT_ROOT / "config"
CONVERTED_DIR = DATA_DIR / "converted"
CHUNKED_DIR = DATA_DIR / "chunked"
