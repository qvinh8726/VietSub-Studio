# Changelog

All notable changes to VietSub Studio are documented here.

## [Unreleased]

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
