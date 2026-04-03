#!/usr/bin/env python3
"""
Retrieve legal chunks from Qdrant: dense vector search + metadata filter.

Pipeline:
  1) Regex / rules extract structured constraints from user query → standard filter JSON
  2) (Optional) write that JSON for inspection
  3) Build Qdrant Filter + encode remainder (semantic_query) with the same bi-encoder
  4) query_points (or legacy search) with query_filter

Dependencies:
  pip install qdrant-client sentence-transformers
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm
except ImportError:
    QdrantClient = None  # type: ignore
    qm = None  # type: ignore

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore

DEFAULT_MODEL = "bkai-foundation-models/vietnamese-bi-encoder"

# --- Roman numerals for chapter normalization (payload uses I, II, III, …) ---

_ROMAN_LETTERS = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def int_to_roman(n: int) -> str:
    if n <= 0:
        return ""
    parts: list[str] = []
    x = n
    for v, s in _ROMAN_LETTERS:
        while x >= v:
            parts.append(s)
            x -= v
    return "".join(parts)


def normalize_chapter_token(raw: str) -> str:
    t = raw.strip().upper()
    if t.isdigit():
        return int_to_roman(int(t))
    if re.fullmatch(r"[IVXLCDM]+", t):
        return t
    return t


# --- Agency / alias hints (regex on query → substring safe for payload regexp) ---

_AGENCY_RULES: list[tuple[str, str]] = [
    (r"\bBTC\b", "Bộ Tài chính"),
    (r"Bộ\s+Tài\s+chính", "Bộ Tài chính"),
    (r"\bBHXH\b|Bảo\s+hiểm\s+xã\s+hội\s+Việt\s+Nam", "Bảo hiểm xã hội Việt Nam"),
    (r"Bộ\s+Quốc\s+phòng|\bBQP\b", "Bộ Quốc phòng"),
    (r"Chính\s+phủ", "Chính phủ"),
    (r"Bộ\s+Tư\s+pháp", "Bộ Tư pháp"),
]


@dataclass
class ExtractionResult:
    semantic_query: str
    filters: dict[str, Any]
    spans_removed: list[tuple[int, int]] = field(default_factory=list)


def _mark_span(spans: list[tuple[int, int]], start: int, end: int) -> None:
    if start < end:
        spans.append((start, end))


def strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return " ".join(text.split())
    spans = sorted(spans)
    out: list[str] = []
    cur = 0
    for s, e in spans:
        if s > cur:
            out.append(text[cur:s])
        cur = max(cur, e)
    out.append(text[cur:])
    return " ".join("".join(out).split())


def extract_filters_from_query(query: str) -> ExtractionResult:
    """Lightweight extraction (regex / keyword rules). No external ML model."""
    text = query.strip()
    spans: list[tuple[int, int]] = []
    flt: dict[str, Any] = {
        "document_id": None,
        "issuing_agency": None,
        "signer": None,
        "issue_date": None,
        "chunk_type": None,
        "domains": None,
        "chapter_number": None,
        "chapter_title_contains": None,
        "article_number": None,
    }

    # document_id: 01.2025.tt-btc_20250115052759 style
    for m in re.finditer(
        r"\b(\d{2}\.\d{4}\.[a-z0-9%]+-[a-z0-9]+_\d{10,}|[a-z0-9][\w.%+-]*\.(?:tt|qd|nq|nd|ct|kl)-[a-z0-9._]+_\d{10,})\b",
        text,
        re.IGNORECASE,
    ):
        flt["document_id"] = m.group(1).replace("%C4%91", "đ").replace("%20", "")
        _mark_span(spans, m.start(), m.end())

    # issue_date dd/mm/yyyy or dd-mm-yyyy
    for m in re.finditer(
        r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b",
        text,
    ):
        d, mo, y = m.group(1), m.group(2), m.group(3)
        flt["issue_date"] = f"{int(d):02d}/{int(mo):02d}/{y}"
        _mark_span(spans, m.start(), m.end())

    # Vietnamese: ngày 09 tháng 01 năm 2025
    for m in re.finditer(
        r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})",
        text,
        re.IGNORECASE,
    ):
        flt["issue_date"] = f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
        _mark_span(spans, m.start(), m.end())

    # Chapter
    for m in re.finditer(
        r"(?:chương|Chương)\s+([IVXLCDM]+|\d+)\b\.?\s*([^\n,.;:]{0,80})?",
        text,
    ):
        flt["chapter_number"] = normalize_chapter_token(m.group(1))
        _mark_span(spans, m.start(), m.end())

    # Article (Điều 3, điều 12a)
    for m in re.finditer(r"(?:điều|Điều)\s+(\d+[a-z]?)\b", text):
        flt["article_number"] = m.group(1).lower()
        _mark_span(spans, m.start(), m.end())

    # Agencies
    for pattern, canonical in _AGENCY_RULES:
        mm = re.search(pattern, text, re.IGNORECASE)
        if mm:
            flt["issuing_agency"] = canonical
            _mark_span(spans, mm.start(), mm.end())
            break

    # Domains: explicit “lĩnh vực pháp luật” / generic law
    if re.search(r"\b(lĩnh\s+vực\s+)?pháp\s+luật\b|\bvbqppl\b|văn\s+bản\s+qppl", text, re.IGNORECASE):
        flt["domains"] = ["law"]

    # chunk type hints
    if re.search(r"\bđiều\s+\d", text, re.IGNORECASE):
        flt["chunk_type"] = "article"

    semantic = strip_spans(text, spans)
    if not semantic:
        semantic = text
    # Remove common leftover glue tokens after stripping structured spans
    semantic = re.sub(r"\b(?:ban\s+hành|theo)\b", " ", semantic, flags=re.IGNORECASE)
    semantic = " ".join(semantic.split())

    return ExtractionResult(semantic_query=semantic, filters=flt, spans_removed=spans)


def filters_to_standard_json(semantic_query: str, filters: dict[str, Any]) -> dict[str, Any]:
    """The canonical JSON shape consumed by ``filters_to_qdrant`` / saved to disk."""
    return {
        "version": 1,
        "semantic_query": semantic_query,
        "filters": filters,
    }


def filters_to_qdrant(filters: dict[str, Any]) -> Optional[qm.Filter]:
    """
    Build a Qdrant filter using only MatchValue / MatchAny (portable across qdrant-client versions).

    ``article_number``, ``chapter_title_contains``, and non-exact ``signer`` / ``issuing_agency``
    matches are applied after search in :meth:`LegalRetriever.retrieve` (post-filter).
    """
    if qm is None:
        raise RuntimeError("qdrant-client is required for filters_to_qdrant(); pip install qdrant-client")
    must: list[Any] = []
    f = filters or {}

    if f.get("document_id"):
        must.append(
            qm.FieldCondition(key="document_id", match=qm.MatchValue(value=f["document_id"]))
        )

    if f.get("chunk_type"):
        must.append(
            qm.FieldCondition(key="chunk_type", match=qm.MatchValue(value=f["chunk_type"]))
        )

    if f.get("issue_date"):
        must.append(
            qm.FieldCondition(key="issue_date", match=qm.MatchValue(value=f["issue_date"]))
        )

    if f.get("domains"):
        must.append(
            qm.FieldCondition(key="domains", match=qm.MatchAny(any=list(f["domains"])))
        )

    if f.get("issuing_agency"):
        must.append(
            qm.FieldCondition(
                key="issuing_agency",
                match=qm.MatchValue(value=str(f["issuing_agency"]).strip()),
            )
        )

    if f.get("signer"):
        must.append(
            qm.FieldCondition(key="signer", match=qm.MatchValue(value=str(f["signer"]).strip()))
        )

    ch = f.get("chapter_number")
    if ch:
        must.append(
            qm.FieldCondition(
                key="metadata.hierarchy.chapter_number",
                match=qm.MatchValue(value=str(ch).strip()),
            )
        )

    if not must:
        return None
    return qm.Filter(must=must)


def _needs_post_filter(filters: dict[str, Any]) -> bool:
    f = filters or {}
    if f.get("article_number"):
        return True
    if f.get("chapter_title_contains"):
        return True
    return False


def _post_filter_chunks(chunks: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    f = filters or {}
    out: list[dict[str, Any]] = []
    art = f.get("article_number")
    title_hint = f.get("chapter_title_contains")
    art_l = str(art).strip().lower() if art else ""
    hint_l = str(title_hint).strip().lower() if title_hint else ""

    for c in chunks:
        cid = (c.get("chunk_id") or "").lower()
        if art_l:
            needle = f"__dieu_{art_l}__"
            if needle not in cid:
                continue
        if hint_l:
            meta = c.get("metadata") or {}
            h = meta.get("hierarchy") or {}
            ch_tit = (h.get("chapter_title") or "").lower()
            if hint_l not in ch_tit:
                continue
        out.append(c)
    return out


class LegalRetriever:
    def __init__(
        self,
        *,
        qdrant_url: str = "http://localhost:6333",
        collection: str = "legal_chunks",
        vector_name: str = "dense",
        model_name: str = DEFAULT_MODEL,
    ) -> None:
        if QdrantClient is None or SentenceTransformer is None:
            raise RuntimeError("Install qdrant-client and sentence-transformers")
        self._client = QdrantClient(url=qdrant_url)
        self.collection = collection
        self.vector_name = vector_name
        self.model = SentenceTransformer(model_name)

    def embed(self, text: str) -> list[float]:
        v = self.model.encode(
            text,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return v.tolist()

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 10,
        filter_json: Optional[dict[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Returns (ranked chunks, debug bundle with semantic_query, filters, qdrant_filter).
        If ``filter_json`` is provided it must follow the template (semantic_query + filters);
        otherwise extraction runs on ``query``.
        """
        if filter_json:
            semantic = (filter_json.get("semantic_query") or query).strip()
            filters = dict(filter_json.get("filters") or {})
        else:
            ext = extract_filters_from_query(query)
            semantic = ext.semantic_query
            filters = ext.filters

        spec = filters_to_standard_json(semantic, filters)
        qf = filters_to_qdrant(filters)

        vec = self.embed(semantic or query)
        search_limit = top_k
        if _needs_post_filter(filters):
            search_limit = max(top_k * 10, 50)

        if hasattr(self._client, "query_points"):
            resp = self._client.query_points(
                collection_name=self.collection,
                query=vec,
                using=self.vector_name,
                query_filter=qf,
                limit=search_limit,
                with_payload=True,
            )
            hits = resp.points
        else:
            hits = self._client.search(
                collection_name=self.collection,
                query_vector=(self.vector_name, vec),
                query_filter=qf,
                limit=search_limit,
                with_payload=True,
            )

        chunks: list[dict[str, Any]] = []
        for h in hits:
            pl = h.payload or {}
            chunks.append(
                {
                    "score": float(h.score),
                    "id": str(h.id),
                    "chunk_id": pl.get("chunk_id"),
                    "document_id": pl.get("document_id"),
                    "title": pl.get("title"),
                    "chunk_type": pl.get("chunk_type"),
                    "issue_date": pl.get("issue_date"),
                    "issuing_agency": pl.get("issuing_agency"),
                    "return_text": pl.get("return_text"),
                    "search_text": pl.get("search_text"),
                    "metadata": pl.get("metadata"),
                    "source_file": pl.get("source_file"),
                }
            )

        chunks = _post_filter_chunks(chunks, filters)
        chunks = chunks[:top_k]

        qf_dump: Any = None
        if qf is not None:
            qf_dump = qf.model_dump() if hasattr(qf, "model_dump") else qf.dict()

        debug = {
            "filter_spec": spec,
            "qdrant_filter": qf_dump,
            "search_limit": search_limit,
            "post_filter_applied": _needs_post_filter(filters),
        }
        return chunks, debug


