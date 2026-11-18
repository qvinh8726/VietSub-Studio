# Releasing / Phát hành

These steps publish a release that every portable build since v1.3.0 can install by itself. The examples use `1.5.4`; replace it with the version you are releasing.

Các bước dưới đây phát hành một bản mà mọi bản portable từ v1.3.0 trở lên tự cài được. Ví dụ dùng `1.5.4`; hãy thay bằng phiên bản bạn phát hành.

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
   node --test tests/frontend/*.test.mjs
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

Releases are not code-signed yet. Anyone who can publish a release on this repository can publish a matching checksum, so keep two-factor authentication enabled on every account with release access.

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
   node --test tests/frontend/*.test.mjs
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

Release chưa được ký số. Ai publish được release trên repository này cũng publish được checksum khớp, nên hãy bật xác thực hai lớp cho mọi tài khoản có quyền phát hành.
