# VietSub Studio → v2.0.0 Creator Workbench

## Phạm vi và baseline (đọc trước khi triển khai)

- Checkout thực tế: `31b5a24`, nhánh `main`, `APP_VERSION = "1.8.0"`. Không hạ về 1.7.0; giữ tương thích cả 1.7.0 và 1.8.0.
- Baseline: 397 Python tests (394 pass, 3 skip); 70 Node tests pass. Fixture độc lập xác nhận 115 ID DOM v1.7.0, tag/field/default và metadata.
- Đã đọc luồng Flask/queue/project/OCR/settings/security, CLI, các adapter storage/publish/browser/lifecycle, template, CSS, JS, VFX và runner QA hiện có. Backend hiện có bulk-delete và headless lifecycle nên sẽ gia cố/tái sử dụng, không nhân đôi.
- Chỉ thực hiện **Phase 1**, dừng để duyệt. Chưa đổi APP_VERSION, tag, commit/push, build phát hành hay triển khai render/TTS/upload thật.

## Ràng buộc kiến trúc

1. `app.py` tiếp tục là compatibility facade: route cũ, response cũ, queue schema, CLI và các ID cũ không bị đổi/xóa. Chức năng mới chỉ thêm trường/route/module.
2. Frontend vanilla JS + Flask hiện tại được giữ nguyên, không đưa React/Tailwind/CDN hay runtime mới vào portable. Sakuga không bị thay thế; giữ palette #07110e/#111318/#f5c542/#7ee0b8/#ff6b3d và ba font cục bộ.
3. Activity Rail 48px nằm trong grid viewport. VietSub luôn mounted, không nhân bản/move các control; hai panel mới `hidden`/`inert` khi không chọn, shared console giữ trạng thái. Đổi workspace không reload, không reset preview/crop/job/polling.
4. Cleanup chỉ unlink inventory đã kiểm tra đường dẫn tuyệt đối/identity, không xóa đệ quy folder. Dry-run không ghi đĩa. Giữ SRT mặc định, bảo vệ file dùng chung/running/prefetch, giữ failed record để retry.
5. Browser dùng profile riêng; đóng bằng identity PID + executable + profile + CDP session, không kill theo tên. Lease bao toàn bộ công việc, không tính thời gian chờ Captcha là idle. Captcha chỉ chuyển cửa sổ để người dùng xác minh, không giải/bypass.
6. Manifest không chứa cookie/API key. API handoff nhận job ID (không nhận path tùy ý), áp dụng token/origin/CSP cũ. File mới atomic UTF-8; dữ liệu không hợp lệ không được ghi đè im lặng.
7. Runtime nặng trong Phase 2/3 phải lazy-check/lazy-import. Core FFmpeg + HTTP TTS không phụ thuộc ONNX/Demucs/inpainting. Chỉ cài bổ sung khi người dùng yêu cầu; không tự pip install lúc import hoặc khởi động.

## Sơ đồ đích

```text
Flask compatibility facade (app.py) + CLI (automate.py)
  ├─ VietSub queue / download / OCR / translate hiện có
  ├─ vietsub/manifest.py  ← hợp đồng chung, atomic load/update/migrate
  ├─ vietsub/storage.py   ← inventory + cleanup an toàn
  ├─ vietsub/editor/      ← Phase 2: filtergraph, render, audio, TTS providers
  └─ vietsub/uploader/    ← Phase 3: profiles, adapters, queue, artifacts

Activity Rail ── VietSub ── manifest handoff ── Auto Edit ── Publish Hub
                    └──────────── handoff trực tiếp ───────────┘
                       Shared status / logs / settings
```

### Manifest v1 (file `<video-stem>.project.json` trong project directory)

