# Báo cáo phân tích kỹ thuật toàn diện — VietSub Studio v1.5.2

## 1. Phạm vi, phương pháp và độ tin cậy

### 1.1. Phạm vi

Báo cáo bao phủ toàn bộ **27 tệp được Git theo dõi** trên nhánh `codex/fix-native-webview-bridge`, gồm:

- mã backend và launcher;
- frontend HTML/CSS/JavaScript;
- CLI;
- cấu hình và dependency;
- PyInstaller specs và batch scripts;
- CI;
- kiểm thử;
- tài liệu, changelog, security policy và issue templates;
- metadata nhị phân của hai tài sản thương hiệu.

Các vùng mã chính:

- `app.py:1-3517`
- `templates/index.html:1-975`
- `tests/test_app.py:1-1443`
- `README.md:1-1113`

### 1.2. Các lớp bằng chứng

| Nhãn | Ý nghĩa |
|---|---|
| **[STATIC]** | Quan sát trực tiếp từ tệp được Git theo dõi. |
| **[UNIT-RUN]** | Suite unit test đã được nguồn xác minh chạy và đạt 65/65. Không đồng nghĩa với kiểm thử ứng dụng thật. |
| **[FAULT-INJECTION]** | Nguồn xác minh báo cáo đã tái hiện bằng mock/fault injection một đường lỗi cụ thể. |
| **[LOCAL ARTIFACT]** | Quan sát từ artifact phát hành cục bộ nằm trong thư mục bị Git ignore; không có trong clean clone. |
| **[INFERENCE]** | Suy luận kỹ thuật dựa trên mã; không khẳng định đã tái hiện ở runtime. |

### 1.3. Phương pháp

- Tệp, byte và dòng được kiểm kê từ cây Git tracked.
- Cấu trúc Python được đếm bằng AST.
- Route được đối chiếu từ decorator và Flask URL map.
- Frontend được kiểm kê từ template duy nhất.
- Các finding trùng nhau đã được hợp nhất thành **một risk register chuẩn duy nhất**.
- Severity được tách khỏi priority:
  - **Severity**: mức nghiêm trọng tổng hợp từ tác động và khả năng xảy ra.
  - **Priority**: thứ tự xử lý có xét thêm công sức và phụ thuộc.
- Các số liệu hiệu năng như số lần `stat`, số lần ghi queue và request/giây là **ước tính từ đường mã**, không phải benchmark.

### 1.4. Những gì không được coi là đã kiểm thử động

Báo cáo này **không khẳng định** đã kiểm thử thực tế:

- cửa sổ pywebview/WebView2;
- EXE PyInstaller;
- Douzy thật;
- VideOCR thật;
- Edge CDP thật;
- Gemini Notebook thật;
- update replacement thật;
- hành vi đóng cửa sổ khi đang OCR;
- khả năng khai thác local API từ trình duyệt hiện đại;
- hiệu năng với video dài hoặc queue lớn.

Nguồn xác minh đã chạy suite unit test 65/65, nhưng đây là kiểm thử logic cô lập, phần lớn boundary bên ngoài vẫn được mock. CI hiện cũng chỉ compile và chạy unit test, không chạy các luồng trên: `.github/workflows/tests.yml:11-25`.

### 1.5. Độ tin cậy

| Loại kết luận | Độ tin cậy |
|---|---|
| Thống kê repository, route, cấu trúc mã | Cao |
| Luồng logic đọc trực tiếp từ mã | Cao |
| Kết quả unit test | Cao, trong phạm vi unit test |
| Tác động của lỗi persistence và Douzy matching đã fault-inject | Cao |
| Hành vi WebView2, process con khi app thoát | Trung bình, cần runtime test |
| Khả năng khai thác local API từ browser | Trung bình-thấp, phụ thuộc browser/PNA/SOP |
| Likelihood thay đổi upstream Douzy/Gemini | Suy luận, không thể định lượng từ repository |

---

## 2. Tóm tắt điều hành

VietSub Studio là một ứng dụng desktop Windows điều phối quy trình:

1. nhận một video Douyin hoặc MP4 cục bộ;
2. chuẩn bị preview và vùng crop;
3. chạy VideOCR;
4. điều khiển Gemini Notebook qua Edge/Playwright để dịch;
5. xác thực cấu trúc SRT;
6. tạo project tự chứa và duy trì queue có khả năng retry.

Kiến trúc là một **modular monolith cục bộ**: mỗi instance có một tiến trình Python, một Flask control plane trên loopback, một frontend vanilla, một queue worker tuần tự và một prefetch download tùy chọn. Phần lớn trách nhiệm nằm trong `app.py:1-3517`.

### 2.1. Kết luận chính

**Điểm mạnh nổi bật**

- Retry theo giai đoạn, tránh lặp lại download/OCR không cần thiết.
- Ghi config, queue, manifest và SRT qua tệp tạm rồi `os.replace`.
- Success được chặn bởi kiểm tra tệp, timestamp, block count và index.
- Tối ưu video lớn bằng xử lý Douyin tại chỗ và hard link local MP4.
- Native bridge rất hẹp.
- URL/path validation và open-folder allowlist khá tốt.
- Suite backend nhanh, có kiểm thử filesystem, SQLite, ZIP, queue và cancellation thực.
- Tài liệu và issue intake chú trọng quyền riêng tư.

**Khoảng trống lớn nhất**

- Queue persistence không có transaction/rollback và có thể làm worker mắc kẹt.
- Preview, workflow, prefetch và updater chưa có coordinator chung.
- Douzy asset matching có thể move nhầm hoặc overwrite tệp.
- CLI có thể ghi đè queue desktop.
- Native startup không có readiness watchdog/fallback tự động.
- Đóng native window không phối hợp hủy worker/process.
- Updater có race với công việc mới bắt đầu và không có trust anchor độc lập.
- CI không build hoặc smoke-test artifact thực.
- Frontend có lỗi stale preview có thể queue nhầm video.

### 2.2. Risk register chuẩn

Báo cáo sử dụng đúng một register gồm:

| Severity | Số finding |
|---|---:|
| High | **9** |
| Medium | **20** |
| Low | **4** |
| **Tổng** | **33** |

Các số trên chỉ tính finding còn tồn tại sau xác minh; không tính câu hỏi mở, cơ hội tối ưu hoặc đặc điểm mang tính thông tin.

### 2.3. Chín finding High

| ID | Finding |
|---|---|
| H-01 | Douzy asset matching có thể move/overwrite nhầm tệp. |
| H-02 | Lỗi persistence và queue mutation không giao dịch có thể làm worker chết hoặc state mắc kẹt. |
| H-03 | CLI có thể ghi đè queue desktop đã lưu. |
| H-04 | Preview và workflow có thể chạy chồng, chia sẻ cancellation/process state. |
| H-05 | Hủy detached prefetch có thể để sót Douzy sidecar. |
| H-06 | Updater có race/re-entry và có thể force-exit công việc mới bắt đầu. |
| H-07 | Native startup không có app-owned readiness deadline hay fallback tự động. |
| H-08 | Đóng native window không phối hợp shutdown worker và process con. |
| H-09 | Frontend có thể queue preview cũ trong khi hiển thị nguồn mới. |

---

## 3. Thống kê repository

### 3.1. Tổng quan

| Chỉ số | Giá trị |
|---|---:|
| Tệp tracked | 27 |
| Tổng dung lượng | 604.210 byte |
| Tệp văn bản | 25 |
| Tệp nhị phân | 2 |
| Dòng văn bản | 7.710 |
| Byte văn bản | 360.743 |
| Byte nhị phân | 243.467 |
| Tỷ lệ byte của hai ảnh | 40,3% |

Hai file nhị phân là:

- `avatar.ico`: 165.226 byte, nhiều kích thước từ 16×16 tới 256×256.
- `avatar.jpg`: 78.241 byte, 736×736 RGB.

### 3.2. Phân bố theo loại tệp

| Loại | Tệp | Dòng | Tỷ lệ dòng |
|---|---:|---:|---:|
| `.py` | 4 | 5.001 | 64,86% |
| `.md` | 4 | 1.307 | 16,95% |
| `.html` | 1 | 975 | 12,65% |
| `.yml` | 4 | 114 | 1,48% |
| `.spec` | 2 | 97 | 1,26% |
| Không phần mở rộng | 3 | 79 | 1,02% |
| `.bat` | 3 | 72 | 0,93% |
| `.txt` | 3 | 55 | 0,71% |
| `.json` | 1 | 10 | 0,13% |
| `.ico`, `.jpg` | 2 | 0 | — |

### 3.3. Tệp lớn nhất

| Tệp | Dòng | Byte | Tỷ lệ tổng dòng |
|---|---:|---:|---:|
| `app.py` | 3.517 | 135.835 | 45,62% |
| `tests/test_app.py` | 1.443 | 62.983 | 18,72% |
| `README.md` | 1.113 | 68.162 | 14,44% |
| `templates/index.html` | 975 | 70.600 | 12,65% |

Bốn tệp này chứa **7.048/7.710 dòng**, tức **91,41%** toàn bộ văn bản.

### 3.4. Cấu trúc mã

| Chỉ số | Giá trị |
|---|---:|
| Hàm module-level trong `app.py` | 150 |
| Tổng hàm/phương thức AST trong `app.py` | 153 |
| Lớp trong `app.py` | 3 |
| Definition có type annotation | 0 |
| `try` block | 105 |
| Exception handler | 101 |
| `except Exception` | 19 |
| `finally` block | 22 |
| Global lock | 8 |
| Global event | 2 |
| Điểm tạo `Thread` | 7 |
| Điểm gọi `subprocess.Popen` | 6 |

Các hotspot lớn:

- `prepare_update_executable`: `app.py:672-754`
- `run_videocr`: `app.py:1723-1850`
- `translate_srt_via_gemini_edge`: `app.py:2046-2219`
- `run_workflow_thread`: `app.py:2856-3024`

### 3.5. HTTP và test

| Chỉ số | Giá trị |
|---|---:|
| Route do ứng dụng khai báo | 23 |
| Method/path operation do ứng dụng khai báo | 25 |
| API route pattern | 21 |
| API operation | 23 |
| API GET / POST / DELETE | 8 / 13 / 2 |
| Test class | 6 |
| Test method | 65 |
| API pattern được gọi trực tiếp trong test | 10/21 |
| API operation được gọi trực tiếp | 11/23 |

Flask còn tự tạo route `/static/<path:filename>`, mặc dù cây tracked không có thư mục `static` riêng. Khi tính route runtime, có 24 rule và 26 operation nếu loại `HEAD`/`OPTIONS` tự động.

### 3.6. Phương pháp và giới hạn của các metric frontend

Nguồn xác minh đếm từ `templates/index.html:460-973`:

- 45 hàm JavaScript có tên;
- 12 biến `let` mutable cấp trang;
- 23 điểm gọi `fetchJson`;
- 19 endpoint pattern;
- 17 `addEventListener`;
- 19 gán `onclick`;
- 6 gán `innerHTML`;
- 58 gán `textContent`.

Đây là số đếm cấu trúc mã, không phải độ phủ hay độ phức tạp runtime.

---

## 4. Sản phẩm, tính năng và tiến hóa phát hành

### 4.1. Mục đích sản phẩm

VietSub Studio không thay thế downloader, OCR engine hoặc LLM. Giá trị cốt lõi là điều phối các công cụ Windows bên ngoài thành một quy trình có khả năng phục hồi:

- resolve/tải Douyin;
- preview và crop;
- OCR;
- dịch sang tiếng Việt;
- kiểm tra SRT;
- tổ chức project;
- queue tuần tự;
- retry theo giai đoạn.

Phạm vi sản phẩm được mô tả tại `README.md:60-90`; workflow chính nằm tại `app.py:2856-3024`.

### 4.2. Input và output

**Input được hỗ trợ**

- Một Douyin URL/share text.
- Một MP4 cục bộ.

**Không hỗ trợ**

- playlist/profile/mix expansion;
- TikTok;
- YouTube;
- Bilibili;
- các nền tảng remote khác;
- Gemini API;
- hardsub/render video mới.

Bằng chứng: `README.md:94-105`, `README.md:520-530`.

**Output**

