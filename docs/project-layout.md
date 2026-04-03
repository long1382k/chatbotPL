# Cấu trúc project `long_parser`

Tài liệu mô tả **cách tổ chức thư mục** và **nhiệm vụ** của từng phần sau khi refactor. Gốc mọi đường dẫn tương đối là **thư mục gốc repo** (cùng cấp với `src/`, `data/`, `artifacts/`).

---

## Sơ đồ tổng quan

```
long_parser/
├── src/long_parser/     # Mã nguồn Python (package cài được)
├── scripts/             # Entry CLI (thêm src/ vào sys.path)
├── web_indexer/         # UI web (FastAPI) + upload/workdir/registry
├── data/                # Dữ liệu đầu vào & thư mục phụ trợ (CSV, file gốc, converted, chunked)
├── artifacts/           # Kết quả pipeline có thể tái tạo (type1, retrieval, báo cáo)
├── config/              # JSON cấu hình / schema (filter, template)
├── docs/                # Tài liệu
├── pyproject.toml       # Package `long-parser`, optional-deps retrieve/web
└── requirements-web.txt # Pip riêng cho UI (có thể dùng pip install -e ".[web]")
```

---

## `src/long_parser/`

Package Python chính; cài local: `pip install -e .` từ gốc repo (đọc `pyproject.toml`).

| Đường dẫn | Nhiệm vụ |
|-----------|----------|
| `paths.py` | Hằng số `Path`: `PROJECT_ROOT`, `DATA_DIR`, `DATA_INPUT_DIR`, `ARTIFACTS_DIR`, `CONFIG_DIR`, `CONVERTED_DIR`, `CHUNKED_DIR`. Dùng thống nhất cho parser CLI và (một phần) mặc định script. |
| `parser.py` | Đọc `.doc` (LibreOffice) / `.docx`, parse cấu trúc Chương–Điều–Khoản–Điểm, gắn metadata từ CSV, ghi JSON type1. Hàm `convert_doc_to_docx`, `parse_docx`, `load_metadata`; `main()` batch: `data/input/type1` → `artifacts/type1`, báo cáo `artifacts/parser_report.json`. |
| `retrieval/type1_to_retrieval.py` | Chuyển JSON type1 → JSON retrieval (chunk theo Điều, `search_text` / `return_text`) phục vụ embed. |
| `retrieval/legal_retrieve.py` | Truy vấn Qdrant: trích filter từ query (regex), embed `semantic_query`, `query_points`, post-filter. |
| `embedding/embed_qdrant_chunks.py` | Nhúng `search_text` bằng bi-encoder, tạo collection nếu cần, `upsert` vào Qdrant. |

---

## `scripts/`

Các file **mỏng**: chèn `src/` vào `sys.path` rồi gọi `main()` của module tương ứng. Chạy từ gốc repo, ví dụ:

- `python3 scripts/run_legal_parser.py` — batch parse type1.
- `python3 scripts/type1_to_retrieval_json.py` — type1 → retrieval (mặc định thư mục trong `artifacts/`).
- `python3 scripts/embed_qdrant_chunks.py <file.json>` — embed một file retrieval.
- `python3 scripts/legal_retrieve.py "…"` — truy vấn.

Nếu đã `pip install -e .`, có thể dùng `python -m long_parser.parser` (và tương tự module khác có khối `if __name__ == "__main__"`).

---

## `web_indexer/`

Giao diện web: upload → parse → sửa type1 → preview retrieval → lưu `data/chunked` → đẩy Qdrant.

| Thành phần | Nhiệm vụ |
|------------|----------|
| `app.py` | FastAPI: API upload, parse, type1 CRUD, preview retrieval, lưu chunked, gọi embed/Qdrant. Import từ `long_parser.*` (cần `src/` trên `sys.path`). |
| `templates/`, `static/` | HTML/JS/CSS UI. |
| `uploads/` | File upload theo `file_id` (nên gitignore nếu chứa dữ liệu nhạy cảm). |
| `workdir/` | `type1.json` tạm theo từng `file_id`. |
| `processed_registry.json` | Registry các file đã xử lý; đường dẫn trong JSON là **relative tới gốc repo** (ví dụ `data/chunked/...`). |

