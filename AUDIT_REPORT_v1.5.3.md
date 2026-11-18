# Báo cáo kiểm toán kỹ thuật VietSub Studio v1.5.3

> Code được kiểm toán: nhánh `main`, commit `de36318` (tag `v1.5.3` cộng landing page). Ngày kiểm toán: 17/11/2026.
>
> Báo cáo này kế thừa hai tài liệu có sẵn: `PROJECT_ANALYSIS_REPORT.md` (audit v1.5.2) và `.kiro/specs/vietsub-studio-qa-fixes/bugfix.md` (18 bug QA đã tái hiện trên v1.5.3). Những gì hai tài liệu đó đã nói đúng không được viết lại. Báo cáo chỉ ghi trạng thái hiện tại của chúng, nguyên nhân gốc trong code và các vấn đề mới.

## 0. Phạm vi, phương pháp và quy ước

**Phạm vi:** `app.py`, `automate.py`, `static/app.js`, `static/style.css`, `templates/index.html`, `tests/`, `.github/`, hai file spec PyInstaller, các script `.bat`, README, HUONG_DAN_SU_DUNG, CHANGELOG, SECURITY. Không gồm `landing-page/`.

**Phương pháp:**
- Đọc trực tiếp code, quét AST để đếm hàm, biến toàn cục và số lần patch trong test.
- Đối chiếu với lịch sử git (`v1.5.2`, `v1.5.3`).
- Chạy bộ unit test: 113/113 xanh trên Python 3.14.3 tại máy kiểm toán.
- Không chạy Douzy, VideOCR, Edge/Gemini hay API dịch thật. Những kết luận cần môi trường thật được đánh dấu [I].

**Quy ước:**
- [V]: đã kiểm chứng trên code. [I]: suy luận, cần chạy thật để xác nhận.
- Số dòng là của `app.py` tại commit `de36318`, trừ khi ghi tên file khác. Sau các bản sửa, số dòng có thể lệch.
- `QA-1.x` là bug số 1.x trong `bugfix.md`. Mã của báo cáo v1.5.2 giữ nguyên dạng `H-0x`, `M-xx`, `L-0x`.
- Severity (High/Medium/Low) gộp tác động và khả năng xảy ra. Thứ tự xử lý nằm ở Phần 4.

## 1. Tóm tắt điều hành

VietSub Studio là app desktop Windows nhận video Douyin (tải qua Douzy) hoặc MP4 trên máy, OCR phụ đề cứng bằng VideOCR, dịch sang tiếng Việt qua Gemini Notebook hoặc API kiểu OpenAI, kiểm tra SRT rồi gom thành một thư mục dự án. Hàng đợi chạy tuần tự và chạy lại được từ bước bị hỏng.

**Kết luận chính:**

1. v1.5.3 đã sửa phần lớn các vấn đề High của v1.5.2: lớp bảo vệ API cục bộ, khoá một instance, ghi hàng đợi kiểu copy-on-write, khớp asset Douzy chính xác, chặn thêm nhầm preview cũ, CI build và chạy thử EXE.
2. Code mới của v1.5.3 (OCR chia đoạn, dịch chia đoạn, provider API/SSE) mang vào một nhóm lỗi gây **job treo và mất công đã làm**:
   - request API dịch không huỷ được và không có hạn chót tổng (C1);
   - mỗi lần chạy VideOCR có trần cứng 30 phút, kể cả khi chạy một lần cho video 45 phút (O1);
   - kết quả OCR của từng đoạn 10 phút không được lưu (O2);
   - cache dịch giữ lại đoạn bị model đánh số lại, nên mọi lần chạy lại đều thất bại (T1).
3. Release v1.5.3 đặt sai tên file checksum và ZIP không có thư mục con (QA-1.1), nên mọi bản từ v1.3.0 đến v1.5.2 không tự cập nhật được. Nguyên nhân là đóng gói release thủ công và không có test kiểm tra tên file.
4. CHANGELOG 1.5.3 ghi là đã sửa việc CLI ghi đè hàng đợi, nhưng code chưa sửa (QA-1.2, chính là H-03 của v1.5.2).
5. `app.py` tăng từ 3.517 lên 5.628 dòng. Ba nguyên nhân trực tiếp của tình trạng "sửa chỗ này vỡ chỗ khác" là: hành vi phụ thuộc vào câu chữ tiếng Việt trong thông báo lỗi, trạng thái toàn cục dùng chung giữa preview/worker/prefetch, và 129 lần `patch.object(app, …)` trong test.
6. Chất lượng dịch bị kéo xuống vì glossary game Delta Force được gửi mặc định cho mọi video, prompt luôn khẳng định đầu vào là tiếng Trung (QA-1.18), và các đoạn dịch không có ngữ cảnh của nhau.

**Điểm tổng (chủ quan, cùng thang với báo cáo v1.5.2): 6,2/10** (v1.5.2: 6,1).

**Khuyến nghị:** phát hành v1.5.4 gồm nhóm Quick Wins ở mục 5.1 trước khi thêm tính năng. Sau đó tách module theo mục 5.2 để chuẩn bị mở rộng nguồn video, công cụ OCR và provider dịch.

**Quyết định đã chốt với chủ dự án (17/11/2026):**
- Prompt dịch mặc định chuyển sang dạng trung tính; glossary Delta Force thành preset tuỳ chọn. Mục 3.24 của `bugfix.md` được cập nhật theo.
- Timeout tải Douzy tính theo việc payload trạng thái có thay đổi hay không (bỏ qua các trường thời gian), kèm trần tổng 6 giờ.
- Bản sửa được đóng thành v1.5.4. Agent tăng version, cập nhật CHANGELOG và để CI xuất đúng 2 file theo định dạng updater. Chủ dự án tự tag và upload release.

## 2. Phần 1: Đánh giá tổng quan và kiến trúc

### 2.1. Điểm mạnh cần giữ

