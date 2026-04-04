#!/usr/bin/env python3
"""
Legal RAG: retrieval (Qdrant) + generation (Ollama chat API).

Context for the LLM is built from each chunk's ``return_text`` (see retrieval JSON / Qdrant payload).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

from long_parser.retrieval.legal_retrieve import DEFAULT_MODEL, LegalRetriever

# Tunable default: how many retrieved chunks are passed into the LLM context
DEFAULT_CONTEXT_TOP_K = 10


def _normalize_ollama_base(raw: str) -> str:
    s = raw.strip().rstrip("/")
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"http://{s}"


DEFAULT_OLLAMA_URL = _normalize_ollama_base(os.environ.get("OLLAMA_HOST", "127.0.0.1:11434"))
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")

SYSTEM_PROMPT_VI = """Bạn là trợ lý tra cứu văn bản pháp luật Việt Nam.
Chỉ trả lời dựa trên ngữ cảnh được cung cấp dưới dạng các đoạn trích [1], [2], ...
Nếu ngữ cảnh không đủ để trả lời chính xác, hãy nói rõ là không có đủ thông tin trong tài liệu đã trích.
Trích dẫn ý chính có thể tham chiếu số thứ tự đoạn [n] khi phù hợp."""


def format_context_from_chunks(
    chunks: list[dict[str, Any]],
    *,
    text_key: str = "return_text",
) -> str:
    """Build a single context string from retrieved chunks (default: return_text)."""
    parts: list[str] = []
    for i, ch in enumerate(chunks, start=1):
        text = (ch.get(text_key) or "").strip()
        if not text:
            continue
        title = (ch.get("title") or "").strip()
        cid = ch.get("chunk_id") or ch.get("id")
        meta = ch.get("metadata") or {}
        hier = meta.get("hierarchy") or {}
        ch_title = (hier.get("chapter_title") or "").strip()
        header_bits = [f"[{i}]"]
        if title:
            header_bits.append(title)
        if ch_title:
            header_bits.append(f"({ch_title})")
        if cid:
            header_bits.append(f"id:{cid}")
        header = " ".join(header_bits)
        parts.append(f"{header}\n{text}")
    return "\n\n---\n\n".join(parts)


def ollama_chat(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float = 300.0,
) -> str:
    """POST /api/chat (non-streaming). Returns assistant message content."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama connection failed ({url}): {e.reason}") from e

    msg = data.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"Unexpected Ollama response: {data!r}")
    return content