- video gốc;
- raw SRT;
- Vietnamese SRT;
- thumbnail tùy chọn;
- tài sản Douzy liên quan;
- `project.json`.

Bằng chứng: `README.md:168-190`, `app.py:1585-1666`.

Ứng dụng không có lời gọi ffmpeg để encode hay burn subtitle; `ffprobe` chỉ lấy kích thước video: `app.py:1708-1721`.

### 4.3. Persona suy luận

**[INFERENCE]** Persona chính là người làm phụ đề tiếng Việt trên Windows, xử lý video Douyin hoặc MP4 dài, ưu tiên độ phục hồi hơn throughput cực đại.

Persona phụ:

- người dùng portable EXE;
- người chạy source;
- người dùng CLI một video Douyin;
- contributor bảo trì selector Gemini, integration và packaging.

### 4.4. Tiến hóa release

| Phiên bản | Tiến hóa chính | Bằng chứng |
|---|---|---|
| v1.0.x | Pipeline Douyin → VideOCR → Gemini, Windows desktop | `CHANGELOG.md:101-126` |
| v1.1.x | Preview crop, queue, update check | `CHANGELOG.md:87-99` |
| v1.2.x | Local MP4, persistence, retry, background Edge | `CHANGELOG.md:73-86` |
| v1.3.x | Self-update có kiểm tra, integrity gate | `CHANGELOG.md:52-72` |
| v1.4.x | Project tự chứa, move tài sản Douzy | `CHANGELOG.md:42-51` |
| v1.5.0 | OCR mode/frame skip, hard link, prefetch | `CHANGELOG.md:20-40` |
| v1.5.1 | Browser trở thành launcher mặc định để tránh một lớp WebView hang | `CHANGELOG.md:14-18` |
| v1.5.2 | Khôi phục native WebView mặc định; giữ `--browser` làm fallback chẩn đoán | `CHANGELOG.md:7-12` |

Phiên bản nguồn hiện tại là `1.5.2`: `app.py:65-70`.

### 4.5. Điều hòa mâu thuẫn tài liệu

Hành vi dự kiến hiện tại đã rõ từ changelog, code và test: với nguồn Douzy đủ điều kiện, MP4 và tài sản được move vào project. Các đoạn nói MP4 vẫn nằm tại Douzy hoặc chỉ được copy là nội dung cũ:

- đúng theo code/changelog: `README.md:31-33`, `CHANGELOG.md:42-51`, `app.py:1528-1582`;
- stale: `README.md:361-364`, `README.md:486-488`, `README.md:895`, `README.md:1018-1020`.

---

## 5. Kiến trúc, topology và bản đồ trách nhiệm

### 5.1. Kiểu kiến trúc

**[INFERENCE]** Đây là một **modular monolith cục bộ**:

- một deployable Windows;
- mỗi instance là một tiến trình Python;
- Flask là boundary giữa UI và backend;
- pywebview chỉ làm native shell;
- state và worker nằm trong process;
- công việc nặng được giao cho subprocess hoặc browser ngoài.

Không nên diễn đạt là “chỉ có một tiến trình trên máy”: repository không ngăn nhiều instance cùng chạy.

### 5.2. Bản đồ trách nhiệm

| Thành phần | Trách nhiệm |
|---|---|
| `app.py:24-117` | Path, Flask app, global state, lock/event |
| `app.py:128-838` | Version, update, config, Windows folders, shortcut |
| `app.py:840-1387` | Douzy, Edge, thumbnail, download lookup |
| `app.py:1390-2001` | File/project handling, VideOCR, SRT validation |
| `app.py:2004-2268` | Gemini/Playwright, URL resolution, download |
| `app.py:2270-3024` | Preview, queue persistence, worker, retry, workflow |
| `app.py:3026-3424` | Flask routes |
| `app.py:3426-3517` | Server/browser/native launchers |
| `templates/index.html:1-975` | Toàn bộ UI, CSS, state, API client và polling |
| `automate.py:9-41` | CLI một Douyin job |
| `tests/test_app.py:1-1443` | Unit/API tests |
| `*.spec`, `build_*.bat` | Packaging |
| `.github/workflows/tests.yml:1-25` | CI source-level |

### 5.3. Process topology

Trong một instance:

- Python main process;
- Werkzeug server thread;
- queue worker thread;
- prefetch thread tùy chọn;
- output-reader threads;
- pywebview/WebView2 process tree do dependency quản lý.

Process ngoài:

- Douzy sidecar: `app.py:1048-1161`
- VideOCR: `app.py:1723-1850`
- Edge/CDP: `app.py:881-1037`
- PowerShell shortcut/update: `app.py:574-637`, `app.py:757-830`
- Explorer: `app.py:3400-3424`
- `ffprobe`: `app.py:1708-1721`

### 5.4. Boundary chính

UI gọi backend chủ yếu qua HTTP tương đối: `templates/index.html:485-493`.

Native bridge chỉ cung cấp folder picker: `app.py:3452-3476`, `templates/index.html:907-918`.

Đây là lựa chọn tốt vì:

- browser/server mode dùng lại gần như toàn bộ UI;
- native bridge không phơi bày toàn bộ object graph backend;
- API có thể được kiểm thử độc lập.

---

## 6. Vòng đời khởi động và runtime

### 6.1. Dispatch entry point

`app.py:3511-3517` chọn:

- `--server` → fixed Flask server;
- `--browser` → browser diagnostic;
- còn lại → native desktop.

Repository còn có entry point thứ tư là `automate.py:9-41`.

### 6.2. Native desktop mặc định

Luồng tại `app.py:3479-3508`:

1. thử tạo desktop shortcut lần đầu;
2. nạp queue persisted;
3. import `webview`;
4. tạo Werkzeug server tại `127.0.0.1`, port do OS chọn;
5. chạy server trong daemon thread;
6. tạo `DesktopApi`;
7. tạo cửa sổ 1320×880, min 960×680;
8. truyền local URL;
9. ép backend `edgechromium`;
10. khi `webview.start()` trả về hoặc ném lỗi đồng bộ, gọi `server.shutdown()`.

Hai bước chạy trước khi cửa sổ xuất hiện:

- shortcut có PowerShell timeout tới 15 giây: `app.py:602-637`;
- queue load rồi rewrite normalized state: `app.py:2499-2542`.

Trong packaged mode, stdout/stderr bị chuyển vào `os.devnull`: `app.py:24-31`.

### 6.3. Khoảng trống native startup — H-07

**Finding H-07 — High**

Không có app-owned:

- WebView2 preflight;
- UI-ready signal;
- startup deadline;
- watchdog;
- tự động chuyển sang browser nếu native không ready.

Bằng chứng: `app.py:3479-3517`.

Changelog cho thấy lớp lỗi startup này đã tạo ra hai patch release liên tiếp: `CHANGELOG.md:7-18`. Điều đó chứng minh bề mặt này quan trọng, nhưng không chứng minh v1.5.2 vẫn treo trên một máy cụ thể.

### 6.4. Native shutdown — H-08

**Finding H-08 — High**

Khi `webview.start()` kết thúc, launcher chỉ shutdown HTTP server. Nó không:

- chặn job mới;
- yêu cầu cancel active workflow;
- cancel prefetch;
- terminate registered processes;
- join worker;
- ghi terminal/interrupted state có phối hợp.

Bằng chứng:

- daemon worker: `app.py:2683-2717`;
- cancellation mechanism có sẵn: `app.py:452-484`;
- launcher shutdown: `app.py:3479-3508`.

Restart có thể chuyển persisted `running` thành `error`, nhưng đó là recovery sau sự cố, không phải graceful shutdown: `app.py:2450-2457`.

### 6.5. Browser diagnostic mode

`app.py:3432-3449`:

1. nạp queue;
2. tạo server cổng ngẫu nhiên;
3. chạy `serve_forever` trong daemon thread;
4. mở default browser;
5. gọi `serve_forever` lần nữa trên main thread;
6. shutdown chỉ sau `KeyboardInterrupt` hoặc exception.

Hai lời gọi `serve_forever` là dư thừa, nhưng repository không chứng minh chúng tự gây deadlock. Vấn đề chắc chắn hơn:

- đóng browser không báo cho Python process;
- browser mode không có native window hoặc tray;
- trong EXE windowed thường không có console để gửi `Ctrl+C`.

### 6.6. Fixed server mode

`--server`:

- nạp queue;
- bind `127.0.0.1:5000`;
- không tự mở browser.

Bằng chứng: `app.py:3426-3429`.

Đây là mode dễ nhắm nhất về local HTTP vì port cố định, nhưng khi chạy từ source, các action chỉ dành cho packaged app như automatic replacement và shortcut sẽ từ chối.

### 6.7. CLI

`automate.py:9-41`:

- nhận một Douyin URL;
- OCR language;
- project name;
- enqueue;
- start worker;
- join thread;
- in kết quả hoặc lỗi.

CLI chia sẻ workflow backend nhưng không có:

- local MP4;
- preview;
- crop interactive;
- multi-job management;
- settings UI.

---

## 7. Các workflow nghiệp vụ chính

## 7.1. Boot giao diện

Khi trang load, frontend:

1. gắn event handlers;
2. tải settings;
3. tải health;
4. tải Douzy history;
5. tải queue;
6. kiểm tra Edge;
7. kiểm tra update;
8. bắt đầu queue/progress polling;
9. kiểm tra Edge mỗi 5 giây.

Bằng chứng: `templates/index.html:939-972`.

Polling:

- 1 giây khi có queued/running job;
- 4 giây khi idle;
- 5 giây cho Edge.

Bằng chứng: `templates/index.html:756-772`, `templates/index.html:939-942`.

## 7.2. Chuẩn bị video Douyin

1. UI gửi `POST /api/previews`.
2. Backend kiểm tra conflict và Notebook URL.
3. Xác thực Douyin host/path.
4. Với `v.douyin.com`, dùng Edge để resolve short URL.
5. Khởi động Douzy sidecar với bearer token UUID và port ngẫu nhiên.
6. Gửi download request.
7. Poll job status.
8. Truy vấn Douzy SQLite.
9. Tìm MP4/title/metadata.
10. Đăng ký preview trong RAM.
11. Trả URL stream `/api/previews/<id>/video`.

Bằng chứng:

- URL/ID: `app.py:415-437`, `app.py:2221-2233`
- Edge resolution: `app.py:1852-1886`
- sidecar: `app.py:1048-1161`
- DB/file lookup: `app.py:1163-1214`, `app.py:1358-1374`
- preview: `app.py:2290-2321`

Preview Douyin là video đã tải hoặc đã tìm thấy trong cache, không chỉ là metadata preview.

## 7.3. Chuẩn bị MP4 cục bộ

1. UI gửi multipart `video`.
2. Backend lấy basename.
3. Yêu cầu extension `.mp4`.
4. Ghi vào UUID `.part`.
5. Kiểm tra kích thước khác 0.
6. `os.replace` thành UUID `.mp4`.
7. Lưu preview trong RAM.
8. Stream file qua preview endpoint.
9. Khi tạo project, ưu tiên hard link; fallback copy atomic.

Bằng chứng:

- upload: `app.py:2324-2373`
- route: `app.py:3182-3194`
- hard link/copy: `app.py:1390-1422`, `app.py:1609-1616`

Validation hiện không kiểm tra:

- giới hạn byte;
- MIME;
- magic bytes;
- container;
- codec;
- `ffprobe` trước khi lưu.

## 7.4. Crop và enqueue

Frontend giữ crop normalized:

- `x`;
- `y`;
- `width`;
- `height`.

Resolution lấy từ `loadedmetadata` của video: `templates/index.html:950-955`.

`POST /api/queue` gửi:

- URL/source;
- preview ID;
- name;
- OCR language;
- crop;
- resolution.

Bằng chứng: `templates/index.html:626-705`, `app.py:2553-2613`.

Nếu enqueue trước khi metadata có resolution và `ffprobe` thất bại, backend có thể dùng full-frame OCR dù crop tồn tại: `app.py:1708-1762`.

## 7.5. Khởi động queue

`POST /api/queue/start` gọi `start_workflow_queue()`.

Worker tại `app.py:2720-2780`:

