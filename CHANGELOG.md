# Changelog

All notable changes to VietSub Studio are documented here.

## [Unreleased]

## [1.7.0] - 2026-11-18

### Changed

- The desktop UI is now a dark Fluent 2 studio workbench: a 44px pipeline header, source tabs and queue sidebar, a dominant video/crop viewport, and a resizable console with logs/results. The document stays within the window; source, queue, settings and logs scroll independently. Existing controls, queue behavior and API routes are retained.
- Settings open in a keyboard-accessible drawer with focus trapping and Escape dismissal. Sidebar/console sizes and collapsed state survive relaunch through local storage and a versioned preference in the existing settings file. Old configurations need no migration.
- Be Vietnam Pro, Noto Sans SC and JetBrains Mono are bundled with their OFL licenses and served locally under the existing restrictive CSP. The interface follows the landing palette: forest/charcoal surfaces, yellow actions/OCR, mint success and vermilion errors.

### Added

- A zero-dependency Sakuga canvas adapter supplies short spring, impact, slash and smoke feedback and deduplicated pipeline transitions. Completion celebrates only a job observed running with a Vietnamese result ready. The renderer stops when idle/hidden, caps effects, and respects both the persisted motion setting and the operating system's reduced-motion preference.
- Video transport with play/pause, seek, timecode and source-FPS frame stepping. Stepping is approximate for VFR sources and disabled when FPS is unknown. Crop remains normalized to the source image across pane resize, aspect ratios and display scaling; unsupported playback codecs still allow OCR when dimensions are known.
- Optional cached FFprobe metadata and live transfer metrics: actual connection count, speed and preview bitrate. Unknown values stay unknown; fallback from parallel to single download updates the count, and background prefetch cannot overwrite foreground telemetry.
- Regression coverage for preserved DOM/field contracts, layout persistence, media fitting, VFX lifecycle, settings validation, metadata caching and downloader telemetry. A deterministic Playwright runner checks 1280×720, 1440×900, 1920×1080 and fractional DPI with real Flask assets/settings/media, controlled queue fixtures and saved screenshots; it does not call external OCR/translation services.

## [1.6.2] - 2026-11-18

### Changed

- Video files are now downloaded over 6 parallel connections when the server supports byte ranges, like a download manager. Each URL is first probed with `Range: bytes=0-0`; when the answer is `206` with `Content-Range: bytes 0-0/<size>` and the file is larger than 2 MB, six connections (`ThreadPoolExecutor(max_workers=6)`) each fetch one byte range and write it at its place in a pre-sized `.part` file. The range requests go to the address the probe was redirected to, so all of them read the same copy of the file. Many Chinese CDNs cap the speed of each connection (about 0.4 MB/s was reported), so the connections add up: against a test server capped at 0.4 MB/s per connection, a 6 MB file took 2.6 s instead of 15.4 s.
- The bytes of all connections are added up for the percent and MB/s shown in the app, and **Cancel job** stops every connection at its next read. A connection that drops resumes its own range from the first missing byte. When the server ignores ranges, answers a range it was not asked for, or a range keeps failing, the file is downloaded again over one connection with resume, as before. The log now ends each download with its size, time, average speed and number of connections.
- Douyin renditions are chosen for OCR instead of for the highest bitrate. H.264 still comes before H.265/ByteVC1, but among H.264 renditions 720p-1080p at a moderate 1.5-3.5 Mbps come first, then lighter ones, then heavier ones (lightest first), then other sizes. Burned-in subtitles stay as readable for VideOCR while a minute of video is about 15-20 MB instead of 40-60 MB at 5-8 Mbps. The log names the chosen rendition, for example "H.264 1080p, 2.6 Mbps". When width and height are missing, the size is read from the rendition name (`normal_720_0`).

### Fixed

- Download progress and messages from a background prefetch no longer reach the visible job's progress bar and console. The download threads have no workflow context of their own, so the callbacks are now bound to the thread that started the download.

