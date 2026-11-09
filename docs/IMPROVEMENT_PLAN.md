# Phương án cải thiện VietSub Studio

Cập nhật: 09/11/2026, sau đợt sửa 15 vấn đề của bản phân tích (nhánh `fix/analysis-findings`).

## 1. Hiện trạng sau đợt sửa

| Hạng mục | Trước | Sau |
|---|---|---|
| Chia đoạn dịch | Chỉ chia khi video dài hơn 45 phút | Mọi video: tối đa 400 block / 24.000 ký tự mỗi request |
| Tải Douyin | Có thể lấy nhầm video khác khi thiếu ID | Bắt buộc khớp ID; redirect CDN phải là HTTPS công khai |
| Ngôn ngữ nguồn | Prompt luôn ghi "tiếng Trung" | Theo ngôn ngữ OCR của job; OCR tiếng Việt bỏ qua bước dịch |
| Prompt mặc định | Từ điển game Delta Force | Prompt chung; từ điển game là preset tuỳ chọn |
| Kiểm tra Edge | PowerShell 1–4 giây mỗi 5 giây | Một lần cho mỗi phiên Edge (cache theo DevTools ID) |
| Chạy tiếp khi lỗi | Chỉ đường API | Cả API và Gemini Notebook |
| MP4 trên máy | Upload qua HTTP, ghi đĩa 2 lần | Hộp thoại Windows, hard-link hoặc chép 1 lần |
| Phân loại lỗi | So khớp chuỗi thông báo | Exception có kiểu và HTTP status |
| Test | Ghi vào log thật, có thể chạm Edge thật | Thư mục dữ liệu tạm, chặn mạng thật |
| Kích thước `app.py` | 4.814 dòng | 4.578 dòng (tách `translation_api.py`, `updater.py`) |

Kiểm tra đã chạy: 144 test pass (pytest và unittest), Ruff pass với rule `E, W, F, B, I, UP`, landing page build pass.

**Chưa kiểm chứng:** chạy thật từ đầu đến cuối với VideOCR, Edge đã đăng nhập Google và API key; hộp thoại chọn MP4 trong cửa sổ pywebview; CI trên GitHub (nhánh chưa push).

Thang ước lượng công sức bên dưới: **S** ≤ 1 ngày, **M** 2–4 ngày, **L** 1–2 tuần.

## 2. Giai đoạn 0: kiểm chứng và phát hành (ưu tiên cao nhất)

| # | Việc | Công sức | Ghi chú |
|---|---|---|---|
| 0.1 | Chạy thử thật theo ma trận: link Douyin ngắn và dài, MP4 cùng ổ và khác ổ, Notebook và DeepSeek, OCR `ch`/`ja`/`vi`, huỷ giữa chừng rồi chạy lại | S | Đây là phần duy nhất unit test không thay được |
| 0.2 | Push `fix/analysis-findings`, mở PR để CI chạy Ruff, unittest và smoke test bản EXE | S | |
| 0.3 | Nâng version lên `1.6.0`: prompt mặc định và cách chia đoạn đã đổi hành vi | S | Sửa đồng thời `vietsub/constants.py` và `pyproject.toml` (đã có test bắt lệch) |
| 0.4 | Workflow release chạy khi push tag `v*` | M | CI hiện tạo `VietSub-Studio-Portable.zip`, còn updater cần đúng tên `VietSub-Studio-Portable-v{version}.zip` và file `.sha256.txt` chứa tên đó. Release đang làm tay nên dễ sai tên làm hỏng auto-update |

## 3. Giai đoạn 1: độ tin cậy của pipeline

