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

- Videos are downloaded on the user's computer; Douzy is no longer used. Douyin and Kuaishou pages are opened in a dedicated Microsoft Edge profile (`%LOCALAPPDATA%\Microsoft\Edge\User Data Debug`) and the app reads the video address that the page itself receives; it does not re-implement the platforms' request signatures. Xiaohongshu, Bilibili, TikTok, and YouTube use the bundled yt-dlp. An old Douzy library database is only read, to reuse videos it already holds.
- Share links are resolved over HTTP, and every redirect must stay on the platform's own hosts. Profile, playlist, collection, and livestream links are rejected.
- Cookies are kept in a per-domain cookie jar, so a platform's cookies are only sent to that platform's hosts. The Edge profile cookies reach yt-dlp through a temporary file that is deleted after the download, and YouTube never receives them. A user-chosen `cookies.txt` is copied to a temporary file for yt-dlp, so the original is never modified.
- Optional per-platform Cookie headers are encrypted with Windows DPAPI, like API keys, and the settings endpoint only reports whether one is saved.
- OCR is delegated to the locally installed VideOCR CLI.
- Subtitle text is submitted either to the user's Gemini Notebook through a local Edge browser session or to the OpenAI-compatible API provider the user configured.
- Notebook mode needs no API key, and VietSub Studio does not bundle Google credentials. API keys are encrypted with Windows DPAPI and saved per provider and Base URL host; the API client refuses redirects, so a key is only sent to the host it was saved for.
- At startup the app asks `api.github.com` for the latest release. Release files are downloaded only when the user installs an update.
- Packaged settings are stored locally under `%APPDATA%\VietSub Studio`.
- The local Flask control plane binds to loopback and requires a fresh per-launch capability token for API requests; Host, Origin, Fetch Metadata, path, and mutation content-type checks are also enforced.
- In the desktop window, a local MP4 or MKV is picked through the native file dialog and read in place; its container header is checked first. In browser mode, MP4 uploads are capped at 8 GiB and must contain an MP4 `ftyp` container header before being admitted to managed cache.
- The self-updater verifies the official repository URL and mandatory release checksum, but community builds remain unsigned; a compromised publisher account is outside the protection offered by same-release hashes.