| Điểm mạnh | Vị trí [V] | Vì sao cần giữ |
|---|---|---|
| Chạy lại theo bước (tải, OCR, dịch); cache dịch API theo từng đoạn trong `.parts` | 3995-4005, 3656-3698 | Lỗi ở bước dịch không bắt tải lại hay OCR lại video dài. |
| Ghi file qua file tạm rồi `os.replace`; hàng đợi trong RAM chỉ đổi sau khi ghi file thành công | 836-845, 4040-4077 | Không để lại JSON/SRT ghi dở. |
| Kiểm tra kết quả trước khi báo xong: timestamp, số block, số thứ tự, chống trả lại tiếng Trung | 3206-3361 | Không báo "Hoàn tất" cho bản dịch thiếu. |
| API cục bộ: token cho mỗi lần mở app (so bằng `compare_digest`), kiểm tra Host/Origin/Fetch-Metadata, API thay đổi trạng thái chỉ nhận JSON, CSP `script-src 'self'` | 146, 4851-4899 | Trang web lạ không điều khiển được app. |
| Updater: URL ghim vào repo và tag, checksum bắt buộc, digest và kích thước GitHub, chỉ lấy đúng một file EXE, kiểm tra header `MZ`, có rollback | 235-320, 978-1182 | Không zip slip, không cài file hỏng. |
| Xác minh tiến trình Edge trước khi tự động hoá (payload CDP, tiến trình sở hữu cổng, profile) | 1262-1384 | Không gửi phụ đề vào trình duyệt lạ đang chiếm cổng 9222. |
| API key mã hoá DPAPI, không trả về giao diện; Base URL bắt buộc HTTPS | 721-799, 443-460 | Không lộ key qua file cấu hình hay API. |
| Giám sát tiến trình con: hàng đợi output có giới hạn, hạn chót theo đồng hồ đơn điệu, kiểm tra huỷ mỗi 0,5 giây | 1562-1577, 2470-2603 | Không deadlock pipe, không phình bộ nhớ. |
| Frontend: `previewRequestId` chống thêm nhầm preview cũ, polling theo revision, không chèn HTML động | `static/app.js` 8, 125-210, 349-364 | Sửa dứt điểm H-09 của v1.5.2. |
| CI Windows build EXE, chạy thử `--server`, kiểm tra instance thứ hai thoát với mã 2 | `.github/workflows/tests.yml` | Lần đầu có kiểm thử trên đúng bản phát hành. |
| Hard link MP4 local vào dự án; OCR Douyin tại chỗ rồi chuyển file sau khi dịch xong | 1938-2196 | Video 6 GB không thành 12 GB. |

### 2.2. Kiến trúc có hợp lý với quy mô và mục tiêu không

**Kết luận:** phần tính năng không bị làm quá phức tạp. Phần cấu trúc đang sơ sài (under-engineering) so với mục tiêu phát hành cho cộng đồng và mở rộng nguồn video, công cụ OCR, provider dịch.

| Chỉ số [V] | v1.5.2 | v1.5.3 |
|---|---:|---:|
| Số dòng `app.py` | 3.517 | 5.628 |
| Hàm cấp module trong `app.py` | 150 | 223 |
| Route Flask | 23 | 25 |
| Số dòng `tests/test_app.py` | 1.443 | 2.591 |
| Số test | 65 | 113 |
| `patch.object(app, …)` trong test | không đo | 129 lần trên 69 tên |
| Handler bắt rộng (`except Exception`/`BaseException`) | 19 | 26 |
| Đối tượng trạng thái toàn cục có thể thay đổi | không đo | 28 |

**Bốn nguyên nhân trực tiếp của "sửa chỗ này vỡ chỗ khác":**

1. **Hành vi phụ thuộc câu chữ tiếng Việt (A2).** Logic điều khiển đọc nội dung thông báo lỗi:
   - `"tiếng Trung" in error` quyết định bước chạy lại (`normalize_persisted_job`, khoảng dòng 4140).
   - `"HTTP 400" in str(error)` quyết định chuyển sang JSON (`translate_api_chunk`, khoảng 3577).
   - `"SRT"`, `"song ngữ"`, `"cắt câu trả lời"` trong message quyết định không thử lại (khoảng 3594-3604).
   - `"nút gửi" in str(error)` quyết định đòi đăng nhập Edge (khoảng 3806).

   Sửa một câu thông báo là đổi hành vi của app mà không test nào báo.
2. **Trạng thái toàn cục dùng chung.** Preview, worker hàng đợi, prefetch và CLI dùng chung `cancel_event`, `progress_status` và registry tiến trình. Riêng `cancel_event.clear()` được gọi ở 5 hàm, nên lệnh huỷ có thể bị một thao tác khác xoá mất (QA-1.5, C3).
3. **Không có ranh giới cho nguồn video, công cụ OCR và provider dịch.** Douyin/Douzy gắn cứng trong `download_video_source` (3835-3866), `health` (5021-5108) và `missing_runtime_dependencies` (4441-4458). Luồng API và luồng Gemini mỗi bên tự viết lại phần chia đoạn, chuẩn hoá và ghi file (3635-3818).
4. **Test patch theo tên trong module `app`.** Khi một hàm được chuyển sang module khác, `patch.object(app, "f")` không còn tác dụng với lời gọi bên trong module mới. Test vẫn xanh trong khi thực tế đã gọi PowerShell, Edge hay Douzy thật.

**Chỗ phức tạp quá mức (nhỏ):**
- Mỗi lần giao diện polling 5 giây, app chạy PowerShell và WMI để xác minh tiến trình Edge (QA-1.14), mất 1,2-2,5 giây mỗi lần trên máy kiểm thử QA.
- Bốn khoá điều phối (`operation_admission_lock`, `workflow_lock`, `preview_lock`, `update_install_lock`) chồng lên nhau nhưng vẫn hở khoảng chuyển giữa hai job (QA-1.5).

**Chỗ vừa đủ, nên giữ:** Flask cục bộ kết hợp pywebview, hàng đợi tuần tự, file JSON cho tối đa 500 job, một tiến trình Python. Không cần microservices hay database server. Hướng đúng là một monolith chia module, có interface cho `VideoSource`, `OcrEngine`, `TranslationProvider` (mục 5.2 và 5.3).

**Bản đồ trách nhiệm hiện tại của `app.py` [V]:**

| Dòng | Nội dung |
|---|---|
| 1-196 | Import, đường dẫn tính từ biến môi trường lúc import, hằng số, trạng thái toàn cục |
| 222-418 | Kiểm tra cập nhật, trạng thái tiến trình job |
| 421-605 | Validation |
| 608-718 | Registry tiến trình, huỷ, khoá instance, tắt app |
| 721-975 | DPAPI, cấu hình, thư mục Windows, shortcut |
| 978-1190 | Cài cập nhật (script PowerShell nhúng) |
| 1204-1252 | Log |
| 1254-1559 | Edge/CDP, selector Gemini |
| 1580-1935 | Douzy sidecar, SQLite, thumbnail |
| 1938-2280 | File dự án, chuyển asset Douzy |
| 2283-2820 | VideOCR, FFmpeg, ffprobe |
| 2860-3378 | Prompt, client API, SRT parse/split/validate |
| 3381-3818 | Gemini, điều phối dịch |
| 3820-4262 | Nguồn video, preview, lưu hàng đợi |
| 4265-4849 | Thêm job, prefetch, worker, `run_workflow_thread` |
| 4851-5488 | Bảo mật HTTP, 25 route |
| 5490-5628 | Launcher |