- `schema_version`, stable `project_id`, `project_name`, `updated_at`.
- `source`: original/source video, source URL, video ID.
- `assets`: video đang dùng, source video, raw SRT, Vietnamese SRT, thumbnail, edited video, TTS audio.
- `crop_box`: normalized source coordinates `{x,y,width,height}` (0–1), đi kèm `source_resolution`; không lấy tọa độ CSS preview.
- `metadata`: title, description, hashtags; đọc pack `.publish.json` hiện có khi tạo lần đầu.
- `stages`: VietSub status/error; editor/uploader state mặc định `not_started`.
- Giữ `project.json` cũ và mọi key cũ. Lazy migration từ kết quả queue/legacy khi handoff; không quét/ghi hàng loạt project cũ lúc boot. Update VietSub không làm mất metadata/edited assets/state của module khác.
- GET snapshot không có tác dụng phụ. POST handoff kiểm tra video/subtitle trên đĩa và trả manifest + capability URL xem video của đúng job; không hứa render/upload trong Phase 1.

## Phase 1 — Khung 3 workspace + nâng cấp cốt lõi (thực hiện lần này)

1. Ghi baseline, giữ fixture 115 IDs; bổ sung Activity Rail 48px, tab keyboard/ARIA, hai workspace staging có chỉ báo rõ Phase 2/3, shared console. Các control render/upload chưa triển khai không giả vờ chạy được.
2. Chuẩn hóa manifest và ghi song song với legacy manifest; lưu đúng crop/resolution OCR thực tế, lazy handoff cho kết quả/historical jobs. Quick actions ở kết quả và từng job thành công, hiển thị tên/video/sub/crop/metadata khi nhận.
3. Rà soát và gia cố bulk-delete sẵn có: default keep_srt, dry-run, shared paths/hardlinks, lỗi từng job, chạy đồng thời, bảo toàn manifest/metadata. Hoàn thiện nhãn chọn nhanh, badge và cảnh báo auto-delete (mất video thì không thể edit/upload).
4. Gia cố headless lifecycle sẵn có: startup tab reuse, không thừa blank tab, idle 60s, cookie continuity, trở về headless sau xác minh thành công; không đóng headed login/challenge chưa xong.
5. Test: manifest round-trip/migration/corrupt/future version/atomic failure/crop, API token/validation/media scope/handoff, cleanup regressions, browser mock + Chrome local smoke. Node integration + 115 IDs + VFX. Playwright real Flask/asset/media ở 1280×720, 1440×900, 1920×1080 (kèm DPI hiện có), đổi workspace không reload/tràn/crop mất; 0 console/CSP error.
6. Review final diff; báo số test pass/skip, ảnh QA và giới hạn smoke thực tế để duyệt. Không đi tiếp Phase 2.

### API bổ sung Phase 1

- `GET /api/projects/<job_id>?workspace=editor|uploader`: snapshot normalized, không tạo file; uploader ưu tiên edited video đã có, editor dùng video nguồn.
- `POST /api/projects/<job_id>/handoff` body `{workspace: "editor" | "uploader"}`: kiểm tra job thành công/video/sub tồn tại, atomic persist normalized manifest, trả snapshot. Không nhận đường dẫn tùy ý.
- `GET /api/projects/<job_id>/video?signature=<HMAC>&asset=source|edited`: chỉ stream asset video của job, qua capability URL ký theo job + path + phiên app; không đọc path query, không mở API file tùy ý.
- `POST /api/queue/bulk-delete`: giữ contract v1.8 có sẵn `ids/delete_files/keep_srt/dry_run`; chỉ thêm gia cố nếu có lỗi.

## Phase 2 — Auto Edit (chỉ kế hoạch, sau duyệt)

