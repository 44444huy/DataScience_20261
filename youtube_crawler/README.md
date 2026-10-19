# YouTube DOM crawler

MVP Python 3.10+ để tự thu thập thông tin video và bình luận qua HTML/DOM.
Code không gọi YouTube Data API, endpoint JSON nội bộ, ytInitialData, fetch hay XHR.
Trình duyệt vẫn tự tải các tài nguyên của website như khi truy cập thông thường.

## Chạy nhanh

Giải nén, mở terminal trong thư mục có crawl_youtube.py:

    python -m pip install -r requirements.txt
    python -m playwright install chromium
    python crawl_youtube.py --urls-file urls.txt --max-videos 1 --max-comments 20

Trên Windows, có thể dùng trình duyệt Chrome đang cài để quan sát:

    python crawl_youtube.py --urls-file urls.txt --max-videos 1 --max-comments 20 --channel chrome --headed

Trên Ubuntu nếu thiếu thư viện hệ thống:

    python -m playwright install --with-deps chromium

## Các cách chọn nguồn

Một video:

    python crawl_youtube.py --url "https://www.youtube.com/watch?v=B9Gn-K8zQ2Q" --max-comments 50

Nhiều video, lấy tối đa 3 URL đầu trong urls.txt:

    python crawl_youtube.py --urls-file urls.txt --max-videos 3 --max-comments 50

Tìm qua giao diện web:

    python crawl_youtube.py --query "review điện thoại Vật Vờ Studio" --max-videos 3 --max-comments 30

Trang tìm kiếm mới đọc các kết quả đã được tải trong lượt đầu; chưa quét toàn bộ kênh.

## Kết quả

Trong thư mục data/:

- comments.jsonl: sự kiện insert/update của bình luận.
- videos.jsonl: sự kiện insert/update của thông tin video.
- state.sqlite: dữ liệu hiện tại và lịch sử sự kiện, dùng cho các lượt chạy sau.
- run_summary.json: kết quả, lỗi và lý do dừng của lượt gần nhất.

Mỗi dòng JSONL gồm event_id, entity_id, operation, observed_at, data.
Giữ lại state.sqlite để chống trùng giữa các lần chạy.
Bình luận cùng ID và nội dung/like không thay đổi không tạo sự kiện mới.
Nội dung hoặc like thay đổi tạo update; đó là một phiên bản mới hợp lệ.
published_text được giữ nguyên như giao diện hiển thị, chưa đổi thành ngày tuyệt đối.
SQLite là nguồn dữ liệu chính. JSONL được xuất lại từ lịch sử đã commit,
để tránh mất đồng bộ nếu chương trình bị ngắt giữa thao tác ghi dữ liệu và xuất file.
Chạy một tiến trình cho mỗi thư mục output; chưa hỗ trợ nhiều worker cùng ghi.

## Phạm vi và giới hạn

- Chạy tuần tự, 10 giây giữa video và 2 giây giữa các lần cuộn mặc định.
- max-comments giới hạn mẫu bình luận, không phải cam kết đọc đủ toàn bộ.
- Bình luận theo thứ tự website đang hiển thị; chưa mở hết các nhánh phản hồi.
- Một số bình luận dài có thể bị thu gọn; dữ liệu cần kiểm tra trước khi gán nhãn AI.
- Dữ liệu lấy từ DOM có thể thiếu trường; run_summary lưu trạng thái riêng.
- Trường lượt xem/ngày/like giữ dạng văn bản; cần chuẩn hóa khi đưa vào Silver.
- Trang có thể đổi cấu trúc. Cập nhật các selector trong READ_DOM khi cần.
- Các giới hạn request giúp giảm tải, không bảo đảm tránh được chặn.

Gặp yêu cầu xác minh, CAPTCHA hiển thị, HTTP 403/429 hoặc chuyển sang trang
đăng nhập/xác minh: dừng cả lượt chạy, giữ dữ liệu đã thu thập.
Video đang đọc chỉ được ghi sau khi kết thúc bước lấy mẫu; nếu bị chặn giữa
video, mẫu chưa ghi của video đó có thể mất. Các video đã ghi vẫn được giữ.
Không xoay proxy, đổi fingerprint, dùng stealth hay giải CAPTCHA.
Chỉ sử dụng trên nội dung bạn có quyền thu thập; điều khoản YouTube hạn chế
truy cập bằng scraper tự động nếu chưa được cho phép.

Exit code: 0 hoàn tất, 1 lỗi/hoàn tất có lỗi, 2 hạn chế truy cập, 130 ngắt bằng Ctrl+C.

## Kiểm tra

    python -m unittest -v test_crawler.py

Các kiểm tra gồm chống trùng, nhận diện phiên bản mới, tái xuất sau gián đoạn,
chuẩn hóa URL, giữ emoji và trích ID từ DOM, dừng khi gặp thông báo xác minh.

Thông tin kiểm tra thực tế của bản đóng gói nằm trong VALIDATION.md.
