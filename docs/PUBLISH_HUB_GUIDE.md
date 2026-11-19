# Publish Hub — Multi-Platform Uploader v2.0.0

## Luồng sử dụng

1. Từ VietSub/Auto Edit, bàn giao project sang **Đăng đa nền tảng**. Hub ưu tiên MP4 đã edit; nếu dùng nguồn chưa edit, cần xác nhận chủ động. Có thể chọn MP4 khác trên máy: file chỉ được tải vào app cục bộ, chưa đăng.
2. Sửa title/description/hashtags, lưu vào manifest nếu là project. **Tạo bằng AI từ SRT** chỉ gửi khi bấm, dùng provider DeepSeek/API hoặc Gemini Notebook đã cấu hình; trả đề xuất để duyệt, không tự ghi đè metadata mới hơn hay tự đăng.
3. Tạo profile theo nền tảng/tên gợi nhớ. Mỗi UUID có persistent browser directory, config và storage_state riêng trong config/uploader. Không dùng browser cá nhân hoặc profile của VietSub.
4. **Đăng nhập thủ công** mở cửa sổ riêng, tự đăng nhập/scanQR/xác minh rồi bấm **Đã đăng nhập xong**. Giới hạn10 phút, có huỷ. **Kiểm tra phiên** yêu cầu bằng chứng UI đăng nhập, không coi có cookie là chắc chắn đăng nhập.
5. Chọn nhiều tài khoản đã sẵn sàng, chọn visibility nằm trong giao của capability các nền tảng. Public cần xác nhận rõ. Queue không tự chuyển private/draft thành public.
6. Chọn đăng khi tới lượt hoặc lịch cục bộ Asia/Saigon UTC+07. **App/máy/mạng phải hoạt động vào giờ đăng**; không phải lịch đăng native trên nền tảng.
7. Gửi hàng đợi, theo dõi từng child job. Một lỗi/xác minh không làm sập các nền tảng khác. Screenshot/log lỗi lưu cục bộ; ảnh có thể chứa nội dung trang/tài khoản, không chia sẻ công khai.
8. Sau bước submit cuối, nếu không có xác nhận, trạng thái **uncertain**: kiểm tra nội dung trên nền tảng trước khi xác nhận retry để tránh đăng trùng. Cancel không đảm bảo thu hồi video đã đăng.

## Ma trận chế độ và điều kiện

| Nền tảng | Public | Private | Draft | Điều kiện không tự đoán |
|---|---|---|---|---|
| TikTok | Có | Có | Có | Privacy được chọn/đọc lại; UI iframe fallback |
| YouTube Shorts/Video | Có | Có | Không explicit | Cần chọn audience/kênh mặc định trước trong Studio; đúng dạng Shorts do video/nền tảng quyết định |
| Facebook Page / Meta Business Suite | Có | Không | Có | Page numeric asset ID đã nhớ, chỉ destination Facebook, story sharing tắt; không hỗ trợ arbitrary personal profile |
| Douyin | Có | Có | Có | Quyền riêng tư xác nhận lại, xác minh do người dùng |
| Bilibili | Có | Không | Có | Public cần category/copyright/tags đã được người dùng chọn trước; browser flow độc lập |
| Xiaohongshu | Có | Có | Không explicit | Privacy đọc lại; không coi implicit autosave là draft thành công |

Nếu form, category, audience, identity hoặc privacy không nhận diện được, job dừng để xác minh; không click cưỡng bức hoặc nhận là thành công. Selectors browser có thể thay đổi theo ngôn ngữ/account/region; release không bảo đảm platform uptime hay quyền tài khoản.

## Tính an toàn / dữ liệu

- Chỉ queue khi người dùng gửi request rõ ràng; boot/status/probe không tự đăng. Tác vụ đã được xếp lịch là uỷ quyền persisted, được thực hiện khi app chạy lại và đến hạn; chỉ state queued/scheduled, không tự replay uncertain.
- Request ID ổn định ngăn duplicate khi mất phản hồi; payload khác cùng ID bị từ chối. Trước irreversible submit lưu checkpoint atomic; restart sau checkpoint không tự retry.
- Profile khóa theo UUID giữa thread/process; registry và state atomic. Xóa record tài khoản không xóa đệ quy browser data; cần tự sao lưu/xoá config theo chủ ý nếu muốn giải phóng dung lượng.
- Secrets/cookie/storage_state không nằm project manifest hoặc API public. Data directory nên ở tài khoản Windows riêng/ổ đĩa riêng tư, không commit/cloud-share profiles.
- Queue source path/file identity được chụp khi gửi; file đổi/di chuyển thì dừng, không upload một video khác. Cleanup VietSub/editor bảo vệ input của queued/running uploads.
- Session hết hạn/challenge/captcha không được giải hoặc bypass tự động. Login lại bằng nút thủ công rồi retry có kiểm tra.
- Không có video/tài khoản production nào được đăng trong quá trình QA. Tests mock browser chứng minh contract/isolated state, không phải bằng chứng platform đã chấp nhận nội dung live.

## Tham chiếu upstream / license

Kiến trúc profile/cookie và selector flows được đối chiếu [social-auto-upload pinned revision3847fff](<https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59>) ([MIT ©2023 dreammis](<https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/LICENSE>)). Không thêm toàn bộ upstream hay runtime auto-download vào portable. Bilibili upstream dùng biliup CLI; adapter browser trong app là implementation độc lập, mock-tested. Tuân thủ điều khoản nền tảng và chỉ đăng nội dung/tài khoản mà bạn có quyền sử dụng.