## [1.6.1] - 2026-11-18

### Changed

- Google Chrome is now the automation browser by default, with its own profile in the config folder (`chrome_debug_profile`, passed as `--user-data-dir`). It runs next to the Chrome or Edge you work in and never touches your profile. Chrome is found through the `App Paths` registry entry, `Program Files`, `Program Files (x86)`, `%LOCALAPPDATA%\Google\Chrome\Application` and then `PATH`. Settings can switch to Microsoft Edge (`edge_debug_profile`) or another Chromium `.exe` (`custom_browser_profile`).
- The browser only starts when it is needed: Gemini Notebook translation, a download that needs a page, or a login button in Settings. Opening the app with an API provider, the status pill, the health check and the login list never start it, and the pill shows "not open" as a normal state instead of an error.
- Douyin tries three methods in order. (1) The public share page `www.iesdouyin.com/share/video/<id>/` over HTTP with an iPhone User-Agent: the `_ROUTER_DATA` or `RENDER_DATA` data gives the play address (`playwm` becomes `play`, H.264 preferred) without opening a browser. (2) The video in the browser at `www.douyin.com/jingxuan?modal_id=<id>`: the `aweme/detail` JSON, the page's `RENDER_DATA` or the modal's `<video>`. (3) yt-dlp with cookies exported from the browser profile (`context.cookies()`), a pasted Cookie header or `cookies.txt`, always through a temporary Netscape file that is deleted afterwards. `--cookies-from-browser` is never used.
- The Edge profile of 1.6.0 (`%LOCALAPPDATA%\Microsoft\Edge\User Data Debug`) is no longer used. Sign in once more to the platforms, and to Google for Gemini Notebook, in the new browser profile.

### Fixed

