# Kiểm tra bản đóng gói

Ngày kiểm tra: 14/10/2026. Python 3.12, Playwright 1.64.0.

## Đã thực hiện

- Cài các dependency Python thành công.
- Biên dịch cú pháp Python và chạy `--help` thành công.
- Ba kiểm tra không cần trình duyệt đã đạt: chống trùng và ghi phiên bản mới;
  khôi phục JSONL từ SQLite sau khi chưa xuất file; chuẩn hóa URL và từ chối
  host khác; nhận diện các biến thể thông báo xác minh.
- Thử chạy khi thiếu browser binary: chương trình trả exit code 1, lưu
  `run_summary.json` với trạng thái `failed`, và vẫn xuất JSONL từ SQLite.

## Chưa kiểm chứng

- Tải Chromium trong môi trường kiểm tra thất bại. Nhóm kiểm tra DOM bằng
  trình duyệt đã bị bỏ qua, không được tính là đã đạt.
- Chưa chạy toàn bộ script Python này trực tiếp trên YouTube. Cần thử một
  video, khoảng 20 bình luận, trên máy có Chromium hoặc Chrome/Edge.
- Một thử nghiệm trình duyệt riêng trước đó đã đọc được bình luận trên
  YouTube, nhưng đó không phải bằng chứng bản script này đã chạy thành công.

Nếu chương trình hoàn tất nhưng `comments_read` là 0, xem `stop_reason`
trong `run_summary.json`, chạy lại với `--headed` để quan sát. Dữ liệu thiếu
không có nghĩa video không có bình luận. Nếu có thông báo hạn chế truy cập,
chương trình dừng và cần xử lý quyền truy cập trước khi chạy tiếp.
