# Releasing / Phát hành

These steps publish a release that every portable build since v1.3.0 can install by itself. The examples use `1.5.4`; replace it with the version you are releasing.

Các bước dưới đây phát hành một bản mà mọi bản portable từ v1.3.0 trở lên tự cài được. Ví dụ dùng `1.5.4`; hãy thay bằng phiên bản bạn phát hành.

## v2.0.2 release gate — 20/11/2026

Người dùng đã duyệt README và toàn bộ thay đổi v2.0.2. Chốt `APP_VERSION`/changelog/landing ở 2.0.2, commit source đã rà soát, push `main` và chỉ gắn tag/phát hành sau khi workflow **Tests and package smoke** của đúng SHA xanh toàn bộ. Lấy artifact `VietSub-Studio-Portable-v2.0.2-release-assets`, kiểm chứng ZIP + `.zip.sha256.txt` bằng release contract rồi xuất bản release thường, đánh dấu Latest và kiểm tra `releases/latest`. Giữ nguyên tag/assets v2.0.1 đã phát hành.

v2.0.2 bổ sung dependency nhẹ `requests` cho keep-alive, vẫn không đóng gói Edge-TTS/tách vocal/model. Chạy `python tools/benchmark_tts.py --cues 85 --latency .15 --output artifacts/tts-benchmark-v2.0.2.json` để đo offline với FFmpeg thật; báo số đo kèm điều kiện, không dùng thay cho live CapCut acceptance. Chế độ pitch mới chỉ tác động TTS, giữ legacy payload không alias theo hợp đồng cũ. Xem [hướng dẫn Auto Edit](<docs/AUTO_EDIT_GUIDE.md>) và [audit cleanup](<docs/v2.0.2-cleanup-audit.md>).

## v2.0.1 release gate — Creator Workbench

The four-phase implementation and the latest desktop/Soft Cinema integration are released as v2.0.1. Keep the already-published v2.0.0 tag/assets unchanged so the updater can offer this newer version. Historical Phase1/2 notes below describe those approval points, not the current release scope. Run all deterministic QA before publishing; no real account uploads are part of CI.

```powershell
python -m py_compile app.py automate.py tools/make_release.py tools/phase1_qa.py tools/phase2_qa.py tools/phase3_qa.py
python -m compileall -q vietsub
python -m unittest discover -s tests -v
node --check static/app.js
node --check static/workbench.js
node --check static/editor.js
node --check static/uploader.js
node --check static/sakuga-vfx.js
node --test tests/*.js tests/frontend/*.test.mjs
python tools/phase3_qa.py --output "$env:TEMP\vietsub-publish-qa"
```

Publish Hub needs the selected local Chromium browser; Playwright remains bundled and lazy-loaded, Patchright is optional. Independent account directories/storage_state must never be committed or packaged. Local schedules need the app running; uncertain final submissions require user reconciliation, not auto retry. See [Publish Hub capabilities](<docs/PUBLISH_HUB_GUIDE.md>) for visibility/platform limitations and account preparation. Facebook targets Page/Business Suite, not personal profiles. No production platform upload has been performed for release QA; mock and local browser tests do not establish live account/platform acceptance.

Commit only reviewed source/docs/tests; exclude scratch `hi.txt`/`plan_v2.txt`, profiles, cookies, media and artifacts. Push `main`, require the exact SHA's full **Tests and package smoke** workflow green, then tag/publish verified ZIP+SHA256 assets as v2.0.1. Keep release notes explicit about browser-selector drift, optional runtime/model setup and local scheduling.

## Phase 1 Creator Workbench preview / Bản xem trước Phase 1

The Phase 1 preview was implemented on the v1.8.0 baseline; Phase 2 is now added below. Keep `APP_VERSION` unchanged until all four phases are approved; do not publish the three-workspace shell as a complete v2.0.0 release. Phase 1 has no new runtime dependencies; editor AI libraries/models remain optional external runtimes, not portable defaults.