Hàm dài nhất: `run_workflow_thread` 187 dòng, `run_videocr` 167, `translate_srt_via_gemini_edge` 115, `handle_settings` 97, `run_queue_worker` 97.

### 2.3. Scorecard

| Chiều | v1.5.2 | v1.5.3 | Lý do thay đổi |
|---|---:|---:|---|
| Product clarity | 8,5 | 8,5 | |
| Feature completeness | 8,0 | 8,0 | |
| Architecture | 5,5 | 5,5 | |
| Core workflow correctness | 6,5 | 6,5 | Sửa H-01, H-09; thêm T1, T2, O1 |
| Recovery/data integrity | 6,0 | 6,0 | Copy-on-write; thêm QA-1.2, QA-1.11, C6 |
| Concurrency/lifecycle | 4,5 | 5,5 | Admission lock, watchdog native; còn C1, C2, QA-1.5 |
| Security/privacy | 6,0 | 7,0 | Token, CSP, xác minh Edge; còn S1, S2, S9 |
| Performance | 6,5 | 6,5 | |
| Observability | 4,5 | 4,5 | |
| Backend testing | 7,5 | 7,5 | |
| Integration/package testing | 3,5 | 5,0 | Smoke test EXE trong CI |
| Build/release engineering | 4,5 | 4,0 | QA-1.1 xảy ra thật |
| Maintainability | 5,0 | 4,5 | Thêm 2.100 dòng vào một file |
| UX | 7,0 | 7,0 | |
| Accessibility | 5,0 | 5,0 | |
| Documentation | 7,5 | 6,5 | Lệch với code (QA-1.17) |
| **Tổng (chủ quan)** | **6,1** | **6,2** | |

## 3. Phần 2: "Bắt bệnh" chuyên sâu

### 3.1. Nguyên nhân gốc của 18 bug QA

Cả 18 bug trong `bugfix.md` vẫn đúng với code tại `de36318`. Bảng dưới chỉ ghi nguyên nhân gốc và task sửa trong v1.5.4.

| Bug | Nguyên nhân gốc [V] | Sửa ở |
|---|---|---|
| QA-1.1 | Tên asset (`release_asset_names`, 274-276) và đường dẫn EXE trong ZIP (1049) không đổi từ v1.3.0, nhưng release được đóng gói thủ công. `build_portable.bat` chỉ build EXE; CI chỉ băm EXE; không có test nào so tên file với chuỗi cố định. | Task 3 |
| QA-1.2 | `automate.py:27` → `run_single_workflow` (4338) không nạp hàng đợi → `finalize_project_assets` → `replace_source_video_references` (2103-2117) → `commit_queue_jobs_locked` → `write_queue_file(QUEUE_PATH, [])`. | Task 4 |
| QA-1.3 | `poll_download_job` (1668-1674) đo 300 giây từ lúc bắt đầu, không nhìn tiến độ. | Task 8 |
| QA-1.4 | `get_videocr_crop_coords` (2287) và `run_videocr` (2692) mở ini bằng `encoding="utf-8"`; lỗi giải mã nằm ngoài khối `try`. | Task 7 |
| QA-1.5 | `workflow_lock` được nhả giữa hai job (4564, 4848) và chỉ lấy lại ở 4526 sau khi chờ prefetch; preview chỉ kiểm tra `workflow_lock` (3893); huỷ bị từ chối khi khoá đang trống (642); preview gọi `cancel_event.clear()` (3897, 3922); preview và job dùng chung khoá registry `"sidecar Douzy"`. | Task 9 (phần chặn), mục 5.2 (huỷ theo job) |
| QA-1.6 | `shutil.which("ffprobe"/"ffmpeg")` (2323, 2331) tìm cả thư mục hiện hành; `explorer.exe` gọi theo tên (5482, 5484). | Task 6 |
| QA-1.7 | `split_srt_for_translation` trả nguyên SRT khi video ≤45 phút (3161-3164); giới hạn 400 block và 24.000 ký tự chỉ áp cho video dài. | Task 11 |
| QA-1.8 | `read_streaming_chat_completion` coi EOF như `[DONE]` (3022-3031); lỗi cấu trúc sau đó có chữ "SRT" nên bị xếp vào loại không thử lại (3601). | Task 10 |
| QA-1.9 | `normalize_persisted_job` (4106-4116) gộp mọi lỗi kiểm tra thành một thông báo nhắc `deepseek-v4-pro`; `retry_queue_job` tính lại bước chạy từ file còn tồn tại (3995-4005). | Task 5, Task 14 |
| QA-1.10 | `automate.py` in tiếng Việt ra stdout (38-40) mà không đổi encoding. | Task 4 |
| QA-1.11 | `load_app_config` đọc bằng `utf-8` (819); mọi `OSError` bị coi là file hỏng và bị cách ly (832); lần lưu Thiết lập sau đó ghi giá trị mặc định đè lên. | Task 5 |
| QA-1.12 | `QueuePersistenceError` kế thừa `OSError`; các route chỉ bắt `ValueError`/`RuntimeError` (5169-5220). | Task 5 |
| QA-1.13 | `translate_api_chunk` coi mọi chuỗi "HTTP 400" là "không hỗ trợ streaming" (3577-3590). | Task 10 |
| QA-1.14 | `static/app.js:635` gọi `/api/check-edge` mỗi 5 giây → `check_edge_status` → `edge_debug_owner_uses_profile` chạy PowerShell + WMI (1329-1374). | Task 6 |
| QA-1.15 | Werkzeug ghi upload vào TEMP khi truy cập `request.files` (5128) [I], sau đó `upload.save` chép thêm một lần (3958). | Mục 5.2 (chọn file bằng hộp thoại native) |
| QA-1.16 | Ở chế độ source, `CONFIG_DIR` là thư mục chứa `app.py` (74-78); `write_durable_log` không có cơ chế tắt; `APPDATA`/`LOCALAPPDATA` được đọc lúc import (41-42) nên test đọc file Douzy/VideOCR thật. | Task 2 |
| QA-1.17 | README Troubleshooting (cả hai ngôn ngữ) mô tả hành vi sao chép cũ; README hứa ZIP có `SHA256.txt`. | Task 15 |
| QA-1.18 | Prompt khẳng định "ĐẦU VÀO LÀ TIẾNG TRUNG" (2928-2930), system prompt "Trung sang Việt" (3548-3550); `ocr_lang` không được truyền vào bước dịch (4763-4775). | Task 11 |

