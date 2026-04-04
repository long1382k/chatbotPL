#!/usr/bin/env python3
"""
Convert hierarchical type1 JSON (from legal_parser) into Qdrant-oriented retrieval JSON:
one child chunk per Điều (article), with search_text (contextualized) and return_text (clean body).
Document-level title is taken from the type1 `title` field (filled in after parse, e.g. on UI).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any, Generator, Iterator, Optional, Tuple

from long_parser.paths import ARTIFACTS_DIR, DATA_INPUT_DIR

DOMAINS_DEFAULT = ["law"]


def load_csv_metadata(csv_path: str) -> dict[str, dict[str, str]]:
    if not csv_path or not os.path.isfile(csv_path):
        return {}
    out: dict[str, dict[str, str]] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            key = (row.get("name_docx") or "").strip()
            if key:
                out[key] = row
    return out


def document_stem_from_type1(data: dict[str, Any], json_stem: str) -> str:
    name = (data.get("document_name") or "").strip()
    if name:
        return os.path.splitext(name)[0]
    return json_stem


def source_file_for_document(stem: str, meta_row: Optional[dict[str, str]], base_dir: str) -> str:
    if meta_row:
        link = (meta_row.get("link_docx") or "").strip()
        if link:
            return link
    rel = os.path.join(base_dir, f"{stem}.docx")
    return rel.replace("\\", "/")


def issue_date_from_meta(meta_row: Optional[dict[str, str]], data: dict[str, Any]) -> str:
    if meta_row:
        d = (meta_row.get("Ngày ban hành") or "").strip()
        if d:
            return d
    return (data.get("issue_date") or data.get("issued_date") or "").strip()


def collect_subtree_lines(node: dict[str, Any]) -> list[str]:
    """All content lines in document order for a node and its descendants."""
    lines: list[str] = []
    for line in node.get("content") or []:
        s = line.strip() if isinstance(line, str) else str(line).strip()
        if s:
            lines.append(line if isinstance(line, str) else s)
    for ch in node.get("children") or []:
        lines.extend(collect_subtree_lines(ch))
    return lines


def format_return_text(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        if isinstance(line, str):
            parts.append(line.rstrip())
        else:
            parts.append(str(line).rstrip())
    return "\n".join(parts).strip()


def chapter_label(chapter: Optional[dict[str, Any]]) -> str:
    if not chapter:
        return ""
    num = (chapter.get("number") or "").strip()
    title = (chapter.get("title") or "").strip()
    if num and title:
        return f"Chương {num}. {title}"
    if num:
        return f"Chương {num}"
    return title


def build_search_text(
    doc_title: str,
    chapter: Optional[dict[str, Any]],
    return_text: str,
) -> str:
    """Context for embedding: full document title + chapter header + article body (Điều … is already first lines of return_text)."""
    blocks: list[str] = []
    if doc_title:
        blocks.append(doc_title)
    cl = chapter_label(chapter)
    if cl:
        blocks.append(cl)
    if return_text:
        blocks.append(return_text)
    return "\n\n".join(blocks).strip()


def iter_dieus(
    nodes: list[dict[str, Any]],
    chapter: Optional[dict[str, Any]],
) -> Generator[Tuple[Optional[dict[str, Any]], dict[str, Any]], None, None]:
    for node in nodes or []:
        level = (node.get("level") or "").upper()
        if level == "CHƯƠNG":
            ch = {
                "number": node.get("number"),
                "title": node.get("title"),
                "level": "CHƯƠNG",
            }
            yield from iter_dieus(node.get("children") or [], ch)
        elif level == "ĐIỀU":
            yield chapter, node
        else:
            yield from iter_dieus(node.get("children") or [], chapter)


def extract_articles(root_children: list[dict[str, Any]]) -> Iterator[Tuple[Optional[dict[str, Any]], dict[str, Any]]]:
    yield from iter_dieus(root_children, None)


def stable_chunk_id(document_id: str, dieu_number: str, index: int) -> str:
    n = (dieu_number or "x").strip().replace(" ", "_")
    return f"{document_id}__dieu_{n}__{index}"


def type1_to_retrieval(
    data: dict[str, Any],
    *,
    document_id: str,
    meta_row: Optional[dict[str, str]],
    source_base_dir: str,
) -> dict[str, Any]:
    doc_title = (data.get("title") or "").strip()
    stem = document_stem_from_type1(data, document_id)
    out: dict[str, Any] = {
        "document_id": document_id,
        "title": doc_title,
        "source_file": source_file_for_document(stem, meta_row, source_base_dir),
        "domains": list(DOMAINS_DEFAULT),
        "issue_date": issue_date_from_meta(meta_row, data),
        "issuing_agency": (meta_row.get("Cơ quan ban hành") if meta_row else None)
        or (data.get("issuing_agency") or ""),
        "signer": (meta_row.get("Người ký") if meta_row else None) or (data.get("signer") or ""),
        "children_chunks": [],
    }

    children_chunks: list[dict[str, Any]] = []
    for idx, (chapter, dieu) in enumerate(extract_articles(data.get("children") or []), start=1):
        lines = collect_subtree_lines(dieu)
        return_text = format_return_text(lines)
        if not return_text:
            continue
        num = (dieu.get("number") or "").strip()
        chunk_id = stable_chunk_id(document_id, num, idx)
        ch_num = (chapter.get("number") if chapter else None) or ""
        ch_title = (chapter.get("title") if chapter else None) or ""
        children_chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "content": {
                    "search_text": build_search_text(doc_title, chapter, return_text),
                    "return_text": return_text,
                    "summary": "",
                },
                "chunk_type": "article",
                "metadata": {
                    "hierarchy": {
                        "chapter_number": str(ch_num).strip(),
                        "chapter_title": (ch_title or "").strip(),
                    },
                    "search_boosters": {
                        "keywords": [],
                        "aliases": [],
                    },
                },
            }
        )
    out["children_chunks"] = children_chunks
    return out


def convert_file(
    input_path: str,
    output_path: str,
    metadata: dict[str, dict[str, str]],
    source_base_dir: str,
) -> None:
    stem = os.path.splitext(os.path.basename(input_path))[0]
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)
    doc_stem = document_stem_from_type1(data, stem)
    meta_row = metadata.get(doc_stem)
    retrieval = type1_to_retrieval(data, document_id=doc_stem, meta_row=meta_row, source_base_dir=source_base_dir)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(retrieval, f, ensure_ascii=False, indent=4)


def main() -> None:
    p = argparse.ArgumentParser(description="type1 JSON → retrieval JSON for Qdrant")
    p.add_argument(
        "--input-dir",
        default=str(ARTIFACTS_DIR / "type1"),
        help="Directory of type1 hierarchical JSON files",
    )
    p.add_argument(
        "--output-dir",
        default=str(ARTIFACTS_DIR / "retrieval"),
        help="Where to write retrieval JSON files",
    )
    p.add_argument(
        "--metadata-csv",
        default=str(DATA_INPUT_DIR / "full_metadata.csv"),
        help="CSV for Ngày ban hành, link_docx, Tên văn bản, …",
    )
    p.add_argument(
        "--source-base-dir",
        default="data/converted",
        help="Relative path shown when link_docx is missing (e.g. data/converted/<id>.docx)",
    )
    args = p.parse_args()
    metadata = load_csv_metadata(args.metadata_csv)
    if not os.path.isdir(args.input_dir):
        raise SystemExit(f"Input dir not found: {args.input_dir}")
    os.makedirs(args.output_dir, exist_ok=True)
    for name in sorted(os.listdir(args.input_dir)):
        if not name.endswith(".json"):
            continue
        in_path = os.path.join(args.input_dir, name)
        out_path = os.path.join(args.output_dir, name)
        convert_file(in_path, out_path, metadata, args.source_base_dir)
        print(out_path)


if __name__ == "__main__":
    main()
