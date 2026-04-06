"""Shared Ollama URL / host normalization."""

from __future__ import annotations


def normalize_ollama_base(raw: str) -> str:
    s = raw.strip().rstrip("/")
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"http://{s}"