Chạy UI (sau khi cài dependency): từ thư mục `web_indexer/`, `uvicorn app:app` hoặc `python app.py` theo hướng dẫn hiện có.

---

## `data/`

Dữ liệu và thư mục phụ trợ **không** phải mã nguồn.

| Đường dẫn | Nhiệm vụ |
|-----------|----------|
| `data/input/` | Đầu vào batch: `full_metadata.csv`, các thư mục con theo loại (`type1/`, `type2/`, …) chứa `.doc`/`.pdf`/… tùy pipeline bạn dùng. |
| `data/converted/` | `.docx` sinh ra khi chuyển từ `.doc` (LibreOffice); web indexer và parser CLI dùng chung quy ước này. |
| `data/chunked/` | File retrieval JSON đã lưu từ UI (hoặc copy tay) trước khi embed / làm mẫu. |

---

## `artifacts/`

**Output** có thể tạo lại từ input + code.

| Đường dẫn | Nhiệm vụ |
|-----------|----------|
| `artifacts/type1/` | JSON phân cấp sau parser (type1). |
| `artifacts/type2/`, `artifacts/type3/` | (Nếu có) output các pipeline type2/type3 — giữ nguyên khi migrate. |
| `artifacts/retrieval/` | JSON chuẩn bị embed (từ `type1_to_retrieval` hoặc tương đương). |
| `artifacts/parser_report.json` | Báo cáo batch `run_legal_parser` (thành công / lỗi từng file). |
| `artifacts/parser_report_legacy.json` | (Nếu tồn tại) bản `report.json` cũ trước khi đổi tên. |

---

## `config/`

| Đường dẫn | Nhiệm vụ |
|-----------|----------|
| `config/schemas/query_filter_template.json` | Mẫu / tài liệu cấu trúc filter (semantic + fields) cho bước truy vấn. |
| `config/filter.json` | (Nếu có) file filter mẫu hoặc snapshot dùng với `--filter-json` khi chạy `legal_retrieve`. |

---

## `docs/`

Tài liệu kỹ thuật: luồng truy vấn, cấu trúc project (file này), v.v.

- **[huong-dan-chay-web-va-truy-hoi.md](./huong-dan-chay-web-va-truy-hoi.md)** — các bước cài đặt, chạy Qdrant, web indexer và truy hồi CLI.

---

## File cấu hình gốc repo

| File | Nhiệm vụ |
|------|----------|
| `pyproject.toml` | Khai báo package `long-parser`, `requires-python`, dependencies tối thiểu, optional `retrieve` / `web`. |
| `requirements-web.txt` | Danh sách pip cho stack FastAPI + embed/Qdrant (UI). |

---

## Luồng dữ liệu tham khảo

1. **Batch type1**: `data/input/type1/*.doc` + `data/input/full_metadata.csv` → `scripts/run_legal_parser.py` → `artifacts/type1/*.json`, `data/converted/*.docx`.
2. **Retrieval JSON**: `scripts/type1_to_retrieval_json.py` → `artifacts/retrieval/*.json`.
3. **Embed**: `scripts/embed_qdrant_chunks.py artifacts/retrieval/<tên>.json` → Qdrant.
4. **Query**: `scripts/legal_retrieve.py "…"` → JSON chunks + debug.

Song song, **web_indexer** thực hiện parse → chỉnh type1 → lưu trực tiếp vào `data/chunked` rồi có thể gọi cùng logic embed như bước 3.

---

*Khi thêm thư mục hoặc đổi tên file entry, cập nhật lại tài liệu này và các tham chiếu trong `docs/truy-van-tai-lieu-tu-query.md` nếu liên quan.*