1. chọn job `queued` đầu tiên;
2. phối hợp prefetch;
3. chuyển job thành `running`;
4. đặt `current_job_id`;
5. persist queue;
6. acquire `workflow_lock`;
7. reset progress;
8. start prefetch;
9. gọi workflow đồng bộ;
10. ghi `finished_at`;
11. lặp FIFO.

## 7.6. Resolve/download và chuẩn bị project

`run_workflow_thread()`:

1. đọc config;
2. validate Notebook/output;
3. dùng prepared source nếu có;
4. nếu không, resolve/download;
5. tạo project directory duy nhất;
6. thiết lập path video/raw/translated/thumbnail;
7. ghi manifest `processing`;
8. chuẩn bị thumbnail tùy chọn.

Bằng chứng: `app.py:2856-2936`.

## 7.7. OCR

`run_videocr()`:

1. lấy resolution từ job hoặc `ffprobe`;
2. chuyển crop normalized thành pixel;
3. chọn crop/full-frame;
4. tạo argv cho VideOCR;
5. chọn language/model/frame skip/GPU flags;
6. stream stdout;
7. kiểm tra cancellation;
8. timeout 30 phút;
9. kiểm tra exit code;
10. yêu cầu output path tồn tại.

Bằng chứng: `app.py:1708-1850`, `app.py:2938-2951`.

Boundary này chưa xóa output cũ trước chạy và chỉ kiểm tra path tồn tại ngay sau process, nên output stale vẫn là khả năng nếu CLI exit 0 nhưng không rewrite: `app.py:1740-1744`, `app.py:1837-1849`.

## 7.8. Dịch qua Gemini Notebook

1. đọc toàn bộ raw SRT;
2. tạo một prompt;
3. bảo đảm Edge debug chạy;
4. nối Playwright qua CDP;
5. tìm Notebook tab/input;
6. fill prompt;
7. click send;
8. poll response;
9. chờ không còn stop control và có khoảng ba mẫu cùng độ dài;
10. lấy `pre/code/text`;
11. restore source timestamps nếu block/index tương thích;
12. ghi translated SRT atomically.

Bằng chứng: `app.py:1898-1906`, `app.py:2046-2219`.

Completion detection không so sánh toàn bộ content giữa các mẫu; nó chủ yếu so độ dài: `app.py:2148-2179`.

## 7.9. Finalization và success

Trình tự thực tế:

1. tạo translated SRT và kiểm tra tương thích cơ bản;
2. `finalize_project_assets()`;
3. move tài sản Douzy đủ điều kiện;
4. chạy `validate_project_result()`;
5. ghi success manifest;
6. set `success/done/result_ready`.

Bằng chứng: `app.py:2954-2974`.

Điểm quan trọng: asset move diễn ra **trước** full project validation.

Validation yêu cầu:

- video/raw/translated tồn tại và không rỗng;
- timestamp sequence giống nhau;
- block count bằng nhau;
- index bằng nhau.

Bằng chứng: `app.py:1909-1984`.

Không kiểm tra:

- nội dung thực sự là tiếng Việt;
- tỷ lệ text thay đổi;
- index tăng đơn điệu;
- end time sau start time;
- chronology hoàn toàn hợp lệ.

Sau success, polling chỉ recheck path tồn tại, không revalidate size/content: `app.py:1994-2001`, `app.py:2388-2405`.

## 7.10. Prefetch

Khi current job đạt `ocr` hoặc `translate`:

1. chọn job Douyin queued tiếp theo chưa có `source_video`;
2. tải trong detached prefetch thread;
3. dùng cancel event riêng;
4. suppress log;
5. ghi source vào job nếu job vẫn queued.

Bằng chứng: `app.py:2621-2691`.

Foreground queue và prefetch có coordination, nhưng preview dùng một lock khác nên không có invariant toàn cục rằng chỉ một Douzy operation chạy trên máy/process.

## 7.11. Cancel

`POST /api/cancel`:

1. kiểm tra `workflow_lock.locked()`;
2. set `cancel_event`;
3. snapshot registered processes;
4. terminate;
5. chờ 5 giây;
6. kill nếu cần;
7. workflow chuyển sang `cancelled`.

Bằng chứng: `app.py:440-484`, `app.py:2975-3024`.

Cancellation dùng một event chung, không gắn với `job_id` hoặc generation. Nếu job thay đổi giữa check và set/clear, cancellation có thể bị mất hoặc tác động sai generation: `app.py:473-484`, `app.py:2750-2752`, `app.py:3015-3024`.

## 7.12. Retry

Với job `error` hoặc `cancelled`:

- có video và raw SRT → `translate`;
- có video → `ocr`;
- không có reusable source → `download`.

Bằng chứng: `app.py:2376-2385`, `app.py:2814-2842`.

## 7.13. Restart recovery

Khi startup:

- load queue;
- normalize tối đa 500 record cuối;
- `running` cũ → `error`;
- local source thiếu → error;
- legacy success thiếu `result_ready` → validate một lần;
- persist normalized queue.

Bằng chứng: `app.py:2440-2509`, `app.py:2535-2542`.

## 7.14. Settings, diagnostics và history

- Settings GET/POST đọc/ghi config.
- Health kiểm tra file/path/URL syntax và Edge port.
- History đọc tối đa 15 record từ Douzy SQLite.
- Folder picker native chọn output.
- Open-folder chỉ mở path nằm trong result allowlist.

Bằng chứng: `app.py:3118-3164`, `app.py:3267-3370`, `app.py:3400-3424`, `app.py:3452-3476`.

## 7.15. Tự cập nhật

1. lấy latest release;
2. xác thực version và official GitHub URL;
3. tải checksum và ZIP;
4. kiểm checksum bắt buộc;
5. kiểm GitHub digest/size nếu metadata có;
6. chọn đúng ZIP member;
7. kiểm kích thước và `MZ`;
8. stage EXE;
9. chạy PowerShell helper;
10. lên lịch `os._exit(0)`;
11. helper đổi target thành `.old`;
12. staged thành target;
13. start target mới;
14. chờ hai giây;
15. xóa `.old`.

Bằng chứng: `app.py:161-226`, `app.py:640-838`, `app.py:3053-3102`.

---

## 8. Inventory HTTP/API và hợp đồng

### 8.1. Tổng quan

- 23 URL rule do ứng dụng khai báo.
- 25 method/path operation.
- 21 API pattern.
- 23 API operation.
- Tất cả launcher bind loopback.
- Không có API version prefix.
- API chủ yếu là internal control plane cho UI bundled.

### 8.2. Route không phải API

| Method | Route | Contract | Bằng chứng |
|---|---|---|---|
| GET | `/` | Render `index.html` với `app_version` | `app.py:3027-3029` |
| GET | `/avatar.jpg` | Trả avatar bundled | `app.py:3032-3034` |

### 8.3. API route

| Method | Route | Input / success / lỗi chính | Side effect | Bằng chứng |
|---|---|---|---|---|
| GET | `/api/check-edge` | `200 {connected}`; lỗi probe thành `false` | Không | `app.py:3037-3040` |
| POST | `/api/edge/show` | `200 {ok}`; 400 cho config/runtime lỗi đã xử lý | Start/restore/navigate Edge | `app.py:3043-3050` |
| GET | `/api/update` | `200` update status; failure vẫn có thể là degraded `200` | Network/cache | `app.py:3053-3063` |
| POST | `/api/update/open` | `200 {ok,url}`; 500 nếu browser open thất bại | Mở browser | `app.py:3066-3074` |
| POST | `/api/update/install` | `202`; `409` nếu lock đã held; `400` cho preparation lỗi | Download, stage, helper, exit | `app.py:3077-3102` |
| POST | `/api/shortcut` | `200 {ok,path}`; `400` nếu unsupported/failure | Tạo `.lnk`, ghi config | `app.py:3105-3115` |
| GET | `/api/health` | `200 {ready,checks}` | Probe local dependencies | `app.py:3118-3164` |
| POST | `/api/previews` | JSON `{url}`; `201`; `400/409/500` | Resolve/download Douyin | `app.py:3167-3179` |
| POST | `/api/previews/local` | Multipart `video`; `201`; `400/409/500` | Ghi managed MP4 | `app.py:3182-3194` |
| GET | `/api/previews/<id>/video` | File/range response; `404` nếu preview hết hạn | Có thể prune preview/cache | `app.py:3197-3202` |
| GET | `/api/queue` | `200 {jobs,running,current_job_id}` | Recheck result paths | `app.py:3205-3212` |
| POST | `/api/queue` | JSON job; `201`; `400` validation | Mutate/persist queue | `app.py:3214-3228` |
| POST | `/api/queue/start` | `202`; `409` nếu không có queued job | Start daemon worker | `app.py:3231-3237` |
| DELETE | `/api/queue/<job_id>` | `200`; `404/409` | Xóa queued job/cache | `app.py:3240-3247` |
| POST | `/api/queue/<job_id>/retry` | `200`; `400/404/409` | Requeue theo resume step | `app.py:3250-3259` |
| DELETE | `/api/queue/finished` | `200 {ok}` | Xóa success/error/cancelled | `app.py:3262-3265` |
| POST | `/api/settings` | Partial JSON; `200`; `400` validation/I/O | Ghi config | `app.py:3267-3302` |
| GET | `/api/settings` | `200` effective settings/defaults | Đọc local paths/config | `app.py:3294-3341` |
| GET | `/api/history` | `200 []` hoặc tối đa 15 record | Đọc Douzy SQLite | `app.py:3343-3370` |
| POST | `/api/process` | JSON `{url,lang,name}`; `202`; `400/409` | Legacy enqueue+autostart | `app.py:3372-3383` |
| POST | `/api/cancel` | `202`; `409` nếu không có active lock | Set event, terminate process | `app.py:3386-3390` |
| GET | `/api/progress` | Query `after`; `200` status/log delta/result | Không chủ ý | `app.py:3392-3398` |
| POST | `/api/open-folder` | JSON `{path}`; `200/400/403/500` | Mở Explorer | `app.py:3400-3424` |

### 8.4. Đặc điểm contract tích cực

- Validation tập trung cho Notebook, OCR, crop, resolution, project name và Douyin URL: `app.py:309-437`.
- Open-folder yêu cầu exact membership trong result path đã biết: `app.py:3400-3424`.
- Queue/config ghi qua temporary file và replace.
- Frontend có một `fetchJson` wrapper chung: `templates/index.html:485-493`.

### 8.5. Finding M-13 — API contract và error handling

**Severity: Medium**

Các vấn đề hợp nhất:

- README chỉ liệt kê 13/21 API pattern: `README.md:373-391`.
- Chỉ 11/23 API operation được test qua Flask client.
- `/api/open-folder` gọi `.get` trên truthy scalar JSON và có thể tạo framework HTML 500: `app.py:3400-3424`.
- Queue/process enqueue cho phép nhiều Douyin path không-root rồi mới thất bại bất đồng bộ: `app.py:415-437`, `app.py:3205-3228`.
- `/api/history`, `/api/update`, `/api/health` dùng degraded-200 theo cách không thống nhất.
- Persistence exception có thể rơi ra thành framework-default 500 thay vì JSON envelope.
- `/api/process` là legacy/undocumented và có contract hẹp hơn queue flow.

Khuyến nghị là chuẩn hóa JSON validation/error envelope và hoặc tài liệu hóa đủ, hoặc ghi rõ đây là API nội bộ không ổn định.

---

## 9. Hành vi frontend/UI và state

### 9.1. Cấu trúc

`templates/index.html` chứa:

- CSS inline: `templates/index.html:9-268`;
- markup: `templates/index.html:270-458`;
- JavaScript inline: `templates/index.html:460-973`.

UI gồm:

- header/version;
- update banner;
- composer;
- video preview/crop;
- queue;
- progress/log/result;
- settings;
- diagnostics;
- Douzy history;
- toast.

### 9.2. State client

Có 12 biến mutable cấp trang, gồm các nhóm:

- current preview;
- crop drag;
- queue snapshot/signature;
- polling/log cursor;
- result path;
- update state;
- toast/settings debounce.

Bằng chứng: `templates/index.html:461-475`.

Đây là state machine thủ công bằng DOM mutation, không có reducer hoặc component boundary.

