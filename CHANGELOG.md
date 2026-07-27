# Changelog

All notable changes to VietSub Studio are documented here.

## [Unreleased]

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
