# Changelog

All notable changes to VietSub Studio are documented here.

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
