#!/usr/bin/env python3
"""
Generate per-chunk summaries (content.summary) using Ollama.

Used by the web indexer endpoint ``POST /api/retrieval/summarize``.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from long_parser.ollama_util import normalize_ollama_base
from long_parser.prompt_util import load_prompt

CHUNK_SUMMARIZE_SYSTEM_VI = load_prompt("chunk_summarize_system_vi.txt")
CHUNK_SUMMARIZE_USER_VI = load_prompt("chunk_summarize_user_vi.txt")


def _chat_complete(
    *,
    base_url: str,
    model: str,
    user_content: str,
    timeout: int,
    temperature: float,
    max_tokens: int,
) -> str:
    """
    Ollama API:
      POST /api/chat
      Body: { model, messages, stream:false, options:{temperature,num_predict} }
    """
    url = base_url.rstrip("/") + "/api/chat"
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": CHUNK_SUMMARIZE_SYSTEM_VI},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    msg = data.get("message") or {}
    return (msg.get("content") or "").strip()


def _with_retries(fn, *, max_attempts: int = 5) -> str:
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                time.sleep((2**attempt) + 0.25)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt < max_attempts - 1:
                time.sleep((2**attempt) + 0.25)
                continue
            raise
    assert last
    raise last


def _legal_text_for_chunk(chunk: dict[str, Any], max_chars: int) -> str:
    c = chunk.get("content") or {}
    raw = (c.get("return_text") or "").strip() or (c.get("search_text") or "").strip()
    if max_chars > 0 and len(raw) > max_chars:
        raw = raw[:max_chars].rstrip() + "\n\n[… rút gọn do vượt giới hạn độ dài …]"
    return raw


def ensure_chunk_summaries(
    doc: dict[str, Any],
    *,
    base_url: str | None = None,
    model: str | None = None,
    delay_s: float = 0.2,
    max_input_chars: int = 24000,
    timeout: int = 180,
    temperature: float = 0.2,
    max_tokens: int = 256,
    force: bool = False,
) -> int:
    """
    Mutates `doc` in-place by filling `chunk.content.summary`.

    Returns number of chunks for which summary was generated (not skipped).
    """
    chunks = doc.get("children_chunks") or []
    if not isinstance(chunks, list) or not chunks:
        return 0

    ollama_base = normalize_ollama_base(
        base_url or os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    )
    ollama_model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")

    generated = 0
    for ch in chunks:
        c = ch.setdefault("content", {})
        existing = (c.get("summary") or "").strip()
        if existing and not force:
            continue

        legal_text = _legal_text_for_chunk(ch, max_input_chars)
        if not legal_text:
            c["summary"] = ""
            continue

        user_msg = CHUNK_SUMMARIZE_USER_VI.format(legal_text=legal_text)

        def call() -> str:
            return _chat_complete(
                base_url=ollama_base,
                model=ollama_model,
                user_content=user_msg,
                timeout=timeout,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        summary = _with_retries(call)
        summary = re.sub(r"\s+", " ", summary).strip()
        c["summary"] = summary
        generated += 1

        if delay_s > 0:
            time.sleep(delay_s)

    return generated