Phần Phase 1 đã hoàn tất, hiện tiếp tục Phase 2 Auto Edit bên dưới, cùng baseline v1.8.0. Chưa đổi version/push/release v2.0.0. Không đóng gói model ONNX/Demucs/inpainting. Local portable build/smoke là kiểm tra kỹ thuật; phát hành và xác nhận GitHub Actions của SHA phát hành vẫn là cổng Phase 4, không được thay thế bằng `py_compile`.

Additional local acceptance commands / Kiểm tra bổ sung:

```powershell
python -m py_compile vietsub/manifest.py vietsub/sources/browser.py vietsub/sources/browser_lifecycle.py tools/phase1_qa.py
python tools/phase1_qa.py --output "$env:TEMP\vietsub-phase1-qa"
# Opt-in isolated local Chrome lifecycle smoke (no platform/account traffic):
$env:VIETSUB_BROWSER_SMOKE = "1"
python -m unittest tests.test_browser_lifecycle.RealChromeLifecycleTests -v
Remove-Item Env:VIETSUB_BROWSER_SMOKE
```

The Phase 1 runner uses real local project handoff/signed media/settings/cleanup in a temporary sandbox at 1280×720, 1440×900 and 1920×1080. It must preserve all 115 legacy DOM IDs, preview/crop/transport state, local fonts, Sakuga behavior and the existing CSP. It does not validate external CAPTCHA/account sessions, render edited videos or upload anything. Keep generated QA artifacts and local scratch files out of the release commit.

## Phase 2 Auto Edit / Optional runtimes

Phase 2 adds no mandatory Python dependency: core uses FFmpeg/FFprobe + stdlib. Do not add `torch`, `demucs`, `onnxruntime`, `audio_separator` or `edge_tts` to the default portable requirements. Both PyInstaller specs explicitly exclude them; optional providers run in an external interpreter.

- Configure **Auto Edit → Runtime** (also linked from Settings) with an absolute external `python.exe` path. Commands are copy-only; app does not run pip or download models.
- Edge fallback: `<python.exe> -m pip install edge-tts` in that interpreter. Account/network/voice availability is still provider-dependent.
- Stem separation: `<python.exe> -m pip install "audio-separator[cpu]"`, separately obtain a compatible ONNX model and required metadata under its license, then set model directory + filename. The adapter defaults to offline mode; missing model/config fails explicitly, never returns original vocals as “instrumental”. BGM/SFX preservation is not guaranteed.
- CapCut uses an internal common-task compatibility route. A fixed anonymous Vietnamese phrase and rendered MP4 were live-tested on 2026-11-19; this is not an entitlement/uptime guarantee. CapCut compatibility can be disabled in Runtime. No request is made at boot and text is sent only on explicit synthesis/render. Explicit operator auth, if needed, is read only from `VIETSUB_CAPCUT_COOKIE` / `VIETSUB_CAPCUT_BEARER`, kept in memory and never sent to artifact hosts.

Additional acceptance (FFmpeg/Playwright Chromium required):

```powershell
python -m compileall -q vietsub
node --check static/editor.js
python -m unittest discover -s tests -v
node --test tests/*.js tests/frontend/*.test.mjs
python tools/phase2_qa.py --output "$env:TEMP\vietsub-phase2-qa"
# Optional live external test, fixed public phrase only; empty output directory:
python tools/editor_live_smoke.py --live --output "$env:TEMP\vietsub-capcut-live"
```

The deterministic UI runner uses a synthetic provider tone for audio alignment/mixing; it does not represent a live CapCut voice. Never run live TTS tests in CI automatically. Phase 2 local build smoke is validation only, not permission to tag/push/release v2.0.0. The integrated v2.0.1 now includes experimental CapCut Draft export: prepared media and draft JSON are bundled together, speed/tone remain timeline properties, and visual edits/audio mixing are baked. Verify import on the installed CapCut desktop version and relink media after moving the bundle.

## English

### 1. Prepare the version