| # | Việc | Công sức | Lý do |
|---|---|---|---|
| 1.1 | Mở cuộc chat Notebook mới cho mỗi job (hoặc mỗi đoạn) | M | Mọi đoạn đang dồn vào một hội thoại; ngữ cảnh phình dần với video dài. Cần thử selector nút "chat mới" trên Gemini thật trước khi viết |
| 1.2 | Retry từng đoạn Gemini khi quá thời gian 5 phút | S | Hiện một đoạn timeout là job lỗi; nhờ resume nên retry giờ rẻ |
| 1.3 | Khoá ghi `download_history.json` | S | Luồng prefetch và worker chính đều đọc–sửa–ghi file này mà không có lock, có thể mất bản ghi |
| 1.4 | Khoá dùng chung Edge giữa prefetch và bước dịch Notebook | M | Prefetch có thể mở tab CDP (giải mã link ngắn, lấy metadata) đúng lúc worker đang điều khiển Notebook |
| 1.5 | Cho phép đổi giới hạn chia đoạn theo provider | S | 400 block / 24.000 ký tự là ước lượng an toàn; model có output lớn có thể gửi ít request hơn |
| 1.6 | Báo cáo chất lượng bản dịch sau mỗi job: block còn chữ Hán, block trống, block dài bất thường | M | Giúp phát hiện lỗi dịch mà kiểm tra cấu trúc SRT không bắt được |
| 1.7 | Cho hàng đợi chỉ gồm job OCR tiếng Việt chạy khi chưa cấu hình provider dịch | S | Backend đã bỏ qua bước dịch, nhưng UI vẫn chặn nút chạy khi thiếu cấu hình dịch |

## 4. Giai đoạn 2: kiến trúc và chất lượng mã

| # | Việc | Công sức | Lý do |
|---|---|---|---|
| 2.1 | Tách tiếp `app.py` thành `vietsub/edge.py`, `gemini.py`, `videocr.py`, `projects.py`, `queue.py`, `web.py` | L | `app.py` vẫn gom điều phối, tự động hoá trình duyệt, OCR, file dự án, hàng đợi và route Flask |
| 2.2 | Gom trạng thái toàn cục (`progress_status`, `workflow_jobs`, lock, cancel event) vào một đối tượng `WorkflowState` truyền vào các module | L | Điều kiện để làm 2.1 mà không phải patch `app` trong test |
| 2.3 | Tách `tests/test_app.py` (3.345 dòng) theo module; patch đúng module sở hữu hàm | M | Làm song song với 2.1. Bộ chặn mạng trong test đã giúp patch sai chỗ lộ ra ngay |
| 2.4 | Thêm type hint và chạy pyright hoặc mypy cho `vietsub/` | M | Các module mới tách đã đủ nhỏ để kiểm tra kiểu |
| 2.5 | Chia `static/app.js` (837 dòng, nhiều dòng rất dài) thành ES module không cần build | M | Dễ đọc và review hơn |
| 2.6 | Smoke test giao diện bằng Playwright chạy với `--server` trong CI | M | Hiện chỉ có kiểm tra cú pháp JS và render template |

## 5. Giai đoạn 3: tính năng (tuỳ nhu cầu)

- Chọn ngôn ngữ đích khác tiếng Việt (prompt và tên file `.vi.srt` đang cố định).
- Quản lý từ điển thuật ngữ: nhiều preset, từ điển theo project, nhập/xuất file.
- Ký số bản EXE để giảm cảnh báo SmartScreen.
- Hỗ trợ thêm định dạng video ngoài MP4.

## 6. Rủi ro cần theo dõi

- **Cấu trúc trang Douyin** (`_ROUTER_DATA`, `RENDER_DATA`, API `aweme/detail`) có thể đổi bất cứ lúc nào và làm hỏng bộ tải.
- **Selector giao diện Gemini** dễ vỡ khi Google đổi UI.
- **Tên model DeepSeek:** `deepseek-chat` và `deepseek-reasoner` đã bị khai tử ngày 24/07/2026; nên theo dõi khi có đợt đổi tên tiếp.
- **Pháp lý:** tải video bỏ logo và phát lại kèm phụ đề phải tuân thủ điều khoản Douyin và luật bản quyền.

## 7. Checklist trước mỗi bản phát hành

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m compileall -q app.py automate.py vietsub
python -m unittest discover -s tests -v
.\build_portable.bat
```

Sau đó chạy ma trận thử thật ở mục 0.1, kiểm tra tên file ZIP và checksum khớp mẫu updater cần, rồi mới đăng release.
