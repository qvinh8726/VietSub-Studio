# Auto Edit + CapCut TTS — hướng dẫn v2.0.2

> Auto Edit là một trong ba phân hệ Creator Workbench. Video đã render có thể chuyển sang [Publish Hub](<PUBLISH_HUB_GUIDE.md>) để chuẩn bị và xác nhận đăng; việc bàn giao không tự đăng nội dung.

## Cách dùng

1. Chạy VietSub như trước, giữ video nguồn và sub Việt sau khi hoàn tất. Nếu bật tự xoá video nguồn, cần khôi phục video trước khi edit.
2. Ở kết quả/lịch sử, bấm **Chuyển sang Auto Edit →**. Preview bên trái luôn là nguồn, hiệu ứng chỉ hiện trong output render riêng.
3. Vùng che kế thừa crop OCR. Kéo khung/góc trên preview, dùng phím mũi tên (Shift để đổi cỡ) hoặc nhập X/Y/rộng/cao %. Viền đen preview không nằm trong tọa độ nguồn. Crop editor riêng không thay crop OCR gốc.
4. Chọn boxblur, delogo hoặc thanh màu bo góc bán trong suốt; chỉnh độ mờ/đệm. Delogo là nội suy FFmpeg, không phải AI inpainting.
5. Chọn style ASS/font local/cỡ/màu/viền/bóng/nền, vị trí theo vùng che hoặc top/bottom. Banner cố định và logo PNG là tùy chọn. Subtitle/banner được escape, không đưa text vào lệnh shell.
6. Chọn giữ âm thanh gốc, mute hoặc instrumental (cần runtime/model riêng). **Tách audio** xuất MP3/WAV độc lập, không cần AI.
7. CapCut là provider mặc định; **Nạp danh sách giọng** chỉ đọc catalog tham chiếu, **Nghe thử** mới gửi câu đã nhập tới provider. Chọn tốc độ/cao độ, bật TTS khi render hoặc tạo track từ SRT riêng. Fallback Edge hiển thị cảnh báo đổi giọng, không giả là cùng giọng CapCut.
8. Bấm **Render video**. Theo dõi phase, %, fps và warning. Huỷ dừng tác vụ riêng của editor, không xóa source/output tốt cũ. Output tên duy nhất trong project và được xác minh FFprobe trước khi bàn giao.
9. **Chuyển sang Đăng đa nền tảng →** dùng video đã edit trong manifest. Bước này chưa đăng nội dung lên nền tảng.

## Core và runtime tùy chọn

FFmpeg và FFprobe phải có trong PATH cho Auto Edit. App probe NVENC bằng encode thật, fallback `libx264` khi không có GPU/driver. Original giữ khung nguồn (pad tối đa 1px cho kích thước lẻ); vertical dùng exact 9:16, cao 1280/1920. Chọn 720 được làm tròn thành **396×704** để exact 9:16 và hai chiều chẵn.

Mở **Thiết lập → Runtime Auto Edit** hoặc khối **Runtime** trong workspace:

- Chọn đường dẫn tuyệt đối tới Python runtime ngoài, ví dụ `C:\Python312\python.exe`. Portable EXE không đóng vai Python worker.
- Edge TTS: `python -m pip install edge-tts` trong đúng interpreter. Là provider online không chính thức; mạng/provider có thể lỗi.
- Tách giọng: `python -m pip install "audio-separator[cpu]"`; cài model ONNX và metadata tương thích theo license riêng; chọn thư mục và tên model. Mặc định offline, không tự download model. Model mặc định adapter là `UVR-MDX-NET-Inst_HQ_3.onnx` nhưng không được đóng gói.
- Nút sao chép chỉ sao chép lệnh, không chạy pip/cài tự động. Thiếu runtime/model chỉ ảnh hưởng chức năng liên quan, không chặn blur/ASS/mute/extract.
- Instrumental không bảo đảm giữ mọi SFX/BGM hoặc xoá mọi tiếng nói; nghe kiểm tra kết quả.

## CapCut: giao thức nội bộ và bằng chứng

Client dùng `requests.Session`/connection pool cho create/query common task, tái sử dụng TLS; tải artifact vẫn kiểm tra và pin DNS public. Speed/pitch xử lý FFmpeg cục bộ, không bịa tham số CapCut. Catalog giọng offline không chứng minh quyền sử dụng giọng. HTTP connect tối đa 8s, read 15s; toàn bộ tạo/poll/tải cho mỗi câu dùng chung deadline 20s, retry tối đa một lần. Poll tăng dần 0.15 → 0.25 → 0.4 → 0.8s; khi chờ HTTP/poll kiểm tra hủy mỗi 50ms. Nhật ký ghi HTTP status, mã lỗi/trạng thái và lý do fallback, không ghi token/nội dung phụ đề.

Ngày 2026-11-19 đã kiểm chứng online bằng **câu mẫu cố định “Xin chào.”**, giọng `BV421_vivn_streaming`, không dùng cookie/tài khoản người dùng: dịch vụ trả MP3 thật; luồng SRT alignment/ducking/render tạo MP4 đủ 5 giây. Có thể xem báo cáo live cục bộ ở [report](<../artifacts/phase2-live-hardened/report.json>). Đây không bảo đảm uptime, entitlement mọi giọng/region hoặc giao thức tương lai.