### 9.3. Polling

- `loadQueue()` gọi queue và progress.
- `queueLoadInFlight` tránh overlap một chuỗi poll.
- JSON signature tránh rebuild queue nếu không đổi.
- Backend logs được lấy incremental.
- DOM console giữ tối đa 500 dòng.

Bằng chứng: `templates/index.html:724-833`.

### 9.4. Rendering an toàn

Phần lớn dữ liệu backend được gán bằng `textContent`, đặc biệt:

- project names;
- error;
- log;
- path;
- health detail;
- history.

Bằng chứng: `templates/index.html:724-753`, `templates/index.html:817-848`, `templates/index.html:921-936`.

Một sink data-bearing còn lại dùng `innerHTML` cho queue metadata: `templates/index.html:734-744`. Normal IDs bị giới hạn, nên đây là defense-in-depth hơn là exploit có xác suất cao.

### 9.5. Finding H-09 — stale preview

**Severity: High**

Kịch bản:

1. preview A thành công;
2. người dùng sửa URL thành B, dùng history “Nạp”, hoặc preview B thất bại;
3. `currentPreview` vẫn là A;
4. Add to Queue vẫn hoạt động;
5. request gửi URL và preview ID của A dù input hiển thị B.

Bằng chứng:

- preview state: `templates/index.html:563-602`
- enqueue: `templates/index.html:692-705`
- history/input handlers: `templates/index.html:930-949`

Hai biến thể liên quan:

- project name auto-fill của A có thể còn khi preview B thành công: `templates/index.html:585-589`;
- Add to Queue được enable trước khi video metadata bảo đảm resolution đã có: `templates/index.html:591-602`, `templates/index.html:950-955`.

### 9.6. Browser/native khác biệt

Folder picker chỉ hoạt động khi có `window.pywebview.api`; browser mode hiển thị lỗi và yêu cầu nhập path theo cách khác: `templates/index.html:907-918`.

Desktop shortcut cũng không áp dụng tương đương trong browser/source mode.

---

## 10. Native WebView bridge

### 10.1. Public surface

`DesktopApi` chỉ có một public method:

- `select_output_directory`.

Các reference pywebview/window dùng tên private:

- `_webview`;
- `_window`.

Bằng chứng: `app.py:3452-3476`.

### 10.2. Ý nghĩa của thay đổi v1.5.2

Nhánh hiện tại loại hai object reference khỏi public bridge surface. Test xác nhận:

- folder dialog được gọi đúng;
- không có public `webview` hoặc `window` attribute.

Bằng chứng: `tests/test_app.py:81-100`.

Đây là thay đổi nhỏ và phù hợp với giả thuyết object graph public từng làm bridge scan quá sâu. Tuy nhiên, repository không có:

- reproduction trace của v1.5.1;
- packaged before/after timing;
- test chạy scanner pywebview thật;
- test startup native thật.

Vì vậy nên diễn đạt đây là **cơ chế sửa có bằng chứng mạnh**, không phải chứng minh mọi lớp WebView hang đã được loại bỏ.

### 10.3. Coverage còn thiếu

Không có test gọi:

- `run_desktop_app`;
- `run_browser_app`;
- `make_server`;
- `webview.create_window`;
- `webview.start`;
- dispatch arguments.

CI không chạy launcher: `.github/workflows/tests.yml:20-25`.

### 10.4. Finding H-07 và H-08

- **H-07:** không có app-owned readiness/fallback.
- **H-08:** không có coordinated shutdown.

Đây là hai finding khác nhau: một về startup availability, một về ownership khi ứng dụng kết thúc.

---

## 11. Mô hình concurrency, job, state và cancellation

### 11.1. State trong RAM

`app.py:77-117` khai báo:

- `progress_status`;
- `workflow_jobs`;
- `current_job_id`;
- `queue_worker_thread`;
- `preview_registry`;
- `active_processes`;
- update cache;
- prefetch thread/job ID;
- cancellation events.

### 11.2. Lock và event

| Primitive | Vai trò |
|---|---|
| `status_lock` | progress/log |
| `workflow_lock` | một visible workflow trong process |
| `active_process_lock` | registry subprocess |
| `preview_lock` | serialize preview với preview |
| `preview_registry_lock` | registry preview |
| `queue_state_lock` | queue list/persistence |
| `update_cache_lock` | update-status cache |
| `config_write_lock` | settings read-modify-write |
| `cancel_event` | active visible workflow/preview |
| `prefetch_cancel_event` | detached prefetch |

Bằng chứng: `app.py:96-117`.

### 11.3. Invariant dự kiến — chỉ trong một process

- một queue worker;
- một active workflow;
- OCR và Gemini tuần tự;
- một prefetch;
- queue FIFO;
- foreground queue và prefetch cố tránh hai sidecar cùng lúc.

Bằng chứng: `app.py:2621-2780`.

Những invariant này **không có hiệu lực toàn máy** vì không có inter-process lock.

### 11.4. Finding H-04 — preview/workflow race

Preview:

1. kiểm tra `workflow_lock.locked()`;
2. acquire `preview_lock`.

Queue worker:

1. không kiểm tra `preview_lock`;
2. acquire `workflow_lock` sau đó.

Hai operation có thể overlap và dùng chung:

- `cancel_event`;
- key process `"sidecar Douzy"`;
- Edge/CDP;
- global progress/log behavior.

Bằng chứng: `app.py:452-484`, `app.py:2290-2321`, `app.py:2707-2753`.

Cancellation cũng không gắn với job/generation, nên cần được xử lý trong cùng operation coordinator.

### 11.5. Finding H-05 — prefetch sidecar orphan

Detached prefetch không đăng ký process vào visible registry. Nếu cancellation xảy ra sau `Popen` nhưng trước khi `start_douzy_sidecar()` handoff handle:

- startup ném exception;
- caller vẫn chưa có `sidecar_proc`;
- global registry không có process;
- không có owner chắc chắn để cleanup.

Bằng chứng: `app.py:1048-1104`, `app.py:2236-2267`, `app.py:2621-2626`, `app.py:2694-2704`.

Nguồn xác minh báo cáo fault injection đã tái hiện đường này.

### 11.6. Finding M-01 — single-instance không được enforce

**Severity: Medium**

Nhiều process có thể:

- load state riêng;
- cùng ghi `workflow_queue.json`;
- cùng ghi config;
- cùng điều khiển Edge 9222;
- cùng chạy Douzy/VideOCR;
- cùng tạo output.

Bằng chứng:

- process-local globals: `app.py:77-117`;
- shared files: `app.py:2421-2437`, `app.py:2535-2542`;
- launchers không có mutex: `app.py:3426-3517`.

Cần Windows mutex hoặc inter-process lock nếu multi-instance không được hỗ trợ.

---

## 12. Filesystem, configuration và vòng đời dữ liệu

### 12.1. Resource và writable roots

**Packaged**

- `RESOURCE_DIR`: `_MEIPASS`;
- `APP_HOME`: executable directory;
- `CONFIG_DIR`: `%APPDATA%\VietSub Studio`;
- queue/config/local videos dưới `CONFIG_DIR`.

**Source**

- resource;
- config;
- queue;
- local videos

đều ở cạnh `app.py`.

Bằng chứng: `app.py:24-52`.

Việc đặt video lớn dưới Roaming AppData có thể không phù hợp ở môi trường enterprise. Trong checkout hiện tại, source root nằm trong OneDrive; đây là đặc điểm môi trường, không phải hành vi Git.

### 12.2. Config

Tám default:

- `notebook_url`;
- `ocr_lang`;
- `ocr_model_mode`;
- `ocr_frames_to_skip`;
- `output_dir`;
- `crop_coords`;
- `edge_background`;
- `desktop_shortcut_initialized`.

Bằng chứng: `app.py:486-503`, `app_config.example.json:1-10`.

`app_config.example.json` **không thiếu** `edge_background`.

Đây không phải schema cưỡng chế:

- arbitrary JSON keys được merge;
- unknown keys có thể được giữ;
- không có schema version;
- parse failure trả defaults.

### 12.3. Finding M-02 — corrupt queue/config không được quarantine

**Severity: Medium**

Queue malformed:

1. `load_queue_file()` trả `[]`;
2. startup nhận queue rỗng;
3. persist lại;
4. ghi đè queue hỏng.

Bằng chứng: `app.py:2499-2509`, `app.py:2535-2542`.

Config malformed:

- load defaults âm thầm;
- settings save sau đó có thể ghi defaults + thay đổi mới lên file cũ.

Bằng chứng: `app.py:486-514`, `app.py:3267-3302`.

Atomic writes làm corruption do app ít xảy ra hơn, nhưng không thay thế backup/quarantine.

### 12.4. Finding M-03 — giới hạn 500 chỉ ở file

**Severity: Medium**

`write_queue_file()` chỉ ghi `jobs[-500:]`: `app.py:2421-2428`.

`enqueue_workflow()` không cap `workflow_jobs`: `app.py:2608-2610`.

Hậu quả:

- current session có thể vượt 500;
- job cũ biến mất sau restart;
- queued/retryable job cũ có thể mất metadata;
- managed source cũ có thể thành orphan.

### 12.5. Local managed media

Upload được lưu trong `LOCAL_VIDEO_DIR`. Cleanup xảy ra khi:

- preview hết hạn và còn registry entry;
- queued job bị xóa;
- terminal jobs được clear;
- không còn queue reference.

Bằng chứng: `app.py:2270-2287`, `app.py:2512-2533`, `app.py:2783-2811`.

Startup không scan cache, nên upload thành công nhưng chưa queue có thể tồn tại lâu dài sau restart.

### 12.6. Finding M-04 — upload/cache lifecycle

**Severity: Medium**

Cụm vấn đề:

- không byte limit;
- chỉ extension và nonzero;
- không container validation;
- không free-space preflight;
- orphan cache qua restart;
- packaged cache nằm dưới `%APPDATA%`;
- source cache nằm cạnh repository.

Bằng chứng: `app.py:52-64`, `app.py:2324-2373`, `app.py:3182-3194`.

### 12.7. Output directory

Default theo thứ tự:

1. redirected Documents;
2. redirected Desktop;
3. home.

Bằng chứng: `app.py:521-567`.

Frontend cảnh báo OneDrive nhưng không ngăn: `app.py:3314-3338`.

`output_dir` chỉ cần absolute. UNC path cũng có thể hợp lệ trên Windows; nếu local API bị vượt qua, việc ghi UNC có thể tạo outbound SMB behavior. Đây là một phần của finding M-10, không phải path traversal thông thường.

### 12.8. Project naming

Tên project:

- loại ký tự Windows invalid;
- trim trailing space/dot;
- cap base 100 ký tự;
- prefix reserved device names;
- exclusive `mkdir`;
- tối đa 999 suffix attempts.

Bằng chứng: `app.py:397-412`, `app.py:1377-1387`.

### 12.9. Atomic writes

Temporary file + `os.replace` được dùng cho:

- config;
- queue;
- manifest;
- translated SRT;
- thumbnail;
- copy video.

Bằng chứng:

- `app.py:505-514`
- `app.py:1325-1334`
- `app.py:1400-1422`
- `app.py:1641-1666`
- `app.py:2203-2216`
- `app.py:2421-2431`

Đây là atomic visibility, không phải power-loss durability đầy đủ vì không `fsync` file và parent directory.

### 12.10. Project portability

Project folder tự chứa, nhưng queue và manifest chứa nhiều absolute path. Nếu folder bị đổi tên/move:

- media/SRT vẫn dùng được bên ngoài app;
- queue linkage;
- retry;
- result readiness;
- open-folder

có thể hỏng.

Bằng chứng: `app.py:1641-1666`, `app.py:2409-2490`, `app.py:3400-3424`.

### 12.11. Douzy asset lifecycle

Eligible Douzy source được xử lý tại chỗ, rồi move sau translation.

Việc cập nhật Douzy DB và persisted queue references là best-effort, không rollback move nếu update thất bại: `app.py:1494-1525`, `app.py:1575-1581`.

---

## 13. External dependencies, provider contracts và failure mode

