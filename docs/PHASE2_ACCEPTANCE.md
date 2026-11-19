# Nghiệm thu Phase 2 — Auto Edit + CapCut TTS

## Phạm vi hoàn thành

Nâng workspace Auto Edit từ staging Phase 1 thành chức năng chạy thật, trên baseline v1.8.0. Không triển khai account/upload Phase 3, không bump APP_VERSION/tag/push/release v2.0.0. Các thay đổi Phase 1 đang có được giữ nguyên, không stage file scratch của người dùng.

### Video / ASS / render

- Package `vietsub.editor` độc lập: options, filtergraph, subtitle ASS, renderer/process, editor queue, Flask API.
- Crop editor kế thừa OCR nguồn; kéo/resize/keyboard/numeric theo object-fit contain, không lấy viền đen hoặc tọa độ CSS làm pixel nguồn. Crop edit riêng không ghi đè crop OCR.
- Boxblur, delogo nội suy edge-safe, solid-bar bo góc bán trong suốt; blur strength/padding có giới hạn.
- SRT→ASS với presets standard/anime/social, ba local fonts, font size/color/outline/shadow/backcolour, vị trí theo crop/top/bottom; text/banner escape không filter injection.
- Banner cố định, PNG thật có kiểm tra header/chunk/CRC/kích thước, watermark corner/opacity; original hoặc exact 9:16 nền mờ. Height720 chuẩn hóa encoder-safe396×704;1280→720×1280;1920→1080×1920.
- Probe NVENC bằng encode thật, fallback libx264; live %, fps, cancel; output riêng tên duy nhất, kiểm tra media/duration/dimension trước khi công bố. Nguồn/SRT không overwrite.
- Xử lý display matrix rotation/reflection/SAR; audio timeline giữ offset nguồn. TTS/ducking pad nền đến hết video, không cắt nhạc sau cue cuối.

### Audio / TTS

- Extract MP3/WAV hoặc mute FFmpeg không cần AI.
- CapCut pure stdlib HTTP common-task, offline Vietnamese/multilanguage catalog, preview, per-cue synthesis; bounded HTTP/poll/download; khởi tạo/boot không gọi dịch vụ.
- Pluggable Edge fallback qua Python worker tùy chọn, cảnh báo provider/voice thay đổi; không import aiohttp/ONNX/torch trong tiến trình chính.
- Tốc độ0.5–2× và pitch±12 semitone xử lý cục bộ. SRT alignment chính xác ms, atempo fit thêm tối đa1.35×, pad silence, clip cue overlap/trim có cảnh báo; cache integrity SHA256 theo text/provider/voice/speed/pitch, không ghi plaintext text vào metadata.
- Adapter audio-separator/ONNX worker offline mặc định, cấu hình external Python/model dir/model file; không cài pip/model tự động. Chưa smoke inference bằng model thật; chất lượng giữ BGM/SFX phụ thuộc model, không bảo đảm tuyệt đối.

### API, state và an toàn

- Queue riêng tối đa20 pending, serial, không thay state machine VietSub. Task input được bảo vệ khỏi cleanup/delete/retry; mỗi project một task active.
- Durable reservation sau admission checks, manifest finish trước báo success, last-good asset/stage giữ khi lỗi/cancel. Terminal result/last render/interrupted task được khôi phục khi mở lại, không tự submit TTS billable.
- Ambiguous/lost POST response được recheck GET phục hồi task/cancel, không tự tạo duplicate. Bản nháp cục bộ versioned/validated tối đa10 project/256KiB + unload guard; raw numeric/crop lỗi không bị âm thầm thay bằng giá trị khác khi chuyển project.
- Output URL exact task endpoint/capability signature; token/origin/Host/CSP cũ giữ nguyên. CapCut artifact HTTPS public DNS được pin IP ở socket TLS để chống rebind; không forward auth header tới CDN, không redirect, không tự đọc cookie database.
- Optional runtime mất/hỏng không làm capabilities FFmpeg fail. Cache/workspace từ chối symlink/junction. Editor→Publish handoff cập nhật identity để recheck đúng job/video đã edit, không upload.

