# Logic nút «Tách đoạn» (web indexer)

Khi người dùng bấm **«Tách đoạn»** trên giao diện web indexer, luồng xử lý như sau.

## 1. Trình duyệt (`web_indexer/static/app.js`)

1. Lấy toàn bộ JSON **cấu trúc type1** đang hiển thị ở editor bước 2 (`type1Editor.get()`).
2. Tính **stem** tài liệu: lấy `document_name`, bỏ đuôi `.doc` / `.docx` → dùng làm `document_id` gửi lên (hoặc `null` nếu trống).
3. Gửi `POST /api/retrieval/preview` với body:
   - `type1`: object vừa lấy
   - `document_id`: stem ở trên
   - `file_id`: dòng đang chọn ở bước 1 (nếu có)
4. Nếu thành công:
   - Tắt các cờ liên quan tới tóm tắt / file tạm (`retrievalPreviewUsedSummarize`, `hasChunkTemp`, …).
   - Đổ **kết quả** vào editor **Retrieval JSON (preview)** ở bước 3.
   - Hiển thị số chunk: `children_chunks.length`.

## 2. Máy chủ (`web_indexer/app.py` → `type1_to_retrieval`)

Endpoint **`POST /api/retrieval/preview`**:

1. Đọc metadata CSV (`data/input/full_metadata.csv`) nếu có.
2. Xác định **stem** tài liệu: ưu tiên `document_id` từ client; nếu không có thì suy từ `document_name` trong type1 (`document_stem_from_type1`).
3. Lấy **dòng CSV** tương ứng stem (ngày ban hành, tên văn bản, link, cơ quan, …).
4. Gọi **`type1_to_retrieval(...)`** (`src/long_parser/retrieval/type1_to_retrieval.py`):
   - Duyệt cây type1, tìm từng nút **ĐIỀU** (trong **CHƯƠNG** hoặc ở gốc).
   - Với mỗi Điều: gom toàn bộ dòng nội dung cây con → `return_text`; ghép **tiêu đề văn bản + chương + thân điều** → `search_text` (phục vụ embed/tìm kiếm).
   - Mỗi Điều thành một phần tử trong `children_chunks` (`chunk_id`, `metadata.hierarchy`, `content.search_text`, `content.return_text`, trường `summary` rỗng, …).
   - Phần đầu object: `document_id`, `title`, `source_file`, `domains`, `issue_date`, `issuing_agency`, `signer`, …
5. Nếu có **`file_id`**: xóa file tạm **`chunk_preview_temp.json`** trong `web_indexer/workdir/<file_id>/` (tránh dùng lại bản đã tóm tắt cũ sau khi tách lại).
6. Trả về JSON retrieval cho client.

## Tóm lại

**«Tách đoạn»** = lấy JSON **type1** đang sửa trên UI → chạy cùng logic **type1 → retrieval** như pipeline `type1_to_retrieval` → nhận JSON có **`children_chunks`** (mỗi **Điều** một chunk) và hiển thị ở preview bước 3.

**Không** gọi Ollama, **không** ghi đĩa tại bước này; chỉ khi bấm **Lưu vào data/chunked** (hoặc các bước sau như Qdrant) mới lưu file / đẩy cơ sở dữ liệu.

## Tham chiếu mã

| Thành phần | Đường dẫn |
|------------|-----------|
| Handler nút | `web_indexer/static/app.js` (sự kiện `#btn-chunk`) |
| API | `web_indexer/app.py` — `retrieval_preview` |
| Chuyển đổi type1 → retrieval | `src/long_parser/retrieval/type1_to_retrieval.py` — `type1_to_retrieval` |
