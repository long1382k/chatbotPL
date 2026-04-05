#!/usr/bin/env python3
"""
Encode retrieval JSON chunks with bkai-foundation-models/vietnamese-bi-encoder and upsert to Qdrant.

- ``--vectors dual`` (mặc định): hai vector — ``dense_search`` từ ``content.search_text``,
  ``dense_summary`` từ ``content.summary`` (nếu rỗng, dùng ``search_text``).
- ``--vectors legacy``: một vector duy nhất (tên ``--vector-name``, mặc định ``dense``) từ ``content.search_text``.

Dùng **dual + search_target=both** (ở legal_retrieve/legal_rag) để so khớp query với **cả summary lẫn search_text**.

Dependencies:
    pip install sentence-transformers qdrant-client

Sparse / BM25: index ``content.search_text`` (or ``return_text``) in a separate inverted index
or Qdrant sparse vector pipeline; this script only uploads dense embeddings.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any

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

# Named vectors when --vectors dual (must match LegalRetriever defaults)
DUAL_VECTOR_SEARCH = "dense_search"
DUAL_VECTOR_SUMMARY = "dense_summary"
LEGACY_VECTOR_DEFAULT = "dense"


def load_chunks(retrieval_path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with open(retrieval_path, encoding="utf-8") as f:
        doc = json.load(f)
    chunks = doc.get("children_chunks") or []
    return doc, chunks


def _content(ch: dict[str, Any]) -> dict[str, Any]:
    return ch.get("content") or {}


def chunks_to_points_legacy(
    doc: dict[str, Any],
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    vector_name: str,
) -> list[Any]:
    points = []
    for ch, vec in zip(chunks, vectors):
        cid = ch.get("chunk_id") or ""
        c = _content(ch)
        payload: dict[str, Any] = _base_payload(doc, ch, c)
        uid = uuid.uuid5(uuid.NAMESPACE_URL, cid or json.dumps(ch, ensure_ascii=True))
        points.append(
            qm.PointStruct(
                id=str(uid),
                vector={vector_name: vec},
                payload=payload,
            )
        )
    return points


def chunks_to_points_dual(
    doc: dict[str, Any],
    chunks: list[dict[str, Any]],
    vectors_search: list[list[float]],
    vectors_summary: list[list[float]],
) -> list[Any]:
    points = []
    for ch, vs, vsum in zip(chunks, vectors_search, vectors_summary):
        cid = ch.get("chunk_id") or ""
        c = _content(ch)
        payload = _base_payload(doc, ch, c)
        uid = uuid.uuid5(uuid.NAMESPACE_URL, cid or json.dumps(ch, ensure_ascii=True))
        points.append(
            qm.PointStruct(
                id=str(uid),
                vector={DUAL_VECTOR_SEARCH: vs, DUAL_VECTOR_SUMMARY: vsum},
                payload=payload,
            )
        )
    return points


def _payload_filter_document_id(document_id: str) -> Any:
    """Filter Qdrant points by payload ``document_id`` (mọi chunk của cùng một văn bản)."""
    return qm.Filter(
        must=[
            qm.FieldCondition(
                key="document_id",
                match=qm.MatchValue(value=document_id),
            ),
        ],
    )


def count_points_for_document(
    *,
    document_id: str,
    collection: str,
    qdrant_url: str | None = None,
) -> int:
    """Đếm point trong collection có ``payload.document_id`` trùng."""
    if QdrantClient is None:
        raise RuntimeError("Install qdrant-client: pip install qdrant-client")
    url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)
    if not client.collection_exists(collection):
        return 0
    r = client.count(
        collection_name=collection,
        count_filter=_payload_filter_document_id(document_id),
        exact=True,
    )
    return int(r.count)


def delete_point_by_id(
    *,
    point_id: str,
    collection: str,
    qdrant_url: str | None = None,
) -> None:
    """Xoá đúng một point theo id (``PointIdsList`` — API Qdrant native)."""
    if QdrantClient is None:
        raise RuntimeError("Install qdrant-client: pip install qdrant-client")
    url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)
    if not client.collection_exists(collection):
        raise ValueError(f"Collection không tồn tại: {collection}")
    pid = str(point_id).strip()
    if not pid:
        raise ValueError("Thiếu point_id")
    client.delete(
        collection_name=collection,
        points_selector=qm.PointIdsList(points=[pid]),
    )


def delete_document_points(
    *,
    document_id: str,
    collection: str,
    qdrant_url: str | None = None,
) -> int:
    """
    Xoá mọi point có ``payload.document_id`` trùng (không đụng văn bản khác).
    Trả về số point đã đếm trước khi xoá (0 nếu collection không tồn tại hoặc không có point).
    """
    if QdrantClient is None:
        raise RuntimeError("Install qdrant-client: pip install qdrant-client")
    url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)
    if not client.collection_exists(collection):
        return 0
    n = count_points_for_document(document_id=document_id, collection=collection, qdrant_url=url)
    if n == 0:
        return 0
    client.delete(
        collection_name=collection,
        points_selector=_payload_filter_document_id(document_id),
    )
    return n


def _jsonify_for_api(v: Any) -> Any:
    """Chuyển giá trị payload Qdrant sang kiểu JSON an toàn."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonify_for_api(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonify_for_api(x) for x in v]
    return str(v)