## Kiểm thử cuối

| Nhóm | Kết quả |
|---|---|
| Python unittest toàn bộ | 561 tests, 558 pass, 3 skip |
| Node frontend | 123/123 pass; gồm32 editor cases |
| 115 DOM IDs / defaults / fonts / palette / VFX | Giữ xanh; baseline fixture không thay |
| JS syntax + Python compileall/py_compile | Pass |
| Playwright Phase2 | 3/3 viewport1280×720,1440×900,1920×1080; real render/TTS-service synthetic tone/SRT track/mix/publish recheck;0 console/CSP/HTTP errors |
| Playwright Phase1 hồi quy | 3 viewport + stress pane/banner, không issue |
| Playwright VietSub cũ | 104 checks, fractional DPI125%/150%, không issue |
| Real FFmpeg integration | Mask/ASS/font/PNG/vertical edge cases, render/extract, rotation/reflection, audio delay, TTS ducking, cancellation/atomic preservation pass |
| Live CapCut | Anonymous fixed phrase “Xin chào.”,BV421_vivn_streaming; MP3 thật và cue alignment/ducked BGM/render MP4 đủ5s; pass |
| Portable EXE | PyInstaller build + isolated startup/asset/font/capabilities/catalog + real FFmpeg render100%; thiếu optional Edge/separator vẫn pass; 79,225,563 bytes, SHA256 `3e972295f7cc919a3483b53f3db7a00aa8c260b17fd882417603aaaff7a3a895` |

Ba Python skip: Chrome lifecycle opt-in, published-release-directory chưa phát hành, symlink thật thiếu Windows privilege. Nhánh từ chối symlink/junction có test mock không cần quyền. Live CapCut không chạy tự động trong CI; trong QA deterministic dùng tone có nhãn, không giả giọng CapCut.

## Bằng chứng

- [Hướng dẫn Auto Edit/runtime](<AUTO_EDIT_GUIDE.md>)
- [Plan triển khai](<PHASE2_IMPLEMENTATION.md>)
- [Python log](<../artifacts/phase2-python-final.utf8.txt>)
- [Node log](<../artifacts/phase2-node-final.utf8.txt>)
- [Playwright report](<../artifacts/phase2-qa/report.json>)
- [Ảnh UI/output1440×900](<../artifacts/phase2-qa/1440x900-rendered.png>)
- [Live CapCut report](<../artifacts/phase2-live-hardened/report.json>)
- [Video mẫu CapCut+ASS+ducking5s](<../artifacts/phase2-live-hardened/qa-source.edited.5da30c29cd.mp4>)
- [Portable smoke report](<../artifacts/phase2-portable-smoke.json>)

## Giới hạn minh bạch

- CapCut là API nội bộ, upstream ghi chữ ký payload chưa chứng minh ổn định toàn bộ. Route compatibility đã kiểm chứng live bằng câu mẫu, không hứa mọi giọng/region/tài khoản hay tương lai. Có thể tắt trong Runtime; auth nếu cần do operator cung cấp rõ ràng qua env, không nhúng hoặc scrape.
- Edge online và AI separation chưa được kiểm chứng live trên máy này vì optional package/model chưa cài. Core FFmpeg và CapCut vẫn chạy không cần các runtime đó.
- Máy smoke không có NVENC khả dụng; thực nghiệm CPU/x264 pass, nhánh NVENC detection/fallback có test. Không tuyên bố GPU render thực trên máy này.
- CapCut Draft exporter là tùy chọn mở rộng, chưa triển khai; không có nút giả.
- Chưa upload đa nền tảng, chưa commit/push/tag, chưa khẳng định GitHub Actions remote xanh. CI definition đã thêm compile/editor syntax/QA; chưa quan sát run remote của SHA mới.
- Portable build dùng Python3.14.3/PyInstaller6.21.0 trên máy dev; clean-machine release matrix/ZIP updater vẫn thuộc Phase4. EXE preview vẫn hiển thị1.8.0 để không phát hành sớm2.0.0.