- `editor/filters.py`, `renderer.py`: pure filtergraph builder từ normalized crop; clamp/padding; boxblur/delogo/solid_bar; ASS source-sized style presets, banner, PNG opacity/corner, 9:16 blur background. FFmpeg command argv, không shell; progress pipe parse %/fps; cancel và atomic output. NVENC phải probe encode thật (không chỉ tên encoder), libx264 fallback.
- `editor/audio.py`, `separation.py`: extract/mute FFmpeg; optional audio-separator/ONNX adapter giữ instrumental/BGM/SFX, thiếu runtime trả trạng thái unavailable thay vì crash. Không bundle model/onnx/demucs vào portable mặc định.
- `editor/tts.py`, `capcut_tts.py`, `edge_tts.py`: provider protocol `voices/preview/synthesize`; Python HTTP client CapCut timeout/retry/schema validation; fallback có thông báo. Credential/signature/endpoint nội bộ là rủi ro cần xác minh với upstream tại thời điểm triển khai, không hardcode suy đoán.
- `editor/alignment.py`: cue timestamp timeline, atempo chain giới hạn tốc độ, silence/trim tránh overlap, mix sidechaincompress ducking. Cache theo cue/text/provider/voice/speed/pitch.
- Preview crop riêng dùng source coordinates; save manifest revisions. Editor queue/progress API tách khỏi VietSub queue để không phá hợp đồng.
- CapCut Draft là optional exporter, không điều kiện hoàn thành core. Test pure graph/cue + mock HTTP + FFmpeg synthetic integration trước real render.

## Phase 3 — Auto Upload đa nền tảng (chỉ kế hoạch)

- `uploader/profiles.py`: UUID profile theo account/platform, thư mục độc lập; storage_state/cookie không nằm manifest, không trả cho UI, quyền file cục bộ hạn chế. Manual login/refresh và probe session, không giải Captcha.
- `uploader/adapters/{tiktok,youtube,facebook,douyin,bilibili,xiaohongshu}.py`: adapter protocol `login/probe/upload`; Playwright/Patchright lazy import. Không chia sẻ context giữa account; khóa từng profile tránh hai Chrome cùng ghi.
- `uploader/queue.py`: persisted states queued→running→success/error/needs_verification/cancelled; schedule timezone-aware Asia/Saigon và idempotency để restart không đăng trùng. Mỗi account là child job cách ly lỗi.
- Publish Hub đọc edited video (fallback nguồn khi user chủ động chọn), metadata manifest, chọn nhiều account, public/private/draft mapping theo khả năng nền tảng (không hứa chế độ không hỗ trợ). Hẹn giờ local yêu cầu app chạy; remote platform schedule chỉ khi platform hỗ trợ.
- Metadata AI tái sử dụng translation provider API/Notebook có sẵn, thêm Gemini API riêng nếu cần. Screenshot/log redacted theo job; không log cookie/token.
- Test state machine/profile isolation/mock Playwright, verification failure isolation, schedule/restart/idempotency; real smoke chỉ bằng account/video do người dùng cho phép, ưu tiên draft.

## Phase 4 — Kiểm thử, portable và phát hành (chỉ kế hoạch)

- Chạy Node syntax tất cả JS + `node --test tests/frontend/*.test.mjs`; Python compile và `python -m unittest discover -s tests -v`; Playwright 3 resolutions + CSP, thực hiện lại regression 115 IDs/VFX/fonts.
- Optional runtimes absent test: core app/editor FFmpeg/CapCut client vẫn import/chạy. Test có/không GPU/FFmpeg/browser/network và timeouts/cancel.
- Cập nhật APP_VERSION=2.0.0 đồng bộ changelog/release metadata/docs sau khi 3 phân hệ hoàn tất; thêm hướng dẫn optional install trong RELEASING, giữ ZIP updater contract.
- Build portable EXE trong môi trường build chính thức và clean-machine smoke (không tự coi py_compile là EXE smoke); kiểm tra fonts/static/new packages, không bundle AI models.
- Review không stage hi.txt/plan_v2.txt/artifact/token/profile; commit có chọn lọc, push main theo phê duyệt phát hành, theo dõi GitHub Actions bằng SHA cho đến xanh. Không tuyên bố CI remote xanh nếu chưa quan sát run.

## Cổng duyệt

Phase 1 chỉ được báo hoàn tất khi regression xanh, manifest/handoff và 3 workspace hoạt động thực, QA bằng browser có bằng chứng. Live Captcha của nền tảng cần người dùng xác minh nên test local/mocked không được gọi là đã qua Captcha production. Phase 2–4 cần duyệt tiếp.
