# Contributing / Đóng góp

Thank you for helping improve VietSub Studio. Cảm ơn bạn đã muốn đóng góp cho dự án.

## English

1. Search existing issues before opening a new one.
2. Fork the repository and create a focused branch.
3. Keep changes small and avoid committing local settings, downloaded videos, subtitles, browser profiles, or credentials.
4. Run the test suite. The Node commands need Node.js 20 or newer:

   ```powershell
   python -m unittest discover -s tests -v
   python -m py_compile app.py automate.py tools/make_release.py
   node --check static/app.js
   node --test tests/frontend/app.test.mjs
   ```

   The tests point `APPDATA`, `LOCALAPPDATA`, `VIETSUB_STUDIO_CONFIG_DIR`, and `VIETSUB_STUDIO_DOWNLOAD_DIR` at a temporary folder, so they never read or change your real settings, queue, logs, downloads, browser profiles, old Douzy library, or VideOCR files. The download tests mock the browser, Douyin's share page, and yt-dlp, and only talk to a local test server on `127.0.0.1`. No test starts a real browser.

5. For UI changes, verify both desktop and a narrow mobile viewport.
6. Explain what changed, why it changed, and how it was tested in the pull request.

Maintainers publish releases with [RELEASING.md](RELEASING.md).

Good first contributions include documentation fixes, clearer diagnostics, new unit tests, filename edge cases, and resilient Gemini selectors.

## Tiếng Việt

1. Tìm trong Issues trước khi tạo báo cáo mới.
2. Fork repository và tạo một nhánh chỉ tập trung vào một thay đổi.
3. Không commit cấu hình cá nhân, video đã tải, phụ đề, profile trình duyệt hoặc thông tin đăng nhập.
4. Chạy bộ kiểm thử. Hai lệnh Node cần Node.js 20 trở lên:

   ```powershell
   python -m unittest discover -s tests -v
   python -m py_compile app.py automate.py tools/make_release.py
   node --check static/app.js
   node --test tests/frontend/app.test.mjs
   ```

   Bộ test trỏ `APPDATA`, `LOCALAPPDATA`, `VIETSUB_STUDIO_CONFIG_DIR` và `VIETSUB_STUDIO_DOWNLOAD_DIR` vào một thư mục tạm, nên không đọc hay sửa thiết lập, hàng đợi, log, video tải về, profile trình duyệt, thư viện Douzy cũ hoặc file VideOCR thật của bạn. Test tải video dùng mock cho trình duyệt, trang chia sẻ Douyin và yt-dlp, và chỉ kết nối tới server thử trên `127.0.0.1`. Không test nào mở trình duyệt thật.

5. Nếu sửa giao diện, hãy kiểm tra cả desktop và màn hình mobile hẹp.
6. Pull request cần mô tả thay đổi, lý do và cách đã kiểm thử.

Người bảo trì phát hành bản mới theo [RELEASING.md](RELEASING.md).

Các đóng góp phù hợp cho người mới gồm sửa tài liệu, làm thông báo lỗi rõ hơn, bổ sung test, xử lý tên file đặc biệt và tăng độ bền của selector Gemini.
