# Stack Overflow Comment Recommender — data pipeline

Project này thu thập dữ liệu từ các trang HTML công khai của Stack Overflow, sau đó tạo
cùng một bộ dữ liệu sạch bằng hai pipeline:

1. Python streaming + Parquet, có thể nạp vào PostgreSQL.
2. Spark + MinIO + Iceberg + Apache Polaris.

Hiện tại project **không sử dụng API và không có Trino**. Crawler chỉ đi từ trang tag đến
trang câu hỏi; không tự động crawl `/search`.

## Kiến trúc

```text
Stack Overflow tag pages
          |
          v
   Shared HTML crawler
          |
          +-- data/raw/html
          +-- posts.jsonl
          +-- comments.jsonl
          +-- duplicate_pairs.jsonl
                    |
             +------+------+
             |             |
       Python pipeline   Spark pipeline
             |             |
       Parquet/Postgres  MinIO/Iceberg
                           Polaris catalog
```

## Schema sạch

- `posts`: một dòng cho mỗi question.
- `comments`: chỉ comment trực tiếp của question.
- `duplicate_pairs`: `query_post_id -> relevant_post_id` lấy từ duplicate notice.

Comments chứa URL câu hỏi Stack Overflow hoặc cụm như `possible duplicate` bị loại để
không làm lộ ground truth cho mô hình.

## 1. Cài môi trường Python

```powershell
cd "E:\Project2\Data Science\stackoverflow_recommender"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,postgres]"
```

Thay địa chỉ liên hệ trong `config/crawler.toml` trước khi crawl.

## 2. Chạy pilot crawler

Mặc định chỉ crawl tối đa 50 question và chờ 3 giây giữa các request:

```powershell
so-crawl --config config/crawler.toml --max-questions 10
```

Crawler lưu trạng thái trong `data/raw/state.sqlite3`; chạy lại sẽ tiếp tục thay vì crawl
lại các trang đã hoàn thành. Nếu nhận HTTP 403, 429 hoặc trang CAPTCHA, crawler dừng.

Các giới hạn hiện tại:

- Chỉ lấy comments có trong HTML trả về.
- `comments_complete=false` cho biết trang có dấu hiệu còn comments bị thu gọn.
- Không gọi endpoint nội bộ để mở rộng comments.
- Không nên tăng quy mô trước khi kiểm tra quy định của giảng viên và chính sách website.

## 3. Pipeline Python

```powershell
so-clean-python --raw-dir data/raw --output-dir data/clean/python --overwrite
so-validate data/clean/python
```

Pipeline đọc JSONL theo dòng, dùng SQLite tạm để khử trùng ID và ghi Parquet theo batch;
không nạp toàn bộ 1–2 triệu dòng vào RAM.

### PostgreSQL tùy chọn

```powershell
docker compose -f docker-compose.python.yml up -d
so-load-postgres --clean-dir data/clean/python
```

## 4. Pipeline Spark + MinIO + Iceberg + Polaris

Yêu cầu Docker Desktop đang chạy. Lệnh sau khởi động các dịch vụ và chạy Spark job:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_spark_pipeline.ps1
```

Lưu ý: từ tháng 9/2026, image MinIO Community chính thức không còn cho anonymous pull.
Compose dùng image công khai của `coollabsio`, được build lại từ source MinIO chính thức,
và chỉ dành cho môi trường học tập/local development.

Đầu ra Spark được ghi đồng thời vào:

- Iceberg tables: `polaris.stackoverflow.posts`, `comments`, `duplicate_pairs`.
- Parquet kiểm chứng: `data/clean/spark`.
- MinIO console: <http://localhost:19001>.
- Polaris API: <http://localhost:18181>.
- Polaris health/metrics: <http://localhost:18182>.
- Spark UI khi job đang chạy: <http://localhost:14040>.

Kiểm tra đầu ra Spark:

```powershell
so-validate data/clean/spark
```

## 5. Nguyên tắc so sánh

Hai pipeline phải nhận cùng thư mục `data/raw` và cho ra cùng schema. So sánh ít nhất:

- số dòng hợp lệ của từng bảng;
- thời gian làm sạch;
- dung lượng đầu ra;
- RAM/CPU khi chạy;
- số dòng bị loại vì invalid, orphan hoặc label leakage.

Không dùng thời gian crawl trong benchmark vì crawler được dùng chung.

## Chạy test

```powershell
pytest
```
