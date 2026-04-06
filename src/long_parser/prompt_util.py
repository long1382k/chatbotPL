"""Load prompt text shipped under ``long_parser/prompts/``."""

from __future__ import annotations

from importlib import resources


def load_prompt(filename: str) -> str:
    """Return UTF-8 text from ``prompts/<filename>`` (trailing newline stripped)."""
    path = resources.files("long_parser").joinpath("prompts", filename)
    text = path.read_text(encoding="utf-8")
    return text.rstrip("\n")