| Dependency | Contract | Timeout/retry | Failure mode chính |
|---|---|---|---|
| Flask 3.1.3 | Local HTTP control plane | Theo server/request | Route exception, stale UI |
| Werkzeug | `make_server` import trực tiếp | — | Direct dependency không khai báo riêng |
| pywebview 6.2.1 | Native EdgeChromium shell | Không có app-ready deadline | Startup không render |
| Edge/WebView2 | Native window | Không app preflight | Native availability |
| Edge CDP | `127.0.0.1:9222` | ≈10 giây startup | Port conflict, sai browser/profile |
| Playwright 1.61.0 | CDP control | Một số call dùng default timeout | DOM/API drift |
| Gemini Notebook | Web UI translation | App wait ≈300 giây | Selector/auth/context/timeout |
| Douzy sidecar | Local bearer-token HTTP API | Startup 15 giây; poll ≈300 giây | Binary/API/ready-line drift |
| Douzy SQLite | `aweme` schema | Lookup retry ≈20 giây | Schema/path mismatch |
| VideOCR CLI | Fixed executable/flags/output | ≈1.800 giây | Flag/output/version mismatch |
| `ffprobe` | Width/height CSV | 15 giây | Missing/invalid output → full frame |
| GitHub Releases | Update metadata/assets | Metadata 10 giây; asset socket timeout | Network/rate limit/trust root |
| PowerShell | Shortcut/update helper | Shortcut 15 giây | Permission/antivirus/rollback |
| Explorer | Mở result | — | Launch failure |

Các timeout trên là ngưỡng trong mã, không nhất thiết là wall-clock ceiling tuyệt đối.

### 13.1. Douzy

Hard-coded paths:

- sidecar: `app.py:37-39`;
- config;
- SQLite DB.

Protocol tại `app.py:1048-1214`.

Ba failure liên tiếp nghĩa là initial failure cộng tối đa hai retry, không phải ba retry sau lần đầu: `app.py:1130-1159`.

### 13.2. Finding H-01 — Douzy asset selection

**Severity: High**

`collect_douzy_assets()` có thể:

- chọn mọi file nếu source directory name trùng video stem;
- nếu không, chọn sibling có stem `startswith(source_stem)`;
- normalize nhiều source về cùng destination;
- dùng move/replace.

Bằng chứng: `app.py:1425-1491`, `app.py:1544-1569`.

Ví dụ:

- `123.mp4` có thể match `1234.mp4`;
- `_cover.jpg` và `_cover.jpeg` có thể cùng thành một thumbnail destination.

Nguồn xác minh báo cáo các trường hợp prefix/collision đã được tái hiện.

Ngoài ra, nếu DB path là directory và expected MP4 không tồn tại, code có thể chọn MP4 mới nhất mà không chứng minh thuộc đúng `aweme_id`: `app.py:1163-1214`.

### 13.3. Finding M-05 — finalize ordering

**Severity: Medium**

- move tài sản diễn ra trước full project validation;
- DB/queue reference update là best-effort;
- không có rollback transaction giữa filesystem, SQLite và queue.

Bằng chứng: `app.py:1528-1582`, `app.py:2954-2973`.

### 13.4. VideOCR

Command construction tại `app.py:1723-1800`; lifecycle tại `app.py:1801-1850`.

Health chỉ kiểm tra `isfile`, không chạy `--version` hoặc `--help`: `app.py:3118-3164`.

### 13.5. Finding M-06 — OCR/translation output contract

**Severity: Medium**

Cụm vấn đề:

- raw SRT cũ không được xóa/đánh dấu freshness trước VideOCR;
- post-process chỉ kiểm tra output path;
- toàn bộ raw SRT được gửi trong một prompt;
- timeout không tự Stop Gemini;
- success gate chỉ kiểm tra cấu trúc;
- unchanged/wrong-language output có thể pass nếu cấu trúc đúng;
- sau success, readiness chỉ kiểm tra existence.

Bằng chứng:

- `app.py:1740-1850`
- `app.py:1898-1984`
- `app.py:2027-2043`
- `app.py:2148-2179`
- `app.py:2388-2405`

### 13.6. Finding M-07 — Edge/CDP ownership

**Severity: Medium**

Ứng dụng:

- coi bất kỳ HTTP success tại 9222 là Edge ready;
- không xác minh browser identity/profile;
- dùng context đầu tiên;
- nhiều đường code có thể cùng check-then-start Edge;
- giữ persistent authenticated profile.

Bằng chứng: `app.py:881-932`, `app.py:1021-1037`, `app.py:2046-2121`, `app.py:3043-3050`.

### 13.7. Finding M-17 — provider coupling/readiness

**Severity: Medium**

Provider contracts dựa vào:

- fixed paths;
- ad hoc HTTP schema;
- SQLite schema;
- CLI flags;
- mutable DOM selector;
- fixed CDP port.

Health chủ yếu kiểm tra existence/syntax, không chứng minh usability: `app.py:3118-3164`.

---

## 14. Build, package, update, release và CI

### 14.1. Python dependencies

Runtime:

- `playwright==1.61.0`
- `flask==3.1.3`
- `pywebview==6.2.1`

Build:

- runtime requirements;
- `pyinstaller==6.21.0`

Bằng chứng: `requirements.txt:1-3`, `requirements-build.txt:1-2`.

`werkzeug.serving.make_server` được import trực tiếp nhưng Werkzeug chỉ đến transitively qua Flask: `app.py:21-22`.

### 14.2. Portable one-file

- `build_portable.bat`
- `VietSub Studio Portable.spec`

Spec không có `COLLECT` và đưa binaries/datas trực tiếp vào EXE, nên là one-file: `VietSub Studio Portable.spec:27-45`.

Bundled:

- templates;
- avatars;
- pywebview resources.

Bằng chứng: `VietSub Studio Portable.spec:2-23`.

### 14.3. Onedir

- `VietSub Studio.spec` dùng `EXE(... exclude_binaries=True)` + `COLLECT`: `VietSub Studio.spec:26-52`.
- `build_app.bat` lại tự lặp option CLI thay vì gọi spec: `build_app.bat:12-17`.

Hai mode không mâu thuẫn; rủi ro là duplication/drift.

### 14.4. Finding L-03 — packaging duplication

**Severity: Low**

- onedir có hai nguồn cấu hình;
- portable script cài `requirements.txt`, rồi `requirements-build.txt` vốn include lại runtime requirements;
- batch có `pause`, không phù hợp CI.

Bằng chứng: `build_app.bat:12-17`, `build_portable.bat:8-20`, `requirements-build.txt:1-2`.

### 14.5. CI

Workflow duy nhất:

- PR và push `main`;
- `windows-latest`;
- Python 3.12;
- cài runtime requirements;
- compile;
- unittest discovery.

Bằng chứng: `.github/workflows/tests.yml:1-25`.

Không:

- cài build requirements;
- build EXE;
- smoke-test frozen resources;
- chạy WebView;
- chạy browser tests;
- coverage gate;
- lint/type check;
- provider integration;
- release upload;
- signing;
- SBOM.

### 14.6. Finding M-12 — test/CI assurance gap

**Severity: Medium, priority P0**

Đây là assurance gap, không phải runtime defect trực tiếp. Tác động có thể cao vì những bề mặt không được test chính là:

- native launcher vừa thay đổi;
- PyInstaller resources;
- updater replacement;
- browser mode;
- external adapters.

Bằng chứng: `.github/workflows/tests.yml:20-25`, `app.py:3479-3517`.

### 14.7. Updater integrity

Điểm mạnh:

- official repository URL allowlist;
- checksum bắt buộc;
- GitHub digest/size nếu có;
- exact ZIP member;
- size cap;
- `MZ` check;
- stage cạnh executable;
- không dùng `extractall`.

Bằng chứng: `app.py:141-198`, `app.py:640-754`.

### 14.8. Finding H-06 — update race/re-entry

**Severity: High**

`/api/update/install`:

1. kiểm tra `workflow_lock` một lần;
2. không reserve;
3. tải/stage update;
4. queue hoặc preview có thể bắt đầu;
5. updater vẫn lên lịch `os._exit(0)`.

Không có installation mutex; nhiều request có thể chạy helper dùng chung `target.old`.

Bằng chứng: `app.py:757-838`, `app.py:2707-2753`, `app.py:3077-3102`.

### 14.9. Finding M-09 — replacement contract

**Severity: Medium**

- helper xóa `.old` sau hai giây, không health handshake;
- helper là PowerShell string dài, khó test;
- output helper bị discard;
- retry/restore có edge case với backup;
- onedir build cũng được coi là packaged và nhận one-file updater.

Bằng chứng:

- `app.py:517-518`
- `app.py:672-745`
- `app.py:757-830`
- `README.md:315-337`

### 14.10. Finding M-18 — reproducibility/provenance

**Severity: Medium**

Build phụ thuộc:

- ambient Python;
- host architecture;
- transitive dependency resolver;
- UPX availability;
- mutable runner/actions;
- quy trình release không tracked.

Không có tag-driven build workflow, build manifest, attestation hoặc SBOM.

Bằng chứng:

- `requirements.txt:1-3`
- `build_portable.bat:8-19`
- `VietSub Studio Portable.spec:35-45`
- `.github/workflows/tests.yml:13-25`
- `.gitignore:7-9`, `.gitignore:26-31`

**[LOCAL ARTIFACT]** Nguồn xác minh báo cáo artifact v1.5.2 cục bộ là CPython 3.14 AMD64 và không có Authenticode signature, trong khi CI dùng Python 3.12. Vì release directory bị ignore, quan sát này không tái lập từ clean clone và không chứng minh binary provenance.

### 14.11. Finding M-11 — update authenticity

**Severity: Medium; impact cao, likelihood không xác định/ước tính thấp**

ZIP, checksum và API digest cùng nằm trong trust domain GitHub Release. Nếu release authority bị chiếm quyền, attacker có thể phát hành payload và checksum khớp.

Repository không có:

- Authenticode verification;
- pinned signer;
- signed manifest bằng khóa độc lập.

Bằng chứng: `app.py:185-198`, `app.py:672-746`, `README.md:540-544`.

---

## 15. Đánh giá test suite

### 15.1. Cấu trúc

| Lớp | Số test |
|---|---:|
| `ValidationTests` | 26 |
| `DownloadLookupTests` | 4 |
| `QueuePersistenceTests` | 2 |
| `DesktopShortcutTests` | 2 |
| `AutomaticUpdateTests` | 1 |
| `ApiTests` | 30 |
| **Tổng** | **65** |

Bằng chứng: `tests/test_app.py:35-1443`.

### 15.2. Kết quả

**[UNIT-RUN]** Nguồn xác minh đã chạy đủ 65 test và tất cả đạt trong khoảng 1,7 giây.

Điều này không chứng minh ứng dụng thật đã được chạy; nó chứng minh suite hiện tại xanh trong môi trường xác minh.

### 15.3. Điểm mạnh của test

Suite không chỉ mock:

- temporary filesystem;
- hard link/copy;
- SQLite;
- ZIP;
- SHA-256;
- Flask client;
- queue persistence;
- retry/cancel;
- update staging.

Ví dụ:

- Douzy asset/file operations: `tests/test_app.py:102-203`
- hard link/copy: `tests/test_app.py:428-475`
- SQLite: `tests/test_app.py:555-637`
- queue persistence: `tests/test_app.py:639-692`
- updater staging: `tests/test_app.py:741-793`
- concurrency/retry logic: `tests/test_app.py:880-1109`
- workflow/cancel: `tests/test_app.py:1139-1439`

### 15.4. Boundary được mock hoặc chưa chạy

Không có live test cho:

- Douzy HTTP;
- Douzy binary;
- VideOCR binary;
- ffprobe;
- Edge CDP;
- Gemini DOM;
- pywebview;
- WebView2;
- PowerShell replacement;
- packaged EXE;
- browser DOM/accessibility.

### 15.5. API coverage trực tiếp

- 21 API pattern;
- 23 API operation;
- 10 pattern/11 operation được gọi trực tiếp qua test client.

Tức khoảng 47,8% API operation được test ở route boundary.

### 15.6. Coverage ước tính

**[ESTIMATE]** Nguồn xác minh dùng Python `trace` 3.14.3:

