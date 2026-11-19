# Auto Edit + CapCut TTS — hướng dẫn Phase 2

> Bản source đang phát triển trên baseline v1.8.0. Phase 2 không đồng nghĩa đã phát hành v2.0.0; Publish Hub vẫn là bàn giao chuẩn bị, chưa upload.

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

Client stdlib triển khai create/query common task và tải audio; speed/pitch xử lý FFmpeg cục bộ, không bịa tham số CapCut. Catalog giọng offline không chứng minh quyền sử dụng giọng.

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

- Mỗi cue có cache theo SHA256 text/provider/voice/speed/pitch; metadata không ghi plaintext subtitle, audio cache vẫn chứa giọng đọc. Cache là dữ liệu project; không đồng bộ công khai nếu chứa nội dung riêng.
- Track WAV 24kHz mono đặt đúng timestamp; cue dài tăng tempo thêm tối đa 1.35× rồi trim có warning, không chồng câu sang cue sau. Ngắn thì pad silence. Pitch ±12 semitone là resample/tempo compensation, không giữ formant như neural voice editing.
- TTS SRT tối đa 1000 cue / 2 giờ / 200k ký tự tổng / 2000 ký tự mỗi cue. Preview tối đa 250 ký tự. Không gửi SRT online khi chưa bật/gọi TTS.
- Renderer giữ delay audio nguồn khi tách stem, xử lý rotation/display matrix/SAR trước mask; kết quả có dimension/time được probe lại.
- Lưu options/outputs/stage trong sidecar project. Khi mở lại, output cuối và reservation bị gián đoạn được nhận diện, không tự chạy lại billable TTS. Editor queue serial; task history trong memory bị giới hạn, manifest giữ kết quả lâu dài.
- Chưa có CapCut Draft exporter (mục mở rộng tùy chọn), chưa AI inpainting, chưa đăng/schedule nhiều nền tảng.
