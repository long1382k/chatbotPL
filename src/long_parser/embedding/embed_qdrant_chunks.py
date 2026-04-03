#!/usr/bin/env python3
"""
Encode retrieval JSON chunks with bkai-foundation-models/vietnamese-bi-encoder and upsert to Qdrant.

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


def load_chunks(retrieval_path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with open(retrieval_path, encoding="utf-8") as f:
        doc = json.load(f)
    chunks = doc.get("children_chunks") or []
    return doc, chunks


def chunks_to_points(
    doc: dict[str, Any],
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    vector_name: str,
) -> list[qm.PointStruct]:
    points = []
    for ch, vec in zip(chunks, vectors):
        cid = ch.get("chunk_id") or ""
        payload: dict[str, Any] = {
            "chunk_id": cid,
            "document_id": doc.get("document_id"),
            "title": doc.get("title"),
            "source_file": doc.get("source_file"),
            "domains": doc.get("domains"),
            "issue_date": doc.get("issue_date"),
            "issuing_agency": doc.get("issuing_agency"),
            "signer": doc.get("signer"),
            "chunk_type": ch.get("chunk_type"),
            "return_text": (ch.get("content") or {}).get("return_text"),
            "search_text": (ch.get("content") or {}).get("search_text"),
            "metadata": ch.get("metadata"),
        }
        uid = uuid.uuid5(uuid.NAMESPACE_URL, cid or json.dumps(ch, ensure_ascii=True))
        points.append(
            qm.PointStruct(
                id=str(uid),
                vector={vector_name: vec},
                payload=payload,
            )
        )
    return points


def upsert_retrieval_document(
    doc: dict[str, Any],
    *,
    model_name: str = DEFAULT_MODEL,
    qdrant_url: str | None = None,
    collection: str = "legal_chunks",
    vector_name: str = "dense",
    batch_size: int = 32,
    dry_run: bool = False,
) -> int:
    """
    Embed ``doc`` (retrieval JSON with children_chunks) and upsert into Qdrant.
    Returns number of points written (0 if dry_run or no chunks).
    """
    if SentenceTransformer is None:
        raise RuntimeError("Install sentence-transformers: pip install sentence-transformers")
    if not dry_run and QdrantClient is None:
        raise RuntimeError("Install qdrant-client: pip install qdrant-client")

    chunks = doc.get("children_chunks") or []
    texts = [
        ((ch.get("content") or {}).get("search_text") or "").strip()
        for ch in chunks
    ]
    if not texts:
        return 0

    model = SentenceTransformer(model_name)
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

    points = chunks_to_points(doc, chunks, vectors, vector_name)
    client.upsert(collection_name=collection, points=points)
    return len(points)


def main() -> None:
    p = argparse.ArgumentParser(description="Embed retrieval chunks and upsert to Qdrant")
    p.add_argument("retrieval_json", help="Path to one retrieval/*.json file")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--collection", default="legal_chunks")
    p.add_argument("--vector-name", default="dense", help="Name of named vector in Qdrant collection")
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--dry-run", action="store_true", help="Only compute embeddings, do not write Qdrant")
    args = p.parse_args()

    if SentenceTransformer is None:
        raise SystemExit("Install sentence-transformers: pip install sentence-transformers")
    if not args.dry_run and QdrantClient is None:
        raise SystemExit("Install qdrant-client: pip install qdrant-client")

    doc, chunks = load_chunks(args.retrieval_json)
    texts = [
        ((ch.get("content") or {}).get("search_text") or "").strip()
        for ch in chunks
    ]
    if not texts:
        print("No chunks in file; nothing to encode.")
        return

    model = SentenceTransformer(args.model)
    vectors = model.encode(
        texts,
        batch_size=args.batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    ).tolist()

    if args.dry_run:
        print(f"Encoded {len(vectors)} chunks, dim={len(vectors[0]) if vectors else 0}")
        return

    client = QdrantClient(url=args.qdrant_url)
    dim = len(vectors[0]) if vectors else 0
    if not client.collection_exists(args.collection):
        client.create_collection(
            collection_name=args.collection,
            vectors_config={args.vector_name: qm.VectorParams(size=dim, distance=qm.Distance.COSINE)},
        )

    points = chunks_to_points(doc, chunks, vectors, args.vector_name)
    client.upsert(collection_name=args.collection, points=points)
    print(f"Upserted {len(points)} points into {args.collection}")


if __name__ == "__main__":
    main()