def run_rag(
    query: str,
    *,
    top_k: int = DEFAULT_CONTEXT_TOP_K,
    filter_json: Optional[dict[str, Any]] = None,
    retriever: LegalRetriever,
    search_target: str = "search_text",
    ollama_url: str = DEFAULT_OLLAMA_URL,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
    ollama_timeout: float = 300.0,
) -> dict[str, Any]:
    chunks, debug = retriever.retrieve(
        query,
        top_k=top_k,
        filter_json=filter_json,
        search_target=search_target,
    )
    context = format_context_from_chunks(chunks)
    if not context.strip():
        answer = (
            "Không có đoạn văn bản nào (return_text) trong kết quả truy vấn để làm ngữ cảnh. "
            "Hãy thử đổi câu hỏi hoặc kiểm tra dữ liệu đã embed."
        )
        return {
            "query": query,
            "answer": answer,
            "chunks": chunks,
            "debug": debug,
            "context": context,
            "model": ollama_model,
            "ollama_skipped": True,
        }

    user_content = (
        f"Dưới đây là các đoạn trích từ văn bản pháp luật (ngữ cảnh):\n\n"
        f"{context}\n\n"
        f"Câu hỏi: {query.strip()}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_VI},
        {"role": "user", "content": user_content},
    ]
    answer = ollama_chat(
        base_url=ollama_url,
        model=ollama_model,
        messages=messages,
        timeout=ollama_timeout,
    )
    return {
        "query": query,
        "answer": answer,
        "chunks": chunks,
        "debug": debug,
        "context": context,
        "model": ollama_model,
        "ollama_skipped": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Legal RAG: retrieve top-k chunks (return_text) then answer via Ollama",
    )
    ap.add_argument("query", nargs="?", default="", help="Câu hỏi người dùng")
    ap.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_CONTEXT_TOP_K,
        metavar="K",
        help=f"Số chunk đưa vào LLM (mặc định {DEFAULT_CONTEXT_TOP_K})",
    )
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    ap.add_argument("--collection", default="legal_chunks_dual")
    ap.add_argument("--vector-name", default="dense")
    ap.add_argument(
        "--vectors-mode",
        choices=("legacy", "dual"),
        default="dual",
        help="Khớp với cách embed lên Qdrant (embed --vectors …)",
    )
    ap.add_argument(
        "--search-target",
        choices=("search_text", "summary", "both"),
        default="both",
        help="Truy vấn vector: search_text, summary, hoặc both (RRF; cần --vectors-mode dual)",
    )
    ap.add_argument("--model", default=DEFAULT_MODEL, help="SentenceTransformer cho embedding retrieval")
    ap.add_argument(
        "--filter-json",
        default="",
        help="File JSON ghi đè bộ lọc (semantic_query + filters), giống legal_retrieve",
    )
    ap.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help="Ollama base URL (mặc định OLLAMA_HOST hoặc http://127.0.0.1:11434)",
    )
    ap.add_argument(
        "--ollama-model",
        default=DEFAULT_OLLAMA_MODEL,
        help="Tên model Ollama (mặc định OLLAMA_MODEL hoặc qwen3.5:7b-instruct)",
    )
    ap.add_argument("--ollama-timeout", type=float, default=300.0)
    ap.add_argument(
        "--retrieve-only",
        action="store_true",
        help="Chỉ chạy retrieval, in JSON chunks+debug (không gọi Ollama)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="In toàn bộ kết quả JSON (query, answer, chunks, debug, context, …)",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Ghi thông tin retrieval lên stderr",
    )
    args = ap.parse_args()

    filter_override: Optional[dict[str, Any]] = None
    if args.filter_json:
        with open(args.filter_json, encoding="utf-8") as fp:
            filter_override = json.load(fp)

    if not args.query and not (filter_override and filter_override.get("semantic_query")):
        ap.error("Cần câu query hoặc --filter-json có semantic_query")

    query_text = args.query or (filter_override or {}).get("semantic_query", "")

    retriever = LegalRetriever(
        qdrant_url=args.qdrant_url,
        collection=args.collection,
        vector_name=args.vector_name,
        vectors_mode=args.vectors_mode,
        model_name=args.model,
    )

    if args.retrieve_only:
        chunks, debug = retriever.retrieve(
            query_text,
            top_k=args.top_k,
            filter_json=filter_override,
            search_target=args.search_target,
        )
        print(json.dumps({"chunks": chunks, "debug": debug}, ensure_ascii=False, indent=2))
        return

    if args.verbose:
        print(f"[RAG] top_k={args.top_k} ollama_model={args.ollama_model!r}", file=sys.stderr)

    try:
        out = run_rag(
            query_text,
            top_k=args.top_k,
            filter_json=filter_override,
            retriever=retriever,
            search_target=args.search_target,
            ollama_url=args.ollama_url,
            ollama_model=args.ollama_model,
            ollama_timeout=args.ollama_timeout,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        for i, ch in enumerate(out["chunks"][: args.top_k], start=1):
            sc = ch.get("score")
            tit = (ch.get("title") or "")[:80]
            print(f"  [{i}] score={sc:.4f} {tit!r}", file=sys.stderr)

    if args.json:
        # context can be large; still useful for debugging
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(out["answer"])


if __name__ == "__main__":
    main()