### 3.2. Vấn đề mới mức High

| ID | Vấn đề | Vị trí | Cơ chế và hậu quả |
|---|---|---|---|
| C1 | Request dịch qua API không huỷ được, không có hạn chót tổng | 3020-3063, 3096-3106, 641-652 | Vòng đọc SSE không kiểm tra lệnh huỷ. `timeout=180` chỉ áp cho từng lần đọc socket, nên gateway gửi keep-alive hoặc model chạy chậm có thể giữ request rất lâu. Nút Huỷ chỉ kill tiến trình con, không đóng được kết nối HTTP. Job kẹt ở bước dịch và cả hàng đợi đứng. Với video ≤45 phút, sau khi stream xong không còn điểm kiểm tra huỷ, nên job vẫn báo thành công. Đây là nguyên nhân "job treo" có khả năng cao nhất. |
| C2 | Đóng app giữa lúc hàng đợi chạy, worker vẫn lấy job kế tiếp | 703-718, 4487-4528 | Worker không đọc `shutdown_event` và tự xoá cờ huỷ khi bắt đầu job mới. Job sau có thể khởi động VideOCR hoặc Douzy sau khi app đã kill tiến trình, để lại tiến trình mồ côi chiếm GPU và một thư mục dự án dở dang. Job trước được lưu là "Đã huỷ" thay vì "bị gián đoạn". |
| O1 | Mỗi lần chạy VideOCR có hạn chót cố định 30 phút | 89-90, 2486-2492, 2805-2817 | Video ≤45 phút chỉ chạy một lần, vẫn với hạn 30 phút, nên phải OCR nhanh hơn 1,5 lần thời gian thực mới kịp. Máy chỉ có CPU thường không đạt [I]. Thiếu ffprobe thì video bất kỳ độ dài cũng chạy một lần. Lỗi lặp lại ở mọi lần chạy lại. |
| O2 | Không lưu kết quả OCR của từng đoạn 10 phút | 2728-2804 | Kết quả từng đoạn chỉ nằm trong thư mục tạm và RAM. Hỏng ở đoạn 17/18 (timeout, VideOCR thoát 0 mà không ghi file, một block SRT lỗi) là mất cả 16 đoạn đã xong; lần chạy lại OCR lại từ đầu. |
| T1 | Đoạn dịch bị model đánh số lại vẫn được lưu cache | 3313-3328, 3669-3675 | `normalize_translated_srt` chỉ khôi phục số thứ tự khi số thứ tự đã khớp. Đoạn 2 (block 401-800) trả về số 1..400 với timecode đúng vẫn qua kiểm tra và được lưu vào `.parts`. File ghép có số thứ tự trùng nên bị từ chối, và mọi lần chạy lại dùng lại đúng đoạn hỏng đó. Sửa QA-1.7 làm app chia đoạn nhiều hơn, nên lỗi này phải sửa cùng lúc. |
| T3 | Glossary game Delta Force là prompt mặc định cho mọi video | 2870-2914, 3548 | Từ điển tên riêng và system prompt "game, phim hành động" được gửi cho mọi nội dung. Từ thông dụng có thể bị ép thành tên riêng, ví dụ 比特 thành Pete, 回响 thành Echo, 三角洲 thành Delta [I]. Prompt còn bắt model liệt kê thuật ngữ sau block SRT, vừa tốn token output vừa làm rối bước trích SRT từ trang Gemini. |
| F1 | `fetch` trong giao diện không có timeout | `static/app.js` 36-52, 349-364 | Một request treo làm `queueLoadInFlight` kẹt ở `true`. Các lần polling sau đều thoát sớm, giao diện ngừng cập nhật nhưng trông vẫn bình thường, và bộ đếm mất kết nối không bao giờ tăng. |
| F2 | Nút "Huỷ job" vẫn bị khoá khi job kế tiếp đã chạy | `static/app.js` 320-321, 398-402 | Sau khi gửi huỷ, nút chỉ mở lại khi không còn job nào chạy, nhưng worker chạy ngay job tiếp theo. Người dùng không huỷ được job đó và tưởng app bị treo. |
| A1 | `app.py` là một module 5.628 dòng với 28 đối tượng trạng thái toàn cục | toàn file | Mọi thay đổi đều chạm vào cùng một namespace; test phải patch theo tên trong module này (mục 2.2). |
| A2 | Hành vi phụ thuộc câu chữ của thông báo lỗi | 3577, 3594-3604, 3806, 4140 | Xem mục 2.2. |

### 3.3. Vấn đề mức Medium

