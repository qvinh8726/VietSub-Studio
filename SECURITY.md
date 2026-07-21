# Security Policy / Chính sách bảo mật

## Supported version

Security fixes currently target the latest release only.

## Reporting a vulnerability

Do not publish credentials, Notebook URLs, private video links, cookies, browser-profile data, or other sensitive information in a public issue. Send a private report through GitHub's security advisory feature when available, or contact the maintainer through the profile linked on the repository.

Include the affected version, reproduction steps, impact, and a minimal proof of concept that does not contain real user data.

## Báo cáo lỗ hổng

Không đăng công khai mật khẩu, link Notebook riêng, link video riêng tư, cookie, dữ liệu profile trình duyệt hoặc thông tin nhạy cảm trong Issue. Hãy dùng tính năng báo cáo bảo mật riêng tư của GitHub khi có thể, hoặc liên hệ maintainer qua hồ sơ GitHub của repository.

Báo cáo nên có phiên bản bị ảnh hưởng, các bước tái hiện, mức độ tác động và ví dụ tối thiểu không chứa dữ liệu thật của người dùng.

## Data handling summary / Tóm tắt xử lý dữ liệu

- Video downloading is delegated to the locally installed Douzy application.
- OCR is delegated to the locally installed VideOCR CLI.
- Subtitle text is submitted to the user's Gemini Notebook through a local Edge browser session.
- VietSub Studio does not require a Gemini API key and does not bundle Google credentials.
- Packaged settings are stored locally under `%APPDATA%\VietSub Studio`.