- nếu import được trace từ đầu: khoảng `1674/2776 ≈ 60,3%`;
- nếu discovery xảy ra trước trace: khoảng `1374/2776 ≈ 49,5%`.

Đây không phải metric chính thức:

- phụ thuộc command order;
- không có branch coverage;
- không được CI thu thập;
- không nên dùng làm release gate hiện tại.

### 15.7. Khoảng trống threading

Các test prefetch/queue chủ yếu gọi worker hoặc helper đồng bộ với mock. Không có test thực sự khởi động queue worker và prefetch cạnh tranh cùng HTTP requests.

---

## 16. Security và privacy

### 16.1. Điểm mạnh bảo mật

1. **Loopback-only binding**  
   Native/browser dùng random port; `--server` dùng port 5000: `app.py:3426-3495`.

2. **URL allowlist**  
   Notebook, Douyin, thumbnail CDN và GitHub được kiểm tra: `app.py:141-198`, `app.py:309-320`, `app.py:415-430`, `app.py:1251-1323`.

3. **Command-injection posture tốt**  
   Không thấy `shell=True` hoặc `os.system`; subprocess chủ yếu dùng argv array.

4. **Open-folder allowlist**  
   Không nhận arbitrary path: `app.py:3400-3424`.

5. **Safe ZIP handling**  
   Chỉ đọc exact member, không extract toàn archive: `app.py:710-746`.

6. **Frontend chủ yếu dùng `textContent`**.

7. **Git hygiene và privacy guidance tốt**  
   `.gitignore:1-47`, `SECURITY.md:7-25`, `.github/ISSUE_TEMPLATE/bug_report.yml:6-17`.

### 16.2. Finding M-10 — local HTTP trust boundary

**Severity: Medium**

Không có:

- authentication;
- per-launch token;
- Host allowlist;
- Origin/Referer/Fetch-Metadata validation;
- CSRF protection.

Bằng chứng: `app.py:31`, `app.py:3026-3424`.

Các bodyless POST có side effect:

- Edge show;
- update open/install;
- shortcut;
- queue start;
- cancel.

Loopback, random ports, SOP và browser Private Network Access là mitigation thực, nên không nên gọi đây là remote high-severity vulnerability đã chứng minh. Fixed `--server` dễ nhắm hơn, nhưng source mode không thực hiện được một số packaged-only effect.

Nếu same-origin hoặc direct local access đạt được, API còn trả:

- Notebook URL;
- local paths;
- source URLs;
- history;
- logs;
- result metadata;
- preview video qua high-entropy ID.

Bằng chứng: `app.py:3197-3229`, `app.py:3294-3398`.

`output_dir` chấp nhận UNC absolute path; trong một attack chain thành công, Windows có thể thực hiện outbound SMB access: `app.py:351-359`, `app.py:3280-3302`.

### 16.3. CDP privacy boundary

Finding M-07 cũng có khía cạnh security:

- port 9222 không có auth;
- loopback TCP không phải per-user boundary tuyệt đối;
- app không xác minh service identity;
- subtitle/Notebook URL có thể được gửi qua browser context không mong đợi.

### 16.4. Finding M-11 — update trust

Current checks bảo vệ tốt chống corruption và asset mismatch, nhưng không bảo vệ khỏi compromise của chính release authority.

Khuyến nghị:

- Authenticode;
- pin expected signer;
- hoặc signed update manifest bằng public key nhúng trong app.

### 16.5. Finding L-02 — executable search path

**Severity: Low**

Các executable được gọi bằng tên gồm:

- `powershell.exe`;
- `explorer.exe`;
- fallback `msedge.exe`;
- `ffprobe`.

Bằng chứng: `app.py:602-617`, `app.py:815-830`, `app.py:899-919`, `app.py:1708-1716`, `app.py:3415-3421`.

Không có shell parsing, nên đây là search-path hardening, không phải command injection trực tiếp.

### 16.6. Finding L-01 — browser hardening

**Severity: Low**

- một data-bearing `innerHTML` sink;
- không thấy CSP;
- không thấy `X-Frame-Options`/`frame-ancestors`;
- không thấy `nosniff` hoặc referrer policy.

Bằng chứng: `templates/index.html:734-744`, `app.py:3026-3424`.

### 16.7. Finding L-04 — privacy wording

**Severity: Low**

README nói theo nghĩa rộng rằng chỉ subtitle text rời máy, nhưng app còn:

- gọi GitHub;
- Douyin/Douzy network;
- thumbnail CDN;
- Gemini;
- có thể ghi output vào Documents được OneDrive redirect.

Bằng chứng: `README.md:72-78`, `app.py:161-177`, `app.py:1106-1157`, `app.py:1305-1323`, `app.py:539-567`.

Nên đổi thành: subtitle text là user content chính được cố ý gửi để dịch; vẫn có network metadata và các request provider khác.

---

## 17. Hiệu năng

### 17.1. Kiến trúc throughput

- một active workflow;
- một prefetch download;
- OCR và translation không song song;
- không có rendering/transcode.

Đây là lựa chọn hợp lý với external tools không reentrant, nhưng throughput tối đa bị giới hạn.

### 17.2. Tối ưu hiện có

- reuse prepared source;
- OCR Douyin tại chỗ;
- hard link local MP4;
- copy chunk 4 MiB;
- prefetch một Douyin job;
- lazy Playwright import;
- success polling dùng `isfile` thay vì parse lại SRT;
- frontend tránh rerender queue khi signature không đổi;
- log cap.

Bằng chứng:

- `app.py:1015-1018`
- `app.py:1390-1423`
- `app.py:1528-1638`
- `app.py:1994-2001`
- `app.py:2621-2705`
- `templates/index.html:724-833`

### 17.3. Ước tính request

**[ESTIMATE từ code]**

- Busy: khoảng 2 request queue/progress mỗi giây + 0,2 Edge request/giây.
- Idle: khoảng 0,5 queue/progress request/giây + 0,2 Edge request/giây.

Bằng chứng: `templates/index.html:756-772`, `templates/index.html:939-942`.

### 17.4. Ước tính file stat

Một success job thường gây ba `isfile` trong serialization: `app.py:1994-2001`, `app.py:2388-2405`.

Với 500 success jobs và poll 1 giây:

- khoảng 1.500 `isfile`/giây cho queue response.

Đây là worst-case code-derived estimate, không phải benchmark. Error/cancelled jobs cũng có retry-step file checks.

### 17.5. Queue writes

Một fresh-success path có khoảng 11 lần persist sau enqueue; deferred Douzy move thường thêm một lần reference rewrite.

Đây là ước tính đường mã từ:

- `app.py:251-279`
- `app.py:2742-2780`
- `app.py:2903-2973`
- `app.py:1512-1525`

Mỗi write chỉ serialize tối đa 500 job, nhưng vẫn giữ `queue_state_lock` qua file I/O.

### 17.6. Finding M-15 — scalability hotspots

**Severity: Medium**

Cụm hotspot:

1. **Sidecar stdout queue không giới hạn**  
   Reader tiếp tục `put` sau READY, không còn consumer: `app.py:1040-1104`.

2. **Live job list không cap**  
   API copy và serialize toàn bộ list: `app.py:2545-2550`, `app.py:2607-2610`.

3. **Full queue DOM rebuild khi bất kỳ field thay đổi**  
   `templates/index.html:724-771`.

4. **Gemini DOM polling đọc full inner text lặp lại**  
   `app.py:2004-2023`, `app.py:2148-2179`.

5. **Cross-volume copy trên critical path**  
   Local video có thể copy trước OCR; Douyin có thể copy/move sau translation: `app.py:1400-1437`, `app.py:1556-1569`, `app.py:1610-1616`.

### 17.7. Những gì chưa biết

Không có:

- p50/p95 cho từng stage;
- cold-start;
- RSS;
- sidecar log rate;
- queue-size distribution;
- Gemini prompt-size distribution;
- OneDrive I/O measurements.

---

## 18. Reliability và observability

## 18.1. Điểm mạnh reliability

- Timeouts và cancellation ở các provider chính.
- Stage-aware retry.
- Atomic replace.
- Partial projects được giữ.
- Success gate có cấu trúc.
- Worker tuần tự.
- Manifest có `processing/success/error/cancelled`.
- Health UI và result readiness.

### 18.2. Finding H-02 — persistence và worker exception safety

**Severity: High**

Cụm root cause:

- state được mutate trước khi persist;
- persistence exception không rollback;
- worker thiếu outer `try/finally` đủ mạnh;
- `update_progress()` có thể ném lỗi file I/O;
- error handler có thể gọi lại cùng đường persistence và thất bại;
- exception từ `reset_progress()` hoặc `start_prefetch_thread()` sau khi acquire lock nhưng trước `run_workflow_thread()` cũng cần cleanup;
- lỗi ghi manifest trong nhánh cancellation có thể thoát worker.

Bằng chứng:

- `app.py:251-279`
- `app.py:2421-2437`
- `app.py:2720-2780`
- `app.py:2750-2753`
- `app.py:2975-3024`
- `app.py:3205-3265`

**[FAULT-INJECTION]** Nguồn xác minh đã tái hiện trạng thái:

- job ở RAM là `running`;
- `current_job_id` còn;
- worker đã thoát;
- `workflow_lock` có thể free.

Hậu quả API:

- enqueue trả lỗi nhưng job đã append;
- delete trả lỗi nhưng job đã remove;
- retry trả lỗi nhưng state RAM đã đổi;
- restart có thể làm job “sống lại” từ disk.

### 18.3. Finding H-03 — CLI queue overwrite

**Severity: High**

`automate.py` không gọi `initialize_queue_state()` trước enqueue.

Bằng chứng:

- CLI: `automate.py:6-29`
- queue initialization: `app.py:2535-2542`
- persistence/enqueue: `app.py:2421-2437`, `app.py:2553-2618`

Nếu desktop queue đã có dữ liệu, một CLI process mới có thể ghi state RAM mới lên cùng queue file.

### 18.4. Finding H-04 — operation race

Đã trình bày tại phần concurrency. Root fix phải bao gồm:

- atomic operation reservation;
- operation ID;
- per-operation cancel event;
- unique process registry key;
- Edge/Douzy resource lock;
- job-scoped cancellation.

### 18.5. Finding H-05 — sidecar ownership

`start_douzy_sidecar()` phải sở hữu cleanup từ ngay sau `Popen` tới khi handoff thành công. Đây là localized fix có impact cao.

### 18.6. Finding H-06 — update lifecycle

Update phải trở thành một exclusive application state, không chỉ là một lần đọc `workflow_lock.locked()`.

### 18.7. Finding M-08 — browser/source launcher lifecycle

**Severity: Medium**

- `serve_forever` gọi hai lần;
- đóng browser không dừng process;
- browser mode không có native folder picker;
- README evergreen không mô tả `--browser`;
- `Chay Tool.bat` kiểm Flask/Playwright nhưng không kiểm `webview`.

Bằng chứng:

- `app.py:3432-3449`
- `templates/index.html:907-918`
- `README.md:275-295`
- `Chay Tool.bat:8-20`
- `requirements.txt:1-3`

### 18.8. Finding M-09 — update recovery

Đã trình bày ở phần build/release. Điểm quan trọng về reliability là backup bị xóa trước khi có health-confirmed startup.

### 18.9. Finding M-14 — observability và client liveness

**Severity: Medium**

Backend:

- packaged stdout/stderr vào null;
- logs chỉ trong RAM;
- backend giữ 2.000 dòng;
- queue persistence không lưu logs/traceback;
- update helper discard output.

Bằng chứng: `app.py:24-31`, `app.py:852-879`, `app.py:2409-2418`, `app.py:815-830`.

Frontend:

- `fetch` không timeout;
- một request không settle có thể dừng chuỗi recursive polling;
- progress/Edge error bị swallow;
- queue error chỉ `console.warn`;
- không có visible disconnected/stale state.

Bằng chứng: `templates/index.html:485-493`, `templates/index.html:756-772`, `templates/index.html:825-834`, `templates/index.html:860-863`.

### 18.10. Result readiness sau success

Đây là một phần của M-06/M-14:

- full validation chạy một lần;
- sau đó `result_ready` chỉ phụ thuộc path tồn tại.