| ID | Vấn đề | Vị trí | Hướng sửa | Đợt |
|---|---|---|---|---|
| C3 | Lệnh huỷ không gắn với job; huỷ trễ có thể trúng job kế tiếp; huỷ đến giữa 4526 và 4527 bị xoá | 641-652, 4526-4527, `static/app.js` 399 | Mỗi job có `Event` huỷ riêng, `/api/cancel` nhận `job_id` | 5.2 |
| C4 | Chỉ kill tiến trình con trực tiếp (`terminate` và `kill` trên Windows là cùng một lệnh); `wait` quá hạn ở lần thứ hai ném `TimeoutExpired` che mất lỗi gốc | 608-617 | `taskkill /PID … /T /F` từ `System32`; về sau dùng Windows Job Object | Task 9 |
| C5 | Ở chế độ trình duyệt dự phòng, đóng tab xong tiến trình vẫn giữ khoá instance; mở lại app thì thoát mà không báo gì | 5499-5515, 5605-5607 | Ghi URL cạnh file khoá; instance thứ hai mở lại URL đó | 5.2 |
| C6 | Lỗi đọc tạm thời (antivirus, OneDrive đang giữ file) bị coi là hàng đợi hỏng, file bị cách ly rồi bị ghi đè bằng danh sách rỗng | 4166-4174, 4244-4251 | Chỉ cách ly khi JSON sai; lỗi OS thì không ghi file trong phiên đó | Task 5 |
| O3 | Bước sửa crash do video đổi độ phân giải chỉ có ở nhánh chia đoạn | 2805-2817 | Dùng chung một hàm OCR cho mọi độ dài | 5.2 |
| O4 | Mọi mã thoát khác 0 đều bị coi là "đổi độ phân giải", và từ đó mọi đoạn còn lại đều bị chuẩn hoá | 2756-2763 | Phân loại lỗi (mã 2 hoặc `usage:` là lỗi cấu hình) trước khi chuẩn hoá | 5.2 |
| T2 | Hai block dính nhau vẫn qua kiểm tra từng đoạn, vì regex timecode dùng `(?m)` tìm thấy timecode lẫn trong phần text; lỗi chỉ lộ ra sau khi `.parts` đã bị xoá | 3209-3213, 3697 | Kiểm tra số block và tìm dòng timecode trong phần text | Task 11 |
| T4 | Quyết định thử lại dựa vào chuỗi; `http.client.IncompleteRead` lọt khỏi vòng thử lại; body lỗi dạng list gây `AttributeError`; bỏ qua `Retry-After` | 3108-3116, 3594-3616 | Exception có kiểu với cờ `retryable` | Task 10 |
| T5 | Gemini không chạy tiếp được, không thử lại, dùng chung một khung chat cho mọi đoạn và mọi job | 3745-3816 | Dùng chung cache `.parts`, mở chat mới mỗi job | 5.2 |
| T6 | Selector chỉ nhận nhãn tiếng Anh/Việt; nối các block `<pre>` bằng `"\n"`; block danh sách thuật ngữ bị nối vào phụ đề cuối | 3390-3435 | Chỉ nối block có timecode, nối bằng `"\n\n"` | Task 11 (phần nối), 5.2 |
| T7 | Không kiểm tra ngôn ngữ đầu ra khi nguồn là en/ko/vi hoặc tiếng Nhật nhiều kana; bản dịch giữ nguyên văn vẫn qua | 3221-3262 | Kiểm tra theo từng block | 5.2 |
| S1 | Cổng debug Edge 9222 cố định và không xác thực, trong khi profile đang đăng nhập Google; Edge vẫn chạy sau khi app thoát | 73, 1425-1441 | Điều khiển qua pipe hoặc cổng ngẫu nhiên đọc từ `DevToolsActivePort`; đóng Edge khi không dùng | 5.2 |
| S2 | API key vẫn được gửi khi đổi provider hoặc Base URL; `urllib` tự theo redirect và gửi kèm `Authorization` sang host khác [I] | 5236-5257, 3084-3099 | Lưu key theo origin; opener chặn redirect | Task 10, Task 13 |
| S3 | Helper cập nhật kế thừa biến môi trường `_PYI_*`, nên PyInstaller ≥ 6.9 có thể coi bản mới là tiến trình con dùng lại thư mục giải nén của bản cũ [I] | 1096, 1144-1160 | Đặt `PYINSTALLER_RESET_ENVIRONMENT=1` theo [tài liệu PyInstaller](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html) | Task 3 |
| S9 | Checksum nằm cùng release GitHub với ZIP (M-11 cũ): ai chiếm được quyền phát hành thì phát hành được cả file và checksum khớp | 1024-1046 | Authenticode hoặc manifest ký Ed25519 với khoá công khai nhúng trong app | 5.3 |
| R2 | Bản phát hành được build trên máy cá nhân bằng Python 3.14 trong khi CI dùng 3.12; dependency không khoá hash; action GitHub ghim theo tag | `.github/workflows/tests.yml` | Build bản phát hành từ CI, khoá hash, ghim action theo SHA | Task 3 (artifact), 5.2 |
| R3 | Test updater lấy tên asset từ chính code nên không phát hiện được tên sai | `tests/test_app.py` 1616-1667 | Test so với chuỗi cố định | Task 3 |
| R5 | CI không có lint, type-check, coverage hay kiểm tra cú pháp JS | `.github/workflows/tests.yml` | `node --check` (Task 14), `ruff`, ngưỡng coverage (5.2) | Task 14, 5.2 |
| R8 | CLI chạy từ source và bản EXE dùng hai file khoá khác nhau, nên chạy song song được và tranh nhau Edge/Douzy | 74-82 | Một đường dẫn khoá chung cho mọi cách chạy | 5.2 |
| F3 | `Promise.all` gộp request hàng đợi với request tiến trình: một bên lỗi làm mất kết quả bên kia | `static/app.js` 354 | `Promise.allSettled` | Task 14 |
| F4 | Không có trạng thái mất kết nối rõ ràng; dòng "Mất kết nối" không tự xoá khi kết nối lại | `static/app.js` 429-436 | Một hàm `setConnection(ok)` | Task 14 |
| F5 | Dựng lại toàn bộ danh sách hàng đợi mỗi lần revision đổi, nút đang được focus bị mất focus | `static/app.js` 323-347 | Cập nhật DOM theo key | 5.2 |
| F6 | Upload MP4 8 GiB không có tiến độ và không huỷ được | `static/app.js` 186-210 | XHR có `upload.onprogress`, hoặc hộp thoại native | 5.2 |
| F7 | Lỗi 409 khi cài cập nhật làm app chuyển hẳn sang chế độ mở GitHub | `static/app.js` 94-104 | Gắn `status` vào lỗi; 409 chỉ hiện thông báo | Task 14 |
| A3 | 8 đoạn ghi file an toàn tự viết lại; 3 bản sao vòng giám sát tiến trình con; 25 timeout viết thẳng bằng số | toàn file | Hàm dùng chung, hằng số có tên | 5.2 |
| A4 | Test phụ thuộc vào patch theo tên (129 lần trên 69 tên) | `tests/test_app.py` | Giữ `app.py` làm lớp xuất lại tên khi tách module; thêm test kiểm tra mục tiêu patch | 5.2 |
| A5 | Provider gắn cứng trong health, kiểm tra phụ thuộc và điều phối | 4441-4458, 4762-4775, 5021-5108 | Registry provider | 5.2 |
| P3 | Mỗi dòng log của VideOCR mở file log để ghi; log 3 × 2 MB xoay vòng nhanh và đẩy mất log của các bước trước | 1204-1228, 2500-2506 | Log VideOCR chỉ lên console; file log nhận 20 dòng cuối khi có lỗi | Task 7 |

### 3.4. Vấn đề mức Low

