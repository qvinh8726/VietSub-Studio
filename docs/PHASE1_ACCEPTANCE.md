# Báo cáo bàn giao Phase 1 — Creator Workbench

## Phạm vi

- Baseline thực tế: v1.8.0, HEAD `31b5a24`, nhánh `main`. Giữ tương thích v1.7.0 và mọi chức năng đã có của v1.8.0.
- Chỉ hoàn thành Phase 1 theo yêu cầu duyệt cuốn chiếu. APP_VERSION giữ nguyên 1.8.0; chưa phát hành v2.0.0, chưa commit/push/tag, chưa chạy build portable phát hành hoặc xác nhận CI remote.
- Kiến trúc/kế hoạch đầy đủ bốn Phase: [CREATOR_WORKBENCH_V2_PLAN.md](<CREATOR_WORKBENCH_V2_PLAN.md>).

## Kết quả triển khai

### 1. Khung ba workspace

- Rail Fluent-style 48 CSS px trong grid cố định `100vh`; VietSub / Auto Edit / Đăng đa nền tảng chuyển bằng click hoặc ArrowUp/Down, Home/End, không reload.
- VietSub giữ nguyên cây DOM thật: preview/player/crop/source tabs/queue không bị clone/remount/reset. Panel không hoạt động dùng `hidden` và `inert`; console, result và drawer dùng chung, focus được bảo vệ.
- Giữ nguyên fixture 115 DOM IDs, loại phần tử, defaults/values, script order, palette/font local. Sakuga không sửa mã; bộ regression VFX vẫn chạy.
- Quick actions từ kết quả hiện tại và từng job lịch sử thành công gọi handoff theo đúng job ID, có loading/error/retry, chống double-click và response đến trễ cướp workspace/focus.
- Workspace 2/3 nhận và xem video, SRT, crop nguồn, metadata, stage state thật từ manifest. UI ghi rõ render/TTS thuộc Phase 2, tài khoản/lịch đăng/upload thật thuộc Phase 3; không có nút chạy giả.

### 2. Manifest và bàn giao

- `vietsub/manifest.py` chỉ dùng Python stdlib, atomic UTF-8 sidecar `<video-stem>.project.json`; `project.json` legacy và API/result cũ được giữ nguyên.
- Schema v1: stable project ID, nguồn/video, SRT, crop/resolution thực dùng cho OCR, edited/TTS slots, metadata và trạng thái riêng ba phân hệ.
- GET snapshot không ghi đĩa; lazy migration từ legacy project/queue/publish pack. Lưu VietSub không xóa metadata/stage/asset của phân hệ khác. Crop sidecar thực tế ưu tiên hơn queue-request cũ; crop mặc định cũ không có bằng chứng được để unknown, không đoán.
- API mới chỉ nhận job ID, không nhận đường dẫn tùy ý. Handoff kiểm tra trạng thái hoàn tất, không còn active, file và nội dung SRT hợp lệ. Video preview dùng capability URL HMAC theo job/path/phiên app; API điều khiển vẫn dùng token/origin/Host/CSP cũ.
- Manifest hỏng, schema tương lai, số quá lớn, JSON lồng sâu, symlink/junction hay lỗi ghi không bị overwrite im lặng và không phá luồng VietSub legacy. Thiếu FFprobe làm OCR toàn khung thì không ghi nhầm crop chưa áp dụng.

### 3. Bulk-delete và dọn dung lượng

- Tái sử dụng contract v1.8: `ids`, `delete_files=false`, `keep_srt=true`, `dry_run`. Hộp thoại mặc định chỉ xóa bản ghi, không mặc định xóa file.
- Checkbox từng item, chọn Tất cả / Đã xong / Đã kết thúc / >3 ngày; badge MB/GB decimal, tabular-nums và bytes dry-run từ server, không cộng trùng các job.
- Inventory bảo vệ running/prefetch/shared files/hardlinks và SRT mặc định; không xóa đệ quy folder hoặc manifest/metadata ngoài phạm vi. Job không đọc được file được giữ để retry; các job độc lập vẫn dọn được. Lỗi inventory bảo vệ cả phụ đề dùng chung.
- `auto_delete_source_video` vẫn opt-in mặc định tắt, chỉ sau khi kiểm tra sub hợp lệ. Cảnh báo rõ xóa cả file nguồn local và không thể tiếp tục edit/upload nếu không khôi phục video.

### 4. Browser và CLI

