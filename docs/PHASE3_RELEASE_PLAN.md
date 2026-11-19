# Phase 3 + Phase 4 — implementation/release gate

User approved Multi-Platform Uploader and v2.0.0 release after completed Phase1/2.

## Phase 3 boundaries

- `vietsub/uploader/profiles.py`: UUID profiles and private independent persistent browser/session storage; atomic registry, perprofile nonblocking locks. No credentials returned in API/manifest.
- `browser.py`, `adapters.py`: lazy Playwright/Patchright persistent-context runtime + six distinct platform flows. Manual login headed, upload headless; challenge/changed UI/privacy failure is explicit. No CAPTCHA solving, no personal Chrome profiles, no global VietSub CDP port reuse.
- `queue.py`: durable child job per account; schedule is local clock with explicit UTC offset/Asia/Saigon UI. App must be running. Persist irreversible-submit checkpoint; interrupted/submitted-but-unconfirmed jobs become uncertain and never auto-post again. Errors do not crash other accounts.
- `api.py`: job-ID/managed-MP4 scoped inputs, metadata save and explicit AI generation; no arbitrary server path input. Keep source identity at enqueue. Public posting requires affirmative confirmation; no scheduled action at boot unless already explicitly queued by user.
- Publish Hub: profiles/login/probe, edited/source/MP4 video, metadata/title/description/tags, peraccount selection/modes, queue schedule/status/cancel/retry. Keep legacy IDs and no viewport overflow. Never claim live upload on mock success.

## Phase 4 gates

1. Unit/mock browser/state machine/profile isolation, Node/115 IDs/API/VFX regression, real Flask+Playwright at1280×720/1440×900/1920×1080; deterministic tests never visit/post to accounts.
2. Security review: token/origin/Host/CSP, profile locks/atomic state/privacy mapping, idempotency and uncertain checkpoint, cancellation, secrets/screenshot access, source cleanup protection while uploads queued/running.
3. APP_VERSION=2.0.0 + changelog/release docs and clear platform limitations; no optional heavy AI dependency bundling.
4. Build portable and smoke startup/assets/editor render/uploader capabilities; updater ZIP layout and checksums remain compatible.
5. Stage only reviewed source/docs/tests/specs. Exclude preexisting hi.txt and all profiles/cookies/temp/artifacts/dist/build. Commit/push main at user authorization; observe GitHub Actions exact SHA. Publish v2.0.0/tag only with verified assets and successful CI; report concrete blockers if credentials/network/CI fails.

## External effects

No production video is uploaded by the agent: user has not selected account/content. Browser fixtures/local tests establish implementation contracts, not real platform acceptance. User can manually log in and explicitly queue content after release. Repository push/CI/release is authorized by the top-level Phase4 request.
