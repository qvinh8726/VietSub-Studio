# v2.0.0 — QA/release acceptance record

## Implemented scope

VietSub + Auto Edit + Publish Hub are integrated as a 3-in-1 Creator Workbench. `APP_VERSION` is2.0.0; the v1.7/v1.8 DOM/API contracts and 115 legacy IDs remain guarded. Heavy optional TTS/separation runtimes stay external and absent from the portable default.

Publish Hub includes six platform adapters, independent private UUID account profiles, manual login/finish/check, edited-video or managed MP4 selection, metadata save/explicit AI proposals, peraccount durable queue, visibility matrix, local timezone-aware scheduling, cancel/retry and signed local diagnostics. No live platform account uploads were made for QA. Mock success must not be interpreted as production platform validation.

## Safety review closure

- Retry shares cleanup admission/project locks; active/scheduled input remains protected.
- Privacy/host/challenge/native scheduling/final-control invariants are revalidated after the durable submit checkpoint, immediately before one irreversible click.
- Stable request IDs handle lost responses; per-account publication identity is separate from recipient batch/order/scheduled time, so uncertain/active/success cannot be bypassed by changing recipients.
- Encoded queue size is checked before write to match the8MiB load limit.
- Native-WebView browser fallback reconstructs stopped uploader state safely.
- Checkpoint persistence failures latch queue stop outside sanitized adapter/browser exceptions; no later account reaches a final click in that session.
- Profile locks span threads/processes; registry/storage reject symlink/junction redirects and unsupported/corrupt schema without overwrite.
- Unsupported privacy modes fail closed; public requires explicit acknowledgement. CAPTCHA/legal/audience/category/account identity are never guessed or bypassed.

## Local evidence before commit

| Gate | Result |
|---|---|
| Python full suite |669 tests,663 pass,6 skips (four unavailable symlink privileges, real Chrome opt-in, published release-dir gate before packaging) |
| Node frontend |143/143 pass,0fail |
| DOM115/local fonts/palette/VFX/API |Regression green; baseline fixture unchanged |
| Compile |`py_compile`, `compileall vietsub`, Node syntax checks pass |
| Publish Hub Playwright |1280×720,1440×900,1920×1080; real Flask profiles/metadata/queue persistence, mock external upload executor;0console/CSP/HTTP errors |
| Phase1 Playwright |All3 resolutions, workspace preservation and stress pane/banner, no issue |
| Auto Edit Playwright |Real FFmpeg render/SRT synthetic provider alignment and handoff, no issue |
| Legacy Workbench Playwright |104 checks including125%/150%DPI, no issue |
| Portable |Build and isolated startup/assets/uploader6capabilities/catalog + actual editor render100%, no optional AI packages required |
| Local release ZIP |Updater layout/checksum tests10/10pass with RELEASE_DIR |

The six ordinary full-suite skips are environment/opt-in gates, not test failures. The published-release-directory check was run separately against actual local v2.0.0ZIP and passed. Real platform upload/privacy/account validation remains a manual user test, not a hidden skip.

## Artifacts (local, gitignored)

- [Python final log](<../artifacts/final-v2-python.utf8.txt>)
- [Publish Hub report](<../artifacts/phase3-qa/report.json>)
- [Publish Hub1440×900](<../artifacts/phase3-qa/1440x900-publish.png>)
- [Portable smoke report](<../artifacts/v2-portable-smoke.json>)

## Release execution gate

The source commit must be selected explicitly, excluding scratch files/profiles/artifacts/media/build/dist. After push `main`, observe **Tests and package smoke** for the exact SHA; do not publish a release based on old successful runs. Prefer the green CI run's portable asset pair for publication. Verify tag target, ZIP SHA256 and GitHub release asset names after publishing. Record remote URLs/SHA in the final response only after observing them.

## Documented limitations

See [Publish Hub guide](<PUBLISH_HUB_GUIDE.md>) and [Auto Edit guide](<AUTO_EDIT_GUIDE.md>). Browser selectors/account privileges are version-dependent; Facebook is Page/Meta Business Suite, not arbitrary personal profile. YouTube/Xiaohongshu do not claim explicit web draft; Bilibili/audience/legal defaults require manual preparation. Local schedules require app/machine/network running. Edge and AI separation live inference were not exercised on this machine; NVENC unavailable here, CPU/fallback tested. CapCut internal protocol was live-smoked with a fixed public phrase, not an official/stable API guarantee. CapCut Draft remains optional/unimplemented.