| ID | Vấn đề | Vị trí | Đợt |
|---|---|---|---|
| C7 | Hai file bị cách ly trong cùng một giây trùng tên và ghi đè nhau | 4153-4163 | Task 5 |
| C8 | Ctrl+C trong CLI để lại VideOCR/FFmpeg chạy (chỉ dừng tiến trình khi `WorkflowCancelled` hoặc timeout) | 2511-2515, 2596-2600 | Task 9 |
| C9 | `min(0.25, deadline - now)` có thể âm và ném `ValueError` thay cho thông báo hết hạn | khoảng 1616 | Task 8 |
| O5 | Thiếu ffprobe và không có độ phân giải từ preview thì app bỏ vùng crop mà chỉ ghi một dòng log chung | 2676-2685 | 5.2 |
| O6 | Các dòng output cuối của VideOCR/FFmpeg có thể bị mất vì không chờ luồng đọc | 2500-2506, 2579-2595 | 5.2 |
| O7 | Khử trùng ở ranh giới đoạn đòi text giống hệt và lệch không quá 250 ms | 2623-2635 | 5.2 |
| O8 | Raw SRT bị xoá trước khi lần OCR mới thành công | 2673-2674 | 5.2 |
| O9 | Dòng ini thiếu dấu `=` gây `IndexError` | 2691-2699 | Task 7 |
| O10 | ffprobe chạy không có `CREATE_NO_WINDOW` nên có thể bật cửa sổ console trong bản EXE [I] | 2348, 2366 | Task 6 |
| T9 | Một block SRT hỏng làm hỏng cả file gốc, thông báo lỗi không nói block nào | 3265-3277 | 5.2 |
| T10 | Dấu nhận dạng cache dịch không tính system prompt, khung prompt và ngôn ngữ nguồn | 3620-3632 | Task 11 |
| S4 | Tiến trình bất kỳ trên máy lấy được token qua `GET /` | 4903-4906 | 5.2 |
| S5 | Token chứa ký tự không phải ASCII làm `compare_digest` ném `TypeError` (lỗi 500 thay vì 403); body dạng list gửi tới `/api/open-folder` gây lỗi 500 | 4860, 5466-5477 | Task 13 |
| S6 | Helper cập nhật không kiểm tra lại hash của EXE đã stage; `Wait-Process` không có timeout | 1095-1182 | 5.2 |
| S7 | Chuỗi lỗi từ API được ghi vào log nguyên cả ký tự xuống dòng | 1219, 3117 | Task 13 |
| S8 | Không gọi `fsync` khi ghi file cấu hình và hàng đợi | 836-845, 4040-4053 | 5.2 |
| F8 | Bấm đúp nút Chạy hàng đợi/Chạy lại/Xoá gửi hai request | `static/app.js` 371-385 | Task 14 |
| F9 | Caption tiến trình luôn ghi "Gemini" bất kể provider | `static/app.js` 411-412 | Task 14 |

### 3.5. Trạng thái các vấn đề trong báo cáo v1.5.2

| Trạng thái | Mã |
|---|---|
| Đã sửa | H-01 (khớp asset Douzy), H-06 (updater chạy chồng), H-09 (preview cũ), M-03 (giới hạn 500 job), L-01 phần lớn (CSP, `innerHTML` chỉ còn chuỗi tĩnh) |
| Sửa gần hết | H-02 (lưu hàng đợi và worker; còn QA-1.12), H-05 (sidecar mồ côi khi huỷ prefetch) |
| Sửa một phần | H-04 (còn QA-1.5), H-07 (còn C5), H-08 (còn C2, C4), M-02 (còn C6), M-05 (chưa có rollback khi chuyển asset), M-07 (còn S1), M-09 (còn S3), M-10 (còn S4, S2), M-12 (chưa smoke test native), M-18 (còn R2), M-19, M-20, L-02 (còn QA-1.6) |
| Còn nguyên | H-03 (chính là QA-1.2; CHANGELOG 1.5.3 ghi là đã sửa nhưng code chưa sửa), M-11 (S9), L-03 (trùng lặp cấu hình đóng gói) |

## 4. Phần 3: Giải pháp cụ thể

Code mẫu dưới đây là hướng sửa cho các vấn đề High và các bug QA nặng nhất. Hướng sửa của vấn đề Medium và Low nằm trong các bảng ở Phần 2. Bản sửa thật trong v1.5.4 có thể khác chi tiết để khớp với test.

### 4.1. C1: request API chạy trong luồng phụ có thể huỷ và có hạn chót

```python
def run_cancellable(func, *, deadline_seconds, on_abort=None):
    box, done = {}, Event()

    def target():
        try:
            box["value"] = func()
        except BaseException as error:
            box["error"] = error
        finally:
            done.set()

    Thread(target=target, daemon=True).start()
    deadline = time.monotonic() + deadline_seconds
    while not done.wait(0.5):
        if cancel_requested() or time.monotonic() > deadline:
            if on_abort:
                on_abort()  # đóng response HTTP đang mở để luồng phụ thoát
            check_cancel_requested()
            raise TranslationApiError("API dịch vượt quá hạn chót.", retryable=True)
    if "error" in box:
        raise box["error"]
    return box["value"]
```

### 4.2. C2: worker dừng khi app đang tắt

Kiểm tra ở đầu vòng lặp và kiểm tra lại ngay trước khi đánh dấu job là `running`. `request_app_shutdown` chờ worker tối đa 15 giây.

```python
if shutdown_event.is_set():
    with queue_state_lock:
        current_job_id, queue_worker_thread = "", None
    return
```

### 4.3. O1: hạn chót VideOCR tính theo độ dài đoạn video

```python
def videocr_timeout_seconds(span_seconds):
    if not span_seconds:
        return VIDEOCR_UNKNOWN_SPAN_TIMEOUT_SECONDS          # 4 giờ
    return max(VIDEOCR_PROCESS_TIMEOUT_SECONDS, math.ceil(span_seconds * 3) + 600)
```

### 4.4. T1 và T2: kiểm tra số block, lấy số thứ tự và timecode từ bản gốc

```python
src, dst = parse_srt_blocks(source_srt), parse_srt_blocks(translated_srt)
if any(SRT_TIMECODE_LINE_PATTERN.match(line) for _, _, text in dst for line in text.splitlines()):
    raise ValueError("Bản dịch có block bị dính nhau.")
if len(src) != len(dst):
    raise ValueError(f"Bản dịch có {len(dst)}/{len(src)} block.")
if [b[0] for b in src] != [b[0] for b in dst] and [b[1] for b in src] != [b[1] for b in dst]:
    raise ValueError("Bản dịch lệch cả số thứ tự lẫn timestamp.")
rebuilt = "\n\n".join(f"{index}\n{timecode}\n{d[2]}" for (index, timecode, _), d in zip(src, dst))
return validate_translated_srt(source_srt, rebuilt)
```

### 4.5. QA-1.2: chỉ ghi hàng đợi khi tiến trình đã nạp hàng đợi thành công