1. Set `APP_VERSION` in `app.py`, for example `APP_VERSION = "1.5.4"`.
2. Move the notes under `## [Unreleased]` in `CHANGELOG.md` into a new `## [1.5.4] - YYYY-MM-DD` section. `tests/test_release_contract.py` fails when `APP_VERSION` differs from the newest changelog heading.
3. Run the checks:

   ```powershell
   python -m py_compile app.py automate.py tools/make_release.py
   python -m unittest discover -s tests -v
   node --check static/app.js
   node --check static/workbench.js
   node --check static/sakuga-vfx.js
   node --test tests/*.js tests/frontend/*.test.mjs
   python tools/workbench_qa.py --output "$env:TEMP\vietsub-workbench-qa"
   ```

   The UI check needs Playwright Chromium (`python -m playwright install chromium`) and FFmpeg/FFprobe in `PATH`. It uses temporary configuration/media and controlled queue fixtures. Open the 1280×720 and 1920×1080 screenshots before committing; confirm controls are not obscured and text stays within its pane. CI uploads the report and screenshots.

4. Commit and push to `main`, then wait for the **Tests and package smoke** workflow to pass.

### 2. Get the two release files

Use one of these sources:

- **CI build (recommended).** Open the workflow run for the exact commit you will tag and download the `VietSub-Studio-Portable-v1.5.4-release-assets` artifact. GitHub delivers every artifact wrapped in one more ZIP: extract it into an empty folder and use the two files inside. Never upload the artifact ZIP itself. Artifacts expire after the repository's retention period (90 days by default).
- **Local build.** Run `build_portable.bat`. It builds the EXE, writes the two files to `release\`, and checks them with `tests/test_release_contract.py`.

You must end up with exactly these two files:

```text
VietSub-Studio-Portable-v1.5.4.zip
VietSub-Studio-Portable-v1.5.4.zip.sha256.txt
```

To check a folder that holds only these two files, run from the repository root at the tagged commit:

```powershell
$env:RELEASE_DIR = "C:\path\to\folder"
python -m unittest discover -s tests -p test_release_contract.py -v
Remove-Item Env:RELEASE_DIR
```

### 3. Publish

1. Create the tag `v1.5.4` on that commit, either with `git tag v1.5.4` and `git push origin v1.5.4`, or from the GitHub release form.
2. Create a GitHub Release for `v1.5.4`, paste the 1.5.4 section of `CHANGELOG.md`, and attach the two files without renaming, re-zipping, or editing them.
3. Publish it as a normal release. Drafts and pre-releases are invisible to the updater, because it reads `releases/latest`.

### 4. Check the published release

1. The release page lists both files with exactly the names above.
2. On a test machine, start an older portable build (for example v1.5.3) from a writable folder. The update banner should offer **Tải & cài ngay**. Install it and confirm that the app restarts and shows the new version.
3. If the old build opens GitHub instead of installing, compare the asset names and the ZIP layout with the contract below. Fix the files on the same release; publish a new version only when the EXE itself is wrong.
4. Optional: update the version shown on the landing page (`landing-page/components/vietsub-site.tsx` and `relay-site.tsx`), then run `npm run lint`, `npm run typecheck`, and `npm run build` in `landing-page`.

### Updater contract

Published updaters depend on every point below. `tools/make_release.py` produces it and `tests/test_release_contract.py` checks it.

- The release is published (not a draft or pre-release) with the tag `v<version>`, and the version is higher than the previous release.
- The ZIP is named `VietSub-Studio-Portable-v<version>.zip`, is at most 512 MiB, and contains `VietSub Studio/VietSub Studio.exe` (a Windows `MZ` executable), `VietSub Studio/SHA256.txt`, `VietSub Studio/LICENSE`, and `VietSub Studio/HUONG_DAN_SU_DUNG.txt`.
- The checksum file is named `VietSub-Studio-Portable-v<version>.zip.sha256.txt` and holds one line: the 64-character SHA256 of the ZIP, two spaces, and the ZIP name.
- Never publish a flat ZIP without the `VietSub Studio/` folder, a checksum named `.zip.sha256`, or the CI artifact ZIP. This is how the 1.5.3 release broke automatic updates for 1.3.0 to 1.5.2.
- `build-manifest.json`, `dependencies.txt`, and the bare EXE belong to the `VietSub-Studio-build-info-<commit>` artifact. The updater does not need them on the release.

### Ed25519 Signed Releases (v2.1.0+)

Starting with the Phase 1 Security Hardening updater (`vietsub/updater.py`), in-app automatic updates fail closed unless an Ed25519 signature asset is also present and verified against `RELEASE_PUBLIC_KEYS` embedded in `vietsub/updater.py`:

- Release assets required for signed auto-updates (3 files):
  ```text
  VietSub-Studio-Portable-v<version>.zip
  VietSub-Studio-Portable-v<version>.zip.sha256.txt
  VietSub-Studio-Portable-v<version>.zip.sig
  ```
- Smooth upgrade path: builds from `v2.0.x` upgrading to `v2.1.0` ignore `.zip.sig` and verify SHA-256 + GitHub digest as before; once updated to `v2.1.0`, every subsequent auto-update enforces SHA-256 + `.zip.sig` + hardcoded Ed25519 public keys (with no env override in frozen builds).
- One-time offline key generation (outside the repository — never commit `*.pem` or upload the private key to GitHub Secrets):
  ```powershell
  python tools/sign_release.py keygen --out "$env:USERPROFILE\.vietsub-signing\release-ed25519.pem"
  ```
  Paste the printed `"<key_id>": "<public_key_hex>"` into `RELEASE_PUBLIC_KEYS` in `vietsub/updater.py` before tagging `v2.1.0`.
- Signing CI-built release assets offline after extracting `VietSub-Studio-Portable-v<version>-release-assets`:
  ```powershell
  python tools/sign_release.py sign --release-dir "C:\path\to\release" --key "$env:USERPROFILE\.vietsub-signing\release-ed25519.pem"
  $env:RELEASE_DIR = "C:\path\to\release"
  $env:REQUIRE_RELEASE_SIGNATURE = "1"
  python -m unittest discover -s tests -p test_release_contract.py -v
  Remove-Item Env:RELEASE_DIR, Env:REQUIRE_RELEASE_SIGNATURE
  ```
- CI `package-smoke` continues running `make_release.py` in unsigned staging mode (2 files) and validates signing/verification using ephemeral keys inside `MakeReleaseTests`, keeping private keys completely out of GitHub.

## Tiếng Việt

### 1. Chuẩn bị phiên bản

1. Sửa `APP_VERSION` trong `app.py`, ví dụ `APP_VERSION = "1.5.4"`.
2. Chuyển ghi chú dưới `## [Unreleased]` trong `CHANGELOG.md` sang mục mới `## [1.5.4] - YYYY-MM-DD`. `tests/test_release_contract.py` sẽ báo lỗi nếu `APP_VERSION` khác tiêu đề phiên bản mới nhất trong changelog.
3. Chạy kiểm tra:

   ```powershell
   python -m py_compile app.py automate.py tools/make_release.py
   python -m unittest discover -s tests -v
   node --check static/app.js
   node --check static/workbench.js
   node --check static/sakuga-vfx.js
   node --test tests/*.js tests/frontend/*.test.mjs
   python tools/workbench_qa.py --output "$env:TEMP\vietsub-workbench-qa"
   ```

   Kiểm thử UI cần Chromium của Playwright (`python -m playwright install chromium`) và FFmpeg/FFprobe trong `PATH`. Cấu hình/media dùng thư mục tạm; hàng đợi dùng fixture. Mở ảnh 1280×720 và 1920×1080 trước commit để kiểm tra nút bị che và chữ tràn pane. CI lưu report cùng ảnh dưới dạng artifact.