def main() -> None:
    ap = argparse.ArgumentParser(description="Legal RAG retrieval from Qdrant")
    ap.add_argument("query", nargs="?", default="", help="User question")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    ap.add_argument("--collection", default="legal_chunks")
    ap.add_argument("--vector-name", default="dense")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument(
        "--filter-json",
        default="",
        help="Use this filter file instead of regex extraction (must contain semantic_query + filters)",
    )
    ap.add_argument(
        "--dump-filter",
        default="",
        help="Write extracted/merged filter spec JSON to this path",
    )
    ap.add_argument("--print-spec", action="store_true", help="Print filter JSON to stdout")
    args = ap.parse_args()

    filter_override: Optional[dict[str, Any]] = None
    if args.filter_json:
        with open(args.filter_json, encoding="utf-8") as fp:
            filter_override = json.load(fp)

    if not args.query and not (filter_override and filter_override.get("semantic_query")):
        ap.error("Provide a query or a --filter-json with semantic_query")

    retriever = LegalRetriever(
        qdrant_url=args.qdrant_url,
        collection=args.collection,
        vector_name=args.vector_name,
        model_name=args.model,
    )
    query_text = args.query or (filter_override or {}).get("semantic_query", "")
    chunks, debug = retriever.retrieve(
        query_text,
        top_k=args.top_k,
        filter_json=filter_override,
    )

    if args.dump_filter:
        path = args.dump_filter
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(debug["filter_spec"], fp, ensure_ascii=False, indent=2)
        print(f"Wrote filter spec: {path}", file=__import__("sys").stderr)

    if args.print_spec:
        print(json.dumps(debug["filter_spec"], ensure_ascii=False, indent=2))

    print(json.dumps({"chunks": chunks, "debug": debug}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