```python
def persist_queue_state_locked(jobs=None):
    if app.config.get("TESTING") or not queue_persistence_enabled:
        return  # CLI và lần nạp lỗi không bao giờ ghi đè file của bản desktop
    write_queue_file(QUEUE_PATH, workflow_jobs if jobs is None else jobs)
```

### 4.6. QA-1.3: Douzy chỉ hết hạn khi trạng thái đứng yên

```python
signature = douzy_job_signature(job)            # payload trừ các trường thời gian
if signature != last_signature:
    last_signature, last_change_at = signature, now
if now - last_change_at > DOUZY_STALL_TIMEOUT_SECONDS:      # 5 phút không đổi
    raise TimeoutError("Douzy không báo tiến độ mới trong 5 phút.")
if now - started_at > DOUZY_MAX_DOWNLOAD_SECONDS:           # trần 6 giờ
    raise TimeoutError("Tải video vượt quá 6 giờ.")
```

### 4.7. T3: prompt mặc định trung tính, glossary thành preset

- Đổi tên hằng hiện tại thành `TRANSLATION_PRESET_DELTA_FORCE`, giữ nguyên nội dung.
- Prompt mặc định mới chỉ nêu quy tắc chung: dịch tự nhiên, xưng hô nhất quán, giữ tên riêng, không giải thích, không liệt kê thuật ngữ.
- Ngôn ngữ nguồn lấy từ `ocr_lang` của job (sửa QA-1.18). Với `ch`, phần ràng buộc chống trả lại tiếng Trung giữ nguyên.
- Người dùng cũ đã lưu prompt tuỳ chỉnh vẫn giữ nguyên prompt đó.

### 4.8. F1 và F2: fetch có timeout, nút Huỷ gắn với đúng job

```js
const signal = timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined;
const response = await fetch(url, {...nextOptions, signal});

// trong renderQueue
const runningJob = queueJobs.find(job => job.status === 'running');
if (!runningJob || runningJob.id !== cancellingJobId) {
    cancellingJobId = '';
    cancelButton.disabled = false;
    cancelButton.textContent = 'Huỷ job';
}
```

### 4.9. Gợi ý tính năng giúp dịch tốt hơn và dùng dễ hơn

| Đề xuất | Lợi ích | Đợt |
|---|---|---|
| Màn hình duyệt bản gốc và bản dịch song song, dịch lại từng dòng | Sửa lỗi mà không chạy lại cả video | 5.2 |
| Glossary riêng cho từng dự án, merge với prompt chung | Tên riêng nhất quán theo từng series | 5.2 |
| Làm sạch kết quả OCR trước khi dịch: gộp dòng gần trùng (độ giống ≥ 0,85, cách ≤ 300 ms), bỏ dòng hiện dưới 250 ms, bỏ watermark kiểu 抖音号 | Bớt rác trước khi gửi model | 5.2 |
| Gửi kèm 5-10 block cuối của đoạn trước làm ngữ cảnh chỉ đọc | Xưng hô nhất quán giữa các đoạn | 5.2 |
| Model trả về dạng `số thứ tự → nội dung`, timecode do app tự ghép | Bỏ hẳn lỗi T1/T2, tiết kiệm khoảng 30 ký tự output mỗi block | 5.2 |
| Cảnh báo khi tốc độ đọc (CPS) quá 17-20 ký tự/giây hoặc dòng quá 42 ký tự | Phụ đề dễ đọc hơn | 5.2 |
| ETA và thời gian đã chạy cho từng job | Người dùng biết job còn sống | 5.2 |
| Nút xuất gói chẩn đoán (log và cấu hình đã bỏ thông tin bí mật) | Báo lỗi trên GitHub nhanh hơn | 5.2 |

## 5. Phần 4: Lộ trình thực thi ưu tiên

### 5.1. Làm ngay (Quick Wins): phát hành v1.5.4

Sửa 17/18 bug QA (QA-1.15 chuyển sang 5.2) và các vấn đề C1, C2, C4, C6, C7, C8, C9, O1, O9, O10, P3, T1, T2, T3, T4, T10, S2, S3, S5, S7, F1, F2, F3, F4, F7, F8, F9. Làm theo TDD, mỗi task có test riêng.

| Task | Nội dung | Bug/vấn đề |
|---|---|---|
| 1 | Lưu báo cáo này | — |
| 2 | Cách ly test khỏi dữ liệu thật trên máy | QA-1.16 |
| 3 | Đóng gói release đúng định dạng updater, test hợp đồng tên file, CI xuất 2 file | QA-1.1, S3, R3 |
| 4 | CLI không ghi đè hàng đợi và in được tiếng Việt | QA-1.2, QA-1.10 |
| 5 | Nạp/ghi trạng thái an toàn, lỗi trả JSON, mã lỗi cho job hoàn tất bị di chuyển | QA-1.9, QA-1.11, QA-1.12, C6, C7 |
| 6 | Gọi chương trình ngoài an toàn, cache xác minh Edge | QA-1.6, QA-1.14, O10 |
| 7 | VideOCR: đọc ini mọi code page, hạn chót theo độ dài, log gọn | QA-1.4, O1, O9, P3 |
| 8 | Douzy hết hạn theo tiến độ | QA-1.3, C9 |
| 9 | Vòng đời tiến trình, đóng app, chặn preview giữa hai job | QA-1.5, C2, C4, C8 |
| 10 | API dịch huỷ được, phân loại lỗi đúng, chặn redirect | QA-1.8, QA-1.13, C1, T4, S2 (redirect) |
| 11 | Toàn vẹn bản dịch, chia đoạn theo kích thước, prompt theo ngôn ngữ nguồn | QA-1.7, QA-1.18, T1, T2, T10 |
| 12 | Prompt trung tính làm mặc định, Delta Force thành preset | T3 |
| 13 | Vá bảo mật nhỏ: key theo origin, so token dạng bytes, kiểm tra kiểu body, escape log | S2, S5, S7 |
| 14 | Frontend không đứng hình | F1, F2, F3, F4, F7, F8, F9, QA-1.9 (giao diện) |
| 15 | README/HUONG_DAN, CHANGELOG 1.5.4, `APP_VERSION = "1.5.4"`, `RELEASING.md` | QA-1.17 |