- Giữ `--headless=new` / `--disable-blink-features=AutomationControlled`, profile độc lập và ownership PID + executable + profile + CDP session. Không kill browser theo tên.
- Reuse đúng target hoặc tab khởi đầu; chỉ đóng blank app-created có marker khi cần, không đụng tab không liên quan. Lease bao active download/translation/manual challenge, idle-close 60 giây chỉ khi rảnh.
- Captcha fallback một lần sang headed cùng profile/cookie; sau khi operation ngoài cùng kết thúc và probe xác nhận đã xong mới về headless. Unresolved login, timeout, lỗi, cancel và explicit headed không bị tự giấu. Restart lỗi không làm mất video đã tải thành công.
- Manual login có grace tối đa 10 phút để không bị idle-close giữa lúc QR/login; polling không gia hạn. Desktop cleanup bounded, shutdown không chờ vô hạn RLock đang được thread khác giữ. CLI tiếp tục dùng chung pipeline/lifecycle.

## Xác minh

Các lệnh bắt buộc đã được chạy trên mã tích hợp; xem báo cáo JSON/log và phần kết quả cuối của phiên làm việc:

```powershell
node --check static/app.js
node --check static/workbench.js
node --check static/sakuga-vfx.js
node --test tests/frontend/*.test.mjs
python -m py_compile app.py automate.py tools/make_release.py tools/workbench_qa.py tools/phase1_qa.py
python -m compileall -q vietsub
python -m unittest discover -s tests -v
python tools/workbench_qa.py --output artifacts/workbench-regression
python tools/phase1_qa.py --output artifacts/phase1-qa
```

- Frontend: **91/91 pass**, không skip. Fixture 115 IDs không thay đổi; local fonts, CSP và VFX regressions giữ xanh.
- Playwright cũ: **104 kiểm tra**, 12 ảnh, không issue; gồm 3 độ phân giải, DPI 125%/150%, portrait/square/4:3, crop/splitters/drawer/reduced-motion/bulk/reload.
- Playwright Phase 1: **3/3 độ phân giải** 1280×720, 1440×900, 1920×1080, 9 ảnh, không issue. Rail đo 48px; không document overflow/reload; giữ player/crop/time/source state; real Flask handoff/media/settings/cleanup; console/CSP/HTTP errors đều 0. Có stress console lớn + update banner và kiểm tra hit-target.
- Chrome thật opt-in: **1/1 pass, 7.554 giây** trên profile tạm/cổng ephemeral; headless→headed→headless, cookie continuity, single-tab reuse, decorated recovery và idle-close.
- Python full-suite: **456 tests, 453 pass và 3 skip**, gồm toàn bộ test legacy và test mới; log cuối [python-final.utf8.txt](<../artifacts/python-final.utf8.txt>). Ba skip thông thường: smoke opt-in (đã chạy riêng pass), kiểm tra release directory chưa build và symlink thật thiếu quyền Windows. Các nhánh từ chối symlink cũng có mock test không cần quyền.
- Review độc lập đã phát hiện ba ca biên manifest/geometry; đã sửa và thêm regression tái hiện cho cả ba trước vòng chạy cuối.

## Bằng chứng cục bộ

- [Báo cáo Playwright Phase 1](<../artifacts/phase1-qa/report.json>)
- [Báo cáo Playwright regression](<../artifacts/workbench-regression/report.json>)
- [VietSub 1280×720](<../artifacts/phase1-qa/1280x720-vietsub.png>)
- [Auto Edit 1440×900](<../artifacts/phase1-qa/1440x900-editor.png>)
- [Đăng đa nền tảng 1440×900](<../artifacts/phase1-qa/1440x900-uploader.png>)
- [Chrome smoke](<../artifacts/chrome-smoke.utf8.txt>)

Ảnh/video QA là fixture tổng hợp có nhãn, không phải kết quả OCR/render production. Artifacts bị gitignore và không được đưa vào commit phát hành.

## Giới hạn / chưa thực hiện

- Chưa làm FFmpeg editor, tách vocal/BGM, CapCut/Edge TTS, ASS hardsub, render/NVENC, CapCut Draft.
- Chưa làm quản lý nhiều account, upload adapters, scheduling hoặc đăng nội dung lên nền tảng thật.
- Không xác nhận production Captcha/login trên nền tảng bên ngoài; test local không giải/bypass Captcha.
- Chưa build/smoke EXE mới hoặc push/quan sát GitHub Actions của SHA phát hành; đó là Phase 4 sau khi các phase được duyệt. CI definition đã thêm runner Phase 1, chưa phải bằng chứng CI remote xanh.
- Không thêm thư viện AI/runtime nặng hay chỉnh requirements. Bản đóng gói hiện tại không tăng phụ thuộc runtime.
- File scratch có sẵn của người dùng không bị stage/xóa; không tạo `plan_v2.txt`. Không tự commit để bảo toàn điểm duyệt Phase 1.

**Cổng tiếp theo:** người dùng duyệt Phase 1 trước khi bắt đầu Phase 2.
