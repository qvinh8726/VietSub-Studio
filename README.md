<p align="center">
  <img src="avatar.jpg" width="132" alt="VietSub Studio avatar">
</p>

<h1 align="center">VietSub Studio</h1>

<p align="center">
  An open-source Windows desktop workflow that turns Douyin videos or local MP4 files into timestamp-safe Vietnamese subtitle projects: preview the exact OCR region, queue multiple videos, extract text with VideOCR, translate through Gemini Notebook or a configured API, validate the SRT, and keep every intermediate result recoverable.<br>
  Công cụ desktop Windows mã nguồn mở biến video Douyin hoặc MP4 trên máy thành dự án phụ đề Việt giữ nguyên timestamp: xem/chỉnh chính xác vùng OCR, xếp nhiều video vào hàng đợi, nhận chữ bằng VideOCR, dịch qua Gemini Notebook hoặc API đã cấu hình, kiểm tra SRT và giữ lại file trung gian để có thể chạy tiếp khi gặp lỗi.
</p>

<p align="center">
  <a href="https://github.com/qvinh8726/VietSub-Studio/releases/latest">Download / Tải bản mới nhất</a>
  · <a href="#english">English</a>
  · <a href="#tiếng-việt">Tiếng Việt</a>
  · <a href="CONTRIBUTING.md">Contributing / Đóng góp</a>
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2010%2F11-1f6feb">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776ab">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-2ea44f">
  <img alt="Release" src="https://img.shields.io/github/v/release/qvinh8726/VietSub-Studio?display_name=tag">
</p>

> [!IMPORTANT]
> The portable EXE contains VietSub Studio, but it does **not** bundle Douzy, VideOCR, Microsoft Edge, a Google account, a Gemini Notebook, or an external API account. Those are external prerequisites depending on the selected source and translation provider.
>
> File EXE portable chứa VietSub Studio nhưng **không** đóng gói Douzy, VideOCR, Microsoft Edge, tài khoản Google, Gemini Notebook hoặc tài khoản API ngoài. Thành phần cần có tuỳ nguồn video và provider dịch đã chọn.
>
> Douyin videos are processed from the MP4 downloaded by Douzy, then that MP4 and its related assets are moved into the selected project folder after the Vietnamese SRT passes validation, and Douzy's history is updated to the new path. This keeps one full-size video copy while making every completed project self-contained. Local MP4 files are first copied into app-managed storage because browser uploads do not expose the original filesystem path safely, then hard-linked into the project on the same drive or copied as a fallback.
>
> Video Douyin được xử lý từ file MP4 do Douzy tải; sau khi SRT tiếng Việt vượt qua kiểm tra, app chuyển MP4 và các asset liên quan vào thư mục project đã chọn rồi cập nhật đường dẫn mới trong lịch sử Douzy. Nhờ vậy chỉ có một bản video dung lượng lớn nhưng mỗi project hoàn tất vẫn tự chứa đầy đủ file. MP4 chọn từ máy được chép vào vùng app quản lý vì trình duyệt không cung cấp đường dẫn file gốc an toàn, sau đó được hard-link vào project nếu cùng ổ đĩa hoặc fallback sang sao chép.

---

# English

## Table of contents