**Trạng thái thực hiện (17/11/2026):** đã làm xong Task 1–15 và đóng thành bản v1.5.4.
- Kết quả kiểm thử: 185 unit test Python đạt (1 test chỉ chạy khi có `RELEASE_DIR`), 6 test frontend Node đạt. Số test ban đầu là 113.
- Bản build PyInstaller trên máy kiểm toán (Python 3.14.3) đã qua smoke test `--server`.
- Code kiểm tra release của 8 bản đã phát hành (v1.3.0 đến v1.5.3) chấp nhận hai file v1.5.4 do `tools/make_release.py` tạo.
- Chưa kiểm chứng: cập nhật thật qua GitHub, Douzy, VideOCR, Edge/Gemini, API dịch thật và giao diện WebView2.
- QA-1.5 mới sửa một phần: chặn preview giữa hai job và giữ lệnh huỷ. Phần huỷ theo từng job (C3) nằm ở mục 5.2, cùng với QA-1.15 và O2.

### 5.2. Trung hạn (Core Refactor): v1.6.x

1. **Tách module an toàn.** Tạo package `vietsub/`, giữ `app.py` làm lớp xuất lại tên cũ. Thứ tự tách:
   1. Các phần không có I/O và test không patch: `srt`, `validation`, `prompt`, `release`, các builder lệnh VideOCR.
   2. Hạ tầng: ghi file an toàn, giám sát tiến trình con, log, `JobContext`.
   3. Provider dịch: `openai_compat`, rồi `gemini_notebook`.
   4. OCR và media, rồi nguồn video, rồi Edge (12 patch, đổi trong cùng một commit).
   5. Hàng đợi (model, repository, worker, coordinator), web (app factory, blueprint), launcher.

   Bên trong package, import module thay vì import tên (`config.load_app_config()`), để một patch trên module định nghĩa áp được cho mọi nơi gọi. Thêm test kiểm tra mỗi tên bị patch vẫn được tra cứu trong đúng module.
2. **`JobContext` và huỷ theo job.** Mỗi job có `Event` huỷ, registry tiến trình và log riêng; sửa dứt điểm QA-1.5 và C3. Khoá riêng cho Douzy và Edge, nhờ đó tạo được preview trong lúc hàng đợi đang chạy.
3. **OCR:** lưu từng đoạn để chạy tiếp (O2), sửa crash cho cả video ngắn (O3), phân loại lỗi (O4), ghi ra file tạm trước (O8), Windows Job Object cho cây tiến trình.
4. **Dịch:** một bộ điều phối chung cho API và Gemini; Gemini chạy tiếp và thử lại được (T5); định dạng `số thứ tự → nội dung`; làm sạch OCR; ngữ cảnh giữa các đoạn; kiểm tra từng block (T7, T9).
5. **Mã lỗi có kiểu** thay cho so chuỗi (A2); một định dạng JSON lỗi chung cho mọi route.
6. **Chọn MP4 bằng hộp thoại native** của pywebview, không upload qua HTTP (QA-1.15, F6).
7. **Phát hành từ CI:** workflow theo tag tạo draft release, khoá hash dependency, ghim action theo SHA, thông tin phiên bản trong EXE, tắt UPX (R2, L-03).
8. **Edge và API key:** Edge chạy qua pipe hoặc cổng ngẫu nhiên, tự đóng khi không dùng (S1); token giao qua bridge pywebview thay vì nhúng trong HTML (S4).
9. **Frontend:** ES module, cập nhật DOM theo key (F5), accessibility, smoke test UI bằng Playwright.
10. **Khoá instance chung** cho mọi cách chạy (R8); mở lại URL khi instance khác đang chạy (C5).
11. **Test:** tách `test_app.py` theo phân hệ; thêm `ruff` và ngưỡng coverage (R5).

### 5.3. Dài hạn (Scale-up): v2.x

1. **Plugin cho nguồn, OCR và dịch:**
   - `VideoSource`: `can_handle(url)`, `resolve`, `download`, `missing_dependencies`, `on_asset_moved`. Cài đặt: Douyin/Douzy, file local, sau đó TikTok/YouTube/Bilibili qua yt-dlp.
   - `OcrEngine`: `missing_dependencies`, `extract(video, options, ctx)`. Cài đặt: VideOCR, PaddleOCR/RapidOCR gọi trực tiếp.
   - `TranslationProvider`: metadata preset, `validate_settings`, `test_connection`, `open_session`, `translate_chunk`. Cài đặt: API kiểu OpenAI, Gemini API, Notebook, LLM chạy trên máy.
   - Registry điều khiển health, kiểm tra phụ thuộc và giao diện Thiết lập.
2. **Lưu job bằng SQLite** (chế độ WAL), có migration theo version.
3. **Biên tập phụ đề:** trình biên tập, xuất SRT/ASS/VTT, tuỳ chọn ghép phụ đề cứng bằng FFmpeg.
4. **Chạy song song theo tài nguyên:** OCR job sau trong lúc job trước đang dịch.
5. **Ký số bản cập nhật:** Authenticode và manifest có chữ ký (S9); kênh beta/stable.
6. **Chuẩn bị đa nền tảng:** tách các phần chỉ có trên Windows (DPAPI, PowerShell, `msvcrt`).

## 6. Phụ lục: hợp đồng định dạng release mà updater cần

Hợp đồng này giống nhau từ v1.3.0 đến v1.5.3 và phải giữ nguyên để các bản cũ tự cập nhật được.

1. Updater đọc `GET /repos/qvinh8726/VietSub-Studio/releases/latest`, nên chỉ thấy release đã publish, không thấy draft hay prerelease.
2. Tag có dạng `vX.Y.Z` và phải mới hơn `APP_VERSION` của bản đang chạy.
3. Tên asset phân biệt hoa thường:
   - `VietSub-Studio-Portable-vX.Y.Z.zip`
   - `VietSub-Studio-Portable-vX.Y.Z.zip.sha256.txt`
4. File checksum ≤ 1 MiB, UTF-8 (có hoặc không BOM), có một dòng dạng `<sha256 64 ký tự hex><khoảng trắng>[*]<tên ZIP>`, tên ZIP không kèm đường dẫn.
5. ZIP ≤ 512 MiB, SHA-256 khớp file checksum; nếu API GitHub có `digest` và `size` thì cũng phải khớp.
6. ZIP phải có đúng file `VietSub Studio/VietSub Studio.exe` (bản one-file), bắt đầu bằng `MZ`. Các file khác được bỏ qua; nên kèm `VietSub Studio/SHA256.txt`, `VietSub Studio/LICENSE`, `VietSub Studio/HUONG_DAN_SU_DUNG.txt`.
7. Không bao giờ phát hành ZIP phẳng hay file `.zip.sha256` thay cho `.zip.sha256.txt`.

Từ v1.5.4, `tools/make_release.py` tạo đúng hai file trên và `tests/test_release_contract.py` chặn mọi lệch định dạng (xem `RELEASING.md`).
