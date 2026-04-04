# Hướng dẫn chạy Web indexer và truy hồi văn bản pháp luật

Tài liệu từng bước để **chạy thành công** giao diện web index tài liệu và **truy hồi** (query) từ Qdrant bằng CLI. Chi tiết thuật toán truy vấn xem thêm [truy-van-tai-lieu-tu-query.md](./truy-van-tai-lieu-tu-query.md).

---

## 1. Điều kiện cần

| Thành phần | Ghi chú |
|------------|---------|
| **Python** | ≥ 3.10 (theo `pyproject.toml`). |
| **Qdrant** | Đang chạy và lắng nghe HTTP (mặc định `http://localhost:6333`). Cần cho bước **đẩy vector** và **truy hồi**. |
| **LibreOffice** (`soffice`) | Chỉ khi upload/parse file **`.doc`** (chuyển sang `.docx` headless). File **`.docx`** không cần. |
| **Dung lượng / mạng** | Lần đầu chạy embed hoặc truy hồi, `sentence-transformers` sẽ tải model `bkai-foundation-models/vietnamese-bi-encoder` (cần mạng hoặc cache sẵn). |

---

## 2. Chuẩn bị môi trường Python

Từ **thư mục gốc repo** (`long_parser/`, nơi có `pyproject.toml`):

```bash
cd /path/to/long_parser
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

Cài package + dependency cho web và Qdrant/embed (một trong hai cách):

```bash
pip install -U pip
pip install -e ".[web]"
```

Hoặc:

```bash
pip install -e .
pip install -r requirements-web.txt
```

---

## 3. Chạy Qdrant

Ví dụ bằng Docker:

```bash
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant
```

Kiểm tra: mở trình duyệt tới `http://localhost:6333/dashboard` (hoặc API health tùy phiên bản).

Biến môi trường (tùy chọn, trùng với URL Qdrant của bạn):

```bash
export QDRANT_URL=http://localhost:6333
```

---

## 4. Chuẩn bị thư mục `data/`

Web indexer đọc **metadata CSV** tại `data/input/full_metadata.csv` (nếu không có file, một số trường trên UI có thể trống; vẫn parse được).

Tối thiểu nên tạo cấu trúc:

```bash
mkdir -p data/input data/converted data/chunked
```

- **`data/converted/`** — LibreOffice ghi `.docx` khi xử lý `.doc`.
- **`data/chunked/`** — JSON retrieval sau khi bấm **Lưu** trên UI (hoặc bạn copy file retrieval vào đây).

Nếu dùng `.gitignore` với `/data/`, các thư mục này chỉ tồn tại trên máy bạn; cần tự tạo và đặt CSV/file nguồn.

---

## 5. Chạy Web indexer thành công

### 5.1 Lệnh khởi động

Vào thư mục **`web_indexer/`** (để `uvicorn` load đúng module `app`):

```bash
cd web_indexer
python -m uvicorn app:app --host 127.0.0.1 --port 8765 --reload
```

Hoặc:

```bash
cd web_indexer
python app.py
```

Mặc định ứng dụng: **`http://127.0.0.1:8765`**.

### 5.2 Luồng trên giao diện (để “chạy trọn” pipeline)

1. **Upload** một hoặc nhiều file `.doc` / `.docx`.
2. **Phân tích (parse)** từng dòng — sinh `type1.json` trong `web_indexer/workdir/<file_id>/`.
3. **Chỉnh sửa** cây JSON nếu cần (tiêu đề văn bản, cấu trúc).
4. **Tách đoạn** để xem retrieval (preview). Tùy chọn: **Tạo tóm tắt** (Ollama) → server ghi bản có `content.summary` vào file tạm `web_indexer/workdir/<file_id>/chunk_preview_temp.json` và cập nhật preview.
5. **Lưu vào `data/chunked`** (bản chính thức; nếu không sửa preview sau tóm tắt thì server copy từ file tạm). Sau đó **Đẩy Qdrant**. Collection ngầm: đã tóm tắt trong phiên → **`legal_chunks_dual`**; chỉ tách đoạn → **`legal_chunks`**.