- Opening the app while Edge was running could fail with "debug port 9222 does not respond", and two requests at once could open two tabs. The cold-start wait ran the PowerShell/WMI owner check (about 1 s, sometimes blocked) on every 0.25 s poll. It now polls `http://127.0.0.1:9222/json/version` every 0.3 s for at most 15 s of wall time, reads the Windows TCP table first (a refused connection to 127.0.0.1 takes about 2 s on Windows), never sends that request through a system or VPN proxy, and checks the port owner once through the Windows API (about 2 ms; PowerShell is only the fallback). A lock lets only one request start the browser, which opens on `about:blank`; Notebook and the login pages reuse that tab.
- A start error now says why: the browser is not installed, port 9222 is used by another program (named in the message, never closed), or the app's profile is open without the debug port. An older automation browser of this app on the port, including the 1.6.0 Edge profile, is closed first.
- A Douyin video that is only shown to signed-in users no longer leaves the preview on "reading the video page" until the timeout. When Douyin leaves the video (for example a redirect to `/jingxuan` or to another video's modal) or shows its login popup without the video data, the download stops at once and asks you to open Douyin in Chrome from Settings, sign in once and try again. yt-dlp is then skipped, because it would get the same guest cookies, unless a Douyin Cookie header or a `cookies.txt` is set.
- A feed video that plays behind the modal is never taken for the requested one, and a share link that the browser resolves to Douyin's "video unavailable" feed page is rejected instead of downloading another video.
- "Could not open Microsoft Edge to download the Douyin video" followed by yt-dlp's "Fresh cookies (not necessarily logged in) are needed": downloads now use the browser chosen in Settings, and yt-dlp gets fresh cookies exported from its profile.
- The "Choose video", "Load preview" and "Add to queue" buttons no longer squeeze into one row, where "Add to queue" wrapped over four lines. The first two share a row, the main button has its own full-width row, labels never wrap, and below 480 px the buttons stack.
- A Douyin share page without data is logged as information instead of an error, and `/api/edge/show` returns a JSON error instead of a server error when Playwright fails.

### Added

- Settings: "Automation browser" (Google Chrome, Microsoft Edge or a custom `.exe` with a file picker), the profile path, and the hidden-browser toggle for every translation provider. The login buttons, header pill, captcha notice, health check and login list name the selected browser.

### Landing page

- Version 1.6.1, the six supported platforms, local MP4/MKV files used in place, and Chrome with its own profile; Douzy is gone.
- `npm run lint` (ESLint 9 with `eslint-config-next`) and `npm run typecheck` (TypeScript 7). typescript-eslint cannot use TypeScript 7 yet, so `@typescript/typescript6` is installed under the `typescript` name and `next build` type-checks with it, as the TypeScript 7 release notes recommend.
- Next.js 16.3.0 -> 16.4.6, which fixes the advisories reported for 16.3.0. `npm audit` still lists high-severity advisories in ESLint's dependencies (development only, nothing in the built site).

### Known issues

- Douyin's share page currently has no video data for most videos (the page loads it with signed JavaScript requests), so the HTTP method falls through in under a second and the browser is used.
- Videos that Douyin only shows to signed-in users need a one-time login in the app's browser profile.

## [1.6.0] - 2026-11-17

This release also contains every fix listed under 1.5.4.

### Added

- Downloads no longer need Douzy. A Douyin video is captured in the dedicated Edge profile: the app opens the video page, reads the `aweme/detail` JSON that the page itself requests (the page's SSR data and its `<video>` element are fallbacks), and streams the watermark-free MP4 with the profile's cookies, User-Agent and `Referer`. H.264 renditions are preferred over H.265 so VideOCR, FFmpeg and the preview can decode them. yt-dlp with the same cookies is the second method.
- New sources: Kuaishou (captured in Edge, since yt-dlp has no Kuaishou extractor), Xiaohongshu (yt-dlp, with Edge as the fallback), Bilibili, TikTok and YouTube (yt-dlp 2026.10.26 with the bundled yt-dlp-ejs scripts; YouTube also needs Deno 2.3+ or Node 22+ in `PATH`). One link is always one video: profiles, playlists, collections and livestreams are rejected.
- Share text pasted from the Douyin, Kuaishou and Xiaohongshu phone apps is understood. `v.douyin.com`, `xhslink.com` and `v.kuaishou.com` links are resolved over HTTP, and every redirect must stay on the platform. Douyin feed links with `?modal_id=` become `https://www.douyin.com/video/<id>`.
- When Douyin shows a captcha, or a Kuaishou/Xiaohongshu page shows nothing to download, the Edge window comes to the front and the app asks the user to solve the captcha or sign in there; it waits up to 3 minutes. A background prefetch never waits and leaves the download to the job.
- Previews and queued jobs show live download progress (percent, MB and speed). Cancel stops a download within about a second.
- A new Settings section shows whether the Edge profile is signed in to Douyin, Bilibili, Xiaohongshu and Kuaishou and opens each platform in Edge for a one-time login. An optional Cookie header per platform is stored encrypted with DPAPI and replaces the profile cookies with the same name; a Netscape `cookies.txt` can be used for the yt-dlp sources (yt-dlp gets a temporary copy, so the file itself is never modified). YouTube never receives the profile cookies, because yt-dlp warns that account cookies can get a Google account flagged.

### Fixed

- In the desktop window, an MP4 or MKV file is picked with the native file dialog and used where it is: no upload, no copy in `%TEMP%`, no copy in the app cache. A project gets a hard link on the same drive and a copy otherwise. Browser mode (`--browser`, `--server`) still uploads MP4 files through `%TEMP%`.
- Cancelling an API translation request no longer waits for a stalled response: `HTTPResponse.close()` blocks while another thread is reading, so the response is now closed in the background.

### Changed

- Douzy is no longer used or checked. The health panel drops the Douzy items and adds Edge for Douyin/Kuaishou, yt-dlp, and the JavaScript runtime for YouTube. A video that is still in an old Douzy library is reused without downloading, and the old Douzy history list only appears when that library exists.
- Downloaded videos wait in `<output folder>\.vietsub-downloads` and move into their project after translation; unused downloads older than 6 hours are removed at startup. `VIETSUB_STUDIO_DOWNLOAD_DIR` moves that folder.
- The download code moved into the `vietsub/sources` package; `app.py` keeps the old function names. The CLI accepts the same links as the app.
- CI checks that yt-dlp and yt-dlp-ejs are bundled in the packaged EXE.

### Known issues

- Douyin and Kuaishou change their web pages often, which can break downloads until the app is updated. For Douyin, the error lists the reason of each method (Edge, then yt-dlp); Kuaishou only has the Edge method.
- Xiaohongshu gives guests the same cookies as signed-in users, so its login state shows as unknown.

## [1.5.4] - 2026-11-17

### Security

- API keys are now stored per provider and Base URL origin. Switching to another provider or host no longer sends the previous key there, and the translation client refuses HTTP redirects instead of forwarding the `Authorization` header. A key saved by 1.5.3 or earlier is bound to the provider it was saved for on the first settings save.
- FFmpeg and FFprobe are taken from `PATH` only, never from the current or application folder, and Explorer and `taskkill` are started by absolute path from the Windows directory. A planted `ffprobe.exe`, `ffmpeg.exe` or `explorer.exe` next to the portable EXE (for example in Downloads) is no longer executed.
- The local API token is compared as bytes, so a non-ASCII token header returns 403 instead of a server error. `/api/open-folder` rejects a non-string path with 400.
- Line breaks inside a durable log message are escaped, so text from a provider or tool cannot fake extra log entries.

### Fixed

- Release assets follow the format every updater since 1.3.0 expects again. The 1.5.3 release published `.zip.sha256` instead of `.zip.sha256.txt` and a ZIP without the `VietSub Studio/` folder, so 1.3.0 to 1.5.2 could not update automatically. `tools/make_release.py` now builds exactly `VietSub-Studio-Portable-v<version>.zip` (with `VietSub Studio/VietSub Studio.exe`, `SHA256.txt`, `LICENSE` and `HUONG_DAN_SU_DUNG.txt`) and `VietSub-Studio-Portable-v<version>.zip.sha256.txt`, and `tests/test_release_contract.py` checks both against the updater.
- The update helper starts the new EXE with `PYINSTALLER_RESET_ENVIRONMENT=1`, so it does not inherit the extraction folder of the old process.
- The CLI no longer rewrites the desktop queue. The 1.5.3 notes listed this fix, but `automate.py` could still replace `workflow_queue.json` with an empty list after moving a Douzy video into its project. Only the desktop and server entry points now load and save the queue.
- CLI output is written as UTF-8, so redirecting `automate.py` to a file or pipe on a non-UTF-8 code page no longer fails with `UnicodeEncodeError` after a successful run.
- Douzy downloads no longer fail after a fixed 5 minutes. A download times out only when the sidecar's job status stops changing for 5 minutes (time fields such as timestamps and ETA are ignored) or after 6 hours in total.
- A `videocr_gui_config.ini` written in the Windows ANSI code page, for example under a profile folder with accented or Chinese characters, no longer breaks OCR, Settings or preview creation. Lines without `=` are ignored.
- The VideOCR time limit grows with the scanned span: three times its length plus 10 minutes, at least 30 minutes, and 4 hours when the duration is unknown. Every run used to stop at 30 minutes, including a single run over a 45-minute video.
- VideOCR output lines no longer go to the durable log one by one; a failed run writes its last 20 lines, so earlier steps are not rotated out of the log. FFprobe no longer opens a console window in the packaged EXE.
- Cancelling a job or closing the app stops the whole process tree of VideOCR, FFmpeg and Douzy (`taskkill /T /F`), not only the direct child. A process that does not exit is logged instead of hiding the original error, and Ctrl+C in the CLI also stops VideOCR and FFmpeg.
- Closing the app while the queue runs stops the worker before it can claim another job. A cancel sent while the next job is starting is kept, and new previews are refused while the worker hands over to the next job, so two Douzy downloads never run together.
- API translation requests can be cancelled and have a 30-minute deadline per attempt on top of the 180-second read timeout, so a stalled gateway no longer blocks the queue.
- A streamed reply that ends without `finish_reason` or `data: [DONE]` is treated as truncated and retried. Only a 400, 415 or 422 response that mentions streaming switches the job to non-streaming JSON; any other 400 is reported once with the provider's message. `Retry-After` is honoured up to 120 seconds, and `IncompleteRead` and connection resets are retried.
- Every subtitle file with more than 400 blocks or 24,000 characters is now split for translation, whatever the video length; videos longer than 45 minutes are still also split at 30-minute boundaries. A dense SRT under 45 minutes used to go out as one oversized request.
- A translated chunk that the model renumbered from 1, or whose timecodes it rewrote, is rebuilt on the source indexes and timecodes. Glued blocks (a missing blank line) and missing blocks are rejected before the chunk is cached, so a retry no longer reuses a bad chunk.
- Prompts name the job's real OCR language instead of always saying the input is Chinese, and Vietnamese OCR output is proofread instead of translated. The translation cache fingerprint now includes the system prompt, the prompt template and the source language.
- A settings file saved with a UTF-8 BOM is read normally. A state file that is only locked for a moment (antivirus, backup, OneDrive) is retried and never quarantined, and saving settings no longer writes defaults over a config that could not be read. Only invalid JSON is moved aside, under a unique `.corrupt-*` name.
- When the queue file cannot be written, adding, removing, retrying or clearing jobs returns HTTP 503 with the reason as JSON and keeps the in-memory queue unchanged. Other API errors are also returned as JSON instead of an HTML page.
- A finished job whose project folder was moved or deleted is reported as missing project files instead of an untranslated subtitle, and its retry asks for confirmation before starting over. Retry decisions now use an `error_code` instead of matching message text.
- Edge status polling reuses the Edge process check for 10 minutes per debug session instead of starting PowerShell and WMI every 5 seconds. Automation still checks the process before each use.
- The interface no longer freezes when a request hangs. Reads time out after 15 seconds and actions after 60 seconds (preview, upload and update install have no limit), a banner appears while the local server is unreachable and disappears when it answers again, and a failed progress poll no longer discards a good queue update.
- The **Cancel job** button shows "cancelling" only for the job that was cancelled, so the next job can be cancelled. A second click on an action that is still running is ignored. A 409 while installing an update shows the reason instead of switching to the manual GitHub download, and the progress caption names the translation provider in use.
- Unit tests run in a temporary sandbox and no longer read or write the real `%APPDATA%`, Douzy or VideOCR files.

### Changed

- The default translation instructions are now neutral and suit any kind of video. The Delta Force game glossary that 1.5.3 sent with every request is available as the **Game Delta Force** preset in Settings. Instructions saved in an earlier version are kept unchanged.
- Added the `VIETSUB_STUDIO_CONFIG_DIR` environment variable to keep settings, queue, managed videos, logs and the instance lock in a chosen folder.
- CI uploads `VietSub-Studio-Portable-v<version>-release-assets` with exactly the two release files and `VietSub-Studio-build-info-<commit>` with `build-manifest.json` and `dependencies.txt`; the bare EXE is no longer uploaded. CI also checks `static/app.js` and runs the new frontend tests. `RELEASING.md` describes the release steps.

### Known issues

- Selecting a local MP4 still writes a temporary copy to `%TEMP%` before it is copied into the managed cache, so it needs twice the free space. The fix is planned for 1.6.x.

## [1.5.3] - 2026-08-04

### Security

- Added a per-launch API capability token, local Host/Origin/Fetch-Metadata validation, JSON-only state changes, defensive response headers, and an 8 GiB validated MP4 upload limit.
- Hardened executable discovery, Douzy asset identity matching, thumbnail validation, updater reservation/rollback, and update startup liveness checks.

### Fixed

- Long videos are now OCRed in 10-minute chunks and merged back into one timestamp-safe SRT. If VideOCR crashes on a mixed-resolution HEVC segment, VietSub Studio normalizes that segment to a fixed-size H.264 crop with FFmpeg and retries automatically.
- Videos up to 45 minutes keep the single-request translation flow; longer videos are translated in smaller timestamp-preserving Gemini chunks and merged back into one SRT.
- Added DeepSeek API, OpenRouter, and custom OpenAI-compatible translation providers with encrypted Windows API-key storage, connection testing, retries, and per-chunk resume files.
- Added a shared game/sci-fi translation glossary and character-pronoun rules to every Gemini/API translation request; SRT timestamps remain strict while terminology notes stay outside the saved subtitle file.
- API translation now requests SSE streaming when available, so local gateways such as 9router can deliver partial output without waiting for one large response; non-streaming gateways still fall back to JSON.
- Queue mutations now persist with copy-on-write semantics, the worker converts unexpected exceptions into terminal job errors, corrupt queue/config files are quarantined, the CLI no longer loads or overwrites the desktop queue, and a cross-process lock prevents multiple app/CLI instances from sharing providers and state.
- Fixed stale preview/auto-name/resolution races, Gemini timeout cleanup, detached sidecar startup cleanup, native shutdown process cleanup, SRT ordering validation, and queue admission beyond the persisted limit.

### Changed

- Edge is now a required health capability on the stable loopback CDP port `9222`, allowing the dedicated profile to reconnect across app restarts; native startup exceptions/timeouts fall back to browser mode, and the frontend gates each queued source on source-aware health readiness with improved status/accessibility semantics.
- Extracted inline CSS/JavaScript into packaged static modules, added queue revision polling and bounded provider output buffers, and enabled durable rotating logs.
- Windows CI now builds and smoke-tests the packaged portable executable and uploads the verified artifact; Dependabot tracks Python and Actions dependencies.

## [1.5.2] - 2026-07-27

### Fixed

- Restored the native desktop window as the default launcher. The pywebview bridge now keeps its WebView module and window references private, preventing recursive bridge inspection from deadlocking the WebView2 UI thread.
- The browser launcher remains available explicitly with `--browser` as a diagnostic fallback.

## [1.5.1] - 2026-07-27

### Fixed

- The portable app now opens the local interface in the default browser by default when Windows WebView2 hangs during native window startup. The embedded WebView2 mode remains available with `--desktop` for systems where it works.

## [1.5.0] - 2026-07-27

### Added

- OCR speed options in Settings: choose the fast mobile model or the accurate server model, and skip frames between OCR scans to shorten the longest step of each job.
- The next queued Douyin video now downloads in the background while the current job is in its OCR or translation step, so sequential queue runs no longer pay the download time between jobs. The prefetch stays invisible: it does not write to the running job's console, is not affected by cancelling the running job, and never runs two Douzy sidecars at once.

### Fixed

- Rapid consecutive settings changes no longer overwrite each other; saves are debounced in the interface and serialized on the server.
- Invalid saved OCR options now fall back safely, malformed API values return validation errors instead of server errors, and queue handoff waits for background prefetch to stop before another Douzy download starts.

### Changed

- Local MP4 uploads are hard-linked into the project folder instantly instead of being copied a second time, falling back to a normal copy when the destination is on another drive.
- Settings now warn when the output directory is synced by OneDrive, since cloud sync slows down exporting and re-reading large videos.
- The app window opens faster: browser automation libraries load on first use instead of at startup.
- Queue and progress polling now reuse the validation result cached when a job succeeds instead of re-reading and re-parsing every SRT file on each poll, and job serialization moved outside the queue lock so the worker is never blocked by UI polling.
- The Gemini translation step starts watching for the reply immediately and accepts it after ~3 seconds of stable content once generation stops, removing about 12 seconds of fixed waiting per job.
- The interface only rebuilds the queue list when its data actually changes, skips overlapping poll requests, and polls every second while jobs are active but every 4 seconds when idle.
- Short share links resolve as soon as the redirect lands instead of waiting a fixed 2.5 seconds, Edge debug startup is detected within 0.25 seconds, and Douzy download completion is detected within 1 second.

## [1.4.0] - 2026-07-22

### Added

- Added a native Windows folder picker for choosing a project output directory independent from Douzy.

### Changed

- Completed Douyin projects now move the original MP4 and related Douzy assets into the named project folder after translation validation, keeping video, raw SRT, Vietnamese SRT, thumbnail, and metadata together.
- Empty output settings now default to `Documents\VietSub Studio` instead of the Douzy download directory.

## [1.3.2] - 2026-07-22

### Added

- Included the Douyin thumbnail in each result folder when Douzy or its metadata provides one.

### Changed

- VideOCR now processes the original Douzy download directly instead of creating a second full-size MP4 copy.

## [1.3.1] - 2026-07-22

### Fixed

- Prevented hidden or generic Google account links from being mistaken for an expired Gemini session.
- Waited for the Notebook chat UI before deciding that visible Edge fallback is required.
- Required the video, raw SRT, and validated Vietnamese SRT to exist before a job can report success.
- Cleared stale success cards and result actions when a job starts, retries, fails, or is cancelled.
- Kept failed project folders accessible as incomplete work without labeling them as finished results.

## [1.3.0] - 2026-07-21

### Added

- One-click portable EXE updates that download the official ZIP, verify its SHA256 checksum and GitHub digest, replace the running executable, and reopen the app.
- Automatic GitHub Releases fallback when an in-place update is unavailable or fails.

## [1.2.1] - 2026-07-21

### Added

- Automatic `VietSub Studio` Desktop shortcut creation on the first packaged EXE launch.
- An in-app action to recreate the Desktop shortcut after it is deleted or the portable folder is moved.

## [1.2.0] - 2026-07-21

### Added

- Local MP4 selection with preview and editable per-video OCR crop regions.
- Persistent queue storage across app restarts.
- Retry actions that reuse prepared video or raw OCR subtitles when available.
- Background Edge mode with visible login/Notebook fallback and a manual reopen button.

### Changed

- Douzy is now optional for local MP4 workflows and remains required for Douyin sources.
- Expired managed local previews are cleaned up when no queued job references them.

## [1.1.0] - 2026-07-21

### Added

- Video preview with a draggable, resizable OCR crop overlay and reusable crop defaults.
- Multi-video queue backed by a single sequential worker and per-job result tracking.
- Automatic GitHub release checks with an in-app update banner and update button.

### Changed

- Prepared preview videos are reused by the worker instead of being downloaded twice.
- The main interface now centers preview, crop editing, queue state, settings, and diagnostics.

## [1.0.0] - 2026-07-21

### Added

- Windows desktop interface powered by Flask and pywebview.
- Douyin video and share-text validation.
- Douzy download integration with cache reuse.
- VideOCR subtitle extraction with optional crop conversion through FFprobe.
- Gemini Notebook translation through a dedicated Microsoft Edge debug profile.
- Synchronized project naming for video, raw subtitles, and Vietnamese subtitles.
- Project manifests, atomic file writes, progress restoration, cancellation, and dependency diagnostics.
- Portable one-file Windows build with a custom application icon.
- Responsive desktop/mobile web interface and automated unit tests.

### Security and privacy

- Removed private Notebook defaults from distributed builds.
- Stored packaged-app settings under `%APPDATA%\VietSub Studio`.
- Excluded local configuration, shortcuts, and build artifacts from Git history.