4. Commit, push lên `main` rồi chờ workflow **Tests and package smoke** chạy xanh.

### 2. Lấy hai file release

Chọn một trong hai nguồn:

- **Bản build của CI (khuyên dùng).** Mở lượt chạy workflow của đúng commit sẽ gắn tag và tải artifact `VietSub-Studio-Portable-v1.5.4-release-assets`. GitHub luôn bọc artifact trong thêm một lớp ZIP: giải nén vào một thư mục trống rồi dùng hai file bên trong. Không bao giờ upload chính file ZIP của artifact. Artifact hết hạn theo thời gian lưu của repository (mặc định 90 ngày).
- **Build trên máy.** Chạy `build_portable.bat`. Script build EXE, ghi hai file vào `release\` và kiểm tra bằng `tests/test_release_contract.py`.

Kết quả phải đúng hai file:

```text
VietSub-Studio-Portable-v1.5.4.zip
VietSub-Studio-Portable-v1.5.4.zip.sha256.txt
```

Để kiểm tra một thư mục chỉ chứa hai file này, chạy từ thư mục gốc repository ở đúng commit đã gắn tag:

```powershell
$env:RELEASE_DIR = "C:\duong\dan\thu-muc"
python -m unittest discover -s tests -p test_release_contract.py -v
Remove-Item Env:RELEASE_DIR
```

### 3. Phát hành

1. Tạo tag `v1.5.4` trên commit đó, bằng `git tag v1.5.4` và `git push origin v1.5.4`, hoặc tạo ngay trong form release của GitHub.
2. Tạo GitHub Release cho `v1.5.4`, dán mục 1.5.4 của `CHANGELOG.md` và đính kèm hai file, không đổi tên, không nén lại, không sửa nội dung.
3. Publish thành release bình thường. Trình cập nhật đọc `releases/latest`, nên không thấy bản nháp (draft) hay pre-release.

### 4. Kiểm tra sau khi phát hành

1. Trang release có đúng hai file với đúng tên ở trên.
2. Trên máy thử, mở một bản portable cũ (ví dụ v1.5.3) từ thư mục có quyền ghi. Banner cập nhật phải có nút **Tải & cài ngay**. Cài thử và xác nhận app tự mở lại với phiên bản mới.
3. Nếu bản cũ mở GitHub thay vì tự cài, hãy so tên file và cấu trúc ZIP với hợp đồng bên dưới. Sửa file ngay trên release đó; chỉ phát hành phiên bản mới khi chính EXE bị sai.
4. Tuỳ chọn: cập nhật số phiên bản trên landing page (`landing-page/components/vietsub-site.tsx` và `relay-site.tsx`), rồi chạy `npm run lint`, `npm run typecheck` và `npm run build` trong `landing-page`.

### Hợp đồng với trình cập nhật

Các bản đã phát hành phụ thuộc vào từng điểm dưới đây. `tools/make_release.py` tạo đúng định dạng này và `tests/test_release_contract.py` kiểm tra nó.

- Release đã publish (không phải draft hay pre-release), gắn tag `v<phiên bản>`, và phiên bản cao hơn release trước.
- ZIP tên `VietSub-Studio-Portable-v<phiên bản>.zip`, không quá 512 MiB, chứa `VietSub Studio/VietSub Studio.exe` (file thực thi Windows bắt đầu bằng `MZ`), `VietSub Studio/SHA256.txt`, `VietSub Studio/LICENSE` và `VietSub Studio/HUONG_DAN_SU_DUNG.txt`.
- File checksum tên `VietSub-Studio-Portable-v<phiên bản>.zip.sha256.txt`, gồm một dòng: SHA256 64 ký tự của ZIP, hai dấu cách, rồi tên ZIP.
- Không bao giờ phát hành ZIP phẳng thiếu thư mục `VietSub Studio/`, file checksum tên `.zip.sha256`, hoặc file ZIP của artifact CI. Release 1.5.3 hỏng tự cập nhật cho các bản 1.3.0 đến 1.5.2 chính vì lỗi này.
- `build-manifest.json`, `dependencies.txt` và file EXE rời thuộc artifact `VietSub-Studio-build-info-<commit>`. Trình cập nhật không cần chúng trên release.

Release phát hành chính thức cần ký Ed25519 ngoại tuyến bằng `tools/sign_release.py` (tạo tệp `.zip.sig` thứ 3 bên cạnh `.zip` và `.zip.sha256.txt`); **tuyệt đối không lưu private key trong repo hay GitHub Secrets**, luôn bật 2FA cho tài khoản có quyền phát hành. Bản `v2.0.x` lên `v2.1.0` dùng SHA-256 cũ; từ `v2.1.0` trở đi trình cập nhật bắt buộc xác minh cả SHA-256 và chữ ký Ed25519.