Tệp bị truncate hoặc sửa sai vẫn có thể hiện Open result cho tới khi path biến mất.

---

## 19. Maintainability và technical debt

### 19.1. Finding M-16 — concentration và state contract

**Severity: Medium**

Cụm debt:

- `app.py` 3.517 dòng;
- domain, adapters, storage, HTTP và launcher chung module;
- global state tại import time;
- không có app factory;
- job/config/progress/project dùng raw dictionaries;
- status/step literals lặp giữa Python và JavaScript;
- frontend 514 dòng JS inline;
- test cũng tập trung trong một file 1.443 dòng.

Bằng chứng:

- global composition: `app.py:24-117`
- schemas: `app.py:77-95`, `app.py:2388-2496`, `app.py:2553-2613`
- frontend mirror: `templates/index.html:461-475`, `templates/index.html:719-833`
- test reset globals: `tests/test_app.py:795-815`

Đây là maintainability risk, không phải bằng chứng rằng file lớn tự gây bug hiện tại.

### 19.2. Seams tốt để refactor

Có thể tách tăng dần từ các boundary sẵn có:

- validators: `app.py:309-437`;
- project/file storage: `app.py:1377-1666`;
- SRT: `app.py:1888-2001`;
- Douzy adapter: `app.py:1048-1214`;
- VideOCR adapter: `app.py:1723-1850`;
- Gemini adapter: `app.py:2046-2219`;
- queue repository/state: `app.py:2388-2842`;
- routes: `app.py:3026-3424`;
- launchers: `app.py:3426-3517`.

Không cần chuyển sang microservices.

### 19.3. Exception handling

Có 19 `except Exception`, nhưng không phải tất cả đều xấu:

- một số cleanup rồi re-raise;
- một số top-level chuyển failure thành job error;
- một số probe optional provider.

Những catch đáng cải thiện nhất:

- config load;
- locator probing;
- OCR config parsing;
- ffprobe fallback;
- best-effort prefetch;
- history query.

Bằng chứng: `app.py:486-503`, `app.py:980-993`, `app.py:1669-1721`, `app.py:2621-2661`, `app.py:3368-3370`.

### 19.4. Raw PowerShell updater

Updater replacement logic nằm trong raw PowerShell string dài: `app.py:757-830`.

Python tests chủ yếu mock `launch_update_helper`, nên retry/rollback/startup handshake không được behaviorally test: `tests/test_app.py:1314-1332`.

### 19.5. Finding M-13 — API contract ownership

API không có schema hoặc typed error model. Nếu API chỉ internal, cần ghi rõ; nếu hỗ trợ third-party automation, cần:

- versioning;
- complete docs;
- stable schemas;
- deprecation policy.

### 19.6. Finding M-17 — provider adapters

Narrow helper seams đã tồn tại, nhưng path discovery, health và runtime contract chưa dùng một shared capability model.

Nên có:

- `RuntimePaths`;
- `DouzyAdapter`;
- `VideocrAdapter`;
- `EdgeSession`;
- `TranslationProvider`;
- `UpdateService`.

### 19.7. Finding M-18 — release engineering

Build/release environment là một phần technical debt vì không thể liên hệ chắc chắn:

- tag;
- source;
- Python;
- dependencies;
- EXE;
- checksums;
- uploaded assets.

### 19.8. Finding L-03 — duplication

Nên làm `build_app.bat` gọi `VietSub Studio.spec` hoặc bỏ spec không dùng. Portable script nên cài `requirements-build.txt` đúng một lần.

---

## 20. UX và accessibility

### 20.1. Điểm mạnh UX

- Workflow preview → crop → queue → run rõ ràng.
- Queue card có error/retry/login/result/incomplete folder actions.
- Stage-aware recovery tốt.
- Responsive breakpoints tại 1060/760/480 px.
- Có reduced-motion rule.
- Native controls chủ yếu dùng semantic HTML.
- Crop có keyboard fallback qua bốn number input và preset buttons.
- Toast và update banner có live-region semantics.

Bằng chứng: `templates/index.html:234-267`, `templates/index.html:294-400`, `templates/index.html:724-858`.

### 20.2. Finding M-19 — accessibility

**Severity: Medium**

Các vấn đề đã xác minh:

1. **Crop direct manipulation chỉ dùng pointer**  
   Numeric inputs vẫn cho phép hoàn thành task bằng keyboard, nên không phải “crop hoàn toàn không dùng được bằng bàn phím”. Vấn đề là visual rectangle/handles không có focus, role hoặc keyboard parity: `templates/index.html:343-365`, `templates/index.html:654-680`.

2. **Không có persistent live workflow summary**  
   Queue/progress/result không có `aria-busy`, `role=log`, `progressbar`, `aria-valuenow` hoặc `aria-current`: `templates/index.html:371-400`, `templates/index.html:806-846`.

3. **URL error không nối semantics**  
   Không set `aria-invalid`, `aria-describedby` hoặc focus: `templates/index.html:304-308`, `templates/index.html:558-566`.

4. **Focus indicator yếu**  
   Source verification tính translucent yellow focus outline khoảng 1,4:1 trên nền sáng, thấp hơn mục tiêu 3:1; một số input đặt `outline:0`: `templates/index.html:55-58`, `templates/index.html:114`.

5. **Error text contrast**  
   Theo tính toán WCAG relative luminance từ CSS:
   - `#bc5949` trên `#fffaf0` ≈ 4,33:1;
   - trên `#fff3f0` ≈ 4,15:1;
   thấp hơn 4,5:1 cho normal text nhỏ.  
   Bằng chứng màu: `templates/index.html:116`, `templates/index.html:169-182`.

6. **Reduced motion chưa đầy đủ**  
   Duration được giảm nhưng infinite animations vẫn infinite; JavaScript vẫn smooth-scroll: `templates/index.html:74`, `templates/index.html:231-267`, `templates/index.html:847`, `templates/index.html:935`.

### 20.3. Finding M-20 — long-operation UX và health gating

**Severity: Medium**

1. **Cancel state**  
   Button được bật lại ngay sau `202`, chưa chờ backend chuyển sang cancelled: `templates/index.html:799-804`.

2. **“Dọn” quá rộng**  
   UI nói “xóa job đã xong”, backend xóa cả success/error/cancelled và managed copy không còn tham chiếu: `templates/index.html:377-382`, `templates/index.html:794-796`, `app.py:2800-2811`.

   Nó không xóa MP4 gốc người dùng đã chọn, nhưng có thể xóa retry handle và app-managed copy.

3. **Health không gate Run**  
   Backend có `ready`, frontend chỉ render rows và enable Run theo queue/running: `app.py:3118-3164`, `templates/index.html:724-731`, `templates/index.html:921-927`.

4. **Health không source-aware**  
   Douzy optional vì local MP4 không cần, nhưng Douyin user vẫn có thể thấy overall ready.

5. **Progress coarse**  
   Fixed widths `[8,28,52,78,100]`: `templates/index.html:719-722`. Console cung cấp thêm log, nên vấn đề là thiếu structured elapsed/rate/ETA, không phải hoàn toàn không có dấu hiệu hoạt động.

6. **Preview/upload/update indeterminate**  
   Có spinner/status text nhưng không byte progress hoặc request cancellation: `templates/index.html:519-545`, `templates/index.html:563-624`.

### 20.4. Language và typography

UI là tiếng Việt, không có promise về English UI. Một số label tiếng Anh và text 8–10 px làm giảm consistency/legibility, nhưng đây là polish chứ không phải finding severity riêng:

- `templates/index.html:190-225`
- `templates/index.html:300`
- `templates/index.html:373`
- `templates/index.html:738-739`

---

## 21. Những điểm mạnh nên bảo tồn

### 21.1. Recovery theo giai đoạn

`app.py:2376-2385`, `app.py:2450-2496`, `app.py:2814-2842`

Đây là lợi thế sản phẩm quan trọng nhất: failure ở Gemini không buộc download/OCR lại.

### 21.2. Integrity gate cho SRT

`app.py:1909-1984`

Dù chưa kiểm semantic translation, việc kiểm timestamp/block/index đã ngăn nhiều false success nghiêm trọng.

### 21.3. Atomic replacement

`app.py:505-514`, `app.py:1641-1666`, `app.py:2203-2216`, `app.py:2421-2431`

Nên giữ pattern này khi tách module.

### 21.4. Xử lý video lớn

- hard link;
- chunked copy;
- OCR Douyin tại chỗ;
- move MP4 cuối.

Bằng chứng: `app.py:1390-1437`, `app.py:1528-1638`.

### 21.5. Prefetch có chủ đích

Thread-local quiet/detached context và prefetch event riêng là thiết kế tốt, dù ownership startup cần sửa: `app.py:112-117`, `app.py:2621-2691`.

### 21.6. Native bridge hẹp

`app.py:3452-3476`

Không nên mở rộng bridge để gọi toàn bộ backend; tiếp tục dùng HTTP cho phần lớn chức năng.

### 21.7. URL/path validation

`app.py:141-198`, `app.py:309-437`, `app.py:1251-1334`, `app.py:3400-3424`

### 21.8. Updater integrity checks

Checksum, URL, member, size và `MZ` checks vẫn nên giữ ngay cả khi bổ sung signing.

### 21.9. Test dùng real local primitives

Suite thực sự chạm filesystem, SQLite, ZIP và hash; đây là nền tảng tốt để refactor.

### 21.10. Tài liệu người dùng

README dài và song ngữ, changelog rõ, privacy warning và issue forms tốt:

- `README.md:1-1113`
- `CHANGELOG.md:1-132`
- `SECURITY.md:1-25`
- `.github/ISSUE_TEMPLATE/bug_report.yml:1-55`

### 21.11. Repository hygiene

`.gitignore:1-47`, `.gitattributes:1-11`

Private state và generated artifacts không bị commit.

### 21.12. Command execution posture

Không dùng shell interpolation cho user input; dynamic paths cho PowerShell được truyền qua environment.

---

## 22. Lộ trình ưu tiên P0–P3

Không đưa ra lịch ngày/tuần vì repository không cung cấp dữ liệu staffing hoặc release cadence.

### P0 — trước bản phát hành tiếp theo

| Hành động | Finding | Impact | Effort | Phụ thuộc |
|---|---|---:|---:|---|
| Làm queue transition exception-safe; outer worker `try/finally`; rollback hoặc copy-on-write mutation | H-02, M-02, M-03 | Cao | Medium–Large | Quyết định durability policy |
| Gọi `initialize_queue_state()` trong CLI hoặc tách queue CLI | H-03 | Cao | Small | Chính sách CLI/desktop |
| Invalidate preview khi URL/file/history đổi; phân biệt auto-name; chờ resolution | H-09 | Cao | Small–Medium | Browser test |
| Thay Douzy prefix/newest-file matching bằng identity chính xác; chặn destination collision | H-01 | Cao | Medium | Xác nhận Douzy naming/schema |
| `start_douzy_sidecar()` phải cleanup mọi exception trước handoff | H-05 | Cao | Small | Fault-injection test |
| Thêm operation coordinator chung cho preview/workflow/prefetch/update và job-scoped cancel | H-04, H-06 | Cao | Large | AppState |
| Thêm update-install mutex/reservation; chặn job/preview mới trước update | H-06 | Cao | Medium | Operation coordinator |
| Thêm graceful shutdown coordinator | H-08 | Cao | Medium–Large | Native close policy |
| Thêm native ready/watchdog và controlled browser handoff | H-07 | Cao | Large | Browser lifecycle |
| Build portable EXE và smoke-test `--server` trong Windows CI | M-12 | Cao | Medium | Canonical package command |

### P1 — hardening chức năng, security và UX

