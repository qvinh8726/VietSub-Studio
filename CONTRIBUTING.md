# Contributing / Đóng góp

Thank you for helping improve VietSub Studio. Cảm ơn bạn đã muốn đóng góp cho dự án.

## English

1. Search existing issues before opening a new one.
2. Fork the repository and create a focused branch.
3. Keep changes small and avoid committing local settings, downloaded videos, subtitles, browser profiles, or credentials.
4. Install the development tools, then run the same checks as CI:

   ```powershell
   python -m pip install -r requirements-dev.txt
   python -m ruff check .
   python -m compileall -q app.py automate.py vietsub
   python -m unittest discover -s tests -v
   ```

   Tests run against temporary app-data paths and block real network access. When a test fails with "Tests attempted real network access", patch the network call it reached.

5. For UI changes, verify both desktop and a narrow mobile viewport.
6. Explain what changed, why it changed, and how it was tested in the pull request.

Good first contributions include documentation fixes, clearer diagnostics, new unit tests, filename edge cases, and resilient Gemini selectors.

## Tiếng Việt

1. Tìm trong Issues trước khi tạo báo cáo mới.
2. Fork repository và tạo một nhánh chỉ tập trung vào một thay đổi.
3. Không commit cấu hình cá nhân, video đã tải, phụ đề, profile trình duyệt hoặc thông tin đăng nhập.
4. Cài công cụ phát triển rồi chạy đúng các bước kiểm tra như CI:

   ```powershell
   python -m pip install -r requirements-dev.txt
   python -m ruff check .
   python -m compileall -q app.py automate.py vietsub
   python -m unittest discover -s tests -v
   ```

   Test chạy trên thư mục dữ liệu tạm và chặn truy cập mạng thật. Nếu test báo "Tests attempted real network access", hãy patch lời gọi mạng mà test đó chạm tới.

5. Nếu sửa giao diện, hãy kiểm tra cả desktop và màn hình mobile hẹp.
6. Pull request cần mô tả thay đổi, lý do và cách đã kiểm thử.

Các đóng góp phù hợp cho người mới gồm sửa tài liệu, làm thông báo lỗi rõ hơn, bổ sung test, xử lý tên file đặc biệt và tăng độ bền của selector Gemini.