### 5.3 Lỗi thường gặp

| Hiện tượng | Hướng xử lý |
|------------|-------------|
| `LibreOffice conversion failed` | Cài LibreOffice; kiểm tra `libreoffice` trong `PATH`. |
| `ModuleNotFoundError: long_parser` | Chạy app từ đúng repo (có thư mục `src/`); cài `pip install -e .`. |
| Qdrant lỗi kết nối | Kiểm tra container/process Qdrant và `QDRANT_URL`. |
| Thiếu thư viện embedding | `pip install -r requirements-web.txt` hoặc `pip install -e ".[web]"`. |

---

## 6. Đưa dữ liệu vào Qdrant (không qua web)

Phù hợp khi đã có JSON retrieval (ví dụ từ `artifacts/retrieval/` sau `scripts/type1_to_retrieval_json.py`):

```bash
# Từ gốc repo, với Qdrant đã chạy
python3 scripts/embed_qdrant_chunks.py artifacts/retrieval/ten-van-ban.json
```

Tùy chọn: `--collection`, `--qdrant-url`, `--dry-run` (chỉ encode, không upsert).

Collection và tên vector mặc định:
- Dual (có summary): collection **`legal_chunks_dual`**, vectors **`dense_search`** + **`dense_summary`**
- Legacy (không summary): collection **`legal_chunks`**, vector **`dense`**
=> cần **giống** khi truy hồi.

---

## 7. Truy hồi văn bản pháp luật (CLI)

### 7.1 Điều kiện

- Qdrant đã có điểm (points) do embed tạo ra.
- **Cùng model** embedding với lúc index: mặc định `bkai-foundation-models/vietnamese-bi-encoder` (tham số `--model`).

### 7.2 Ví dụ lệnh

```bash
cd /path/to/long_parser
export QDRANT_URL=http://localhost:6333

python3 scripts/legal_retrieve.py "Điều 3 về thuế giá trị gia tăng" --top-k 8 --print-spec
```

Ghi filter ra file để chỉnh tay:

```bash
python3 scripts/legal_retrieve.py "Thông tư hướng dẫn" --top-k 8 --dump-filter /tmp/filter_spec.json
```

Dùng filter có sẵn (bỏ qua trích regex trên câu query cho phần filter):

```bash
python3 scripts/legal_retrieve.py "" --filter-json config/filter.json --top-k 5
```

*(File `config/filter.json` phải tồn tại và đúng cấu trúc; xem mẫu tại `config/schemas/query_filter_template.json`.)*

### 7.3 Kết quả

Stdout là JSON gồm `chunks` (đoạn có `return_text`, `score`, …) và `debug` (`filter_spec`, `post_filter_applied`, …).

---

## 8. Tóm tắt thứ tự “từ đầu đến truy hồi được”

1. Cài Python venv + `pip install -e ".[web]"`.  
2. Chạy Qdrant (`localhost:6333`).  
3. Tạo `data/input/`, `data/converted/`, `data/chunked/` và (khuyến nghị) `data/input/full_metadata.csv`.  
4. Chạy web: `cd web_indexer && python -m uvicorn app:app --host 127.0.0.1 --port 8765 --reload`.  
5. Upload → parse → lưu chunked → đẩy Qdrant **hoặc** embed bằng `scripts/embed_qdrant_chunks.py`.  
6. Truy hồi: `python3 scripts/legal_retrieve.py "…"`.

---

## 9. Liên kết tài liệu trong repo

| Tài liệu | Nội dung |
|----------|----------|
| [project-layout.md](./project-layout.md) | Cấu trúc thư mục, vai trò từng phần |
| [truy-van-tai-lieu-tu-query.md](./truy-van-tai-lieu-tu-query.md) | Chi tiết kỹ thuật pipeline truy vấn (filter, post-filter, Qdrant) |