def _record_to_plain_dict(rec: Any) -> dict[str, Any]:
    """Chuyển ``Record`` từ ``client.scroll`` sang dict JSON-friendly (không vector)."""
    row: dict[str, Any] = {"id": str(rec.id)}
    pl = getattr(rec, "payload", None)
    if pl is not None:
        if isinstance(pl, dict):
            row["payload"] = _jsonify_for_api(pl)
        elif hasattr(pl, "items"):
            row["payload"] = _jsonify_for_api(dict(pl))
        else:
            row["payload"] = _jsonify_for_api(pl)
    return row


def scroll_records_for_document(
    *,
    document_id: str,
    collection: str,
    qdrant_url: str | None = None,
    limit: int = 10_000,
    with_payload: bool = True,
    with_vectors: bool = False,
) -> list[dict[str, Any]]:
    """
    Dùng ``QdrantClient.scroll`` (API native) lọc theo ``payload.document_id``.
    Trả về danh sách ``{ "id": ..., "payload": ... }`` như bản ghi Qdrant (không gửi vector mặc định).
    """
    if QdrantClient is None:
        raise RuntimeError("Install qdrant-client: pip install qdrant-client")
    url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)
    if not client.collection_exists(collection):
        return []
    flt = _payload_filter_document_id(document_id)
    out: list[dict[str, Any]] = []
    offset: Any = None
    while len(out) < limit:
        take = min(256, limit - len(out))
        batch, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=flt,
            limit=take,
            offset=offset,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )
        if not batch:
            break
        for rec in batch:
            if len(out) >= limit:
                break
            out.append(_record_to_plain_dict(rec))
        offset = next_offset
        if next_offset is None:
            break
    return out


def list_point_ids_for_document(
    *,
    document_id: str,
    collection: str,
    qdrant_url: str | None = None,
    limit: int = 10_000,
) -> list[str]:
    """Chỉ id — dựa trên ``scroll_records_for_document``."""
    rows = scroll_records_for_document(
        document_id=document_id,
        collection=collection,
        qdrant_url=qdrant_url,
        limit=limit,
        with_payload=False,
        with_vectors=False,
    )
    return [str(r["id"]) for r in rows]


def _base_payload(doc: dict[str, Any], ch: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": ch.get("chunk_id") or "",
        "document_id": doc.get("document_id"),
        "title": doc.get("title"),
        "source_file": doc.get("source_file"),
        "domains": doc.get("domains"),
        "issue_date": doc.get("issue_date"),
        "issuing_agency": doc.get("issuing_agency"),
        "signer": doc.get("signer"),
        "chunk_type": ch.get("chunk_type"),
        "summary": (c.get("summary") or "").strip(),
        "return_text": (c.get("return_text") or ""),
        "search_text": (c.get("search_text") or ""),
        "metadata": ch.get("metadata"),
    }


