# Phase 2 — Auto Edit + CapCut TTS: kế hoạch triển khai

## Phạm vi đã duyệt

Tiếp tục mã Phase 1 đang có (APP_VERSION vẫn 1.8.0). Triển khai nghiệp vụ Auto Edit thật; chưa account/upload Phase 3, chưa commit/push/release Phase 4. Giữ VietSub, 115 DOM IDs, API cũ, fonts/palette/Sakuga và portable không cần AI runtime.

## Ranh giới module

- `vietsub/editor/options.py`: schema/defaults/validation strict, không nhận filtergraph/command tùy ý từ HTTP.
- `filters.py`, `subtitles.py`: graph video thuần FFmpeg + ASS; normalized source crop, mask/padding, local font style, banner, watermark, reframe.
- `process.py`, `renderer.py`: bounded cancellable child process, probe media/NVENC encode thực, render atomic MP4, audio extract, TTS timeline/mix.
- `tts.py`, `capcut_tts.py`, `alignment.py`: provider protocol, HTTP theo giao thức upstream đã xác minh, Edge optional fallback, cache từng cue, atempo/align/ducking.
- `separation.py`, `runtime.py`: status/check/hướng dẫn runtime ngoài, audio-separator optional lazy/worker, không import ONNX/Demucs lúc boot và không tự cài package/model.
- `jobs.py`: queue editor riêng, background workers có progress/fps/cancel, bounded history, không đổi state machine VietSub. Mỗi project chỉ một task đang chạy, giữ file nguồn suốt task.
- `api.py`: Flask adapter dựa trên job ID đã biết; validate project/source/SRT, scoped output/media URL, options/save/tasks/voices/preview/watermark.
- `static/editor.js` và workspace Auto Edit: điều chỉnh crop độc lập trên preview, options thật, runtime/TTS test/render/extract/progress/cancel/output và bàn giao video edit sang Publish staging.

## Data / safety

- Manifest schema v1 vẫn giữ, thêm `editor_options`, `stages.editor` và outputs `assets.edited_video/tts_audio/extracted_audio`. Editor crop riêng không ghi đè crop OCR đã dùng của VietSub.
- Không xóa/ghi đè source/SRT. Output có ID riêng dưới project; file part chỉ rename sau FFprobe validation. Re-render không thay file cũ khi thất bại/cancel.
- Cleanup/retry VietSub bị chặn với project đang dùng bởi editor; không thay hợp đồng API cũ, chỉ lỗi conflict có giải thích.
- Tất cả API đổi state dùng token/origin cũ; watermark multipart chỉ endpoint được phép, kiểm tra PNG thực và kích thước. Không nhận FFmpeg filter/command/path input tùy ý.
- TTS gửi text ra provider chỉ khi người dùng bấm preview/TTS/render có bật giọng đọc; không gọi mạng lúc import/boot. Không log cookie/header/signature. Endpoint nội bộ có thể thay đổi hoặc cần phiên đăng nhập: báo rõ và fallback nếu được chọn, không báo giọng thành công giả.
- Giới hạn request, cue, text, thời gian/tài nguyên; voice listing có catalog/caching, missing optional runtime chỉ vô hiệu hóa chức năng liên quan.

## Trình tự

1. Đọc baseline và xác minh upstream CapCut, FFmpeg, audio-separator; giữ chú thích nguồn/license.
2. Build/test thuần graph/ASS và TTS/alignment song song; tích hợp renderer/queue/API trên contract đã chốt.
3. Thay staging Auto Edit bằng controls thật, giữ ID Phase 1 và handoff; Publish vẫn staging Phase 3.
4. Unit + mock HTTP + optional absent + queue/cancel + security tests; render video/audio tổng hợp thật với các mask/ASS/watermark/reframe, probe output.
5. Node syntax/tests, Python compile/unittest đầy đủ, Playwright 1280×720 / 1440×900 / 1920×1080, 115 IDs/CSP/console/no viewport overflow, old Phase1 regressions.
6. Review diff/safety/portable packaging dependencies; báo cáo rõ pass/skip, external service limitations, optional CapCut Draft defer nếu chưa đủ kiểm chứng tương thích.

## Tiêu chí nghiệm thu

Render MP4 thật qua giao diện, có tiến độ và hủy không mất output trước; crop nguồn và phụ đề chính xác theo dimension. TTS client có mock HTTP đúng giao thức và cue timing integration thật. Thiếu Edge/ONNX/audio-separator không ảnh hưởng blur/ASS/extract/mute. Không tuyên bố live CapCut service/AI separation/GPU hoạt động nếu chỉ kiểm tra mock hoặc máy không có runtime/GPU.
