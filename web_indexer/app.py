"""
Local web UI: upload .doc/.docx → parse → edit → chunk → save data/chunked → Qdrant.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
WEB_ROOT = Path(__file__).resolve().parent
UPLOADS_DIR = WEB_ROOT / "uploads"
WORKDIR = WEB_ROOT / "workdir"
CHUNKED_DIR = ROOT / "data" / "chunked"
CONVERTED_DIR = ROOT / "data" / "converted"
REGISTRY_PATH = WEB_ROOT / "processed_registry.json"
METADATA_CSV = ROOT / "data" / "input" / "full_metadata.csv"

import sys

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from long_parser.parser import convert_doc_to_docx, parse_docx  # noqa: E402
from long_parser.retrieval.type1_to_retrieval import (  # noqa: E402
    document_stem_from_type1,
    load_csv_metadata,
    type1_to_retrieval,
)

app = FastAPI(title="Legal document indexer")
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.is_file():
        return {"items": []}
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_registry(data: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find_item(registry: dict[str, Any], file_id: str) -> dict[str, Any] | None:
    for it in registry.get("items", []):
        if it.get("file_id") == file_id:
            return it
    return None


def _apply_metadata_from_csv(result: dict[str, Any], stem: str, metadata: dict[str, dict[str, str]]) -> None:
    meta = metadata.get(stem, {})
    if not meta:
        return
    result["document_type"] = meta.get("Loại văn bản", "") or result.get("document_type", "")
    result["effective_date"] = meta.get("Ngày có hiệu lực", "") or result.get("effective_date", "")
    result["industry"] = meta.get("Ngành", "") or result.get("industry", "")
    result["field"] = meta.get("Lĩnh vực", "") or result.get("field", "")
    result["issuing_agency"] = meta.get("Cơ quan ban hành", "") or result.get("issuing_agency", "")
    result["signer"] = meta.get("Người ký", "") or result.get("signer", "")


def _safe_filename(name: str) -> str:
    base = os.path.basename(name)
    return re.sub(r"[^\w.\- \u00C0-\u1FFF()]+", "_", base)[:200] or "file"


def _parse_uploaded_file(upload_path: Path, original_name: str) -> dict[str, Any]:
    metadata = load_csv_metadata(str(METADATA_CSV))
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix.lower()

    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

    if suffix == ".docx":
        result = parse_docx(str(upload_path), original_name)
    elif suffix == ".doc":
        docx_path = convert_doc_to_docx(str(upload_path), str(CONVERTED_DIR))
        result = parse_docx(docx_path, original_name)
    else:
        raise ValueError("Chỉ hỗ trợ .doc hoặc .docx")

    _apply_metadata_from_csv(result, stem, metadata)
    # Chuẩn hoá các khóa metadata rỗng nếu CSV không có
    for key in (
        "document_type",
        "effective_date",
        "industry",
        "field",
        "issuing_agency",
        "signer",
    ):
        result.setdefault(key, "")
    return result


class Type1SaveBody(BaseModel):
    data: dict[str, Any]


class RetrievalPreviewBody(BaseModel):
    type1: dict[str, Any]
    document_id: str | None = None


class ChunkedSaveBody(BaseModel):
    retrieval: dict[str, Any]
    file_id: str


class QdrantBody(BaseModel):
    file_id: str | None = None
    retrieval: dict[str, Any] | None = None
    collection: str = "legal_chunks"
    qdrant_url: str | None = None
    dry_run: bool = False


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Any:
    # Starlette ≥0.28: (request, name[, context]); old (name, {request}) breaks on newer Jinja2/Starlette.
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/api/registry")
async def get_registry() -> JSONResponse:
    return JSONResponse(_load_registry())


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> JSONResponse:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    registry = _load_registry()
    items_out: list[dict[str, Any]] = []

    for uf in files:
        if not uf.filename:
            continue
        ext = Path(uf.filename).suffix.lower()
        if ext not in (".doc", ".docx"):
            continue
        file_id = str(uuid.uuid4())
        dest_dir = UPLOADS_DIR / file_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_filename(uf.filename)
        dest_path = dest_dir / safe
        content = await uf.read()
        dest_path.write_bytes(content)

        entry = {
            "file_id": file_id,
            "original_filename": uf.filename,
            "upload_rel_path": str(dest_path.relative_to(ROOT)),
            "document_id": Path(uf.filename).stem,
            "uploaded_at": _now_iso(),
            "parsed_at": None,
            "type1_rel_path": None,
            "chunked_rel_path": None,
            "chunked_document_id": None,
            "qdrant_imported_at": None,
            "qdrant_points": None,
            "last_error": None,
        }
        registry.setdefault("items", []).append(entry)
        items_out.append(entry)

    _save_registry(registry)
    return JSONResponse({"files": items_out})


@app.post("/api/parse/{file_id}")
async def parse_file(file_id: str) -> JSONResponse:
    registry = _load_registry()
    entry = _find_item(registry, file_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không tìm thấy file_id")

    upload_rel = entry.get("upload_rel_path")
    if not upload_rel:
        raise HTTPException(status_code=400, detail="Thiếu đường dẫn upload")
    upload_path = ROOT / upload_rel
    if not upload_path.is_file():
        raise HTTPException(status_code=404, detail="File upload không còn trên đĩa")

    try:
        result = _parse_uploaded_file(upload_path, entry["original_filename"])
    except Exception as e:
        entry["last_error"] = str(e)
        _save_registry(registry)
        raise HTTPException(status_code=500, detail=str(e)) from e

    WORKDIR.mkdir(parents=True, exist_ok=True)
    wd = WORKDIR / file_id
    wd.mkdir(parents=True, exist_ok=True)
    type1_path = wd / "type1.json"
    with open(type1_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    entry["parsed_at"] = _now_iso()
    entry["type1_rel_path"] = str(type1_path.relative_to(ROOT))
    entry["last_error"] = None
    entry["document_id"] = Path(entry["original_filename"]).stem
    _save_registry(registry)

    return JSONResponse(result)


@app.get("/api/type1/{file_id}")
async def get_type1(file_id: str) -> JSONResponse:
    registry = _load_registry()
    entry = _find_item(registry, file_id)
    if not entry or not entry.get("type1_rel_path"):
        raise HTTPException(status_code=404, detail="Chưa phân tích hoặc không tồn tại")
    p = ROOT / entry["type1_rel_path"]
    if not p.is_file():
        raise HTTPException(status_code=404, detail="type1.json không tìm thấy")
    with open(p, encoding="utf-8") as f:
        return JSONResponse(json.load(f))


@app.put("/api/type1/{file_id}")
async def put_type1(file_id: str, body: Type1SaveBody) -> JSONResponse:
    registry = _load_registry()
    entry = _find_item(registry, file_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không tìm thấy file_id")

    WORKDIR.mkdir(parents=True, exist_ok=True)
    wd = WORKDIR / file_id
    wd.mkdir(parents=True, exist_ok=True)
    type1_path = wd / "type1.json"
    with open(type1_path, "w", encoding="utf-8") as f:
        json.dump(body.data, f, ensure_ascii=False, indent=2)

    entry["type1_rel_path"] = str(type1_path.relative_to(ROOT))
    entry["last_error"] = None
    _save_registry(registry)
    return JSONResponse({"ok": True, "path": str(type1_path.relative_to(ROOT))})


@app.post("/api/retrieval/preview")
async def retrieval_preview(body: RetrievalPreviewBody) -> JSONResponse:
    metadata = load_csv_metadata(str(METADATA_CSV))
    data = body.type1
    stem = (body.document_id or "").strip() or document_stem_from_type1(
        data, "preview"
    )
    meta_row = metadata.get(stem)
    try:
        out = type1_to_retrieval(
            data,
            document_id=stem,
            meta_row=meta_row,
            source_base_dir="data/converted",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(out)


@app.post("/api/chunked/save")
async def save_chunked(body: ChunkedSaveBody) -> JSONResponse:
    registry = _load_registry()
    entry = _find_item(registry, body.file_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Không tìm thấy file_id")

    doc_id = (body.retrieval.get("document_id") or "").strip()
    if not doc_id:
        raise HTTPException(status_code=400, detail="retrieval thiếu document_id")

    CHUNKED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHUNKED_DIR / f"{doc_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(body.retrieval, f, ensure_ascii=False, indent=2)

    rel = str(out_path.relative_to(ROOT))
    entry["chunked_rel_path"] = rel
    entry["chunked_document_id"] = doc_id
    entry["last_error"] = None
    _save_registry(registry)

    return JSONResponse({"ok": True, "path": rel})


@app.post("/api/qdrant")
async def qdrant_upsert(body: QdrantBody) -> JSONResponse:
    doc: dict[str, Any] | None = body.retrieval
    if doc is None and body.file_id:
        registry = _load_registry()
        entry = _find_item(registry, body.file_id)
        if not entry or not entry.get("chunked_rel_path"):
            raise HTTPException(status_code=400, detail="Chưa có file chunked; hãy Lưu trước")
        p = ROOT / entry["chunked_rel_path"]
        if not p.is_file():
            raise HTTPException(status_code=404, detail="File chunked không tồn tại")
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    if doc is None:
        raise HTTPException(status_code=400, detail="Cần retrieval hoặc file_id đã chunked")

    try:
        from long_parser.embedding.embed_qdrant_chunks import upsert_retrieval_document
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Thiếu thư viện embedding/Qdrant: {e}",
        ) from e

    try:
        n = upsert_retrieval_document(
            doc,
            qdrant_url=body.qdrant_url,
            collection=body.collection,
            dry_run=body.dry_run,
        )
    except Exception as e:
        if body.file_id:
            registry = _load_registry()
            ent = _find_item(registry, body.file_id)
            if ent:
                ent["last_error"] = str(e)
                _save_registry(registry)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if body.file_id:
        registry = _load_registry()
        ent = _find_item(registry, body.file_id)
        if ent:
            ent["qdrant_imported_at"] = _now_iso()
            ent["qdrant_points"] = n
            ent["last_error"] = None
            _save_registry(registry)

    return JSONResponse({"ok": True, "points": n, "dry_run": body.dry_run})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=True)
