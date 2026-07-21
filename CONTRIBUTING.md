# Contributing / Đóng góp

Thank you for helping improve VietSub Studio. Cảm ơn bạn đã muốn đóng góp cho dự án.

## English

1. Search existing issues before opening a new one.
2. Fork the repository and create a focused branch.
3. Keep changes small and avoid committing local settings, downloaded videos, subtitles, browser profiles, or credentials.
4. Run the test suite:

   ```powershell
   python -m unittest discover -s tests -v
   python -m py_compile app.py automate.py
   ```

5. For UI changes, verify both desktop and a narrow mobile viewport.
6. Explain what changed, why it changed, and how it was tested in the pull request.

Good first contributions include documentation fixes, clearer diagnostics, new unit tests, filename edge cases, and resilient Gemini selectors.

## Tiếng Việt

1. Tìm trong Issues trước khi tạo báo cáo mới.
2. Fork repository và tạo một nhánh chỉ tập trung vào một thay đổi.
3. Không commit cấu hình cá nhân, video đã tải, phụ đề, profile trình duyệt hoặc thông tin đăng nhập.
4. Chạy bộ kiểm thử:

   ```powershell
   python -m unittest discover -s tests -v
   python -m py_compile app.py automate.py
   ```

5. Nếu sửa giao diện, hãy kiểm tra cả desktop và màn hình mobile hẹp.
6. Pull request cần mô tả thay đổi, lý do và cách đã kiểm thử.

Các đóng góp phù hợp cho người mới gồm sửa tài liệu, làm thông báo lỗi rõ hơn, bổ sung test, xử lý tên file đặc biệt và tăng độ bền của selector Gemini.