Upstream tự ghi thuật toán payload-sign chưa được chứng minh toàn bộ; client có chế độ RSA compatibility tương ứng route tham chiếu đã smoke. Bật mặc định ở application provider và có thể tắt trong Runtime; lớp CapCut low-level không tự bật signer. Không bypass login/captcha. Không log request text/header/task token, không tự đọc browser/CapCut database. Nếu cần auth riêng, operator có thể đặt `VIETSUB_CAPCUT_COOKIE`/`VIETSUB_CAPCUT_BEARER` trong môi trường, chỉ đọc vào memory, không nhúng key hoặc gửi auth sang CDN.

URL artifact bắt buộc HTTPS/443, không credentials/redirect; actual TLS connection kiểm tra DNS public rồi pin IP để chống rebind vào LAN/loopback, vẫn kiểm tra chứng chỉ/SNI theo hostname gốc.

### Nguồn đã đối chiếu

- [CapCut client pinned source](<https://raw.githubusercontent.com/K07VN/capcut-tts-api/7ab87fbd12cbafa60bea4a48903eddf42a939b07/capcut_tts_api/client.py>)
- [CapCut signer và ghi chú giới hạn](<https://raw.githubusercontent.com/K07VN/capcut-tts-api/7ab87fbd12cbafa60bea4a48903eddf42a939b07/capcut_tts_api/signer.py>)
- [CapCut voice catalog](<https://raw.githubusercontent.com/K07VN/capcut-tts-api/7ab87fbd12cbafa60bea4a48903eddf42a939b07/capcut_tts_api/Voice.json>)
- [License metadata MIT upstream](<https://raw.githubusercontent.com/K07VN/capcut-tts-api/7ab87fbd12cbafa60bea4a48903eddf42a939b07/pyproject.toml>)
- [Audio-separator API](<https://github.com/nomadkaraoke/python-audio-separator/blob/main/audio_separator/separator/separator.py>)

## Alignment, dữ liệu và giới hạn

- Audio gốc từng cue được lưu trong `.vietsub-tts-cache/raw-v1` của project theo SHA256(provider, voice ID, text chuẩn hóa, tốc độ tổng hợp gốc=1). Đổi pitch/tốc độ cục bộ, vị trí sub hoặc mask không gọi lại mạng; audio hiệu ứng có cache riêng. Metadata không ghi plaintext subtitle, nhưng audio vẫn chứa giọng đọc. Sau tác vụ thành công, dọn các entry hash do app sở hữu theo giới hạn 2048 entry / 512 MiB / 30 ngày; không xóa file lạ, symlink hoặc dữ liệu nguồn.
- CapCut mặc định 3 worker; Edge 5 worker, giảm số tác vụ gửi mới và giãn nhịp khi CapCut HTTP 429. Có thể đặt `editor_tts_capcut_workers` (1–6), `editor_tts_edge_workers` (1–8), `editor_tts_batch_size` (1–30, mặc định 25) trong cấu hình ứng dụng; xem `app_config.example.json`. Adapter tùy chỉnh chưa công bố thread-safety vẫn chạy tuần tự. Ghép audio theo thứ tự SRT, FFmpeg dùng filter script theo lô thay vì khởi tạo cho từng câu.
- Track WAV 24kHz mono đặt đúng timestamp; cue dài tăng tempo thêm tối đa 1.35× rồi trim có warning, không chồng câu sang cue sau. Ngắn thì pad silence. Pitch ±12 semitone là resample/tempo compensation, không giữ formant như neural voice editing.
- TTS SRT tối đa 1000 cue / 2 giờ / 200k ký tự tổng / 2000 ký tự mỗi cue. Preview tối đa 250 ký tự. Không gửi SRT online khi chưa bật/gọi TTS.
- Renderer giữ delay audio nguồn khi tách stem, xử lý rotation/display matrix/SAR trước mask; kết quả có dimension/time được probe lại.
- Lưu options/outputs/stage trong sidecar project. Khi mở lại, output cuối và reservation bị gián đoạn được nhận diện, không tự chạy lại billable TTS. Editor queue serial; task history trong memory bị giới hạn, manifest giữ kết quả lâu dài.
- **Cao độ & Tốc độ giọng đọc phụ đề:** thao tác mới trên UI ghi `tts_change_pitch`, chỉ đổi cao độ track TTS trước khi mix/ducking. BGM/âm thanh gốc giữ tông tự nhiên; tốc độ video 0.5–2× vẫn đổi thời lượng toàn timeline bằng `atempo`. Nút nghe thử ba giây dùng cue đầu/cache TTS, không tổng hợp cả bài. Các khóa manifest/API cũ `change_audio_pitch`/`pitch_semitones` vẫn đọc được: payload cũ thiếu `tts_change_pitch` giữ ngữ nghĩa cũ để tương thích; thao tác lưu/chạy từ UI mới chuyển sang phạm vi TTS.
- **Xuất gói CapCut Draft** hiện là thử nghiệm: ZIP chứa media đã chuẩn bị, draft JSON và hướng dẫn. Ở chế độ mới, cao độ được bake riêng trên giọng TTS trước khi trộn; không bật native tone-modify cho video/BGM trong draft. Tốc độ vẫn chỉnh trên timeline; semitone, phụ đề, che chữ, logo và bản trộn được kết xuất vào media. Payload legacy giữ hành vi cũ. Sao chép cả thư mục khi CapCut desktop đã đóng, relink media nếu đổi đường dẫn, và kiểm tra tương thích trên bản CapCut đang dùng. QA tự động kiểm tra JSON/ZIP, chưa xác nhận import thực tế vào CapCut desktop.
- Chưa có AI inpainting. Đăng và lên lịch đa nền tảng nằm trong Publish Hub; lịch cục bộ yêu cầu app/máy/mạng đang hoạt động.