- [What is VietSub Studio?](#what-is-vietsub-studio)
- [Why this project exists](#why-this-project-exists)
- [Features](#features)
- [What the output looks like](#what-the-output-looks-like)
- [Requirements](#requirements)
- [Quick start with the portable EXE](#quick-start-with-the-portable-exe)
- [First-run setup](#first-run-setup)
- [Processing videos](#processing-videos)
- [Running from source](#running-from-source)
- [Command-line usage](#command-line-usage)
- [Building the Windows app](#building-the-windows-app)
- [How it works](#how-it-works)
- [Configuration and local data](#configuration-and-local-data)
- [Privacy and security](#privacy-and-security)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Frequently asked questions](#frequently-asked-questions)
- [Contributing](#contributing)

## What is VietSub Studio?

VietSub Studio is a Windows desktop orchestration tool for creators who need to turn burned-in subtitles inside long videos into editable Vietnamese SRT files. It is not a replacement for Douzy, VideOCR, Edge, Gemini, or a full video editor. Instead, it connects those existing tools into one repeatable workflow with preview, queueing, retry, validation, local project files, and update management.

The complete workflow is:

1. Accept a direct Douyin URL/share message or an MP4 selected from the user's machine.
2. Resolve short Douyin share links, reject mix/profile/playlist URLs, and identify the exact source video.
3. For Douyin, ask the installed Douzy sidecar to download the video or reuse an existing cache record. For a local MP4, store a managed upload copy and skip Douzy completely.
4. Stream the prepared video into the in-app preview and let the user drag or resize the OCR rectangle over the exact subtitle region.
5. Save the crop, OCR language, speed options, project name, source metadata, and video resolution as an independent queue job.
6. Run OCR and the selected translator sequentially for one active video. While that video is in OCR or translation, prefetch the next queued Douyin download without ever running two Douzy sidecars together.
7. Process the original Douzy MP4 in place, then move it and the related Douzy assets into the project folder only after translation succeeds.
8. Ask the installed VideOCR CLI to scan only the selected area and write the original-language `.raw.srt` file.
9. Connect to the selected translator: Playwright for Gemini Notebook or an OpenAI-compatible `/chat/completions` API for DeepSeek/OpenRouter/custom endpoints.
10. Put every translated block back on its source index and timecode when the translator renumbers or retimes it, then reject incomplete translations, missing or glued blocks, invalid timestamps, and output that is still Chinese.
11. Write the Vietnamese `.vi.srt`, optional Douyin thumbnail, and `project.json` manifest. A job is marked successful only when the video reference and both subtitle files pass validation.

The application runs as a Flask service bound only to `127.0.0.1` and is presented as a native desktop window through pywebview/WebView2. Queue data, settings, downloaded media, OCR output, and browser sessions stay on the user's computer. Subtitle text is sent to the translation provider you select. The other network connections (update checks, Douyin links and thumbnails, Douzy downloads) are listed under [Privacy and security](#privacy-and-security).

## Why this project exists

The project is designed around the parts of long-form subtitle work that become slow, repetitive, or unstable when performed manually:

- A two-hour video can make editing software sluggish long before subtitle translation is complete.
- Burned-in subtitles require a carefully selected OCR region; scanning the entire frame wastes time and increases recognition noise.
- Downloading or copying the same large source video more than once consumes unnecessary disk space. A 6 GB Douyin video should remain one 6 GB file, not become 12 GB before OCR even begins.
- Running multiple OCR or browser-automation jobs in parallel creates resource contention and makes failures harder to diagnose. VietSub Studio accepts multiple jobs but deliberately processes them one at a time.
- Gemini can return partial SRT, alter timestamps, or require a renewed Google session. The app validates the final structure and exposes a visible Edge fallback instead of silently reporting success.
- A failed translation should not force another download or OCR pass. Queue persistence and stage-aware retry reuse the prepared video and raw SRT whenever they are still valid.
- Open-source users need a clear installation and update path. The portable build includes an update banner, verified ZIP installation, automatic restart, and a manual GitHub fallback.

## Features

### Video input and source handling

- Accepts direct Douyin video URLs, `v.douyin.com` short links, and complete pasted share messages.
- Extracts URLs automatically and validates the real hostname to reject lookalike or malicious domains.
- Rejects mix, user-profile, and playlist-style sources because every queue item must represent one exact video.
- Accepts MP4 files from the local machine without requiring Douzy.
- Reuses the Douzy history/cache when the requested Douyin video is already available.
- Reads Douzy's recorded title, video path, metadata, and optional cover image.
- Moves the original Douzy MP4, cover, metadata, music, and other per-video assets into the completed project folder without leaving a second full-size copy, then updates Douzy's history to the new path.
- Keeps waiting while Douzy's download status keeps changing. A download fails only after 5 minutes without any change, or after 6 hours in total.
- Copies a local MP4 into managed app storage so it remains available to the persisted queue.
- Hard-links the managed local MP4 into the completed project on the same drive, with a full-copy fallback across drives or unsupported filesystems.

### OCR preview and recognition

- Streams a protected local video preview inside the app before starting OCR.
- Provides a visible, draggable, and resizable crop rectangle for the burned-in subtitle area.
- Stores crop coordinates per queue item and can reuse a saved default crop.
- Supports Chinese, English, Japanese, Korean, and Vietnamese OCR modes.
- Lets users choose the fast mobile OCR model, the accurate server model, or the existing VideOCR configuration, plus an optional frame-skip interval.
- Uses FFprobe when available to convert preview coordinates accurately to the source resolution.
- Scans long videos in 10-minute chunks, merges the raw SRT with original timestamps, and uses FFmpeg to normalize mixed-resolution segments that crash VideOCR.
- Gives each VideOCR run a time limit of three times the scanned length plus 10 minutes, at least 30 minutes, or 4 hours when the length is unknown.
- Calls the locally installed VideOCR CLI and writes raw OCR output to a dedicated `.raw.srt` file.

### Sequential queue and recovery

- Lets users prepare multiple Douyin and local MP4 jobs before processing starts.
- Uses one sequential OCR/Gemini worker and prefetches at most one future Douyin download after the active job leaves its download step.
- Persists queued, running, successful, failed, and cancelled job metadata across app restarts.
- Supports removing waiting jobs, cancelling the active job, and clearing finished jobs.
- Stops VideOCR, FFmpeg, and Douzy together with their child processes when a job is cancelled or the app closes. The worker does not start another job while the app is closing.
- Retries from the earliest reusable stage: video preparation, OCR, or translation.
- Keeps incomplete project folders accessible without presenting them as successful results.
- Restores progress and incremental logs after a UI reload.

### Translation providers and Edge automation

- Supports Gemini Notebook through Edge, direct DeepSeek API, OpenRouter, and other OpenAI-compatible chat APIs.
- Uses the user's own Gemini Notebook URL and Google session when Notebook mode is selected.
- Encrypts API keys with Windows DPAPI and saves a separate key for each provider and Base URL host, so switching providers never sends one service's key to another.
- Ships neutral default translation instructions for any kind of video. The **Game Delta Force** preset adds that game's proper-noun glossary and pronoun rules. Instructions you saved in an earlier version are kept.
- Tells the model the job's real OCR language; Vietnamese OCR text is proofread instead of translated.
- Connects through Playwright to a dedicated Edge profile exposed only on `127.0.0.1:9222`.
- Starts Edge minimized by default while disabling background throttling that could stall automation.
- Waits for the actual Notebook chat interface before deciding that login is required.
- Shows Edge only when Google authentication expires or the Notebook needs manual inspection.
- Provides an explicit **Open Edge login** action for failed jobs.
- Splits any subtitle file with more than 400 blocks or 24,000 characters into timestamp-preserving chunks, whatever the video length. Videos longer than 45 minutes are also split every 30 minutes. With an API provider, each validated chunk is saved so a retry continues from the first unfinished chunk.
- Cancels active Gemini generation when the workflow is stopped.
- Interrupts an API request at once on **Cancel job** or app shutdown and gives each attempt a 30-minute deadline. A request is tried up to 3 times when the connection drops or the provider returns HTTP 429 or 5xx. Redirects are refused, so the API key never goes to another host.

### Subtitle integrity and result management

- Preserves all source timestamps and subtitle block indexes.
- Restores the source indexes and timecodes when the translator keeps the block count but renumbers or retimes blocks.
- Rejects empty output, partial SRT, changed block counts, glued blocks, output that is still Chinese or bilingual, and missing translated files.
- Reports success only after the source video reference, raw SRT, and validated Vietnamese SRT all exist.
- Uses safe Windows project names, including reserved-name handling and length limits.
- Adds duplicate suffixes such as `(2)` and `(3)` without overwriting an existing project.
- Writes configuration, queue state, SRT output, and manifests atomically to reduce partially written files.
- Exports a Douyin thumbnail as JPG, PNG, or WebP when a local cover or valid saved metadata URL is available.
- Records source URL, video ID, source path, output paths, status, error, and update time in `project.json`.

### Desktop experience and diagnostics

- Provides a responsive pywebview desktop interface backed by a local Flask API.
- Includes a dependency health panel for Douzy, VideOCR, FFmpeg/FFprobe, Notebook settings, and Edge.
- Shows recent Douzy history and can reload a previous video quickly.
- Supports a native Windows folder picker and a configurable output directory independent from Douzy.
- Warns when the effective project output directory is inside OneDrive, where large exports may be slowed by cloud synchronization.
- Creates or recreates a Desktop shortcut for the packaged EXE.
- Keeps the Edge window minimized during normal translation while retaining a reliable visible fallback.
- Ships as a portable one-file Windows EXE with a custom icon.

### Updates, openness, and safety

- Checks official GitHub Releases for newer versions and displays an in-app update banner.
- Downloads the official portable ZIP, validates the expected repository URL, ZIP SHA256 file, GitHub asset digest, declared size, and embedded EXE.
- Replaces the running packaged EXE and restarts automatically when in-place updating is supported.
- Falls back to the official GitHub Release page when automatic installation is unavailable.
- Keeps machine-specific settings, queue files, local videos, browser profiles, and credentials out of the repository.
- Uses the MIT License so the workflow can be inspected, modified, redistributed, and extended subject to third-party terms.

## What the output looks like

If the project name is `Product Review`, VietSub Studio creates:

```text
Product Review/
├── Product Review.mp4
├── Product Review.thumbnail.jpg (when available)
├── Product Review.raw.srt
├── Product Review.vi.srt
├── Product Review.data.json (when Douzy provides it)
├── Product Review.music.mp3 (when Douzy provides it)
└── project.json
```

- A completed Douyin project contains the original MP4 moved from Douzy. Local MP4 uploads use a same-drive hard link into the self-contained layout, with a normal copy as fallback.
- `Product Review.thumbnail.jpg`: the Douyin thumbnail copied from Douzy or downloaded from its saved metadata when available; PNG and WebP are also supported.
- `Product Review.raw.srt`: OCR output in the selected source language.
- `Product Review.vi.srt`: translated Vietnamese subtitle with source timestamps preserved.
- Extra Douzy assets use synchronized names such as `Product Review.data.json` and `Product Review.music.mp3`.
- `project.json`: project name, source URL, Douyin video ID, paths, status, error information, and update timestamp.

When a project folder already exists, the next project becomes `Product Review (2)`. The video, subtitles, thumbnail, Douzy assets, and manifest all use the unique project name.

## Requirements

| Component | Required | Expected location or behavior | Why it is needed |
|---|---:|---|---|
| Windows 10/11 x64 | Yes | Desktop Windows session | The current integrations and packaged build are Windows-specific. |
| Microsoft Edge | Notebook mode or short Douyin links | Normal Edge installation | Gemini automation and short-link redirect resolution. |
| Douzy | Douyin only | `%LOCALAPPDATA%\Programs\Douzy` | Downloads Douyin videos and maintains the local history/database. |
| Douzy configuration | Douyin only | `%APPDATA%\douyin-downloader-desktop\config.yml` | Provides the Douyin download directory and sidecar configuration. |
| VideOCR | Yes | `C:\Program Files\VideOCR\videocr-cli.exe` | Extracts subtitles from the video. |
| Gemini Notebook | Notebook mode | `https://gemini.google.com/notebook/<id>` | Translates the SRT text into Vietnamese through the browser. |
| Translation API account | API mode | DeepSeek, OpenRouter, or another OpenAI-compatible provider | Translates SRT text without Notebook/Edge automation. |
| Google login | Notebook mode | Dedicated Edge debug profile | Grants access to the selected Gemini Notebook. |
| FFmpeg / FFprobe | No | `ffmpeg` and `ffprobe` available in `PATH` | Enables long-video chunking metadata and automatic recovery from mixed-resolution VideOCR crashes. |
| Python 3.11+ | Source only | `python` available in `PATH` | Required only when running or building from source. |

Douzy and VideOCR are third-party applications and are not redistributed by this repository. Install them from sources you trust and follow their own licenses and documentation.

## Quick start with the portable EXE

1. Open [GitHub Releases](https://github.com/qvinh8726/VietSub-Studio/releases/latest).
2. Download `VietSub-Studio-Portable-v<version>.zip`.
3. Extract the ZIP into a normal writable folder. Do not run the EXE from inside the ZIP preview. The ZIP contains one `VietSub Studio` folder with `VietSub Studio.exe`, `SHA256.txt`, `LICENSE`, and `HUONG_DAN_SU_DUNG.txt`.
4. Open `VietSub Studio\VietSub Studio.exe`.
5. The packaged app creates a `VietSub Studio` shortcut on the Desktop after the first successful launch. Use **Settings → Recreate Desktop shortcut** if it is deleted or the portable folder is moved.
6. The first launch may take 20-60 seconds while the one-file package is extracted and scanned by Windows security software.
7. If SmartScreen appears because the community build is not code-signed, review the publisher/source and choose **More info → Run anyway** only if you trust the downloaded release.
8. Open **Settings** and complete every required item shown in red.

Each release publishes `VietSub-Studio-Portable-v<version>.zip.sha256.txt` with the ZIP checksum, and `SHA256.txt` inside the ZIP holds the EXE checksum. To compare them by hand, run `Get-FileHash -Algorithm SHA256 <file>` in PowerShell.

## First-run setup

### 1. Prepare Douzy

- Install Douzy in its default location.
- Open it at least once.
- Select a download directory.
- Download one video if the database has not been created yet.

The health panel distinguishes between the sidecar executable, configuration file, and optional history database.

### 2. Prepare VideOCR

- Install VideOCR so that `C:\Program Files\VideOCR\videocr-cli.exe` exists.
- Configure and save a crop area in VideOCR if subtitles occupy only part of the video.
- Install FFmpeg/FFprobe and add both commands to `PATH`; this is strongly recommended for long or concatenated videos whose resolution may change midway.

### 3. Choose a translation provider

- For Notebook mode, create or select a Gemini Notebook that you are allowed to access.
- Copy its full URL in this exact form:

  ```text
  https://gemini.google.com/notebook/YOUR-NOTEBOOK-ID
  ```

- Paste it into **Settings → Gemini Notebook used for translation**.
- Keep **Run Edge in background** enabled if desired. The full browser still renders while minimized.
- If the Google session expires or the Notebook needs inspection, VietSub Studio restores Edge and shows a retry action after login.
- For API mode, select **DeepSeek API**, **OpenRouter**, or **API OpenAI-compatible khác**, then enter the Base URL, model, and API key. Use **Test API connection** before starting a queue.
- Each provider and Base URL host keeps its own key. After switching provider, enter the key for the new provider; the previous key stays saved for its own provider.
- The translation instructions box (**Cách dịch mong muốn**) starts with neutral instructions that suit any video. For Delta Force videos, pick **Game Delta Force** under **Mẫu có sẵn** and click **Nạp mẫu**. **Khôi phục mặc định** brings the neutral instructions back.

VietSub Studio does not ask for your Google password and does not store it. Authentication remains inside the Edge profile managed by Edge.

### 4. Choose an output directory

Use **Choose folder** in Settings to select any destination on the machine. The setting is independent from Douzy. When it is empty, projects are created in `Documents\VietSub Studio` (or the Windows fallback directory if Documents cannot be resolved).

## Processing videos

1. Either choose an MP4 from the machine or paste one of the following into the main input:
   - `https://www.douyin.com/video/<id>`
   - `https://v.douyin.com/<code>/`
   - the complete share message containing either URL
2. Enter an optional shared project name. Leave it empty to use the source title or MP4 filename.
3. Click **Load preview** for Douyin, or **Choose MP4** for a local file.
4. Drag and resize the yellow OCR box over the subtitle region.
5. Select the source OCR language and click **Add to queue**.
6. Repeat for every video, then click **Run queue**.
7. Follow the active job through resolve, video preparation, OCR, translation, and completion.
8. Use **Cancel job** to stop the current item; queued items remain available.
9. Failed/cancelled jobs offer a retry button that reuses existing video or OCR subtitle files when possible.
10. The queue remains available after closing and reopening the app. Open completed project folders from their queue cards or the result panel.

Mix, user-profile, and playlist URLs are still rejected. The queue accepts separate video URLs and processes them sequentially rather than concurrently.

## Running from source

```powershell
git clone https://github.com/qvinh8726/VietSub-Studio.git
cd VietSub-Studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

Alternatively, double-click `Chay Tool.bat`; it checks the core Python dependencies and starts the desktop app.

To run the local browser version on port 5000:

```powershell
python app.py --server
```

Then open `http://127.0.0.1:5000`.

To let the app choose a free local port and open the default browser automatically (the same fallback used when native WebView startup fails), run:

```powershell
python app.py --browser
```

## Command-line usage

`automate.py` runs the same workflow without the graphical interface:

```powershell
python automate.py --url "https://www.douyin.com/video/123" --lang ch --name "Product Review"
```

Arguments:

| Argument | Required | Description |
|---|---:|---|
| `-u`, `--url` | Yes | Douyin video URL or short share URL. |
| `-l`, `--lang` | No | OCR language: `ch`, `en`, `ja`, `ko`, or `vi`. Default: `ch`. |
| `-n`, `--name` | No | Shared name for the project, video, and both subtitle files. |

The CLI still requires the same local Douzy/VideOCR setup and either Notebook/Edge or a configured translation API, depending on the selected provider.

The CLI takes the same single-instance lock as `python app.py` started from the same folder, so close that app first. The packaged EXE keeps its lock under `%APPDATA%\VietSub Studio`, so the CLI cannot detect it; do not run both at the same time, because they would control the same Douzy, VideOCR, and Edge. The CLI does not read or change the desktop queue. Output is UTF-8, so it can be redirected to a file. Exit code `0` means success and `1` means an error or another running instance.

## Building the Windows app

Install the build dependencies and create the portable one-file build:

```powershell
.\build_portable.bat
```

Equivalent manual command:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --distpath dist_portable --workpath build_portable "VietSub Studio Portable.spec"
python tools\make_release.py --clean
```

Output:

```text
dist_portable\VietSub Studio.exe
release\VietSub-Studio-Portable-v<version>.zip
release\VietSub-Studio-Portable-v<version>.zip.sha256.txt
```

`tools/make_release.py` packages the EXE in the layout the in-app updater expects. `build_portable.bat` also runs `tests/test_release_contract.py` against the `release` folder. Read [RELEASING.md](RELEASING.md) before publishing a release.

`build_app.bat` and `VietSub Studio.spec` provide an alternative onedir build. The portable spec bundles templates, the avatar assets, and pywebview resources. External applications and user credentials are never bundled.

## How it works

```mermaid
flowchart TD
    A[Douyin URL/share text or local MP4] --> B{Source type}
    B -->|Douyin| C[Resolve exact video ID]
    C --> D[Douzy download or cache reuse]
    D --> E[Reuse original Douzy MP4]
    D --> T[Local cover or metadata thumbnail]
    B -->|Local MP4| F[Managed local upload copy]
    E --> G[Protected in-app preview]
    F --> G
    G --> H[Edit OCR crop and language]
    H --> I[Persist job in sequential queue]
    I --> J[VideOCR creates raw SRT]
    J --> K[Notebook or OpenAI-compatible API translation]
    K --> L[Restore and validate timestamps and blocks]
    L --> M[Write Vietnamese SRT and project.json]
    T --> M
    M --> N[Expose result only after final validation]
```

The queue is intentionally sequential. A later job does not start until the current job finishes, fails, or is cancelled. This protects the shared VideOCR installation, Douzy sidecar, Edge debug profile, and Notebook conversation from concurrent control.

For a Douyin job, the original MP4 stays in Douzy while OCR and translation run, then the app moves that exact video and its identity-matched assets into the self-contained project after final validation. For a local upload, the managed MP4 is validated, kept in the app cache while queued, and hard-linked or copied into the project so the persisted job can safely resume.

Main implementation areas:

- `app.py`: validation, process orchestration, Flask API, pywebview desktop launcher, file handling, and integrations.
- `templates/index.html`: semantic HTML structure and server-rendered bootstrap metadata.
- `static/style.css` and `static/app.js`: responsive styling, frontend state, settings, diagnostics, progress, history, and result interactions.
- `automate.py`: command-line entry point.
- `tests/test_app.py`: validation, naming, download lookup, API, cancellation, and workflow tests.
- `tests/test_release_contract.py`: release asset names, ZIP layout, and checksum format expected by published updaters.
- `tests/frontend/app.test.mjs`: Node tests for request timeouts, the connection banner, and queue actions in `static/app.js`.
- `tools/make_release.py`: packages a built EXE into the two release files.
- `VietSub Studio Portable.spec`: one-file PyInstaller configuration.

Local API endpoints (an internal contract between the bundled UI and the local server; they require the per-launch token and can change in any release):

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Check Douzy, VideOCR, FFmpeg/FFprobe, Notebook, and Edge readiness. |
| `/api/translation/test` | POST | Send a minimal probe to the configured API provider. |
| `/api/settings` | GET/POST | Read and save local settings. |
| `/api/history` | GET | Read recent items from the local Douzy database. |
| `/api/previews` | POST | Prepare a video and return its protected preview URL. |
| `/api/previews/local` | POST | Copy a local MP4 into managed cache and create a preview. |
| `/api/queue` | GET/POST | List jobs or add a prepared video to the queue. |
| `/api/queue/start` | POST | Start the single sequential worker. |
| `/api/queue/<job_id>/retry` | POST | Retry from video preparation, OCR, or translation. |
| `/api/edge/show` | POST | Restore the dedicated Edge window for login or inspection. |
| `/api/update/install` | POST | Download, verify, install, and restart into the latest packaged release. |
| `/api/progress` | GET | Retrieve state and incremental logs. |
| `/api/cancel` | POST | Cancel the active workflow and child processes. |
| `/api/open-folder` | POST | Open only a path produced by the current workflow. |

The Flask server binds to `127.0.0.1`, not to the public network. Every control-plane API request uses a per-launch unguessable token; the server also validates local Host, Origin, Fetch Metadata, and state-changing content types. Preview video streaming uses an unguessable preview identifier because native video elements cannot attach the custom API header.

## Configuration and local data

| Data | Source mode | Packaged EXE |
|---|---|---|
| VietSub Studio settings | `app_config.json` beside `app.py` | `%APPDATA%\VietSub Studio\app_config.json` |
| Persistent queue | `workflow_queue.json` beside `app.py` | `%APPDATA%\VietSub Studio\workflow_queue.json` |
| Managed local MP4 cache | `local_videos` beside `app.py` | `%APPDATA%\VietSub Studio\local_videos` |
| Dedicated Edge profile | `%LOCALAPPDATA%\Microsoft\Edge\User Data Debug` | Same |
| Douzy config | `%APPDATA%\douyin-downloader-desktop\config.yml` | Same |
| Douzy history database | `%APPDATA%\douyin-downloader-desktop\dy_downloader.db` | Same |
| Project output | Selected output directory or `Documents\VietSub Studio` | Same |
| Durable app log | `vietsub-studio.log` beside `app.py` | `%APPDATA%\VietSub Studio\vietsub-studio.log` |
| Single-instance lock | `instance.lock` beside `app.py` | `%APPDATA%\VietSub Studio\instance.lock` |

`app_config.json` is ignored by Git because it may contain a private Notebook URL. Use `app_config.example.json` only as a safe reference.

Set the `VIETSUB_STUDIO_CONFIG_DIR` environment variable to keep the settings, queue, managed local MP4 cache, log, and lock in another folder. The unit tests use it so they never touch real user data.

If `app_config.json` or `workflow_queue.json` contains invalid JSON, the app renames it to `<name>.corrupt-<time>-<id>` and starts with defaults or an empty queue. A file that is only locked for a moment (antivirus, backup, OneDrive) is retried and never renamed. If the queue still cannot be read, the app keeps that session's queue in memory only and leaves the file on disk untouched; close the program holding the file and reopen the app.

## Privacy and security

- Video download and OCR run through locally installed applications.
- The video itself is not uploaded to this repository or to a VietSub Studio server.
- Selected local MP4 files are limited to 8 GiB, checked for an MP4 `ftyp` container header, and kept in the app's managed local cache so queued jobs survive a restart. While a file is being added, it also passes through a temporary file in `%TEMP%`, which is deleted when the upload finishes.
- Subtitle text is submitted either to the user's Gemini Notebook through the local Edge session or to the configured DeepSeek/OpenRouter/OpenAI-compatible API. Review the selected provider's terms and privacy policy before processing sensitive content.
- Network connections, apart from the `127.0.0.1` connections to the app itself, the Douzy sidecar, and the Edge debug port:
  - GitHub: `api.github.com` is asked for the latest release when the app starts, with a `VietSub-Studio/<version>` User-Agent. Release files are downloaded from GitHub only when you choose to install an update.
  - Translation: Gemini through the dedicated Edge profile, or the API Base URL you entered, with your key. OpenRouter also receives this repository's URL as `HTTP-Referer` and `VietSub Studio` as `X-Title`.
  - Douyin: Edge opens `v.douyin.com` short links to find the video ID. When Douzy has no local cover, the thumbnail is downloaded over HTTPS from `douyinpic.com` or `douyincdn.com` hosts found in Douzy's metadata.
  - Douzy downloads the video from Douyin itself. VietSub Studio only sends it the video URL over `127.0.0.1`.
- API keys are encrypted with Windows DPAPI, saved separately for each provider and Base URL host, and never returned by the local settings endpoint.
- Google credentials remain managed by Edge; VietSub Studio does not read or store the password.
- A random local bearer token is generated for each Douzy sidecar session.
- Settings and output files are written atomically to reduce corruption risk.
- The local API uses a fresh per-launch capability token and validates Host, Origin, Fetch Metadata, paths, languages, filenames, upload type/size, and result-folder access.
- Never share `app_config.json`, Edge profile data, cookies, private Notebook URLs, or logs containing private video links.

See [SECURITY.md](SECURITY.md) before reporting a security issue.

## Known limitations

- Windows only in the current release.
- Queue items run sequentially; parallel OCR/translation is intentionally not supported.
- Playlist, profile, and mix URLs are not expanded automatically.
- Requires VideOCR; Douzy is additionally required when the source is Douyin.
- Requires either an authenticated Gemini Notebook session in Edge or a working translation API configuration.
- Gemini UI changes may occasionally break Notebook browser selectors until the project is updated.
- OCR quality depends on the video, subtitle contrast, language model, and saved crop region.
- FFmpeg/FFprobe are optional for short constant-resolution videos, but long-video crash recovery is unavailable without them.
- OCR results of the 10-minute chunks are not saved yet, so a failed OCR run starts that video's OCR again from the beginning.
- Gemini Notebook translation does not resume from the last finished chunk; only API providers save per-chunk progress.
- Adding a local MP4 temporarily needs free space for two copies, because the upload passes through `%TEMP%` before it reaches the managed cache.
- The community EXE is not code-signed, so SmartScreen may warn on first launch.
- The one-file EXE may start slowly because it extracts bundled files on each launch.
- Download and translation behavior must comply with Douyin, Google, Douzy, VideOCR, and local copyright rules.

## Troubleshooting

### The Settings tab shows “Douzy sidecar: Missing”

Install Douzy in its default location. The current integration expects:

```text
%LOCALAPPDATA%\Programs\Douzy\resources\sidecar\win32-x64\douyin-dl-sidecar.exe
```

Custom Douzy installation paths are not currently configurable.

### “Douzy configuration: Missing”

Open Douzy, select a download folder, close it normally, and click **Check again** in VietSub Studio.

### “Douzy database” is yellow

The database is optional for initial readiness. It normally appears after Douzy processes its first video. History and cache lookup improve once it exists.

### “VideOCR CLI: Missing”

Confirm this file exists:

```text
C:\Program Files\VideOCR\videocr-cli.exe
```

Reinstall VideOCR to its default location if necessary.

### “Gemini Notebook: Missing”

Paste a valid HTTPS URL matching `https://gemini.google.com/notebook/<id>` into Settings. A normal Gemini chat URL is not accepted.

### Edge opens but translation does not start

Use **Open Edge to sign in**, authenticate with Google, confirm the Notebook opens, then retry the failed job. Background mode minimizes Edge without using headless mode, so Gemini still receives a normally rendered page.

### Port 9222 is busy or Edge debug cannot connect

Close stale Edge debug sessions, then reopen VietSub Studio. Advanced users can inspect processes using port `9222`. Avoid terminating normal Edge sessions unless you know which process owns the debug profile.

### OCR subtitle is empty or inaccurate

Check that the selected language matches the video, configure a tighter crop in VideOCR, and test VideOCR directly with the same video. Low contrast, animated subtitles, watermarks, or very small text reduce OCR quality.

For long or concatenated videos, install FFmpeg/FFprobe. VietSub Studio scans them in chunks and automatically normalizes the remaining segments if VideOCR crashes after a resolution change.

### OCR stops because of the time limit

Each VideOCR run may take three times the length it scans plus 10 minutes, and at least 30 minutes. Without FFprobe the app cannot read the video length and allows 4 hours. On a slow CPU, choose the fast OCR model or skip frames in Settings, then retry the job. The last 20 lines of VideOCR output are written to `vietsub-studio.log`.

### The Douyin download times out

VietSub Studio waits as long as Douzy's download status keeps changing. It stops when the status has not changed for 5 minutes, or after 6 hours in total. Check the network and the download in Douzy itself, then retry the job.

### A red “Mất kết nối tới máy chủ cục bộ” banner appears at the top

The banner appears when the app's local server does not answer within 15 seconds (60 seconds for actions such as saving or adding to the queue). It disappears by itself when the server answers again. If it stays, close and reopen VietSub Studio; the queue is saved on disk.

### Translation changed timestamps

VietSub Studio validates every translated chunk. When the block count still matches, it puts each block back on the source index and timecode, even if the translator renumbered the chunk from 1 or rewrote the times. If blocks are missing, merged, or glued together without a blank line, the workflow stops instead of silently exporting a desynchronized file. A rejected chunk is not saved, so a retry translates it again.

### The downloaded video already exists

This is expected. VietSub Studio asks Douzy to reuse its cached record and finds the matching video ID. If the MP4 is still in the Douzy download folder, the app processes it there, moves it with its Douzy assets into the new project after the Vietnamese SRT passes validation, and updates Douzy's history to the new path. If an earlier project already holds that MP4, the new project gets its own copy and the earlier project is left as it is.

### Windows rejects a project name

Characters invalid on Windows are replaced, trailing spaces/dots are removed, reserved names such as `CON` are prefixed, and long names are limited. Duplicate projects receive a numeric suffix.

### The portable EXE appears to do nothing

Wait up to 60 seconds on the first launch. Extract it from the ZIP first, then check Windows Security quarantine/history. Verify its SHA256 against the release files before allowing it.

### Where are my results?

Open Settings to see or choose the output directory. When empty, the default is `Documents\VietSub Studio`. The result panel can open the exact self-contained project folder after success.

## Frequently asked questions

### Is the EXE completely standalone?

No. It bundles the Python application and UI, but VideOCR, Douzy (for Douyin sources), and a translator remain external requirements. The translator is either Edge with a Google login and a Gemini Notebook, or an account with an OpenAI-compatible API provider.

### Is VietSub Studio free and open source?

Yes, the code in this repository is released under the MIT License. Third-party tools, platforms, models, and assets keep their own licenses and terms.

### Does the app upload my video?

VietSub Studio does not upload the MP4 to its own server. Douzy downloads it locally and VideOCR processes it locally. Only the SRT text is sent for translation, either to your Gemini Notebook through your Edge session or to the API provider you configured.

### Does the maintainer receive my Notebook URL or Google account?

No. Settings remain on your machine unless you deliberately share them. The public repository ignores `app_config.json`.

### Can I use an API instead of Notebook?

Yes. In **Settings**, select DeepSeek API, OpenRouter, or another OpenAI-compatible API, enter the Base URL, model, and key, then click **Test API connection**. The app calls the provider's `/chat/completions` endpoint and stores the key with Windows DPAPI encryption, separately for each provider and Base URL host.

### Can it translate to languages other than Vietnamese?

The current translation prompt and `.vi.srt` convention target Vietnamese. OCR supports several source languages, but translated-output language selection is not yet implemented.

### Can it process TikTok, YouTube, Bilibili, or local files?

Local MP4 files are supported. TikTok, YouTube, Bilibili, playlists, profiles, and other remote sources are not currently supported.

### Can it process multiple videos automatically?

Yes. Prepare each video preview, add it to the queue, then start the worker. The app processes one item at a time to avoid Douzy, VideOCR, and Edge/Gemini conflicts.

### Can I move the portable EXE after configuring it?

Yes. Packaged settings live under `%APPDATA%\VietSub Studio`, so moving the EXE does not remove the configuration.

### Why does the EXE trigger SmartScreen?

The release is not signed with a commercial Windows code-signing certificate. Always download from the official GitHub Releases page and verify checksums.

From v1.3.0 onward, the packaged EXE can update itself in place. It accepts only assets from this repository's official GitHub Release URL, requires the published ZIP checksum, checks GitHub digest/size metadata when available, reserves the application so no new preview or queue job can start, replaces the executable with rollback protection, and keeps the old EXE until the new process remains alive through its startup window. If the portable folder is not writable or validation fails, it opens the official release page instead. The community build is still unsigned, so publisher authenticity ultimately depends on the GitHub release account until Authenticode or an independent signing key is added.

### Why does the first launch take so long?

PyInstaller's one-file mode extracts bundled resources to a temporary directory, and Windows security software may scan them. Later launches can still take several seconds.

### Can I sell a modified version?

The MIT License permits commercial use of this repository's code, but you remain responsible for third-party licenses, platform terms, branding, content rights, and all applicable laws.

### How should I report a bug?

Use the bug-report template and include the version, installation type, reproduction steps, health-panel state, and sanitized logs. Remove all credentials, private URLs, cookies, and personal paths.

## Contributing

Pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md), keep changes focused, and run:

```powershell
python -m py_compile app.py automate.py tools/make_release.py
python -m unittest discover -s tests -v
node --check static/app.js
node --test tests/frontend/app.test.mjs
```

The Node commands need Node.js 20 or newer. The repository includes a Windows GitHub Actions workflow that compiles the sources, runs the Python and frontend tests, builds the portable PyInstaller executable, starts the packaged `--server` mode, and verifies the UI and static assets. It then packages the two release files with `tools/make_release.py`, checks them against the updater contract, and uploads them as the `VietSub-Studio-Portable-v<version>-release-assets` artifact. Dependabot also tracks Python and GitHub Actions updates. Maintainers publish releases with [RELEASING.md](RELEASING.md).

---

# Tiếng Việt

## Mục lục

- [VietSub Studio là gì?](#vietsub-studio-là-gì)
- [Vì sao dự án này được tạo ra?](#vì-sao-dự-án-này-được-tạo-ra)
- [Tính năng](#tính-năng)
- [Cấu trúc kết quả](#cấu-trúc-kết-quả)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Dùng nhanh bản EXE portable](#dùng-nhanh-bản-exe-portable)
- [Thiết lập lần đầu](#thiết-lập-lần-đầu)
- [Xử lý nhiều video tuần tự](#xử-lý-nhiều-video-tuần-tự)
- [Chạy từ mã nguồn](#chạy-từ-mã-nguồn)
- [Dùng dòng lệnh](#dùng-dòng-lệnh)
- [Tự build ứng dụng Windows](#tự-build-ứng-dụng-windows)
- [Cách hệ thống hoạt động](#cách-hệ-thống-hoạt-động)
- [Cấu hình và dữ liệu cục bộ](#cấu-hình-và-dữ-liệu-cục-bộ)
- [Quyền riêng tư và bảo mật](#quyền-riêng-tư-và-bảo-mật)
- [Giới hạn hiện tại](#giới-hạn-hiện-tại)
- [Khắc phục sự cố](#khắc-phục-sự-cố)
- [Câu hỏi thường gặp](#câu-hỏi-thường-gặp)
- [Đóng góp](#đóng-góp)

## VietSub Studio là gì?

VietSub Studio là công cụ điều phối quy trình làm phụ đề trên Windows dành cho người cần biến chữ đã dính sẵn trong video dài thành file SRT tiếng Việt có thể chỉnh sửa. App không thay thế Douzy, VideOCR, Edge, Gemini hay phần mềm dựng video. Nó nối các công cụ đó thành một luồng thống nhất có preview, hàng đợi, chạy lại theo từng bước, kiểm tra kết quả, lưu file dự án và cập nhật ứng dụng.

Toàn bộ quy trình gồm:

1. Nhận link/đoạn share Douyin hoặc file MP4 người dùng chọn trên máy.
2. Giải mã link share ngắn, từ chối link mix/profile/playlist và xác định đúng một video nguồn.
3. Với Douyin, yêu cầu sidecar Douzy đã cài trên máy tải video hoặc dùng lại bản ghi cache. Với MP4 local, app lưu một bản upload do app quản lý và bỏ qua Douzy hoàn toàn.
4. Phát video đã chuẩn bị trong khung preview của app để người dùng kéo/đổi kích thước vùng OCR đúng vị trí phụ đề.
5. Lưu crop, ngôn ngữ OCR, tuỳ chọn tốc độ, tên dự án, metadata nguồn và độ phân giải thành một job độc lập trong hàng đợi.
6. Chỉ cho một video chạy OCR/Gemini tại một thời điểm. Khi video đó đã sang bước OCR hoặc dịch, app tải trước video Douyin kế tiếp nhưng không bao giờ chạy hai sidecar Douzy cùng lúc.
7. Với video Douyin, xử lý trực tiếp MP4 gốc tại Douzy rồi chỉ chuyển MP4 và các asset liên quan vào folder project sau khi dịch thành công.
8. Gọi VideOCR CLI quét đúng vùng đã chọn và ghi phụ đề ngôn ngữ gốc vào file `.raw.srt`.
9. Kết nối với provider dịch đã chọn: Playwright điều khiển Gemini Notebook qua profile Edge debug riêng, hoặc API `/chat/completions` OpenAI-compatible cho DeepSeek, OpenRouter và endpoint tuỳ chỉnh.
10. Đặt từng block dịch về đúng số thứ tự và timestamp nguồn khi model đánh số lại hoặc viết lại mốc thời gian; từ chối bản dịch thiếu block, block dính nhau, sai timestamp hoặc vẫn còn tiếng Trung.
11. Ghi `.vi.srt`, thumbnail Douyin nếu có và manifest `project.json`. Job chỉ được báo thành công khi đường dẫn video cùng hai file phụ đề đều tồn tại và vượt qua kiểm tra.

Ứng dụng chạy Flask service chỉ bind vào `127.0.0.1` và hiển thị thành cửa sổ desktop qua pywebview/WebView2. Hàng đợi, thiết lập, video tải về, kết quả OCR và phiên trình duyệt đều nằm trên máy người dùng. Nội dung phụ đề được gửi tới provider dịch bạn chọn. Các kết nối mạng khác (kiểm tra cập nhật, link và thumbnail Douyin, Douzy tải video) được liệt kê ở mục [Quyền riêng tư và bảo mật](#quyền-riêng-tư-và-bảo-mật).

## Vì sao dự án này được tạo ra?

Dự án tập trung vào những phần chậm, lặp lại hoặc dễ lỗi khi làm phụ đề video dài thủ công:

- Video dài khoảng hai giờ có thể khiến phần mềm dựng lag nặng trước cả khi dịch phụ đề xong.
- Phụ đề dính trong hình cần vùng OCR chính xác; quét cả khung hình vừa chậm vừa tăng nhiễu nhận dạng.
- Tải hoặc sao chép cùng một video lớn nhiều lần làm tốn ổ đĩa vô ích. Video Douyin 6 GB nên chỉ chiếm 6 GB, không nên thành 12 GB trước khi OCR bắt đầu.
- Chạy nhiều OCR hoặc phiên tự động trình duyệt song song dễ tranh CPU/RAM/GPU và khiến lỗi khó chẩn đoán. VietSub Studio nhận nhiều job nhưng cố ý xử lý từng video một.
- Gemini có thể trả SRT thiếu, tự đổi timestamp hoặc yêu cầu đăng nhập Google lại. App kiểm tra cấu trúc cuối và hiện Edge khi thật sự cần thay vì âm thầm báo hoàn thành.
- Dịch lỗi không nên buộc người dùng tải và OCR lại từ đầu. Hàng đợi được lưu và cơ chế retry theo bước sẽ dùng lại video hoặc sub OCR còn hợp lệ.
- Người dùng mã nguồn mở cần đường cập nhật rõ ràng. Bản portable có banner phiên bản mới, cài ZIP đã xác minh, tự mở lại và fallback sang GitHub khi không thể tự thay EXE.

## Tính năng

### Đầu vào video và quản lý nguồn

- Nhận link video Douyin trực tiếp, link ngắn `v.douyin.com` và nguyên đoạn văn bản chia sẻ.
- Tự tách URL và kiểm tra hostname thật để từ chối tên miền giả hoặc link đánh lừa.
- Từ chối mix, profile người dùng và playlist vì mỗi job phải tương ứng chính xác với một video.
- Nhận file MP4 trên máy mà không cần Douzy.
- Dùng lại lịch sử/cache Douzy nếu video Douyin đã được tải trước đó.
- Đọc tiêu đề, đường dẫn video, metadata và ảnh cover từ dữ liệu Douzy.
- Chuyển MP4 gốc, cover, metadata, nhạc và các asset riêng của video từ Douzy vào folder project hoàn tất mà không tạo bản sao video dung lượng lớn thứ hai, rồi cập nhật đường dẫn mới trong lịch sử Douzy.
- Tiếp tục chờ khi trạng thái tải của Douzy còn thay đổi. Lượt tải chỉ bị coi là lỗi khi trạng thái đứng yên 5 phút hoặc tổng thời gian vượt 6 giờ.
- Sao chép MP4 local vào vùng app quản lý để job vẫn tồn tại sau khi đóng/mở ứng dụng.
- Hard-link MP4 local do app quản lý vào project nếu cùng ổ đĩa; nếu khác ổ hoặc filesystem không hỗ trợ thì tự fallback sang sao chép đầy đủ.

### Preview vùng OCR và nhận dạng

- Phát preview video qua URL cục bộ được bảo vệ trước khi bắt đầu OCR.
- Có khung crop trực quan, kéo và đổi kích thước được để bao đúng vùng phụ đề dính trong hình.
- Lưu crop riêng cho từng job và có thể dùng lại crop mặc định.
- Hỗ trợ OCR tiếng Trung, Anh, Nhật, Hàn và Việt.
- Cho phép chọn model OCR mobile nhanh, model server chính xác hoặc giữ cấu hình VideOCR hiện có, kèm mức bỏ qua khung hình tuỳ chọn.
- Dùng FFprobe nếu có để quy đổi crop từ kích thước preview sang độ phân giải video nguồn chính xác hơn.
- Quét video dài theo từng đoạn 10 phút, ghép lại SRT với timestamp gốc và dùng FFmpeg chuẩn hoá đoạn đổi độ phân giải làm VideOCR bị crash.
- Mỗi lần chạy VideOCR có giới hạn thời gian bằng ba lần độ dài đoạn quét cộng 10 phút, tối thiểu 30 phút; nếu không biết độ dài video thì giới hạn là 4 giờ.
- Gọi VideOCR CLI đã cài trên máy và ghi kết quả nhận dạng vào file `.raw.srt` riêng.

### Hàng đợi tuần tự và khôi phục lỗi

- Cho phép chuẩn bị nhiều job Douyin và MP4 local trước khi bắt đầu chạy.
- Chỉ có một worker OCR/Gemini tuần tự và tối đa một lượt tải trước Douyin sau khi job hiện tại rời bước download.
- Lưu metadata của job chờ, đang chạy, thành công, lỗi và đã huỷ qua lần đóng/mở app.
- Hỗ trợ xoá job đang chờ, huỷ job hiện tại và dọn các job đã kết thúc.
- Khi huỷ job hoặc đóng app, dừng VideOCR, FFmpeg, Douzy cùng mọi tiến trình con của chúng. Worker không nhận job mới trong lúc app đang đóng.
- Chạy lại từ bước sớm nhất còn tận dụng được: chuẩn bị video, OCR hoặc dịch.
- Cho phép mở thư mục làm dở nhưng không gắn nhãn kết quả thành công cho job lỗi.
- Khôi phục trạng thái và log tăng dần nếu giao diện bị reload.

### Provider dịch và tự động Gemini Notebook/Edge

- Hỗ trợ Gemini Notebook qua Edge, DeepSeek API, OpenRouter và các API chat OpenAI-compatible khác.
- Khi chọn Notebook, app dùng link Notebook và phiên Google của người dùng.
- API key được mã hoá bằng Windows DPAPI và lưu riêng cho từng provider và host của Base URL, nên đổi provider không bao giờ gửi key của dịch vụ này sang dịch vụ khác.
- Hướng dẫn dịch mặc định là prompt trung tính, dùng được cho mọi loại video. Mẫu **Game Delta Force** bổ sung từ điển tên riêng và quy tắc xưng hô của game này. Hướng dẫn dịch đã lưu từ bản cũ được giữ nguyên.
- Prompt nêu đúng ngôn ngữ OCR của job; sub OCR tiếng Việt được biên tập, sửa lỗi nhận dạng thay vì dịch.
- Kết nối Playwright tới profile Edge riêng qua cổng debug chỉ bind ở `127.0.0.1:9222`.
- Mặc định khởi chạy Edge thu nhỏ và tắt các cơ chế throttling nền có thể làm automation đứng.
- Chờ đúng giao diện chat Notebook tải xong rồi mới kết luận có cần đăng nhập hay không.
- Chỉ hiện Edge khi phiên Google hết hạn hoặc Notebook cần người dùng kiểm tra thủ công.
- Có nút **Mở Edge đăng nhập** rõ ràng trên job lỗi.
- SRT có hơn 400 block hoặc hơn 24.000 ký tự được chia thành các đoạn giữ nguyên timestamp, bất kể video dài bao nhiêu; video dài hơn 45 phút còn được chia theo mốc 30 phút. Khi dùng provider API, mỗi đoạn đã kiểm tra được lưu lại để lần chạy lại tiếp tục từ đoạn chưa xong.
- Huỷ quá trình Gemini đang sinh nội dung khi người dùng dừng workflow.
- Ngắt ngay request API khi bấm **Huỷ job** hoặc đóng app; mỗi lượt gửi có hạn chót 30 phút. Request được thử tối đa 3 lần khi mất kết nối hoặc provider trả HTTP 429, 5xx. App không đi theo chuyển hướng, nên API key không bị gửi sang host khác.

### Toàn vẹn phụ đề và quản lý kết quả

- Giữ nguyên toàn bộ timestamp và số thứ tự block của sub nguồn.
- Khôi phục số thứ tự và mốc thời gian gốc khi model giữ đúng số block nhưng đánh số lại hoặc viết lại timestamp.
- Từ chối output trống, SRT thiếu, sai số block, block dính nhau, bản dịch còn tiếng Trung hoặc song ngữ, hoặc không tạo được file dịch.
- Chỉ báo thành công khi đường dẫn video, sub OCR và sub Việt đã kiểm tra đều tồn tại.
- Làm sạch tên dự án an toàn cho Windows, kể cả tên hệ thống bị cấm và giới hạn độ dài.
- Tự thêm hậu tố `(2)`, `(3)` mà không ghi đè dự án cũ.
- Ghi config, hàng đợi, SRT và manifest theo kiểu atomic để giảm file dở dang.
- Xuất thumbnail Douyin dạng JPG, PNG hoặc WebP nếu có ảnh cover local hoặc URL metadata hợp lệ.
- Ghi URL nguồn, ID video, đường dẫn nguồn/kết quả, trạng thái, lỗi và thời gian cập nhật vào `project.json`.

### Trải nghiệm desktop và chẩn đoán

- Giao diện desktop responsive bằng pywebview, phía sau là Flask API cục bộ.
- Bảng kiểm tra Douzy, VideOCR, FFmpeg/FFprobe, Notebook và Edge kèm hướng dẫn sửa từng mục.
- Hiển thị lịch sử Douzy và nạp lại video gần đây nhanh chóng.
- Có hộp thoại Windows để chọn thư mục lưu riêng, hoàn toàn độc lập với thư mục tải Douzy.
- Cảnh báo nếu thư mục project thực tế nằm trong OneDrive vì đồng bộ cloud có thể làm chậm video dung lượng lớn.
- Tự tạo hoặc tạo lại shortcut ngoài Desktop cho bản EXE.
- Giữ Edge thu nhỏ trong luồng dịch bình thường nhưng vẫn có fallback hiện cửa sổ đáng tin cậy.
- Đóng gói thành một file EXE Windows portable với icon riêng.

### Cập nhật, mã nguồn mở và an toàn

- Kiểm tra GitHub Releases chính thức và hiển thị banner khi có phiên bản mới.
- Tải ZIP portable chính thức, kiểm tra URL đúng repo, file SHA256 của ZIP, digest asset GitHub, kích thước công bố và EXE bên trong.
- Tự thay EXE đang chạy và mở lại app nếu môi trường hỗ trợ cập nhật tại chỗ.
- Mở đúng trang GitHub Release chính thức nếu không thể cài tự động.
- Không đưa thiết lập máy, hàng đợi, video local, profile trình duyệt hay thông tin đăng nhập lên repo.
- Phát hành theo giấy phép MIT để cộng đồng có thể đọc, sửa, phân phối và mở rộng mã nguồn trong phạm vi điều khoản của các dịch vụ bên thứ ba.

## Cấu trúc kết quả

Nếu đặt tên dự án là `Review sản phẩm`, ứng dụng tạo:

```text
Review sản phẩm/
├── Review sản phẩm.mp4
├── Review sản phẩm.thumbnail.jpg (nếu có)
├── Review sản phẩm.raw.srt
├── Review sản phẩm.vi.srt
├── Review sản phẩm.data.json (nếu Douzy có)
├── Review sản phẩm.music.mp3 (nếu Douzy có)
└── project.json
```

- Project Douyin hoàn tất chứa MP4 gốc đã được chuyển khỏi thư mục Douzy. MP4 chọn từ máy được hard-link vào bố cục project tự chứa nếu cùng ổ đĩa, nếu không sẽ được sao chép bình thường.
- `Review sản phẩm.thumbnail.jpg`: thumbnail Douyin lấy từ Douzy hoặc tải từ metadata đã lưu nếu có; app cũng hỗ trợ PNG và WebP.
- `Review sản phẩm.raw.srt`: phụ đề OCR theo ngôn ngữ gốc đã chọn.
- `Review sản phẩm.vi.srt`: phụ đề tiếng Việt, giữ mốc thời gian nguồn.
- Các asset Douzy khác được đổi tên đồng bộ như `Review sản phẩm.data.json` và `Review sản phẩm.music.mp3`.
- `project.json`: tên dự án, URL nguồn, ID video Douyin, đường dẫn file, trạng thái, lỗi và thời gian cập nhật.

Nếu thư mục đã tồn tại, dự án tiếp theo sẽ là `Review sản phẩm (2)`. Video, sub, thumbnail, asset Douzy và manifest đều dùng tên project riêng này.

## Yêu cầu hệ thống

| Thành phần | Bắt buộc | Vị trí/hành vi mong đợi | Mục đích |
|---|---:|---|---|
| Windows 10/11 x64 | Có | Phiên desktop Windows | Các tích hợp và bản đóng gói hiện tại dành riêng cho Windows. |
| Microsoft Edge | Chế độ Notebook hoặc link Douyin ngắn | Bản Edge thông thường | Tự động hoá Gemini và giải mã redirect link ngắn. |
| Douzy | Chỉ nguồn Douyin | `%LOCALAPPDATA%\Programs\Douzy` | Tải video Douyin và quản lý lịch sử/cache cục bộ. |
| Cấu hình Douzy | Chỉ nguồn Douyin | `%APPDATA%\douyin-downloader-desktop\config.yml` | Cung cấp thư mục tải Douyin và cấu hình sidecar. |
| VideOCR | Có | `C:\Program Files\VideOCR\videocr-cli.exe` | Trích xuất phụ đề từ video. |
| Gemini Notebook | Chế độ Notebook | `https://gemini.google.com/notebook/<id>` | Dịch nội dung SRT qua trình duyệt. |
| Tài khoản API dịch | Chế độ API | DeepSeek, OpenRouter hoặc provider OpenAI-compatible khác | Dịch SRT không cần Notebook/Edge automation. |
| Tài khoản Google | Chế độ Notebook | Profile Edge debug riêng | Cấp quyền truy cập Notebook đã chọn. |
| FFmpeg / FFprobe | Không | Có cả lệnh `ffmpeg` và `ffprobe` trong `PATH` | Lấy metadata video dài và tự phục hồi khi VideOCR crash do video đổi độ phân giải. |
| Python 3.11+ | Chỉ mã nguồn | Lệnh `python` có trong `PATH` | Chỉ cần khi chạy/build từ source. |

Douzy và VideOCR là ứng dụng bên thứ ba, không được phân phối lại trong repository. Hãy cài từ nguồn bạn tin tưởng và tuân thủ giấy phép/tài liệu riêng của chúng.

## Dùng nhanh bản EXE portable

1. Mở [GitHub Releases](https://github.com/qvinh8726/VietSub-Studio/releases/latest).
2. Tải file `VietSub-Studio-Portable-v<phiên bản>.zip`.
3. Giải nén ZIP vào thư mục bình thường có quyền ghi. Không chạy EXE trực tiếp trong cửa sổ xem trước ZIP. ZIP chứa một thư mục `VietSub Studio` gồm `VietSub Studio.exe`, `SHA256.txt`, `LICENSE` và `HUONG_DAN_SU_DUNG.txt`.
4. Mở `VietSub Studio\VietSub Studio.exe`.
5. Bản EXE tự tạo shortcut `VietSub Studio` ngoài Desktop sau lần mở đầu tiên thành công. Nếu shortcut bị xóa hoặc chuyển thư mục portable, dùng **Thiết lập → Tạo lại shortcut ngoài Desktop**.
6. Lần mở đầu có thể mất 20-60 giây vì bản một file cần giải nén tạm và bị Windows quét bảo mật.
7. Nếu SmartScreen cảnh báo do bản cộng đồng chưa có chữ ký số, hãy kiểm tra đúng nguồn/checksum rồi chỉ chọn **More info → Run anyway** khi bạn tin tưởng bản tải.
8. Mở **Thiết lập** và xử lý hết các mục bắt buộc màu đỏ.

Mỗi release có file `VietSub-Studio-Portable-v<phiên bản>.zip.sha256.txt` chứa checksum của ZIP, còn `SHA256.txt` trong ZIP chứa checksum của EXE. Để tự đối chiếu, chạy `Get-FileHash -Algorithm SHA256 <file>` trong PowerShell.

## Thiết lập lần đầu

### 1. Chuẩn bị Douzy

- Cài Douzy vào đường dẫn mặc định.
- Mở ít nhất một lần.
- Chọn thư mục tải video.
- Nếu chưa có database, hãy thử tải một video bằng Douzy.

Bảng sức khoẻ phân biệt riêng file sidecar, file cấu hình và database lịch sử tuỳ chọn.

### 2. Chuẩn bị VideOCR

- Cài sao cho file `C:\Program Files\VideOCR\videocr-cli.exe` tồn tại.
- Nếu phụ đề chỉ nằm trong một phần khung hình, cấu hình và lưu vùng crop trong VideOCR.
- Nên cài FFmpeg/FFprobe và thêm cả hai lệnh vào `PATH`, nhất là với video dài hoặc video ghép có thể đổi độ phân giải giữa chừng.

### 3. Chọn provider dịch

- Với chế độ Notebook, tạo hoặc chọn Gemini Notebook mà bạn có quyền truy cập.
- Sao chép URL đầy đủ đúng định dạng:

  ```text
  https://gemini.google.com/notebook/ID-NOTEBOOK-CUA-BAN
  ```

- Dán vào **Thiết lập → Notebook Gemini dùng để dịch**.
- Có thể giữ bật **Chạy Edge ẩn**; trang vẫn render đầy đủ dù cửa sổ được thu nhỏ.
- Khi phiên Google hết hạn hoặc cần kiểm tra Notebook, app sẽ hiện Edge và cho chạy lại job sau khi đăng nhập.
- Với chế độ API, chọn **DeepSeek API**, **OpenRouter** hoặc **API OpenAI-compatible khác**, nhập Base URL, model và API key rồi bấm **Kiểm tra kết nối API** trước khi chạy hàng đợi.
- Mỗi provider và host của Base URL giữ key riêng. Sau khi đổi provider, hãy nhập key của provider mới; key cũ vẫn được giữ cho provider cũ.
- Ô **Cách dịch mong muốn** chứa hướng dẫn dịch. Mặc định là prompt trung tính dùng cho mọi video. Với video Delta Force, chọn **Game Delta Force** ở **Mẫu có sẵn** rồi bấm **Nạp mẫu**. Nút **Khôi phục mặc định** đưa prompt trung tính trở lại.

VietSub Studio không hỏi và không lưu mật khẩu Google. Việc đăng nhập nằm hoàn toàn trong profile do Edge quản lý.

### 4. Chọn thư mục xuất

Bấm **Chọn thư mục…** trong Thiết lập để chọn bất kỳ nơi lưu nào trên máy, độc lập với Douzy. Nếu để trống, app dùng `Documents\VietSub Studio` (hoặc thư mục Windows dự phòng nếu không xác định được Documents).

## Xử lý nhiều video tuần tự

1. Chọn MP4 trên máy hoặc dán một trong các dạng sau vào ô chính:
   - `https://www.douyin.com/video/<id>`
   - `https://v.douyin.com/<mã>/`
   - nguyên đoạn chia sẻ có chứa một trong hai link trên
2. Nhập tên dùng chung nếu muốn; để trống để lấy tiêu đề nguồn hoặc tên file MP4.
3. Bấm **Tải preview** cho Douyin hoặc **Chọn MP4** cho video trên máy.
4. Kéo/đổi kích thước khung vàng cho khớp vùng phụ đề.
5. Chọn ngôn ngữ OCR rồi bấm **Thêm vào hàng đợi**.
6. Lặp lại cho các video khác, sau đó bấm **Chạy hàng đợi**.
7. Theo dõi job hiện tại qua năm bước: giải mã, video, OCR, dịch và hoàn tất.
8. Bấm **Huỷ job** nếu cần dừng video hiện tại; các video đang chờ vẫn được giữ.
9. Job lỗi/đã huỷ có nút chạy lại, tự dùng video hoặc sub OCR còn tồn tại nếu có.
10. Hàng đợi vẫn còn sau khi đóng/mở app. Mở thư mục dự án từ card hàng đợi hoặc bảng kết quả.

Link mix, hồ sơ người dùng và playlist vẫn bị từ chối. Hàng đợi nhận từng link video riêng và xử lý tuần tự, không chạy đồng thời.

## Chạy từ mã nguồn

```powershell
git clone https://github.com/qvinh8726/VietSub-Studio.git
cd VietSub-Studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

Hoặc bấm đúp `Chay Tool.bat`; script sẽ kiểm tra thư viện Python cốt lõi và mở ứng dụng desktop.

Chạy giao diện web cục bộ ở cổng 5000:

```powershell
python app.py --server
```

Sau đó mở `http://127.0.0.1:5000`.

Để app tự chọn cổng local trống và tự mở trình duyệt mặc định (cùng cơ chế dự phòng khi WebView native không khởi động được), chạy:

```powershell
python app.py --browser
```

## Dùng dòng lệnh

`automate.py` chạy cùng luồng xử lý mà không cần giao diện:

```powershell
python automate.py --url "https://www.douyin.com/video/123" --lang ch --name "Review sản phẩm"
```

| Tham số | Bắt buộc | Ý nghĩa |
|---|---:|---|
| `-u`, `--url` | Có | Link video Douyin hoặc link share ngắn. |
| `-l`, `--lang` | Không | Ngôn ngữ OCR: `ch`, `en`, `ja`, `ko`, `vi`; mặc định `ch`. |
| `-n`, `--name` | Không | Tên dùng chung cho dự án, video và hai file phụ đề. |

CLI vẫn cần Douzy, VideOCR và cấu hình cục bộ như bản desktop, cùng Notebook/Edge hoặc API dịch đã cấu hình, tuỳ provider đang chọn.

CLI dùng chung khoá một instance với `python app.py` chạy từ cùng thư mục, nên hãy đóng app đó trước. Bản EXE giữ khoá ở `%APPDATA%\VietSub Studio` nên CLI không nhận ra; đừng chạy cả hai cùng lúc vì chúng sẽ cùng điều khiển Douzy, VideOCR và Edge. CLI không đọc và không sửa hàng đợi của bản desktop. Output là UTF-8 nên có thể chuyển hướng ra file. Mã thoát `0` là thành công, `1` là lỗi hoặc đang có instance khác chạy.

## Tự build ứng dụng Windows

Build bản portable một file:

```powershell
.\build_portable.bat
```

Lệnh tương đương:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --distpath dist_portable --workpath build_portable "VietSub Studio Portable.spec"
python tools\make_release.py --clean
```

Kết quả:

```text
dist_portable\VietSub Studio.exe
release\VietSub-Studio-Portable-v<phiên bản>.zip
release\VietSub-Studio-Portable-v<phiên bản>.zip.sha256.txt
```

`tools/make_release.py` đóng gói EXE theo đúng cấu trúc mà trình tự cập nhật trong app cần. `build_portable.bat` còn chạy `tests/test_release_contract.py` trên thư mục `release`. Đọc [RELEASING.md](RELEASING.md) trước khi phát hành.

`build_app.bat` và `VietSub Studio.spec` cung cấp bản onedir thay thế. Spec portable đóng gói template, avatar và tài nguyên pywebview; không bao giờ đóng gói ứng dụng bên thứ ba hoặc thông tin đăng nhập.

## Cách hệ thống hoạt động

```mermaid
flowchart TD
    A[Link/đoạn share Douyin hoặc MP4 local] --> B{Loại nguồn}
    B -->|Douyin| C[Giải mã đúng ID video]
    C --> D[Douzy tải hoặc dùng cache]
    D --> E[Dùng trực tiếp MP4 gốc của Douzy]
    D --> T[Ảnh cover local hoặc thumbnail metadata]
    B -->|MP4 local| F[Bản upload do app quản lý]
    E --> G[Preview cục bộ được bảo vệ]
    F --> G
    G --> H[Chỉnh crop và ngôn ngữ OCR]
    H --> I[Lưu job vào hàng đợi tuần tự]
    I --> J[VideOCR tạo SRT thô]
    J --> K[Dịch qua Gemini Notebook hoặc API OpenAI-compatible]
    K --> L[Khôi phục và kiểm tra timestamp/block]
    L --> M[Ghi sub Việt và project.json]
    T --> M
    M --> N[Chỉ hiện kết quả sau kiểm tra cuối]
```

Hàng đợi cố ý chạy tuần tự. Job sau chỉ bắt đầu khi job hiện tại thành công, lỗi hoặc bị huỷ. Cách này bảo vệ bản cài VideOCR, sidecar Douzy, profile Edge debug và cuộc hội thoại Notebook dùng chung khỏi bị nhiều luồng điều khiển cùng lúc.

Với job Douyin, MP4 gốc chỉ ở lại thư mục Douzy trong lúc OCR và dịch; sau khi kiểm tra kết quả cuối, app chuyển đúng video và các asset khớp định danh vào project tự chứa. Với MP4 local, app giới hạn 8 GiB, kiểm tra header container MP4, giữ bản cache trong lúc chờ và hard-link hoặc sao chép vào project để job đã lưu có thể chạy lại an toàn.

Các phần chính:

- `app.py`: kiểm tra đầu vào, điều phối tiến trình, Flask API, cửa sổ pywebview, xử lý file và tích hợp ngoài.
- `templates/index.html`: cấu trúc HTML semantic và metadata bootstrap do server render.
- `static/style.css` và `static/app.js`: giao diện responsive, state frontend, thiết lập, chẩn đoán, tiến trình, lịch sử và kết quả.
- `automate.py`: entry point dòng lệnh.
- `tests/test_app.py`: test validation, đặt tên, tìm video, API, huỷ và luồng xử lý.
- `tests/test_release_contract.py`: tên file release, cấu trúc ZIP và định dạng checksum mà các bản đã phát hành cần để tự cập nhật.
- `tests/frontend/app.test.mjs`: test Node cho timeout request, banner mất kết nối và các thao tác hàng đợi trong `static/app.js`.
- `tools/make_release.py`: đóng gói EXE đã build thành hai file release.
- `VietSub Studio Portable.spec`: cấu hình PyInstaller bản một file.

Các API cục bộ (đây là giao tiếp nội bộ giữa giao diện đi kèm và server cục bộ; mọi request cần token theo phiên và API có thể thay đổi ở bất kỳ bản nào):

| Endpoint | Method | Mục đích |
|---|---|---|
| `/api/health` | GET | Kiểm tra Douzy, VideOCR, FFmpeg/FFprobe, Notebook và Edge. |
| `/api/settings` | GET/POST | Đọc và lưu thiết lập cục bộ. |
| `/api/translation/test` | POST | Gửi một yêu cầu thử tối thiểu tới provider API đã cấu hình. |
| `/api/history` | GET | Đọc video gần đây từ database Douzy. |
| `/api/previews` | POST | Chuẩn bị video và trả URL preview được bảo vệ. |
| `/api/previews/local` | POST | Sao chép MP4 local vào cache quản lý và tạo preview. |
| `/api/queue` | GET/POST | Xem hàng đợi hoặc thêm video đã chuẩn bị. |
| `/api/queue/start` | POST | Chạy worker tuần tự duy nhất. |
| `/api/queue/<job_id>/retry` | POST | Chạy lại từ bước video, OCR hoặc dịch. |
| `/api/edge/show` | POST | Hiện cửa sổ Edge riêng để đăng nhập hoặc kiểm tra. |
| `/api/update/install` | POST | Tải, xác minh, cài và khởi động lại vào bản packaged mới nhất. |
| `/api/progress` | GET | Lấy trạng thái và log tăng dần. |
| `/api/cancel` | POST | Huỷ quy trình cùng tiến trình con đang hoạt động. |
| `/api/open-folder` | POST | Chỉ mở đường dẫn do quy trình hiện tại tạo ra. |

Flask chỉ bind vào `127.0.0.1`, không mở service ra mạng công cộng. Mỗi lần mở app tạo một token API ngẫu nhiên; control plane còn kiểm tra Host, Origin, Fetch Metadata và content type của yêu cầu thay đổi trạng thái. Luồng video preview dùng ID ngẫu nhiên vì thẻ video native không thể gắn custom API header.

## Cấu hình và dữ liệu cục bộ

| Dữ liệu | Chạy source | Bản EXE |
|---|---|---|
| Thiết lập VietSub Studio | `app_config.json` cạnh `app.py` | `%APPDATA%\VietSub Studio\app_config.json` |
| Hàng đợi đã lưu | `workflow_queue.json` cạnh `app.py` | `%APPDATA%\VietSub Studio\workflow_queue.json` |
| Cache MP4 local | `local_videos` cạnh `app.py` | `%APPDATA%\VietSub Studio\local_videos` |
| Profile Edge riêng | `%LOCALAPPDATA%\Microsoft\Edge\User Data Debug` | Giống nhau |
| Cấu hình Douzy | `%APPDATA%\douyin-downloader-desktop\config.yml` | Giống nhau |
| Database lịch sử Douzy | `%APPDATA%\douyin-downloader-desktop\dy_downloader.db` | Giống nhau |
| Kết quả dự án | Thư mục đã chọn hoặc `Documents\VietSub Studio` | Giống nhau |
| Log ứng dụng bền vững | `vietsub-studio.log` cạnh `app.py` | `%APPDATA%\VietSub Studio\vietsub-studio.log` |
| Khoá một instance | `instance.lock` cạnh `app.py` | `%APPDATA%\VietSub Studio\instance.lock` |

`app_config.json` bị Git bỏ qua vì có thể chứa link Notebook riêng. `app_config.example.json` chỉ là mẫu an toàn.

Đặt biến môi trường `VIETSUB_STUDIO_CONFIG_DIR` để lưu thiết lập, hàng đợi, cache MP4 local, log và file khoá ở thư mục khác. Bộ unit test dùng biến này nên không bao giờ chạm vào dữ liệu thật của người dùng.

Nếu `app_config.json` hoặc `workflow_queue.json` chứa JSON sai, app đổi tên file thành `<tên>.corrupt-<thời điểm>-<mã>` rồi dùng thiết lập mặc định hoặc hàng đợi rỗng. File chỉ bị khoá tạm (antivirus, phần mềm sao lưu, OneDrive) sẽ được đọc lại và không bao giờ bị đổi tên. Nếu vẫn không đọc được hàng đợi, phiên đó chỉ giữ hàng đợi trong bộ nhớ và không ghi đè file trên đĩa; hãy đóng chương trình đang giữ file rồi mở lại app.

## Quyền riêng tư và bảo mật

- Video được tải và OCR thông qua ứng dụng đã cài cục bộ.
- File MP4 không bị upload lên repository hay server riêng của VietSub Studio.
- MP4 được chọn trên máy bị giới hạn 8 GiB, được kiểm tra header container `ftyp` rồi lưu trong cache cục bộ do app quản lý để hàng đợi sống qua lần khởi động lại. Trong lúc thêm file, video còn đi qua một file tạm trong `%TEMP%`; file tạm này bị xoá khi upload xong.
- Nội dung SRT được gửi đến Gemini Notebook qua phiên Edge hoặc provider API mà người dùng chọn. Hãy đọc điều khoản/quyền riêng tư của provider trước khi xử lý nội dung nhạy cảm.
- Các kết nối mạng, ngoài kết nối `127.0.0.1` tới chính app, sidecar Douzy và cổng debug Edge:
  - GitHub: app hỏi `api.github.com` về release mới nhất mỗi lần khởi động, kèm User-Agent `VietSub-Studio/<phiên bản>`. File release chỉ được tải từ GitHub khi bạn chọn cài bản cập nhật.
  - Dịch: Gemini qua profile Edge riêng, hoặc Base URL API bạn đã nhập, kèm key của bạn. OpenRouter còn nhận URL repository này trong `HTTP-Referer` và `VietSub Studio` trong `X-Title`.
  - Douyin: Edge mở link ngắn `v.douyin.com` để lấy ID video. Khi Douzy không có ảnh cover local, thumbnail được tải qua HTTPS từ host `douyinpic.com` hoặc `douyincdn.com` ghi trong metadata của Douzy.
  - Douzy tự tải video từ Douyin. VietSub Studio chỉ gửi link video cho Douzy qua `127.0.0.1`.
- API key được mã hoá bằng Windows DPAPI, lưu riêng cho từng provider và host của Base URL, và không bao giờ trả ngược qua API settings.
- Thông tin đăng nhập Google do Edge giữ; VietSub Studio không đọc hoặc lưu mật khẩu.
- Mỗi phiên sidecar Douzy dùng bearer token cục bộ ngẫu nhiên.
- Thiết lập và file kết quả được ghi atomic để giảm nguy cơ hỏng.
- API cục bộ dùng token capability ngẫu nhiên theo mỗi lần mở app và kiểm tra Host, Origin, Fetch Metadata, đường dẫn, ngôn ngữ, tên file, loại/kích thước upload và quyền mở thư mục kết quả.
- Không chia sẻ `app_config.json`, profile Edge, cookie, link Notebook riêng hoặc log chứa link video riêng tư.

Đọc [SECURITY.md](SECURITY.md) trước khi báo cáo vấn đề bảo mật.

## Giới hạn hiện tại

- Chỉ hỗ trợ Windows.
- Hàng đợi chạy tuần tự; không chạy OCR/dịch song song.
- Không tự bung link playlist, profile hoặc mix thành nhiều video.
- Bắt buộc có VideOCR; Douzy chỉ cần thêm khi nguồn là Douyin.
- Cần phiên Gemini Notebook đã đăng nhập trong Edge hoặc cấu hình API dịch hoạt động.
- Gemini thay đổi giao diện có thể làm selector lỗi cho tới khi dự án được cập nhật.
- Chất lượng OCR phụ thuộc video, độ tương phản, ngôn ngữ và vùng crop.
- FFmpeg/FFprobe không bắt buộc với video ngắn có độ phân giải cố định, nhưng thiếu chúng thì không thể tự phục hồi crash ở video dài.
- Kết quả OCR của từng đoạn 10 phút chưa được lưu, nên lượt OCR bị lỗi sẽ quét lại video đó từ đầu.
- Dịch bằng Gemini Notebook chưa chạy tiếp được từ đoạn đã xong; chỉ provider API lưu tiến độ theo từng đoạn.
- Khi thêm MP4 từ máy, ổ đĩa tạm thời cần chỗ trống cho hai bản video vì file upload đi qua `%TEMP%` trước khi vào cache do app quản lý.
- EXE cộng đồng chưa ký số nên SmartScreen có thể cảnh báo.
- EXE một file có thể mở chậm vì phải giải nén tài nguyên mỗi lần.
- Việc tải/dịch phải tuân thủ điều khoản Douyin, Google, Douzy, VideOCR và luật bản quyền.

## Khắc phục sự cố

### Thiết lập báo “Douzy sidecar: Thiếu”

Cài Douzy đúng đường dẫn mặc định. Tích hợp hiện tại tìm file:

```text
%LOCALAPPDATA%\Programs\Douzy\resources\sidecar\win32-x64\douyin-dl-sidecar.exe
```

Hiện chưa hỗ trợ tuỳ chỉnh đường dẫn cài Douzy.

### “Cấu hình Douzy: Thiếu”

Mở Douzy, chọn thư mục tải, đóng ứng dụng bình thường rồi bấm **Kiểm tra lại** trong VietSub Studio.

### “Database Douzy” màu vàng

Database không bắt buộc cho trạng thái sẵn sàng ban đầu. Nó thường xuất hiện sau khi Douzy xử lý video đầu tiên; lịch sử và tìm cache sẽ đầy đủ hơn khi có database.

### “VideOCR CLI: Thiếu”

Kiểm tra file sau có tồn tại:

```text
C:\Program Files\VideOCR\videocr-cli.exe
```

Nếu không, cài lại VideOCR vào đường dẫn mặc định.

### “Notebook Gemini: Thiếu”

Dán URL HTTPS đúng dạng `https://gemini.google.com/notebook/<id>` vào Thiết lập. Link chat Gemini thông thường không được chấp nhận.

### Edge mở nhưng không bắt đầu dịch

Dùng nút **Mở Edge để đăng nhập**, đăng nhập Google, xác nhận Notebook truy cập được rồi chạy lại job lỗi. Chế độ nền chỉ thu nhỏ Edge chứ không chạy headless, nên trang Gemini vẫn render bình thường.

### Cổng 9222 bận hoặc không kết nối được Edge debug

Đóng các phiên Edge debug cũ rồi mở lại VietSub Studio. Người dùng nâng cao có thể kiểm tra tiến trình đang giữ cổng `9222`; không nên tắt Edge bình thường nếu chưa xác định đúng tiến trình.

### Sub OCR trống hoặc sai nhiều

Kiểm tra đúng ngôn ngữ nguồn, cấu hình crop hẹp hơn trong VideOCR và thử VideOCR trực tiếp với cùng video. Chữ quá nhỏ, nền kém tương phản, subtitle động hoặc watermark sẽ làm OCR kém.

Với video dài hoặc video ghép, hãy cài FFmpeg/FFprobe. VietSub Studio sẽ quét theo từng đoạn và tự chuẩn hoá các đoạn còn lại nếu VideOCR crash sau khi độ phân giải thay đổi.

### OCR dừng vì quá thời gian

Mỗi lần chạy VideOCR được phép chạy tối đa ba lần độ dài đoạn quét cộng 10 phút, và không dưới 30 phút. Thiếu FFprobe thì app không đọc được độ dài video và cho phép 4 giờ. Trên máy chỉ có CPU chậm, hãy chọn model OCR nhanh hoặc bỏ qua khung hình trong Thiết lập rồi chạy lại job. 20 dòng output cuối của VideOCR được ghi vào `vietsub-studio.log`.

### Tải video Douyin bị quá thời gian

VietSub Studio chờ chừng nào trạng thái tải của Douzy còn thay đổi. App chỉ dừng khi trạng thái đứng yên 5 phút hoặc tổng thời gian vượt 6 giờ. Hãy kiểm tra mạng và lượt tải ngay trong Douzy rồi chạy lại job.

### Banner đỏ “Mất kết nối tới máy chủ cục bộ” hiện ở đầu cửa sổ

Banner hiện khi server cục bộ của app không trả lời trong 15 giây (60 giây với thao tác như lưu thiết lập hoặc thêm vào hàng đợi). Banner tự ẩn khi server trả lời lại. Nếu banner không tắt, hãy đóng rồi mở lại VietSub Studio; hàng đợi đã được lưu trên đĩa.

### Bản dịch làm thay đổi timestamp

VietSub Studio kiểm tra từng đoạn dịch. Nếu số block vẫn khớp, app đặt từng block về đúng số thứ tự và timestamp nguồn, kể cả khi model đánh số lại từ 1 hoặc viết lại mốc thời gian. Nếu block bị mất, bị gộp hoặc dính nhau vì thiếu dòng trống, quy trình dừng thay vì âm thầm xuất sub lệch thời gian. Đoạn bị từ chối không được lưu, nên lần chạy lại sẽ dịch lại đoạn đó.

### Video đã tồn tại trong Douzy

Đây là hành vi bình thường. App yêu cầu Douzy dùng bản ghi cache và tìm đúng video ID. Nếu MP4 vẫn nằm trong thư mục tải của Douzy, app xử lý ngay tại đó, chuyển video cùng các asset Douzy vào dự án mới sau khi sub Việt vượt qua kiểm tra, rồi cập nhật đường dẫn mới trong lịch sử Douzy. Nếu MP4 đã nằm trong một dự án trước, dự án mới nhận một bản sao riêng và dự án cũ được giữ nguyên.

### Windows không chấp nhận tên dự án

Ký tự cấm được thay thế, dấu cách/dấu chấm cuối bị bỏ, tên hệ thống như `CON` được thêm tiền tố và tên quá dài bị giới hạn. Dự án trùng tên tự nhận hậu tố số.

### Mở EXE nhưng tưởng không chạy

Lần đầu hãy chờ tối đa 60 giây. Phải giải nén ZIP trước, sau đó kiểm tra lịch sử cách ly của Windows Security. Luôn đối chiếu SHA256 trước khi cho phép chạy.

### Kết quả nằm ở đâu?

Mở Thiết lập để xem hoặc chọn thư mục lưu. Nếu để trống, mặc định là `Documents\VietSub Studio`. Sau khi thành công, bảng kết quả có nút mở đúng folder project tự chứa.

## Câu hỏi thường gặp

### EXE có hoàn toàn độc lập không?

Không. Nó chứa ứng dụng Python và UI, nhưng VideOCR, Douzy (khi dùng nguồn Douyin) và công cụ dịch vẫn là phụ thuộc bên ngoài. Công cụ dịch là Edge cùng tài khoản Google và Gemini Notebook, hoặc tài khoản của một provider API OpenAI-compatible.

### VietSub Studio có miễn phí và mã nguồn mở không?

Có. Mã trong repository dùng giấy phép MIT. Công cụ, nền tảng, mô hình và tài sản bên thứ ba vẫn dùng giấy phép/điều khoản riêng.

### Tool có upload video của tôi không?

VietSub Studio không upload MP4 lên server riêng. Douzy tải video cục bộ và VideOCR xử lý cục bộ. Chỉ nội dung SRT được gửi đi để dịch: tới Gemini Notebook qua phiên Edge của bạn, hoặc tới provider API bạn đã cấu hình.

### Maintainer có nhận được link Notebook hoặc tài khoản Google không?

Không. Cấu hình nằm trên máy của bạn trừ khi bạn tự chia sẻ. Repository public bỏ qua `app_config.json`.

### Có thể dùng API thay Notebook không?

Có. Trong **Thiết lập**, chọn DeepSeek API, OpenRouter hoặc API OpenAI-compatible khác, nhập Base URL, model và API key rồi bấm **Kiểm tra kết nối API**. DeepSeek/OpenRouter dùng endpoint `/chat/completions`; API key được mã hoá bằng Windows DPAPI và lưu riêng cho từng provider và host của Base URL.

### Có thể dịch sang ngôn ngữ khác tiếng Việt không?

Prompt hiện tại và quy ước `.vi.srt` nhắm tới tiếng Việt. OCR hỗ trợ nhiều ngôn ngữ nguồn, nhưng chưa có tuỳ chọn ngôn ngữ đích.

### Có dùng được TikTok, YouTube, Bilibili hoặc video local không?

Có hỗ trợ file MP4 local. TikTok, YouTube, Bilibili, playlist, profile và các nguồn online khác hiện chưa được hỗ trợ.

### Có thể xử lý nhiều video tự động không?

Có. Tạo preview cho từng video, thêm vào hàng đợi rồi bấm chạy. App chỉ xử lý một job tại một thời điểm để Douzy, VideOCR và Edge/Gemini không xung đột.

### Có thể di chuyển EXE sau khi cấu hình không?

Có. Bản đóng gói lưu thiết lập tại `%APPDATA%\VietSub Studio`, nên di chuyển EXE không làm mất cấu hình.

### Tại sao SmartScreen cảnh báo?

Bản release chưa được ký bằng chứng thư code-signing thương mại. Chỉ tải từ GitHub Releases chính thức và luôn kiểm tra checksum.

Từ v1.3.0, bản EXE có thể tự cập nhật tại chỗ. App chỉ nhận asset từ release chính thức của repository này, bắt buộc kiểm tra checksum ZIP và kiểm tra digest/kích thước GitHub khi metadata có sẵn. Khi cập nhật bắt đầu, app giữ reservation để không preview hay job mới nào được chạy, thay EXE với cơ chế rollback và chỉ xoá bản cũ sau khi tiến trình mới sống qua cửa sổ khởi động. Nếu thư mục portable không có quyền ghi hoặc xác minh thất bại, app sẽ mở trang release chính thức. Bản cộng đồng vẫn chưa được ký số nên tính xác thực nhà phát hành còn phụ thuộc tài khoản GitHub cho đến khi có Authenticode hoặc khoá ký độc lập.

### Tại sao lần đầu mở lâu?

Chế độ one-file của PyInstaller giải nén tài nguyên vào thư mục tạm và Windows có thể quét từng file. Những lần sau vẫn có thể mất vài giây.

### Có thể bán phiên bản đã sửa không?

MIT cho phép dùng mã nguồn repository với mục đích thương mại, nhưng bạn phải tự chịu trách nhiệm về giấy phép bên thứ ba, điều khoản nền tảng, thương hiệu, quyền nội dung và pháp luật liên quan.

### Báo lỗi thế nào để dễ được hỗ trợ?

Dùng mẫu Bug Report, ghi phiên bản, kiểu cài đặt, bước tái hiện, trạng thái bảng sức khoẻ và log đã làm sạch. Xoá toàn bộ mật khẩu, link riêng, cookie và đường dẫn cá nhân.

## Đóng góp

Pull request luôn được chào đón. Đọc [CONTRIBUTING.md](CONTRIBUTING.md), giữ thay đổi tập trung và chạy:

```powershell
python -m py_compile app.py automate.py tools/make_release.py
python -m unittest discover -s tests -v
node --check static/app.js
node --test tests/frontend/app.test.mjs
```

Hai lệnh Node cần Node.js 20 trở lên. Repository có workflow GitHub Actions compile source, chạy test Python và test giao diện, build EXE portable, khởi động chế độ packaged `--server` và kiểm tra UI/static asset. Sau đó workflow đóng gói hai file release bằng `tools/make_release.py`, kiểm tra chúng theo định dạng mà trình cập nhật cần và upload thành artifact `VietSub-Studio-Portable-v<phiên bản>-release-assets`. Dependabot theo dõi dependency Python và GitHub Actions. Người bảo trì phát hành bản mới theo [RELEASING.md](RELEASING.md).

---

## License / Giấy phép

VietSub Studio is released under the [MIT License](LICENSE). Third-party applications and services are not covered by this repository's license.

VietSub Studio được phát hành theo [MIT License](LICENSE). Ứng dụng và dịch vụ bên thứ ba không thuộc giấy phép của repository này.

## Disclaimer / Miễn trừ trách nhiệm

Use this project only with content you are authorized to download, process, and translate. You are responsible for platform terms, copyright, privacy, and local law.

Chỉ sử dụng dự án với nội dung bạn có quyền tải, xử lý và dịch. Người dùng tự chịu trách nhiệm về điều khoản nền tảng, bản quyền, quyền riêng tư và pháp luật địa phương.