| Hành động | Finding | Impact | Effort | Phụ thuộc |
|---|---|---:|---:|---|
| Quarantine queue/config hỏng; schema version/migration | M-02 | Trung bình-cao | Medium | Queue repository |
| Admission/history policy thay cho `jobs[-500:]` | M-03 | Trung bình | Small–Medium | Queue schema |
| Upload byte cap, streaming counter, free-space check, container validation, cache sweep | M-04 | Trung bình-cao | Medium | Storage policy |
| Chạy full validation trước move khi có thể; stage/commit Douzy move; verify DB update | M-05 | Cao | Medium | H-01 |
| Xóa/rename OCR output cũ; freshness check; Stop Gemini khi timeout | M-06 | Trung bình-cao | Medium | Provider tests |
| Chunk SRT dài theo block và retry theo chunk | M-06 | Cao cho video dài | Large | Persisted chunk state |
| Quản lý Edge port/PID/profile identity | M-07 | Trung bình-cao | Medium–Large | Edge lifecycle |
| Chỉ một browser server loop; cung cấp exit mechanism | M-08 | Trung bình | Small–Medium | Product policy |
| Sửa `Chay Tool.bat` kiểm `webview` hoặc sync requirements | M-08 | Trung bình | Small | — |
| Post-update readiness handshake; giữ `.old` tới khi app khỏe | M-09 | Cao | Medium | H-07 |
| Tắt automatic updater cho onedir hoặc ghi package mode | M-09 | Trung bình | Small | Package contract |
| Per-launch API token, Host/Origin/Fetch-Metadata checks, JSON-only mutation | M-10 | Trung bình-cao | Medium | Frontend bootstrap |
| Authenticode/pinned signer hoặc signed manifest | M-11 | Cao | Medium kỹ thuật, cao vận hành | Key management |
| Durable rotating structured logs và helper status file | M-14 | Trung bình-cao | Medium | Privacy policy |
| AbortController, visible stale-connection state, polling retry UX | M-14, M-20 | Trung bình | Small–Medium | Browser tests |
| Test đủ route operations và standardized error envelope | M-13 | Trung bình | Medium | API policy |
| Source-aware health và capability probe | M-17, M-20 | Trung bình | Medium | Provider adapters |
| Accessibility fixes: live status, focus, contrast, error semantics, reduced motion | M-19 | Trung bình | Medium | Browser/Narrator tests |
| Tách “clear success” và “remove failed/cancelled”, thêm confirmation | M-20 | Trung bình | Small | — |

### P2 — kiến trúc và hiệu năng

| Hành động | Finding | Impact | Effort | Phụ thuộc |
|---|---|---:|---:|---|
| Tạo `AppContext/AppState` và Flask app factory | M-01, M-16 | Cao dài hạn | Large | P0 state stabilization |
| Tách validators, storage, queue, providers, update, routes, launchers | M-16, M-17 | Cao dài hạn | Large, incremental | AppContext |
| Dataclass/schema version cho settings/job/progress/project | M-16 | Trung bình-cao | Medium | Static checking/tests |
| Tách CSS/JS thành static modules; browser contract tests | M-12, M-16 | Trung bình-cao | Medium | Packaging datas |
| Queue revision/delta và incremental DOM updates | M-15 | Trung bình | Medium | Canonical queue model |
| Bound/drain sidecar stdout | M-15 | Trung bình | Small | Sidecar logging test |
| Relative project paths và rebase moved projects | M-04/M-16 | Trung bình | Medium–Large | Manifest migration |
| Tag-driven release workflow, exact Python x64, dependency hashes, build manifest | M-18 | Cao | Medium | Release permissions |
| Pin action SHAs và thêm SBOM/dependency audit | M-18 | Trung bình | Small–Medium | Release CI |
| Hoàn chỉnh API docs hoặc ghi rõ internal/selected | M-13 | Thấp-trung bình | Small | API ownership |
| Sửa README Douzy/launcher/privacy ở cả hai ngôn ngữ | M-05, M-08, L-04 | Trung bình | Small | Final behavior |

### P3 — polish và defense-in-depth

| Hành động | Finding/unknown | Impact | Effort |
|---|---|---:|---:|
| Thay remaining dynamic `innerHTML`; thêm CSP/frame protection | L-01 | Thấp | Medium |
| Resolve executable paths rõ ràng và hiển thị trong diagnostics | L-02 | Thấp | Small |
| Hợp nhất onedir build definition; bỏ redundant pip install | L-03 | Thấp | Small |
| Chuẩn hóa privacy/outbound-data table | L-04 | Thấp-trung bình | Small |
| Một nguồn version duy nhất và Windows file metadata | Release hygiene | Thấp-trung bình | Small–Medium |
| Tối ưu `collect_all('webview')` sau packaged smoke | Performance | Thấp | Medium |
| `ASSETS.md` cho avatar nếu provenance đã biết | Open question | Thấp | Small |
| Chuẩn hóa tiếng Việt/English fragments và tăng text size | UX polish | Thấp | Small |

---

## 23. Những điều chưa biết và kiểm thử động còn cần

### 23.1. Startup/native

1. v1.5.2 có khởi động trên đúng cấu hình Windows/WebView2 từng treo ở v1.5.1 không?
2. Thời gian từ process start tới Flask ready, native loaded và UI interactive là bao nhiêu?
3. WebView2 missing/corrupt tạo exception, hang hay native dialog?
4. Browser fallback packaged có thể dừng bằng cách nào?

### 23.2. Shutdown/process

5. Đóng native window giữa Douzy/VideOCR có để process con sống tiếp không?
6. Douzy/VideOCR có spawn descendant process không?
7. Product policy khi đóng app nên là:
   - cancel;
   - prompt;
   - minimize-to-tray;
   - tiếp tục nền?

### 23.3. Concurrency/multi-instance

8. Hai desktop instance hoặc desktop + CLI hiện được hỗ trợ hay phải chặn?
9. Test cần chạy hai process cùng ghi queue/config.
10. Test cần tạo preview và start queue đồng thời.
11. Test cancellation ngay sau sidecar `Popen`.
12. Test update và queue start đồng thời.

### 23.4. Provider

13. Phiên bản Douzy/VideOCR/Edge/WebView2/ffprobe nào được hỗ trợ?
14. Douzy có stable API/schema và cho phép move file rồi sửa DB không?
15. Douzy asset naming chính thức là gì?
16. Gemini Notebook có prompt/context/rate/retention limit nào?
17. Selector có khác theo locale/account?
18. Provider terms có cho phép kiểu automation này không?

### 23.5. Security

19. Browser thực tế có cho phép CSRF/rebinding vào port 5000 dưới PNA/mixed-content policy nào?
20. Loopback port 9222 có thể được local account khác truy cập trên Windows policy thực tế không?
21. GitHub branch protection, MFA, release approval và two-person review hiện tại là gì?
22. Release signing key management có khả thi không?

### 23.6. Filesystem/data

23. Max local MP4 được hỗ trợ là bao nhiêu?
24. Retention policy cho:
   - local cache;
   - failed projects;
   - queue history;
   - Edge profile;
   - stale update files?
25. Network shares, FAT/exFAT, removable drives và OneDrive Files On-Demand có được hỗ trợ?
26. Project có cần tiếp tục được app nhận diện sau khi move/rename không?
27. ACL thực tế của `%APPDATA%\VietSub Studio` và Edge profile là gì?

### 23.7. Build/release

28. Artifact chính thức được build bằng Python patch nào, UPX nào và command nào?
29. Onedir là sản phẩm end-user hay developer fallback?
30. Có manual release checklist ngoài repository không?
31. ZIP updater luôn bắt buộc chứa `VietSub Studio/VietSub Studio.exe` chứ?

### 23.8. Hiệu năng

32. p50/p95 của resolve, download, OCR, Gemini, move/copy?
33. RSS peak cho video dài?
34. Douzy stdout rate?
35. Queue size thực tế?
36. OneDrive I/O penalty?
37. Packaged one-file cold start?

### 23.9. Accessibility

38. Mục tiêu WCAG nào?
39. Có hỗ trợ Narrator/NVDA/forced-colors/high zoom không?
40. Crop overlay có chặn native video controls trong WebView2 thực tế không?

### 23.10. Dynamic test plan tối thiểu

| Test | Acceptance |
|---|---|
| Packaged `--server` smoke | EXE start, `/`, avatar, settings, health trả đúng |
| Mocked native launcher | `create_window`, bridge attach, shutdown success/failure |
| WebView affected-machine smoke | UI ready trong deadline hoặc browser fallback |
| Close during synthetic long job | Job về interrupted/error, process được dừng |
| Two-instance test | Instance thứ hai bị chặn hoặc state không mất |
| Disk-full fault injection | Worker không mắc `running`, API outcome rõ |
| Corrupt queue/config | File được quarantine, warning hiển thị |
| Douzy multi-file fixture | Không chọn sibling prefix/collision |
| Update concurrency | Chỉ một helper, không job mới bắt đầu |
| Update delayed-start failure | `.old` được giữ và restore |
| Browser test | stale preview, crop, polling, settings, clear confirmation |
| Accessibility test | focus, live status, error semantics, reduced motion |
| Long-SRT test | timeout dừng generation hoặc chunk retry sạch |

---

## 24. Scorecard cuối cùng

Các điểm dưới đây là **đánh giá chủ quan có giải thích**, không phải chỉ số chuẩn hóa. Chúng phản ánh chất lượng source hiện tại và coverage đã quan sát, không giả định các luồng runtime chưa test đều hoạt động hoặc đều hỏng.

| Chiều | Điểm /10 | Giải thích |
|---|---:|---|
| Product clarity | **8,5** | Mục tiêu rõ, workflow phù hợp nhu cầu thực, non-goals khá minh bạch. |
| Feature completeness | **8,0** | Preview, crop, queue, local MP4, retry, update, health và project đều có mặt. |
| Architecture | **5,5** | Local HTTP boundary và bridge tốt, nhưng quá nhiều trách nhiệm/global state trong một module. |
| Core workflow correctness | **6,5** | Validation và retry mạnh; còn lỗi stale preview, Douzy matching và output-quality gap. |
| Recovery/data integrity | **6,0** | Atomic replace và stage-aware retry tốt, nhưng persistence transaction và shutdown là điểm yếu lớn. |
| Concurrency/lifecycle | **4,5** | Intent tuần tự rõ, nhưng preview/update/prefetch/native-close ownership chưa an toàn. |
| Security/privacy | **6,0** | URL/path/subprocess posture tốt; local API, CDP và update trust cần hardening. |
| Performance | **6,5** | Có hard link, prefetch và caching; chưa benchmark, queue/polling và sidecar log chưa scale tốt. |
| Observability | **4,5** | UI có logs/health, nhưng packaged logs không bền và polling failures khó thấy. |
| Backend testing | **7,5** | 65 test nhanh, có real filesystem/SQLite/ZIP; logic backend được bao phủ đáng kể. |
| Integration/package testing | **3,5** | Không build/smoke EXE, WebView, browser DOM hoặc provider thật. |
| Build/release engineering | **4,5** | Có specs và updater cẩn thận, nhưng release thủ công, unsigned, không provenance và rollback health. |
| Maintainability | **5,0** | Nhiều pure seams tốt, nhưng monolith, raw dict và inline frontend làm change radius lớn. |
| UX | **7,0** | Workflow dễ hiểu và recovery tốt; async feedback, health gating và cleanup copy cần sửa. |
| Accessibility | **5,0** | Có semantic controls và numeric crop fallback, nhưng progress/live/focus/contrast còn thiếu. |
| Documentation | **7,5** | Rất đầy đủ và song ngữ; có drift quan trọng về Douzy, launcher và privacy wording. |

### Điểm tổng hợp chủ quan: **6,1/10**

### Diễn giải cuối

VietSub Studio có nền tảng sản phẩm tốt hơn mức thường thấy ở một repository 27 tệp:

- workflow có chiều sâu;
- recovery được xem là tính năng hạng nhất;
- output có integrity gate;
- test backend đủ nhanh để hỗ trợ refactor;
- tài liệu và privacy hygiene tốt.

Tuy nhiên, độ tin cậy của **logic hàm đã unit-test** đang cao hơn đáng kể độ tin cậy của **ứng dụng Windows đóng gói như một hệ thống hoàn chỉnh**. Khoảng cách này tập trung ở:

1. queue persistence;
2. operation ownership;
3. native startup/shutdown;
4. Douzy asset handling;
5. update lifecycle;
6. packaged CI;
7. frontend state correctness.

Ưu tiên hợp lý trước khi thêm tính năng mới là biến các boundary trên thành state machine có owner rõ ràng, có rollback hoặc terminal outcome rõ, có log bền vững và có kiểm thử ở đúng bề mặt phát hành.