def upsert_retrieval_document(
    doc: dict[str, Any],
    *,
    model_name: str = DEFAULT_MODEL,
    qdrant_url: str | None = None,
    collection: str = "legal_chunks",
    vector_name: str = LEGACY_VECTOR_DEFAULT,
    vectors_mode: str = "dual",
    batch_size: int = 32,
    dry_run: bool = False,
    replace_existing_by_document_id: bool = False,
) -> int:
    """
    Embed ``doc`` (retrieval JSON with children_chunks) and upsert into Qdrant.
    ``vectors_mode``: ``legacy`` (single vector) or ``dual`` (dense_search + dense_summary).
    If ``replace_existing_by_document_id`` is True, xoá trước mọi point có cùng
    ``payload.document_id`` trong collection (tránh sót chunk khi tái index).

    Returns number of points written (0 if dry_run or no chunks).
    """
    if SentenceTransformer is None:
        raise RuntimeError("Install sentence-transformers: pip install sentence-transformers")
    if not dry_run and QdrantClient is None:
        raise RuntimeError("Install qdrant-client: pip install qdrant-client")

    chunks = doc.get("children_chunks") or []
    if not chunks:
        return 0
    doc_id = (doc.get("document_id") or "").strip()

    model = SentenceTransformer(model_name)

    if vectors_mode == "dual":
        url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
        client = QdrantClient(url=url)

        # If the collection already exists in legacy mode, dual upsert would fail.
        # Detect and fallback to legacy encoding.
        if client.collection_exists(collection):
            try:
                col = client.get_collection(collection_name=collection)
                vectors_spec = getattr(col, "vectors", None)
                existing_names: set[str] = set()
                if isinstance(vectors_spec, dict):
                    existing_names = {str(k) for k in vectors_spec.keys()}
                else:
                    if hasattr(vectors_spec, "__dict__"):
                        d = vectors_spec.__dict__
                        for key in ("vectors", "config", "named_vectors", "vectors_config"):
                            val = d.get(key)
                            if isinstance(val, dict):
                                existing_names = {str(k) for k in val.keys()}
                                break

                # Required dual vectors must both exist.
                if existing_names and (
                    DUAL_VECTOR_SEARCH not in existing_names or DUAL_VECTOR_SUMMARY not in existing_names
                ):
                    # Fallback: behave like legacy embed (vector `dense` from search_text).
                    vectors_mode = "legacy"
                # If detection returned empty set, we still attempt dual;
                # in that case Qdrant likely has the vectors and won't error.
            except Exception:
                # If detection fails, keep dual; later upsert will raise if incompatible.
                pass
        if not (vectors_mode == "dual"):
            # continue into legacy block below
            pass
        else:
            texts_s = [(_content(ch).get("search_text") or "").strip() for ch in chunks]
            texts_sum: list[str] = []
            for ch in chunks:
                c = _content(ch)
                st = (c.get("search_text") or "").strip()
                su = (c.get("summary") or "").strip()
                texts_sum.append(su if su else st)
            v_s = model.encode(
                texts_s,
                batch_size=batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            ).tolist()
            v_m = model.encode(
                texts_sum,
                batch_size=batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            ).tolist()
            if dry_run:
                return len(v_s)
            dim = len(v_s[0]) if v_s else 0

            if not client.collection_exists(collection):
                client.create_collection(
                    collection_name=collection,
                    vectors_config={
                        DUAL_VECTOR_SEARCH: qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                        DUAL_VECTOR_SUMMARY: qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                    },
                )
            points = chunks_to_points_dual(doc, chunks, v_s, v_m)
            try:
                if replace_existing_by_document_id and doc_id and client.collection_exists(collection):
                    client.delete(
                        collection_name=collection,
                        points_selector=_payload_filter_document_id(doc_id),
                    )
                client.upsert(collection_name=collection, points=points)
                return len(points)
            except Exception as e:
                msg = str(e).lower()
                if "not existing vector name" in msg or DUAL_VECTOR_SUMMARY.lower() in msg:
                    # Fallback to legacy upsert below
                    vectors_mode = "legacy"
                else:
                    raise

    # legacy: single vector from search_text
    texts = [(_content(ch).get("search_text") or "").strip() for ch in chunks]
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).tolist()
    if dry_run:
        return len(vectors)
    url = qdrant_url or os.environ.get("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)
    dim = len(vectors[0]) if vectors else 0
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config={vector_name: qm.VectorParams(size=dim, distance=qm.Distance.COSINE)},
        )
    points = chunks_to_points_legacy(doc, chunks, vectors, vector_name)
    if replace_existing_by_document_id and doc_id and client.collection_exists(collection):
        client.delete(
            collection_name=collection,
            points_selector=_payload_filter_document_id(doc_id),
        )
    client.upsert(collection_name=collection, points=points)
    return len(points)


def main() -> None:
    p = argparse.ArgumentParser(description="Embed retrieval chunks and upsert to Qdrant")
    p.add_argument("retrieval_json", help="Path to one retrieval/*.json file")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--collection", default="legal_chunks")
    p.add_argument(
        "--vector-name",
        default=LEGACY_VECTOR_DEFAULT,
        help="Legacy mode only: single vector name",
    )
    p.add_argument(
        "--vectors",
        choices=("legacy", "dual"),
        default="dual",
        help="dual (mặc định): dense_search + dense_summary; legacy: một vector từ search_text",
    )
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--dry-run", action="store_true", help="Only compute embeddings, do not write Qdrant")
    args = p.parse_args()

    if SentenceTransformer is None:
        raise SystemExit("Install sentence-transformers: pip install sentence-transformers")
    if not args.dry_run and QdrantClient is None:
        raise SystemExit("Install qdrant-client: pip install qdrant-client")

    doc, chunks = load_chunks(args.retrieval_json)
    if not chunks:
        print("No chunks in file; nothing to encode.")
        return

    n = upsert_retrieval_document(
        doc,
        model_name=args.model,
        qdrant_url=args.qdrant_url,
        collection=args.collection,
        vector_name=args.vector_name,
        vectors_mode=args.vectors,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"Would encode {n} chunks (vectors={args.vectors})")
        return
    print(f"Upserted {n} points into {args.collection} (vectors={args.vectors})")


if __name__ == "__main__":
    main()
