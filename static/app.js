        const byId = id => document.getElementById(id);
        const apiToken = document.querySelector('meta[name="api-token"]').content;
        const appVersion = document.querySelector('meta[name="app-version"]').content;
        const steps = ['resolve', 'download', 'ocr', 'translate', 'done'];
        const statusLabels = { queued: 'Đang chờ', running: 'Đang chạy', success: 'Hoàn tất', error: 'Lỗi', cancelled: 'Đã huỷ' };
        const stepIndex = { resolve: 0, download: 1, ocr: 2, translate: 3, done: 4 };
        let currentPreview = null;
        let previewRequestId = 0;
        let autoProjectName = '';
        let healthReadiness = {local: false, douyin: false, sources: {}};
        const sourceLabels = {douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu', bilibili: 'Bilibili', tiktok: 'TikTok', youtube: 'YouTube', local: 'Video trên máy'};
        let sourceSessionSites = [];
        let sourceCookiesSet = {};
        let downloadWatchTimer = null;
        let crop = { crop_x: 0, crop_y: 0, crop_width: 1, crop_height: 1 };
        let dragState = null;
        let queueJobs = [];
        let queueRevision = -1;
        let queueLoadInFlight = false;
        let lastQueueSignature = '';
        const selectedQueueIds = new Set();
        const finishedStatuses = new Set(['success', 'error', 'cancelled']);
        let queueEpoch = 0;
        let bulkEstimateSequence = 0;
        let bulkEstimate = null;
        let bulkDeleteBusy = false;
        let bulkDialogOpen = false;
        let bulkRestoreFocus = null;
        let batchImportBusy = false;
        let localPreviewBusy = false;
        let linkPreviewBusy = false;
        let displayedLogCount = 0;
        let progressJobId = '';
        let resultOpenPath = '';
        let visibleResult = null, resultJobId = '';
        let handoffSequence = 0, activeHandoff = null;
        const pendingHandoffs = new Set(), handoffStates = new Map();
        let queueHandoffViews = [];
        const workspaceProjects = {editor: '', uploader: ''}, projectReadSequence = {editor: 0, uploader: 0};
        const workspaceLabels = {editor: 'Auto Edit', uploader: 'Đăng đa nền tảng'};
        const handoffLabels = {editor: 'Chuyển sang Auto Edit →', uploader: 'Đẩy sang Đăng đa nền tảng →'};
        let toastTimer = null;
        let automaticUpdateSupported = false;
        let pollFailureCount = 0;
        let translationProviderPresets = {};
        let translationApiKeySet = false;
        let lastTranslationProvider = 'notebook';
        let defaultTranslationInstructions = '';
        let translationInstructionPresets = {};
        let glossaryStore = {active_id: 'default', profiles: [{id: 'default', name: 'Mặc định', style_note: '', entries: []}]};
        let glossariesLoaded = false;
        let connectionOk = true;
        let cancellingJobId = '';
        let translationProviderName = 'Gemini';
        // Short name of the automation browser chosen in Settings (Chrome by default).
        let browserName = 'Chrome';

        function showToast(message, type = 'success') {
            clearTimeout(toastTimer);
            const toast = byId('toast');
            toast.textContent = message;
            toast.className = `toast ${type === 'error' ? 'error' : ''} show`;
            toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3500);
        }

        function setConnection(ok) {
            if (connectionOk === ok) return;
            connectionOk = ok;
            byId('connection-banner').hidden = ok;
            if (ok) {
                pollFailureCount = 0;
                if (byId('process-caption').textContent.startsWith('Mất kết nối')) byId('process-caption').textContent = 'Sẵn sàng';
                showToast('Đã kết nối lại với VietSub Studio.');
            }
        }

        // timeoutMs: 0 means no limit (preview download, MP4 upload, update install).
        async function fetchJson(url, options = {}) {
            const {timeoutMs: requestedTimeout, ...fetchOptions} = options;
            const method = (fetchOptions.method || 'GET').toUpperCase();
            const timeoutMs = requestedTimeout ?? (method === 'GET' ? 15000 : 60000);
            const headers = new Headers(fetchOptions.headers || {});
            headers.set('X-VietSub-Token', apiToken);
            const nextOptions = {...fetchOptions, headers};
            if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && !headers.has('Content-Type') && !(fetchOptions.body instanceof FormData)) {
                headers.set('Content-Type', 'application/json');
                if (fetchOptions.body == null) nextOptions.body = '{}';
            }
            const controller = timeoutMs ? new AbortController() : null;
            const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
            if (controller) nextOptions.signal = controller.signal;
            let response;
            try {
                response = await fetch(url, nextOptions);
            } catch (cause) {
                setConnection(false);
                const error = new Error(cause?.name === 'AbortError'
                    ? `VietSub Studio không phản hồi sau ${Math.round(timeoutMs / 1000)} giây.`
                    : 'Mất kết nối tới máy chủ cục bộ của VietSub Studio.');
                error.network = true;
                throw error;
            } finally {
                if (timer) clearTimeout(timer);
            }
            setConnection(true);
            let data = {};
            try { data = await response.json(); } catch (_) {}
            if (!response.ok) {
                const error = new Error(data.error || `HTTP ${response.status}`);
                error.status = response.status;
                throw error;
            }
            return data;
        }

        const busyActions = new Set();
        // Ignore a second click while the same action is still waiting for the server.
        async function runOnce(key, action) {
            if (busyActions.has(key)) return;
            busyActions.add(key);
            try { await action(); } finally { busyActions.delete(key); }
        }

        async function checkForUpdate() {
            const pill = byId('version-pill');
            try {
                const data = await fetchJson('/api/update');
                if (data.update_available) {
                    automaticUpdateSupported = Boolean(data.automatic_update_supported);
                    pill.className = 'status-pill update';
                    pill.querySelector('span').textContent = `Có v${data.latest_version}`;
                    byId('update-title').textContent = `VietSub Studio v${data.latest_version} đã sẵn sàng`;
                    byId('update-copy').textContent = automaticUpdateSupported
                        ? `Bạn đang dùng v${data.current_version}. App sẽ tự tải, kiểm tra checksum, cài và mở lại.`
                        : `Bạn đang dùng v${data.current_version}. Bản này cần mở GitHub để tải cập nhật thủ công.`;
                    byId('update-btn').textContent = automaticUpdateSupported ? 'Tải & cài ngay →' : 'Mở trang tải →';
                    byId('update-banner').hidden = false;
                    showToast(`Có bản cập nhật v${data.latest_version}.`);
                } else {
                    pill.className = 'status-pill ready';
                    pill.querySelector('span').textContent = `v${data.current_version} · mới nhất`;
                }
            } catch (_) {
                pill.querySelector('span').textContent = `v${appVersion}`;
            }
        }

        async function installUpdate() {
            const button = byId('update-btn');
            button.disabled = true;
            button.textContent = automaticUpdateSupported ? 'Đang tải & kiểm tra…' : 'Đang mở trang tải…';
            try {
                if (!automaticUpdateSupported) {
                    await fetchJson('/api/update/open', {method: 'POST'});
                    showToast('Đã mở trang GitHub Releases chính thức.');
                    button.disabled = false;
                    button.textContent = 'Mở trang tải →';
                    return;
                }
                const data = await fetchJson('/api/update/install', {method: 'POST', timeoutMs: 0});
                button.textContent = 'Đang khởi động lại…';
                byId('update-copy').textContent = data.message || 'Đã tải xong. App đang cài bản mới và sẽ tự mở lại.';
                showToast(`Đang cài VietSub Studio v${data.version}.`);
            } catch (error) {
                if (error.status === 409) {
                    // A job or preview is still running: wait and let the user try again later.
                    showToast(error.message, 'error');
                    button.disabled = false;
                    button.textContent = 'Tải & cài ngay →';
                    return;
                }
                try {
                    await fetchJson('/api/update/open', {method: 'POST'});
                    showToast(`${error.message} Đã mở GitHub để cập nhật thủ công.`, 'error');
                } catch (_) {
                    showToast(error.message, 'error');
                }
                button.disabled = false;
                button.textContent = 'Mở trang tải →';
                automaticUpdateSupported = false;
            }
        }

        function cleanName(value) {
            return (value || 'Tên video').replace(/[<>:"/\\|?*\x00-\x1F]/g, ' ').replace(/\s+/g, ' ').trim() || 'Tên video';
        }

        function updateNamePreview() {
            const name = cleanName(byId('project-name').value || currentPreview?.video_title);
            byId('preview-video-name').textContent = `${name}.mp4`;
            byId('preview-sub-name').textContent = `${name}.raw.srt · ${name}.vi.srt`;
        }

        function setUrlError(message = '') {
            const input = byId('url-input');
            byId('url-error').textContent = message;
            input.classList.toggle('invalid', Boolean(message));
            input.setAttribute('aria-invalid', message ? 'true' : 'false');
        }

        function invalidatePreview(status = 'Nguồn đã thay đổi · hãy tạo preview lại') {
            previewRequestId += 1;
            currentPreview = null;
            const player = byId('preview-player');
            player.pause(); player.removeAttribute('src'); player.removeAttribute('data-preview-id'); player.load(); player.hidden = true;
            const stage = byId('video-stage');
            stage.style.aspectRatio = ''; stage.style.minHeight = ''; stage.style.width = ''; stage.style.height = '';
            byId('preview-placeholder').hidden = false; byId('crop-box').hidden = true; byId('crop-tools').hidden = true;
            byId('preview-title').textContent = 'Chưa có video'; byId('crop-summary').textContent = 'Crop: —';
            byId('preview-status').value = status; byId('add-queue-btn').disabled = true;
            window.VietSubWorkbench?.previewChanged(null);
        }

        function formatMegabytes(bytes) {
            return (Number(bytes || 0) / 1048576).toFixed(1).replace('.', ',');
        }

        // prefix 'preview' is the panel under the composer, 'job' the one in the job progress card.
        function renderDownloadProgress(download, prefix = 'job') {
            window.VietSubWorkbench?.downloadChanged(download, prefix);
            const box = byId(`${prefix}-download`);
            if (!download || !download.active) { box.hidden = true; return; }
            box.hidden = false;
            const phases = {start: 'Đang chuẩn bị tải', capture: 'Đang đọc trang video', captcha: `Đang chờ bạn thao tác trên ${browserName}`, download: 'Đang tải'};
            byId(`${prefix}-download-label`).textContent = `${phases[download.phase] || 'Đang tải'} · ${sourceLabels[download.source] || 'video'}`;
            const parts = [];
            if (download.percent != null) parts.push(`${download.percent}%`);
            if (download.downloaded) parts.push(download.total ? `${formatMegabytes(download.downloaded)}/${formatMegabytes(download.total)} MB` : `${formatMegabytes(download.downloaded)} MB`);
            if (download.speed) parts.push(`${formatMegabytes(download.speed)} MB/s`);
            byId(`${prefix}-download-stats`).textContent = parts.join(' · ');
            const known = download.percent != null;
            const bar = byId(`${prefix}-download-bar`);
            bar.setAttribute('aria-valuenow', String(known ? download.percent : 0));
            bar.classList.toggle('indeterminate', !known);
            byId(`${prefix}-download-fill`).style.width = `${known ? download.percent : 0}%`;
            const notice = byId(`${prefix}-download-notice`);
            notice.hidden = !download.notice;
            notice.textContent = download.notice || '';
        }

        // While a preview downloads, poll its live progress (percent, speed, captcha notice).
        function startDownloadWatch() {
            stopDownloadWatch();
            const tick = async () => {
                try {
                    const data = await fetchJson(`/api/progress?after=${displayedLogCount}`);
                    if (downloadWatchTimer) renderDownloadProgress(data.download, 'preview');
                } catch (_) {}
            };
            downloadWatchTimer = setInterval(tick, 700);
            tick();
        }

        function stopDownloadWatch() {
            if (downloadWatchTimer) clearInterval(downloadWatchTimer);
            downloadWatchTimer = null;
            renderDownloadProgress(null, 'preview');
        }

        // Match the backend's share-text extraction; validate whole host boundaries and
        // keep query tokens (notably Xiaohongshu xsec_token) intact.
        function parseShareLinks(text) {
            const domains = ['douyin.com', 'iesdouyin.com', 'kuaishou.com', 'chenzhongtech.com', 'gifshow.com',
                'xiaohongshu.com', 'bilibili.com', 'tiktok.com', 'youtube.com', 'youtube-nocookie.com'];
            const shortHosts = ['youtu.be', 'www.youtu.be', 'b23.tv', 'www.b23.tv', 'xhslink.com', 'www.xhslink.com'];
            const urls = [], unsupported = [], seen = new Set();
            let duplicates = 0;
            for (const match of String(text || '').matchAll(/https?:\/\/[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+/gi)) {
                const candidate = match[0].replace(/[.,;:!?)\]}'"]+$/g, '');
                try {
                    const url = new URL(candidate), host = url.hostname.toLowerCase().replace(/\.$/, '');
                    if (url.username || url.password || (!shortHosts.includes(host) && !domains.some(domain => host === domain || host.endsWith(`.${domain}`)))) {
                        unsupported.push(candidate); continue;
                    }
                    const value = url.href;
                    if (seen.has(value)) { duplicates += 1; continue; }
                    seen.add(value); urls.push(value);
                } catch (_) { unsupported.push(candidate); }
            }
            return {urls, unsupported, duplicates};
        }

        function refreshComposerControls() {
            const busy = batchImportBusy || localPreviewBusy || linkPreviewBusy;
            const parsed = parseShareLinks(byId('url-input').value);
            byId('char-count').textContent = parsed.urls.length ? `${parsed.urls.length} link` : `${byId('url-input').value.length} ký tự`;
            byId('prepare-btn').disabled = busy || parsed.urls.length > 1;
            byId('prepare-btn').title = parsed.urls.length > 1 ? 'Dùng Thêm nhiều link; preview chỉ dành cho một video.' : 'Tạo preview để chỉnh crop một video';
            byId('batch-queue-btn').disabled = busy || !parsed.urls.length;
            byId('batch-queue-btn').textContent = `Thêm ${parsed.urls.length || ''} link vào hàng đợi`.replace('  ', ' ');
            for (const id of ['local-video-btn', 'local-batch-btn', 'local-video-input', 'url-input']) byId(id).disabled = busy;
            byId('add-queue-btn').disabled = busy || !currentPreview?.video_resolution;
            byId('local-drop-zone').setAttribute('aria-disabled', String(busy));
        }

        async function enqueueSequentially(items, enqueue, progress = () => {}) {
            const failures = [];
            let added = 0;
            for (let index = 0; index < items.length; index += 1) {
                progress(index + 1, items.length);
                try { await enqueue(items[index], index); added += 1; }
                catch (error) { failures.push({item: items[index], error: error.message || String(error)}); }
            }
            return {added, failures};
        }

        function showBatchResult(result, label, note = '') {
            byId('batch-import-status').textContent = `Đã thêm ${result.added} video${result.failures.length ? ` · ${result.failures.length} lỗi` : ''}.${note ? ` ${note}` : ''}`;
            const list = byId('batch-import-errors'); list.replaceChildren();
            for (const failure of result.failures) {
                const item = document.createElement('li'); item.textContent = `${label(failure.item)}: ${failure.error}`; list.appendChild(item);
            }
            list.hidden = !result.failures.length;
        }

        async function importShareLinks() {
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy) return;
            const parsed = parseShareLinks(byId('url-input').value);
            if (!parsed.urls.length) { setUrlError('Chưa tìm thấy link từ nền tảng được hỗ trợ.'); return; }
            const lang = byId('ocr-lang').value;
            batchImportBusy = true; refreshComposerControls(); renderQueue(); setUrlError();
            byId('batch-import-errors').replaceChildren();
            try {
                const result = await enqueueSequentially(parsed.urls, url => fetchJson('/api/queue', {
                    method: 'POST', body: JSON.stringify({url, lang}),
                }), (index, total) => { byId('batch-import-status').textContent = `Đang thêm link ${index}/${total}…`; });
                // Keep failed inputs for review; successful inputs cannot be re-added by accident.
                const previousInput = byId('url-input').value;
                const nextInput = result.failures.map(failure => failure.item).join('\n');
                byId('url-input').value = nextInput;
                if (nextInput !== previousInput) invalidatePreview();
                const notes = [];
                if (parsed.duplicates) notes.push(`Bỏ ${parsed.duplicates} link trùng.`);
                if (parsed.unsupported.length) notes.push(`Bỏ ${parsed.unsupported.length} link ngoài nền tảng hỗ trợ.`);
                showBatchResult(result, value => value, notes.join(' '));
                queueRevision = -1; await loadQueue();
            } finally { batchImportBusy = false; refreshComposerControls(); renderQueue(); }
        }

        async function importLocalFiles(files) {
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy || !files.length) return;
            const lang = byId('ocr-lang').value;
            batchImportBusy = true; refreshComposerControls(); renderQueue();
            byId('batch-import-errors').replaceChildren();
            try {
                const result = await enqueueSequentially(files, async file => {
                    if (!/\.(mp4|mkv)$/i.test(file.name)) throw new Error('Nhập nhiều file hỗ trợ MP4 hoặc MKV.');
                    const form = new FormData(); form.append('video', file);
                    const preview = await fetchJson('/api/previews/local', {method: 'POST', body: form, timeoutMs: 0});
                    await fetchJson('/api/queue', {method: 'POST', body: JSON.stringify({
                        url: preview.source_url, preview_id: preview.id, lang,
                        crop_coords: preview.crop_coords, video_resolution: preview.video_resolution || null,
                    })});
                }, (index, total) => { byId('batch-import-status').textContent = `Đang nạp file ${index}/${total}…`; });
                showBatchResult(result, file => file.name);
                byId('local-video-input').value = '';
                queueRevision = -1; await loadQueue();
            } finally { batchImportBusy = false; refreshComposerControls(); renderQueue(); }
        }

        function dropLocalFiles(event) {
            event.preventDefault(); byId('local-drop-zone').classList.remove('is-dragover');
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy) return;
            const files = Array.from(event.dataTransfer?.files || []);
            if (files.length > 1) return importLocalFiles(files);
            if (files.length === 1) return prepareLocalPreview(files[0]);
        }

        async function preparePreview(event) {
            event.preventDefault();
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy) return;
            const url = byId('url-input').value.trim();
            if (!url) { setUrlError('Hãy dán link video hoặc đoạn chia sẻ.'); return; }
            if (parseShareLinks(url).urls.length > 1) { setUrlError('Dùng Thêm nhiều link để đưa từng video vào hàng đợi.'); return; }
            linkPreviewBusy = true; refreshComposerControls();
            setUrlError();
            invalidatePreview('Đang chuẩn bị…');
            const requestId = previewRequestId;
            const button = byId('prepare-btn');
            button.disabled = true;
            button.innerHTML = '<span style="display:inline-flex;gap:8px;align-items:center"><i class="spinner"></i> Đang tải preview</span>';
            startDownloadWatch();
            try {
                const data = await fetchJson('/api/previews', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url}), timeoutMs: 0 });
                if (requestId !== previewRequestId) return;
                applyPreview(data);
                showToast('Preview đã sẵn sàng. Kéo khung vàng vào vùng phụ đề.');
            } catch (error) {
                if (requestId !== previewRequestId) return;
                byId('preview-status').value = 'Chuẩn bị thất bại';
                showToast(error.message, 'error');
            } finally {
                stopDownloadWatch();
                linkPreviewBusy = false;
                button.textContent = 'Tải preview';
                refreshComposerControls();
            }
        }

        // In the desktop window the native dialog gives the real path: the video is used in
        // place (no upload, no %TEMP% copy). The browser fallback still uploads the MP4.
        async function chooseLocalVideo() {
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy) return;
            const api = window.pywebview && window.pywebview.api;
            if (!api || typeof api.choose_local_video !== 'function') { byId('local-video-input').click(); return; }
            localPreviewBusy = true; refreshComposerControls();
            invalidatePreview('Đang mở hộp thoại chọn video…');
            const requestId = previewRequestId;
            const button = byId('local-video-btn');
            button.disabled = true;
            try {
                const data = await api.choose_local_video();
                if (requestId !== previewRequestId) return;
                if (!data) { byId('preview-status').value = 'Chưa chọn video'; return; }
                if (data.error) { byId('preview-status').value = 'Không nạp được video'; showToast(data.error, 'error'); return; }
                applyPreview(data);
                showToast('Video trên máy đã sẵn sàng; app dùng file tại chỗ, không sao chép.');
            } catch (error) {
                if (requestId !== previewRequestId) return;
                byId('preview-status').value = 'Không nạp được video';
                showToast(error.message || String(error), 'error');
            } finally {
                localPreviewBusy = false; refreshComposerControls();
            }
        }

        function applyPreview(data) {
            currentPreview = data;
            if (data.source_type !== 'local') byId('url-input').value = data.source_url;
            const projectName = byId('project-name');
            if (data.video_title && (!projectName.value.trim() || projectName.value.trim() === autoProjectName)) {
                projectName.value = data.video_title;
                autoProjectName = data.video_title;
            } else if (projectName.value.trim() !== autoProjectName) {
                autoProjectName = '';
            }
            updateNamePreview();
            byId('preview-placeholder').hidden = true;
            const player = byId('preview-player');
            player.hidden = false;
            player.dataset.previewId = data.id;
            player.src = `${data.video_url}?v=${Date.now()}`;
            player.load();
            setCrop(data.crop_coords || {crop_x: 0, crop_y: 0, crop_width: 1, crop_height: 1});
            byId('crop-box').hidden = false;
            byId('crop-tools').hidden = false;
            const sourceLabel = data.source_type === 'local' ? 'LOCAL · ' : '';
            byId('preview-title').textContent = `${sourceLabel}${data.video_title || data.video_id}`;
            byId('preview-status').value = 'Đang đọc độ phân giải video…';
            byId('add-queue-btn').disabled = true;
            window.VietSubWorkbench?.previewChanged(data);
        }

        async function prepareLocalPreview(fileOrEvent) {
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy) return;
            const input = byId('local-video-input');
            const files = fileOrEvent?.name ? [fileOrEvent] : Array.from(input.files || []);
            if (files.length > 1) return importLocalFiles(files);
            const file = files[0];
            if (!file) return;
            localPreviewBusy = true; refreshComposerControls();
            invalidatePreview('Đang tải video lên app…');
            const requestId = previewRequestId;
            const button = byId('local-video-btn');
            button.disabled = true;
            button.innerHTML = '<span style="display:inline-flex;gap:8px;align-items:center"><i class="spinner"></i> Đang nạp video</span>';
            byId('preview-status').value = 'Đang tải video lên app…';
            const form = new FormData(); form.append('video', file);
            try {
                const data = await fetchJson('/api/previews/local', {method:'POST', body:form, timeoutMs: 0});
                if (requestId !== previewRequestId) return;
                applyPreview(data);
                showToast('Video local đã sẵn sàng để chỉnh vùng OCR.');
            } catch (error) {
                if (requestId !== previewRequestId) return;
                byId('preview-status').value = 'Không nạp được video';
                showToast(error.message, 'error');
            } finally {
                localPreviewBusy = false;
                input.value = '';
                button.textContent = 'Chọn video';
                refreshComposerControls();
            }
        }

        function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }

        function setCrop(next, updateInputs = true) {
            const minSize = .04;
            let x = clamp(Number(next.crop_x) || 0, 0, 1 - minSize);
            let y = clamp(Number(next.crop_y) || 0, 0, 1 - minSize);
            let width = clamp(Number(next.crop_width) || minSize, minSize, 1 - x);
            let height = clamp(Number(next.crop_height) || minSize, minSize, 1 - y);
            crop = { crop_x: x, crop_y: y, crop_width: width, crop_height: height };
            const box = byId('crop-box');
            box.style.left = `${x * 100}%`; box.style.top = `${y * 100}%`;
            box.style.width = `${width * 100}%`; box.style.height = `${height * 100}%`;
            if (updateInputs) {
                byId('crop-x').value = (x * 100).toFixed(1); byId('crop-y').value = (y * 100).toFixed(1);
                byId('crop-width').value = (width * 100).toFixed(1); byId('crop-height').value = (height * 100).toFixed(1);
            }
            byId('crop-summary').textContent = `Crop: x ${Math.round(x*100)} · y ${Math.round(y*100)} · ${Math.round(width*100)}×${Math.round(height*100)}%`;
        }

        function cropFromInputs() {
            setCrop({
                crop_x: Number(byId('crop-x').value) / 100,
                crop_y: Number(byId('crop-y').value) / 100,
                crop_width: Number(byId('crop-width').value) / 100,
                crop_height: Number(byId('crop-height').value) / 100
            });
        }

        function startCropDrag(event) {
            if (!currentPreview || event.button !== 0) return;
            const stage = byId('video-stage').getBoundingClientRect();
            if (!stage.width || !stage.height) return;
            dragState = { startX: event.clientX, startY: event.clientY, crop: {...crop}, handle: event.target.dataset.handle || 'move', stage };
            event.preventDefault();
            event.currentTarget.setPointerCapture?.(event.pointerId);
        }

        function moveCrop(event) {
            if (!dragState) return;
            const dx = (event.clientX - dragState.startX) / dragState.stage.width;
            const dy = (event.clientY - dragState.startY) / dragState.stage.height;
            const base = dragState.crop;
            let next = {...base};
            if (dragState.handle === 'move') {
                next.crop_x = clamp(base.crop_x + dx, 0, 1 - base.crop_width);
                next.crop_y = clamp(base.crop_y + dy, 0, 1 - base.crop_height);
            } else {
                const west = dragState.handle.includes('w');
                const north = dragState.handle.includes('n');
                if (west) { const right = base.crop_x + base.crop_width; next.crop_x = clamp(base.crop_x + dx, 0, right - .04); next.crop_width = right - next.crop_x; }
                else { next.crop_width = clamp(base.crop_width + dx, .04, 1 - base.crop_x); }
                if (north) { const bottom = base.crop_y + base.crop_height; next.crop_y = clamp(base.crop_y + dy, 0, bottom - .04); next.crop_height = bottom - next.crop_y; }
                else { next.crop_height = clamp(base.crop_height + dy, .04, 1 - base.crop_y); }
            }
            setCrop(next);
        }

        function endCropDrag() { dragState = null; }

        async function saveDefaultCrop() {
            try {
                await fetchJson('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({crop_coords: crop}) });
                byId('setting-crop').value = JSON.stringify(crop);
                showToast('Đã lưu vùng OCR làm mặc định.');
            } catch (error) { showToast(error.message, 'error'); }
        }

        async function addToQueue() {
            if (batchImportBusy || localPreviewBusy || linkPreviewBusy || !currentPreview) return;
            if (!currentPreview.video_resolution) {
                showToast('Đang đọc độ phân giải video; hãy chờ preview sẵn sàng.', 'error');
                return;
            }
            const button = byId('add-queue-btn');
            button.disabled = true;
            try {
                await fetchJson('/api/queue', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ url: currentPreview.source_url, preview_id: currentPreview.id, name: byId('project-name').value.trim(), lang: byId('ocr-lang').value, crop_coords: crop, video_resolution: currentPreview.video_resolution || null })
                });
                showToast('Đã thêm video vào hàng đợi.');
                clearComposer();
                await loadQueue();
            } catch (error) { showToast(error.message, 'error'); button.disabled = false; }
        }

        function clearComposer() {
            invalidatePreview('Chưa chuẩn bị');
            autoProjectName = '';
            byId('url-input').value = ''; byId('project-name').value = '';
            byId('local-video-input').value = '';
            byId('char-count').textContent = '0 ký tự'; updateNamePreview(); refreshComposerControls();
        }

        function sourceReady(source) {
            const sources = healthReadiness.sources || {};
            if (Object.prototype.hasOwnProperty.call(sources, source)) return Boolean(sources[source]);
            return source === 'local' ? Boolean(healthReadiness.local) : Boolean(healthReadiness.douyin);
        }

        function progressWidth(step, status) {
            if (status === 'success') return 100;
            return [8, 28, 52, 78, 100][stepIndex[step] ?? 0];
        }

        function formatDiskBytes(value) {
            const bytes = Math.max(0, Number(value) || 0);
            if (bytes < 1000) return `${Math.round(bytes)} B`;
            const units = ['KB', 'MB', 'GB', 'TB'];
            const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1000)), units.length);
            return `${(bytes / 1000 ** power).toLocaleString('vi-VN', {maximumFractionDigits: 1})} ${units[power - 1]}`;
        }

        function isOlderFinished(job, now = Date.now()) {
            if (!finishedStatuses.has(job.status)) return false;
            // Queue timestamps are Unix seconds. Prefer completion time over creation.
            const value = job.finished_at || job.created_at;
            const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value);
            return Number.isFinite(timestamp) && timestamp > 0 && timestamp < now - 3 * 86400000;
        }

        function selectedQueueJobs() {
            return queueJobs.filter(job => job.status !== 'running' && selectedQueueIds.has(job.id));
        }

        function bulkPayload() {
            return {ids: selectedQueueJobs().map(job => job.id),
                delete_files: bulkDialogOpen ? byId('bulk-delete-files').checked : true,
                keep_srt: bulkDialogOpen ? byId('bulk-keep-srt').checked : true};
        }

        function bulkEstimateKey() {
            return JSON.stringify([bulkPayload(), queueJobs.map(job => [job.id, job.status]), selectedQueueJobs().map(job =>
                [job.id, job.status, job.disk_bytes, job.disk_bytes_with_srt, job.finished_at])]);
        }

        function updateBulkControls() {
            const selectable = queueJobs.filter(job => job.status !== 'running');
            const count = selectedQueueJobs().length, all = byId('queue-select-all');
            all.checked = Boolean(selectable.length) && count === selectable.length;
            all.indeterminate = count > 0 && count < selectable.length;
            all.disabled = bulkDeleteBusy || !selectable.length;
            byId('queue-selection-count').textContent = `${count} đã chọn`;
            byId('bulk-delete-btn').disabled = bulkDeleteBusy || !count;
            byId('queue-clear-selection').disabled = bulkDeleteBusy || !count;
            byId('queue-select-success').disabled = bulkDeleteBusy || !queueJobs.some(job => job.status === 'success');
            byId('queue-select-finished').disabled = bulkDeleteBusy || !queueJobs.some(job => finishedStatuses.has(job.status));
            byId('queue-select-old').disabled = bulkDeleteBusy || !queueJobs.some(job => isOlderFinished(job));
            byId('clear-finished-btn').disabled = byId('queue-select-finished').disabled;
            const estimate = bulkEstimate?.key === bulkEstimateKey() ? bulkEstimate : null;
            const badge = byId('queue-disk-badge');
            badge.textContent = !count ? '0 B có thể dọn' : estimate?.data ? `${formatDiskBytes(estimate.data.reclaimable_bytes)} có thể dọn` : estimate?.error ? 'Chưa đo được' : 'Đang tính…';
            badge.setAttribute('aria-busy', String(count > 0 && !estimate?.data && !estimate?.error));
            if (!bulkDialogOpen) return;
            byId('bulk-delete-count').textContent = `${count} job đã chọn`;
            byId('bulk-keep-srt').disabled = bulkDeleteBusy || !byId('bulk-delete-files').checked;
            for (const id of ['bulk-delete-records', 'bulk-delete-files', 'bulk-delete-cancel']) byId(id).disabled = bulkDeleteBusy;
            const failures = estimate?.data?.failures || estimate?.failures || [];
            byId('bulk-estimate-retry').hidden = !estimate?.error && !failures.length;
            byId('bulk-estimate-retry').disabled = bulkDeleteBusy;
            const eligible = selectedQueueJobs().filter(job => !failures.some(failure => failure.id === job.id)).length;
            byId('bulk-delete-confirm').disabled = bulkDeleteBusy || !eligible || !estimate?.data || estimate.data.selected_count === 0;
            byId('bulk-delete-confirm').textContent = bulkDeleteBusy ? 'Đang xoá…' : byId('bulk-delete-files').checked ? 'Xoá video / file tạm' : 'Xoá bản ghi';
            byId('bulk-delete-estimate').textContent = estimate?.error ? `Không đo được dung lượng: ${estimate.error}` : estimate?.data ?
                `Có thể giải phóng ${formatDiskBytes(estimate.data.reclaimable_bytes)}.${byId('bulk-delete-files').checked ? ' Dung lượng thực tế được báo sau khi xoá.' : ' Toàn bộ file được giữ nguyên.'}` : 'Đang kiểm tra dung lượng và các file được phép xoá…';
            renderBulkFailures(failures);
        }

        function renderBulkFailures(failures) {
            const list = byId('bulk-delete-failures'); list.replaceChildren(); list.hidden = !failures.length;
            for (const failure of failures) {
                const item = document.createElement('li');
                const job = queueJobs.find(job => job.id === failure.id);
                item.textContent = `${job?.project_name || job?.video_title || failure.id}: ${failure.error}`;
                list.appendChild(item);
            }
        }

        async function refreshBulkEstimate(force = false) {
            const key = bulkEstimateKey();
            if (bulkDeleteBusy || (!force && bulkEstimate?.key === key)) { updateBulkControls(); return; }
            const sequence = ++bulkEstimateSequence;
            bulkEstimate = {key}; updateBulkControls();
            const payload = bulkPayload();
            if (!payload.ids.length) return;
            try {
                const data = await fetchJson('/api/queue/bulk-delete', {method: 'POST', body: JSON.stringify({...payload, dry_run: true})});
                if (sequence !== bulkEstimateSequence || key !== bulkEstimateKey()) return;
                if (!Number.isFinite(data.reclaimable_bytes) || !Number.isInteger(data.selected_count) || !Array.isArray(data.failures)) throw new Error('Máy chủ chưa trả về ước lượng hợp lệ.');
                bulkEstimate = {key, data};
            } catch (error) {
                if (sequence !== bulkEstimateSequence || key !== bulkEstimateKey()) return;
                bulkEstimate = {key, error: error.message};
            }
            updateBulkControls();
        }

        function setQueueSelection(mode) {
            if (bulkDeleteBusy) return;
            selectedQueueIds.clear();
            for (const job of queueJobs) {
                if (job.status === 'running') continue;
                if (mode === 'all' || (mode === 'success' && job.status === 'success') || (mode === 'finished' && finishedStatuses.has(job.status)) || (mode === 'old' && isOlderFinished(job))) selectedQueueIds.add(job.id);
            }
            renderQueue();
        }

        function openBulkDelete() {
            if (bulkDeleteBusy || !selectedQueueJobs().length) return;
            bulkRestoreFocus = document.activeElement;
            bulkDialogOpen = true;
            byId('bulk-delete-records').checked = true; byId('bulk-delete-files').checked = false; byId('bulk-keep-srt').checked = true;
            byId('bulk-delete-dialog').showModal();
            byId('bulk-delete-cancel').focus();
            refreshBulkEstimate();
        }

        function closeBulkDelete() {
            if (bulkDeleteBusy) return;
            bulkDialogOpen = false; byId('bulk-delete-dialog').close();
            (bulkRestoreFocus?.isConnected && !bulkRestoreFocus.disabled ? bulkRestoreFocus : byId('queue-title')).focus();
            refreshBulkEstimate();
        }

        async function deleteSelectedQueueJobs() {
            if (bulkDeleteBusy || !bulkDialogOpen || !bulkEstimate?.data || bulkEstimate.key !== bulkEstimateKey() || byId('bulk-delete-confirm').disabled) return;
            const payload = bulkPayload();
            bulkDeleteBusy = true; ++bulkEstimateSequence; ++queueEpoch;
            updateBulkControls(); renderQueue();
            try {
                const data = await fetchJson('/api/queue/bulk-delete', {method: 'POST', body: JSON.stringify({...payload, dry_run: false})});
                ++queueEpoch; queueRevision = -1;
                const deleted = new Set(data.deleted_ids || []);
                for (const id of deleted) selectedQueueIds.delete(id);
                queueJobs = Array.isArray(data.jobs) ? data.jobs : queueJobs.filter(job => !deleted.has(job.id));
                lastQueueSignature = JSON.stringify(queueJobs);
                if (deleted.size) hideResult();
                const failures = data.failures || [];
                byId('queue-bulk-status').textContent = `Đã xoá ${deleted.size} bản ghi · giải phóng ${formatDiskBytes(data.freed_bytes)}${failures.length ? ` · ${failures.length} lỗi` : ''}.`;
                bulkDeleteBusy = false; bulkEstimate = null;
                if (failures.length) {
                    // Leave failed jobs selected and report the server's per-job result.
                    bulkEstimate = {key: bulkEstimateKey(), failures, error: 'Một số job chưa xoá được. Kiểm tra lại trước khi thử lại.'};
                    renderQueue(); renderBulkFailures(failures);
                } else { closeBulkDelete(); renderQueue(); }
                if (deleted.size) window.VietSubVFX?.dissolve?.(byId('queue-bulk-status'));
            } catch (error) {
                bulkDeleteBusy = false;
                bulkEstimate = {key: bulkEstimateKey(), error: error.message};
                renderQueue();
            } finally {
                bulkDeleteBusy = false; queueRevision = -1;
                await loadQueue();
            }
        }

        function sameResult(left, right) {
            if (!left || !right) return false;
            if (left === right) return true;
            for (const key of ['translated_srt', 'project_dir']) {
                if (left[key] && right[key]) return left[key] === right[key];
            }
            return Boolean(left.video && left.raw_srt && left.video === right.video && left.raw_srt === right.raw_srt);
        }

        function findResultJobId(result, preferred = '') {
            // Old queueResult objects have paths, not job IDs. Paths only identify a matching
            // queue entry locally; the handoff API always receives that entry's ID, never paths.
            if (preferred) return preferred;
            const matches = queueJobs.filter(job => job.status === 'success' && sameResult(result, job.result));
            return matches.length === 1 ? matches[0].id : '';
        }

        function updateHandoffView(view) {
            const state = handoffStates.get(view.jobId);
            for (const name of ['editor', 'uploader']) {
                const button = view.buttons[name], pending = pendingHandoffs.has(`${view.jobId}:${name}`);
                button.disabled = !view.jobId || view.deleted || pending || bulkDeleteBusy;
                button.textContent = pending ? `Đang bàn giao sang ${workspaceLabels[name]}…` : handoffLabels[name];
                button.setAttribute('aria-busy', String(pending));
                button.dataset.handoffJob = view.jobId;
                button.dataset.handoffWorkspace = name;
            }
            view.status.textContent = view.deleted ? 'Đã dọn video nguồn; không thể bàn giao sang Auto Edit / Đăng đa nền tảng.' :
                !view.jobId ? 'Chưa xác định được job của kết quả; hãy mở từ lịch sử job thành công.' : state?.message || '';
            view.status.dataset.state = view.deleted || !view.jobId ? 'error' : state?.state || '';
        }

        function refreshHandoffViews() {
            if (visibleResult && !resultJobId) resultJobId = findResultJobId(visibleResult);
            byId('result-handoff-actions').hidden = !visibleResult;
            updateHandoffView({jobId: resultJobId, deleted: visibleResult?.source_video_deleted === true,
                buttons: {editor: byId('result-editor-btn'), uploader: byId('result-uploader-btn')}, status: byId('result-handoff-status')});
            for (const view of queueHandoffViews) updateHandoffView(view);
        }

        function queueHandoffActions(job) {
            const group = document.createElement('div'); group.className = 'handoff-actions';
            group.setAttribute('aria-label', `Bàn giao ${job.project_name || job.video_title || job.id}`);
            const view = {jobId: job.id, deleted: job.result?.source_video_deleted === true, buttons: {}};
            for (const name of ['editor', 'uploader']) {
                const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-ghost';
                button.onclick = () => handoffProject(job.id, name);
                view.buttons[name] = button; group.appendChild(button);
            }
            view.status = document.createElement('span'); view.status.className = 'handoff-status';
            view.status.setAttribute('role', 'status'); view.status.setAttribute('aria-live', 'polite');
            group.appendChild(view.status); queueHandoffViews.push(view); updateHandoffView(view);
            return group;
        }

        function validateProjectSnapshot(data, jobId, workspace, requireReady = false) {
            if (!data || data.job_id !== jobId || (requireReady && data.workspace !== workspace) ||
                !data.manifest || data.manifest.schema_version !== 1 || typeof data.manifest.project_id !== 'string' ||
                !data.availability || typeof data.availability.video !== 'boolean' || typeof data.availability.translated_srt !== 'boolean' ||
                typeof data.ready !== 'boolean') throw new Error('Máy chủ trả về project không hợp lệ hoặc không đúng job.');
            if (requireReady && (!data.ready || data.availability.video !== true || data.availability.translated_srt !== true)) {
                throw new Error('Video nguồn hoặc phụ đề Việt không còn khả dụng; không thể bàn giao. Nếu đã dọn video, hãy khôi phục file trước.');
            }
            return data;
        }

        async function handoffProject(jobId, workspace) {
            if (!jobId || !Object.hasOwn(workspaceLabels, workspace) || bulkDeleteBusy) return;
            const key = `${jobId}:${workspace}`;
            if (pendingHandoffs.has(key)) return;
            const knownJob = queueJobs.find(job => job.id === jobId);
            if (knownJob?.result?.source_video_deleted === true) return;
            const request = {sequence: ++handoffSequence, jobId, workspace, epoch: queueEpoch,
                navigation: window.VietSubWorkbench?.workspaceState().revision};
            const previous = activeHandoff;
            if (previous && previous.jobId !== jobId) handoffStates.delete(previous.jobId);
            if (previous && previous.workspace !== workspace) window.VietSubWorkbench?.projectStatus(previous.workspace, '', '');
            activeHandoff = request; pendingHandoffs.add(key); projectReadSequence[workspace]++;
            handoffStates.set(jobId, {state: 'loading', message: `Đang kiểm tra file và bàn giao sang ${workspaceLabels[workspace]}…`});
            window.VietSubWorkbench?.projectStatus(workspace, 'Đang kiểm tra video và phụ đề trên máy…', 'loading');
            refreshHandoffViews();
            try {
                const data = await fetchJson(`/api/projects/${encodeURIComponent(jobId)}/handoff`, {
                    method: 'POST', body: JSON.stringify({workspace}),
                });
                if (activeHandoff !== request || request.epoch !== queueEpoch) return;
                validateProjectSnapshot(data, jobId, workspace, true);
                workspaceProjects[workspace] = jobId;
                window.VietSubWorkbench?.projectReceived(workspace, data);
                handoffStates.set(jobId, {state: 'ready', message: `Đã bàn giao sang ${workspaceLabels[workspace]}${workspace === 'editor' ? ' · sẵn sàng Auto Edit.' : ' · chưa upload lên nền tảng.'}`});
                const navigation = window.VietSubWorkbench?.workspaceState().revision;
                // A late response may update staging data, but must not undo a user's tab/modal choice.
                if (navigation === request.navigation && !byId('workbench-shell').inert && !bulkDialogOpen) window.VietSubWorkbench?.selectWorkspace(workspace, true);
                showToast(`Đã nhận project trong ${workspaceLabels[workspace]}.${workspace === 'editor' ? ' Sẵn sàng chỉnh sửa và render.' : ' Chưa upload lên nền tảng.'}`);
            } catch (error) {
                if (activeHandoff !== request || request.epoch !== queueEpoch) return;
                const message = error.status === 409 ? `${error.message} Video gốc đã bị dọn sẽ không thể dùng cho Auto Edit / Đăng đa nền tảng.` : error.message;
                handoffStates.set(jobId, {state: 'error', message});
                window.VietSubWorkbench?.projectStatus(workspace, message, 'error');
                showToast(message, 'error');
            } finally {
                pendingHandoffs.delete(key);
                if (activeHandoff === request) {
                    activeHandoff = null;
                    if (request.epoch !== queueEpoch) {
                        handoffStates.delete(jobId);
                        window.VietSubWorkbench?.projectStatus(workspace, 'Hàng đợi đã thay đổi; hãy thử bàn giao lại.', 'error');
                    }
                }
                refreshHandoffViews();
            }
        }

        async function refreshProject(workspace, jobId) {
            if (!Object.hasOwn(workspaceProjects, workspace) || !jobId || workspaceProjects[workspace] !== jobId) return;
            const sequence = ++projectReadSequence[workspace];
            window.VietSubWorkbench?.projectStatus(workspace, 'Đang kiểm tra lại video và phụ đề…', 'loading');
            try {
                const data = await fetchJson(`/api/projects/${encodeURIComponent(jobId)}?workspace=${encodeURIComponent(workspace)}`);
                if (sequence !== projectReadSequence[workspace] || workspaceProjects[workspace] !== jobId) return;
                validateProjectSnapshot(data, jobId, workspace);
                window.VietSubWorkbench?.projectReceived(workspace, data);
            } catch (error) {
                if (sequence !== projectReadSequence[workspace]) return;
                window.VietSubWorkbench?.projectStatus(workspace, error.message, 'error');
            }
        }

        function renderQueue() {
            const list = byId('queue-list');
            for (const id of selectedQueueIds) {
                if (!queueJobs.some(job => job.id === id && job.status !== 'running')) selectedQueueIds.delete(id);
            }
            refreshBulkEstimate();
            byId('queue-count').textContent = queueJobs.length;
            const queuedCount = queueJobs.filter(job => job.status === 'queued').length;
            const running = queueJobs.some(job => job.status === 'running');
            const queuedSources = new Set(queueJobs.filter(job => job.status === 'queued').map(job => job.source_type || 'douyin'));
            const queueReady = [...queuedSources].every(sourceReady);
            byId('start-queue-btn').disabled = !queuedCount || running || !queueReady || batchImportBusy || bulkDeleteBusy;
            byId('start-queue-btn').textContent = running ? 'Đang chạy tuần tự…' : !queueReady ? 'Thiếu thành phần bắt buộc' : `Chạy hàng đợi${queuedCount ? ` (${queuedCount})` : ''}`;
            const cancelButton = byId('cancel-btn');
            const runningJob = queueJobs.find(job => job.status === 'running');
            cancelButton.hidden = !runningJob;
            // "Đang huỷ…" only belongs to the job that was cancelled; the next job gets a usable button.
            if (!runningJob || runningJob.id !== cancellingJobId) {
                cancellingJobId = '';
                cancelButton.disabled = false;
                cancelButton.textContent = 'Huỷ job';
            }
            queueHandoffViews = []; refreshHandoffViews();
            if (!queueJobs.length) {
                list.innerHTML = '<div class="queue-empty"><span class="queue-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 9h6m-6 4h6m-6 4h3"/></svg></span><strong>Hàng đợi đang trống</strong><span>Chuẩn bị preview đầu tiên để bắt đầu.</span></div>';
                return;
            }
            const active = document.activeElement;
            const focusJobId = active?.dataset.selectJob, focusHandoffJob = active?.dataset.handoffJob, focusHandoffWorkspace = active?.dataset.handoffWorkspace;
            const listIsVisible = !byId('workbench').hidden && !byId('workbench-shell').inert;
            let focusCheckbox = null, focusHandoff = null;
            list.replaceChildren();
            queueJobs.forEach((job, index) => {
                const row = document.createElement('article'); row.className = `queue-item ${job.status}${selectedQueueIds.has(job.id) ? ' is-selected' : ''}`; row.dataset.jobId = job.id;
                const top = document.createElement('div'); top.className = 'queue-item-top';
                const selectLabel = document.createElement('label'); selectLabel.className = 'queue-select';
                const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = selectedQueueIds.has(job.id);
                checkbox.disabled = bulkDeleteBusy || job.status === 'running'; checkbox.dataset.selectJob = job.id;
                if (focusJobId === job.id && !checkbox.disabled) focusCheckbox = checkbox;
                checkbox.setAttribute('aria-label', `Chọn ${job.project_name || job.video_title || `job ${index + 1}`}`);
                selectLabel.title = job.status === 'running' ? 'Không thể xoá job đang chạy' : 'Chọn để dọn hàng loạt';
                checkbox.addEventListener('change', () => {
                    if (bulkDeleteBusy || job.status === 'running') return;
                    if (checkbox.checked) selectedQueueIds.add(job.id); else selectedQueueIds.delete(job.id);
                    row.classList.toggle('is-selected', checkbox.checked); refreshBulkEstimate();
                });
                selectLabel.appendChild(checkbox); top.appendChild(selectLabel);
                const info = document.createElement('div'); info.style.minWidth = '0';
                const sourceLabel = sourceLabels[job.source_type] || 'Douyin';
                const sourceMeta = job.source_type === 'local' ? 'VIDEO TRÊN MÁY' : `${sourceLabel.toUpperCase()} · ID ${job.video_id || 'đang chờ giải mã'}`;
                const queueIndex = document.createElement('div'); queueIndex.className = 'queue-index'; queueIndex.textContent = `JOB ${String(index + 1).padStart(2,'0')}`;
                const queueName = document.createElement('div'); queueName.className = 'queue-name'; queueName.textContent = job.project_name || job.video_title || (job.source_type === 'local' ? 'Video trên máy' : `Tên tự động từ ${sourceLabel}`); queueName.title = queueName.textContent;
                const queueId = document.createElement('div'); queueId.className = 'queue-id'; queueId.textContent = `${sourceMeta} · ${String(job.ocr_lang || '').toUpperCase()}`;
                info.append(queueIndex, queueName, queueId);
                const badge = document.createElement('span'); badge.className = `badge ${job.status}`; badge.textContent = statusLabels[job.status] || job.status;
                top.append(info, badge); row.appendChild(top);
                const progress = document.createElement('div'); progress.className = 'queue-progress'; progress.setAttribute('role', 'progressbar'); progress.setAttribute('aria-valuemin', '0'); progress.setAttribute('aria-valuemax', '100');
                const progressValue = progressWidth(job.step, job.status); progress.setAttribute('aria-valuenow', String(progressValue)); const bar = document.createElement('i'); bar.style.width = `${progressValue}%`; progress.appendChild(bar); row.appendChild(progress);
                if (job.error) { const error = document.createElement('div'); error.className = 'queue-error'; error.textContent = job.error; row.appendChild(error); }
                const actions = document.createElement('div'); actions.className = 'queue-item-actions';
                if (job.status !== 'running') { const remove = document.createElement('button'); remove.className = 'btn-ghost'; remove.textContent = 'Xoá bản ghi'; remove.title = 'Chỉ xoá bản ghi; giữ nguyên tất cả file'; remove.disabled = bulkDeleteBusy; remove.onclick = () => removeQueueJob(job.id, job.status); actions.appendChild(remove); }
                if (job.status === 'error' || job.status === 'cancelled') { const retry = document.createElement('button'); retry.className = 'btn-secondary'; retry.disabled = bulkDeleteBusy; retry.textContent = job.retry_step === 'translate' ? 'Chạy lại bước dịch' : job.retry_step === 'ocr' ? 'Chạy lại từ OCR' : 'Chạy lại từ video'; retry.onclick = () => retryQueueJob(job.id); actions.appendChild(retry); }
                if (job.needs_edge_login) { const login = document.createElement('button'); login.className = 'btn-ghost'; login.textContent = `Mở ${browserName} đăng nhập`; login.onclick = showEdgeLogin; actions.appendChild(login); }
                if (job.status === 'success' && job.result_ready) { const open = document.createElement('button'); open.className = 'btn-ghost'; open.textContent = 'Mở kết quả'; open.onclick = () => showResult(job.result, true, job.id); actions.appendChild(open); }
                else if ((job.status === 'error' || job.status === 'cancelled') && job.result?.project_dir) { const open = document.createElement('button'); open.className = 'btn-ghost'; open.textContent = 'Mở thư mục làm dở'; open.onclick = () => openResultPath(job.result.project_dir); actions.appendChild(open); }
                if (job.status === 'success') {
                    actions.appendChild(queueHandoffActions(job));
                    if (job.id === focusHandoffJob) focusHandoff = queueHandoffViews[queueHandoffViews.length - 1].buttons[focusHandoffWorkspace];
                }
                if (actions.children.length) row.appendChild(actions);
                list.appendChild(row);
            });
            if (listIsVisible) {
                if (focusHandoff && !focusHandoff.disabled) focusHandoff.focus({preventScroll: true});
                else focusCheckbox?.focus({preventScroll: true});
            }
        }

        async function loadQueue() {
            if (queueLoadInFlight) return;
            queueLoadInFlight = true;
            const epoch = queueEpoch;
            byId('queue-list').setAttribute('aria-busy', 'true');
            try {
                // Handle each response on its own, so a failed progress poll never drops a good queue update.
                const [queueResult, progressResult] = await Promise.allSettled([
                    fetchJson(`/api/queue?revision=${queueRevision}`),
                    pollProgress(),
                ]);
                if (queueResult.status === 'fulfilled') {
                    if (epoch !== queueEpoch) return;
                    const data = queueResult.value;
                    queueRevision = Number.isInteger(data.revision) ? data.revision : queueRevision;
                    if (Array.isArray(data.jobs)) {
                        queueJobs = data.jobs;
                        const signature = JSON.stringify(queueJobs);
                        if (signature !== lastQueueSignature) { lastQueueSignature = signature; renderQueue(); }
                    }
                } else {
                    console.warn(queueResult.reason);
                }
                if (progressResult.status === 'rejected') console.warn(progressResult.reason);
            }
            finally { queueLoadInFlight = false; byId('queue-list').setAttribute('aria-busy', 'false'); }
        }

        function scheduleQueuePolling() {
            const busy = queueJobs.some(job => job.status === 'running' || job.status === 'queued');
            setTimeout(async () => { await loadQueue(); scheduleQueuePolling(); }, busy ? 1000 : 4000);
        }

        async function startQueue() {
            await runOnce('start-queue', async () => {
                try { await fetchJson('/api/queue/start', {method: 'POST'}); hideResult(); displayedLogCount = 0; progressJobId = ''; byId('console-box').replaceChildren(); await loadQueue(); showToast('Hàng đợi đã bắt đầu chạy tuần tự.'); }
                catch (error) { showToast(error.message, 'error'); }
            });
        }

        async function removeQueueJob(id, status = 'queued') {
            if (status !== 'queued' && !window.confirm('Xoá job này khỏi lịch sử? File project đã tạo sẽ không bị xoá.')) return;
            const effectPoint = window.VietSubVFX?.point([...byId('queue-list').children].find(row => row.dataset.jobId === id));
            await runOnce(`remove-${id}`, async () => {
                try { await fetchJson(`/api/queue/${encodeURIComponent(id)}`, {method: 'DELETE'}); window.VietSubVFX?.smoke(effectPoint); queueRevision = -1; await loadQueue(); }
                catch (error) { showToast(error.message, 'error'); }
            });
        }

        async function retryQueueJob(id) {
            const job = queueJobs.find(item => item.id === id);
            if (job?.error_code === 'project_missing' && !window.confirm(
                'Không tìm thấy file dự án ở vị trí cũ (có thể đã bị di chuyển hoặc xoá). '
                + 'Chạy lại sẽ chuẩn bị video và làm lại từ đầu. Tiếp tục?'
            )) return;
            await runOnce(`retry-${id}`, async () => {
                try { await fetchJson(`/api/queue/${encodeURIComponent(id)}/retry`, {method:'POST'}); hideResult(); await loadQueue(); showToast('Job đã được đưa lại vào hàng đợi.'); }
                catch (error) { showToast(error.message, 'error'); }
            });
        }

        async function showEdgeLogin() {
            try { await fetchJson('/api/edge/show', {method:'POST', timeoutMs: 90000}); showToast(`Đã mở ${browserName}. Đăng nhập xong rồi bấm chạy lại job.`); }
            catch (error) { showToast(error.message, 'error'); }
        }

        function clearFinished() {
            setQueueSelection('finished');
            openBulkDelete();
        }

        async function cancelJob() {
            const button = byId('cancel-btn'); button.disabled = true; button.textContent = 'Đang huỷ…';
            cancellingJobId = queueJobs.find(job => job.status === 'running')?.id || '';
            try { await fetchJson('/api/cancel', {method:'POST'}); showToast('Đã gửi yêu cầu huỷ job hiện tại.'); }
            catch (error) { cancellingJobId = ''; button.disabled = false; button.textContent = 'Huỷ job'; showToast(error.message, 'error'); }
        }

        function updateSteps(step, status) {
            const activeIndex = stepIndex[step] ?? 0;
            steps.forEach((name, index) => {
                const item = byId(`step-${name}`);
                item.className = index < activeIndex || status === 'success' ? 'step completed' : index === activeIndex ? `step ${status === 'error' || status === 'cancelled' ? 'error' : 'active'}` : 'step';
            });
            byId('workflow-progress').setAttribute('aria-valuenow', String(progressWidth(step, status)));
            const captions = {resolve:'Đang giải mã link',download:'Đang chuẩn bị video',ocr:'VideOCR đang quét',translate:`${translationProviderName} đang dịch`,done:'Đã hoàn tất'};
            const errorCaptions = {resolve:'Lỗi giải mã link',download:'Lỗi chuẩn bị video',ocr:'VideOCR bị lỗi',translate:`Dịch bằng ${translationProviderName} bị lỗi`,done:'Hoàn tất không hợp lệ'};
            byId('process-caption').textContent = status === 'cancelled' ? 'Job đã huỷ' : status === 'error' ? errorCaptions[step] || 'Job bị lỗi' : captions[step] || 'Sẵn sàng';
        }

        function addLog(text) {
            const line = document.createElement('div');
            line.className = `log-line ${text.includes('[VideOCR]') ? 'ocr' : text.includes('[!]') ? 'error' : text.includes('Success') ? 'success' : ''}`;
            line.textContent = text; byId('console-box').appendChild(line);
            while (byId('console-box').children.length > 500) byId('console-box').firstChild.remove();
            byId('console-box').scrollTop = byId('console-box').scrollHeight;
        }

        async function pollProgress() {
            const epoch = queueEpoch;
            try {
                const data = await fetchJson(`/api/progress?after=${displayedLogCount}`);
                if (epoch !== queueEpoch) return;
                if (data.job_id && data.job_id !== progressJobId) { hideResult(); progressJobId = data.job_id; displayedLogCount = 0; byId('console-box').replaceChildren(); return pollProgress(); }
                (data.logs || []).forEach(addLog); displayedLogCount = data.next_log_index ?? displayedLogCount;
                // A preview download has its own panel; otherwise the live download belongs to the job.
                if (!downloadWatchTimer) renderDownloadProgress(data.download, 'job');
                if (data.status !== 'idle') updateSteps(data.step, data.status);
                if (data.status === 'success' && data.result_ready) showResult(data.result, false, data.job_id || progressJobId);
                else if (['running', 'error', 'cancelled'].includes(data.status)) hideResult();
                window.VietSubWorkbench?.progressChanged(data);
                pollFailureCount = 0;
            } catch (error) {
                pollFailureCount += 1;
                if (pollFailureCount >= 3) byId('process-caption').textContent = 'Mất kết nối cục bộ · đang thử lại';
                throw error;
            }
        }

        function hideResult() {
            resultOpenPath = ''; visibleResult = null; resultJobId = '';
            refreshHandoffViews();
            byId('res-video').textContent = ''; byId('res-thumbnail').textContent = ''; byId('res-thumbnail-row').hidden = true; byId('res-raw').textContent = ''; byId('res-vi').textContent = ''; byId('res-publish').textContent = ''; byId('res-publish-row').hidden = true;
            byId('result-box').hidden = true;
            window.VietSubWorkbench?.resultHidden();
        }

        function showResult(result, scroll = false, jobId = '') {
            visibleResult = result;
            resultJobId = findResultJobId(result, jobId || progressJobId);
            refreshHandoffViews();
            resultOpenPath = result.project_dir || result.translated_srt || '';
            byId('result-title').textContent = `${result.project_name || 'Bộ file'} đã sẵn sàng`;
            byId('res-video').textContent = result.source_video_deleted === true ? 'Đã dọn video nguồn · phụ đề được giữ lại' : result.video || ''; byId('res-thumbnail').textContent = result.thumbnail || ''; byId('res-thumbnail-row').hidden = !result.thumbnail; byId('res-raw').textContent = result.raw_srt || ''; byId('res-vi').textContent = result.translated_srt || '';
            byId('res-publish').textContent = result.publish_metadata || '';
            byId('res-publish-row').hidden = !result.publish_metadata;
            byId('result-box').hidden = false;
            if (window.VietSubWorkbench) window.VietSubWorkbench.resultShown(result, scroll);
            else if (scroll) byId('result-box').scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth', block:'center'});
        }

        async function openResult() {
            await openResultPath(resultOpenPath);
        }

        async function openResultPath(path) {
            if (!path) return;
            try { await fetchJson('/api/open-folder', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({path})}); }
            catch (error) { showToast(error.message, 'error'); }
        }

        // The browser opens on demand (Notebook, a download that needs it, a login button),
        // so "not open yet" is the normal idle state, not an error.
        async function checkEdge() {
            try {
                const data = await fetchJson('/api/check-edge');
                if (data.browser && data.browser !== browserName) applyBrowserName(data.browser);
                byId('edge-pill').className = data.connected ? 'status-pill ready' : 'status-pill';
                byId('edge-pill-text').textContent = data.connected ? `${browserName} sẵn sàng` : `${browserName} chưa mở`;
            }
            catch (_) {}
        }

        // Every label that names the automation browser follows the choice in Settings.
        function applyBrowserName(name) {
            browserName = String(name || '').trim() || 'Chrome';
            byId('source-sessions-intro').textContent = `Mỗi nền tảng chỉ cần đăng nhập một lần trong profile ${browserName} riêng của app; cookie ở lại trong profile đó. Đăng nhập giúp ít gặp captcha và tải được video cần tài khoản.`;
            byId('show-edge-btn').textContent = `Mở Gemini trên ${browserName} để đăng nhập`;
        }

        function updateBrowserSettingsUI(data = null) {
            const kind = byId('setting-browser-kind').value || 'chrome';
            byId('browser-path-field').hidden = kind !== 'custom';
            if (!data) return;
            const where = data.browser_profile_dir ? ` Profile riêng: ${data.browser_profile_dir}.` : '';
            const missing = data.browser_executable ? '' : (kind === 'custom'
                ? ' Hãy chọn file .exe của trình duyệt.'
                : ` Chưa tìm thấy ${data.browser_label || 'trình duyệt này'} trên máy.`);
            byId('browser-help').textContent = `App mở trình duyệt này với profile riêng, chạy song song và không đụng tới trình duyệt bạn đang dùng. Trình duyệt chỉ mở khi cần: dịch bằng Gemini Notebook, tải video cần trình duyệt, hoặc khi bạn bấm đăng nhập.${where}${missing}`;
        }

        function applyBrowserSettings(data) {
            if (!data || !data.browser_kind) return;
            byId('setting-browser-kind').value = data.browser_kind;
            byId('setting-browser-path').value = data.browser_path || '';
            applyBrowserName(data.browser_short_label);
            updateBrowserSettingsUI(data);
        }

        async function chooseBrowserExecutable() {
            const api = window.pywebview && window.pywebview.api;
            if (!api || typeof api.choose_browser_executable !== 'function') {
                showToast('Hãy dán đường dẫn đầy đủ tới file .exe của trình duyệt vào ô bên cạnh.', 'error');
                byId('setting-browser-path').focus();
                return;
            }
            try {
                const path = await api.choose_browser_executable();
                if (!path) return;
                byId('setting-browser-path').value = path;
                clearTimeout(saveSettingsTimer);
                await saveSettingsNow();
            } catch (error) { showToast(error.message || String(error), 'error'); }
        }

        function updateApiKeyHelp() {
            const help = byId('api-key-help');
            help.textContent = translationApiKeySet
                ? 'API key đã được mã hoá bằng tài khoản Windows hiện tại. Để trống ô này sẽ giữ nguyên key.'
                : 'Chưa lưu API key. DeepSeek và OpenRouter bắt buộc phải có key.';
            byId('clear-api-key-btn').disabled = !translationApiKeySet;
            byId('setting-api-key').placeholder = translationApiKeySet ? 'API key đã lưu' : 'Nhập API key';
        }

        function updateTranslationProviderUI(applyPreset = false) {
            const provider = byId('setting-translation-provider').value;
            const usesNotebook = provider === 'notebook';
            translationProviderName = {notebook: 'Gemini', deepseek: 'DeepSeek API', openrouter: 'OpenRouter'}[provider] || 'API dịch';
            // The browser settings stay visible for every provider: downloads use the browser too.
            byId('notebook-settings').hidden = !usesNotebook;
            byId('api-translation-settings').hidden = usesNotebook;
            if (applyPreset && !usesNotebook) {
                const previousPreset = translationProviderPresets[lastTranslationProvider] || {};
                const nextPreset = translationProviderPresets[provider] || {};
                const baseInput = byId('setting-api-base-url');
                const modelInput = byId('setting-api-model');
                if (!baseInput.value.trim() || baseInput.value.trim() === (previousPreset.base_url || '')) {
                    baseInput.value = nextPreset.base_url || '';
                }
                if (!modelInput.value.trim() || modelInput.value.trim() === (previousPreset.model || '')) {
                    modelInput.value = nextPreset.model || '';
                }
            }
            lastTranslationProvider = provider;
        }

        async function loadSettings() {
            try {
                const data = await fetchJson('/api/settings');
                byId('setting-notebook').value = data.notebook_url || ''; byId('setting-output').value = data.output_dir || '';
                translationProviderPresets = data.translation_provider_presets || {};
                byId('setting-translation-provider').value = data.translation_provider || 'notebook';
                lastTranslationProvider = byId('setting-translation-provider').value;
                byId('setting-api-base-url').value = data.translation_api_base_url || '';
                byId('setting-api-model').value = data.translation_api_model || '';
                defaultTranslationInstructions = data.translation_default_instructions || '';
                translationInstructionPresets = data.translation_instruction_presets || {};
                renderTranslationPresets();
                byId('setting-translation-instructions').value = data.translation_instructions || defaultTranslationInstructions;
                byId('setting-api-key').value = '';
                translationApiKeySet = Boolean(data.translation_api_key_set);
                updateTranslationProviderUI(false);
                updateApiKeyHelp();
                byId('setting-output').placeholder = data.default_output_dir || 'Documents\\VietSub Studio';
                const syncWarning = data.output_dir_synced ? ' ⚠ Thư mục này đang được OneDrive đồng bộ: mỗi video xuất sẽ bị upload lên cloud và có thể chậm hơn — cân nhắc chọn thư mục ngoài OneDrive.' : '';
                byId('output-help').textContent = `Để trống sẽ lưu tại ${data.default_output_dir}. Mỗi project có folder riêng chứa đủ video, SRT và thumbnail.${syncWarning}`;
                byId('setting-crop').value = data.crop_coords ? JSON.stringify(data.crop_coords) : 'Toàn khung hình';
                byId('ocr-lang').value = data.ocr_lang || 'ch';
                sourceSessionSites = Array.isArray(data.source_session_sites) ? data.source_session_sites : [];
                sourceCookiesSet = data.source_cookies_set || {};
                renderSourceCookieSites();
                byId('setting-cookies-file').value = data.cookies_file || '';
                byId('setting-ocr-speed').value = data.ocr_model_mode || 'auto';
                byId('setting-frames-skip').value = data.ocr_frames_to_skip == null ? '' : String(data.ocr_frames_to_skip);
                byId('setting-edge-background').checked = data.edge_background !== false;
                byId('setting-auto-delete-source-video').checked = data.auto_delete_source_video === true;
                byId('setting-generate-publish-metadata').checked = data.generate_publish_metadata === true;
                window.VietSubWorkbench?.applySettings(data);
                applyBrowserSettings(data);
                const shortcutButton = byId('create-shortcut-btn');
                shortcutButton.disabled = !data.desktop_shortcut_supported;
                shortcutButton.textContent = data.desktop_shortcut_exists ? 'Tạo lại shortcut ngoài Desktop' : 'Tạo shortcut ngoài Desktop';
                byId('shortcut-help').textContent = data.desktop_shortcut_supported
                    ? 'Shortcut giúp mở app ngay từ Desktop, không cần tìm lại file EXE.'
                    : 'Nút này hoạt động trong bản VietSub Studio EXE.';
            } catch (error) { showToast(error.message, 'error'); }
        }

        async function createDesktopShortcut() {
            try {
                await fetchJson('/api/shortcut', {method:'POST'});
                showToast('Đã tạo shortcut VietSub Studio ngoài Desktop.');
                await loadSettings();
            } catch (error) { showToast(error.message, 'error'); }
        }

        let saveSettingsTimer = null;
        async function saveSettingsNow(options = {}) {
            try {
                const provider = byId('setting-translation-provider').value;
                const payload = {translation_provider:provider, translation_instructions:byId('setting-translation-instructions').value.trim(), notebook_url:byId('setting-notebook').value.trim(), output_dir:byId('setting-output').value.trim(), ocr_lang:byId('ocr-lang').value, ocr_model_mode:byId('setting-ocr-speed').value, ocr_frames_to_skip:byId('setting-frames-skip').value === '' ? null : Number(byId('setting-frames-skip').value), edge_background:byId('setting-edge-background').checked};
                payload.auto_delete_source_video = byId('setting-auto-delete-source-video').checked === true;
                payload.generate_publish_metadata = byId('setting-generate-publish-metadata').checked === true;
                if (provider !== 'notebook') {
                    payload.translation_api_base_url = byId('setting-api-base-url').value.trim();
                    payload.translation_api_model = byId('setting-api-model').value.trim();
                    const apiKey = byId('setting-api-key').value.trim();
                    if (apiKey) payload.translation_api_key = apiKey;
                }
                if (options.clearApiKey) payload.translation_api_key = null;
                const browserKind = byId('setting-browser-kind').value || 'chrome';
                const browserPath = byId('setting-browser-path').value.trim();
                // A custom browser is saved once its .exe is set; until then the previous browser stays in use.
                if (browserKind !== 'custom') payload.browser_kind = browserKind;
                else if (browserPath) { payload.browser_kind = 'custom'; payload.browser_path = browserPath; }
                const saved = await fetchJson('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
                if (payload.browser_kind) {
                    const previousBrowser = browserName;
                    applyBrowserSettings(saved);
                    if (browserName !== previousBrowser) { checkEdge(); loadSourceSessions(); }
                }
                if (payload.translation_api_key) byId('setting-api-key').value = '';
                // Keys are stored per provider and API host, so the server says whether
                // the provider selected now has one.
                if (typeof saved.translation_api_key_set === 'boolean') translationApiKeySet = saved.translation_api_key_set;
                else if (payload.translation_api_key) translationApiKeySet = true;
                else if (options.clearApiKey) translationApiKeySet = false;
                updateApiKeyHelp();
                if (!options.silent) showToast('Đã lưu thiết lập.');
                loadHealth();
            } catch (error) {
                showToast(error.message, 'error');
                if (options.throwOnError) throw error;
            }
        }

        function saveSettings() {
            clearTimeout(saveSettingsTimer);
            saveSettingsTimer = setTimeout(saveSettingsNow, 250);
        }

        function renderTranslationPresets() {
            const select = byId('setting-translation-preset');
            const previous = select.value;
            select.replaceChildren();
            Object.entries(translationInstructionPresets).forEach(([key, preset]) => {
                const option = document.createElement('option');
                option.value = key;
                option.textContent = preset.label || key;
                select.appendChild(option);
            });
            if (previous && translationInstructionPresets[previous]) select.value = previous;
            byId('apply-translation-preset-btn').disabled = !select.options.length;
        }

        async function applyTranslationPreset() {
            const preset = translationInstructionPresets[byId('setting-translation-preset').value];
            if (!preset) return;
            const textarea = byId('setting-translation-instructions');
            const current = textarea.value.trim();
            const isKnownPreset = Object.values(translationInstructionPresets).some(item => item.instructions === current);
            if (current && !isKnownPreset && !window.confirm('Thay nội dung "Cách dịch mong muốn" hiện tại bằng mẫu đã chọn?')) return;
            textarea.value = preset.instructions;
            clearTimeout(saveSettingsTimer);
            await saveSettingsNow({silent: true});
            showToast(`Đã nạp mẫu: ${preset.label}.`);
        }

        async function resetTranslationInstructions() {
            byId('setting-translation-instructions').value = defaultTranslationInstructions;
            clearTimeout(saveSettingsTimer);
            await saveSettingsNow();
        }

        function currentGlossaryProfile() {
            const profiles = Array.isArray(glossaryStore?.profiles) ? glossaryStore.profiles : [];
            const selectedId = byId('setting-glossary-select')?.value || glossaryStore?.active_id || 'default';
            return profiles.find(item => item.id === selectedId) || profiles[0] || {id: 'default', name: 'Mặc định', style_note: '', entries: []};
        }

        function createGlossaryEntryRow(entry = {}) {
            const row = document.createElement('div');
            row.className = 'glossary-entry-row';
            const src = document.createElement('input');
            src.type = 'text'; src.className = 'text-input'; src.maxLength = 120;
            src.placeholder = 'Thuật ngữ gốc'; src.value = entry.source || ''; src.dataset.glossaryField = 'source';
            src.setAttribute('aria-label', 'Thuật ngữ gốc');
            const tgt = document.createElement('input');
            tgt.type = 'text'; tgt.className = 'text-input'; tgt.maxLength = 120;
            tgt.placeholder = 'Bản dịch chuẩn'; tgt.value = entry.target || ''; tgt.dataset.glossaryField = 'target';
            tgt.setAttribute('aria-label', 'Bản dịch chuẩn');
            const note = document.createElement('input');
            note.type = 'text'; note.className = 'text-input'; note.maxLength = 120;
            note.placeholder = 'Ghi chú (tuỳ chọn)'; note.value = entry.note || ''; note.dataset.glossaryField = 'note';
            note.setAttribute('aria-label', 'Ghi chú thuật ngữ');
            const remove = document.createElement('button');
            remove.type = 'button'; remove.className = 'btn-ghost glossary-entry-remove';
            remove.textContent = '×'; remove.title = 'Xoá mục thuật ngữ này'; remove.dataset.glossaryRemove = '1';
            remove.setAttribute('aria-label', 'Xoá mục thuật ngữ');
            row.append(src, tgt, note, remove);
            return row;
        }

        function renderGlossaryEditor(profile) {
            const nameInput = byId('setting-glossary-name');
            const styleInput = byId('setting-glossary-style');
            const list = byId('setting-glossary-entries');
            const deleteBtn = byId('setting-glossary-delete-btn');
            if (!nameInput || !styleInput || !list) return;
            nameInput.value = profile.name || '';
            styleInput.value = profile.style_note || '';
            if (deleteBtn) deleteBtn.disabled = profile.id === 'default';
            const entries = Array.isArray(profile.entries) ? profile.entries : [];
            const rows = entries.map(entry => createGlossaryEntryRow(entry));
            list.replaceChildren(...rows);
        }

        function renderGlossaryStore(data) {
            if (data && Array.isArray(data.profiles) && data.profiles.length) {
                glossaryStore = {active_id: data.active_id || data.profiles[0].id, profiles: data.profiles};
            }
            const select = byId('setting-glossary-select');
            if (!select) return;
            const nodes = glossaryStore.profiles.map(profile => {
                const option = document.createElement('option');
                option.value = profile.id;
                const count = Array.isArray(profile.entries) ? profile.entries.length : 0;
                option.textContent = `${profile.name}${count ? ` (${count} mục)` : ''}`;
                return option;
            });
            select.replaceChildren(...nodes);
            select.value = glossaryStore.active_id;
            renderGlossaryEditor(currentGlossaryProfile());
            document.dispatchEvent(new CustomEvent('vietsub:glossaries-updated', {detail: glossaryStore}));
        }

        async function loadGlossaries() {
            if (!byId('setting-glossary-select')) return;
            const status = byId('setting-glossary-status');
            if (status) status.textContent = 'Đang nạp từ điển…';
            try {
                const data = await fetchJson('/api/glossaries');
                glossariesLoaded = true;
                renderGlossaryStore(data);
                if (status) status.textContent = `${glossaryStore.profiles.length} bộ từ điển`;
            } catch (error) {
                if (status) status.textContent = error.message;
            }
        }

        async function activateGlossary(id) {
            if (!id) return;
            const status = byId('setting-glossary-status');
            try {
                const data = await fetchJson('/api/glossaries/active', {method: 'POST', body: JSON.stringify({active_id: id})});
                renderGlossaryStore(data);
                if (status) status.textContent = 'Đã chuyển bộ từ điển đang dùng.';
                showToast(`Đang dùng bộ từ điển: ${currentGlossaryProfile().name}.`);
            } catch (error) {
                if (status) status.textContent = error.message;
                showToast(error.message, 'error');
            }
        }

        async function createGlossaryProfile() {
            const profiles = glossaryStore.profiles || [];
            let idx = profiles.length + 1;
            let candidate = `Bộ từ điển ${idx}`;
            while (profiles.some(item => item.name === candidate)) { idx += 1; candidate = `Bộ từ điển ${idx}`; }
            try {
                const data = await fetchJson('/api/glossaries', {
                    method: 'POST',
                    body: JSON.stringify({name: candidate, style_note: '', entries: [], activate: true}),
                });
                glossariesLoaded = true;
                renderGlossaryStore(data);
                const nameInput = byId('setting-glossary-name');
                if (nameInput) { nameInput.focus(); nameInput.select?.(); }
                showToast(`Đã tạo bộ từ điển "${candidate}".`);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function saveCurrentGlossary() {
            const profile = currentGlossaryProfile();
            const name = (byId('setting-glossary-name')?.value || '').trim();
            const styleNote = (byId('setting-glossary-style')?.value || '').trim();
            const list = byId('setting-glossary-entries');
            if (!name) { showToast('Tên bộ từ điển không được để trống.', 'error'); return; }
            const entries = [];
            for (const row of (list?.children || [])) {
                const inputs = [...(row.querySelectorAll?.('input[data-glossary-field]') || row.children || [])];
                const byField = {};
                for (const input of inputs) if (input.dataset?.glossaryField) byField[input.dataset.glossaryField] = (input.value || '').trim();
                if (!byField.source && !byField.target && !byField.note) continue;
                if (!byField.source || !byField.target) {
                    showToast('Mỗi dòng thuật ngữ cần có đủ cả "Thuật ngữ gốc" và "Bản dịch chuẩn".', 'error');
                    return;
                }
                entries.push({source: byField.source, target: byField.target, note: byField.note || ''});
            }
            const status = byId('setting-glossary-status');
            try {
                const data = await fetchJson(`/api/glossaries/${encodeURIComponent(profile.id)}`, {
                    method: 'PUT',
                    body: JSON.stringify({name, style_note: styleNote, entries}),
                });
                renderGlossaryStore(data);
                if (status) status.textContent = 'Đã lưu từ điển.';
                showToast(`Đã lưu bộ từ điển "${name}" (${entries.length} mục).`);
            } catch (error) {
                if (status) status.textContent = error.message;
                showToast(error.message, 'error');
            }
        }

        async function deleteCurrentGlossary() {
            const profile = currentGlossaryProfile();
            if (!profile || profile.id === 'default') return;
            if (!window.confirm(`Xoá bộ từ điển "${profile.name}"?`)) return;
            try {
                const data = await fetchJson(`/api/glossaries/${encodeURIComponent(profile.id)}`, {method: 'DELETE'});
                renderGlossaryStore(data);
                showToast(`Đã xoá bộ từ điển "${profile.name}".`);
            } catch (error) {
                showToast(error.message, 'error');
            }
        }

        async function testTranslationApi() {
            const button = byId('test-api-btn');
            button.disabled = true;
            button.textContent = 'Đang kiểm tra…';
            try {
                await saveSettingsNow({silent:true, throwOnError:true});
                const data = await fetchJson('/api/translation/test', {method:'POST', timeoutMs: 200000});
                showToast(`${data.provider} · ${data.model}: kết nối OK`);
            } catch (error) {
                showToast(error.message, 'error');
            } finally {
                button.disabled = false;
                button.textContent = 'Kiểm tra kết nối API';
            }
        }

        async function clearTranslationApiKey() {
            if (!translationApiKeySet || !window.confirm('Xoá API key đã lưu trên máy này?')) return;
            await saveSettingsNow({clearApiKey:true});
        }

        async function chooseOutputDirectory() {
            if (!window.pywebview || !window.pywebview.api) {
                showToast('Nút chọn thư mục hoạt động trong app VietSub Studio.', 'error');
                return;
            }
            try {
                const selected = await window.pywebview.api.select_output_directory(byId('setting-output').value.trim());
                if (!selected) return;
                byId('setting-output').value = selected;
                clearTimeout(saveSettingsTimer);
                await saveSettingsNow();
            } catch (error) { showToast(error.message || String(error), 'error'); }
        }

        async function loadHealth() {
            const list = byId('diagnostics-list'); list.innerHTML = '<span>Đang kiểm tra…</span>';
            try {
                const data = await fetchJson('/api/health'); list.replaceChildren();
                healthReadiness = {local: Boolean(data.ready_local), douyin: Boolean(data.ready_douyin), sources: data.ready_sources || {}};
                renderQueue();
                const labels = {videocr:'VideOCR',ffprobe:'FFprobe',ffmpeg:'FFmpeg',translation:'Bộ máy dịch',notebook:'Gemini Notebook',browser:'Trình duyệt tự động hoá',douyin_source:'Tải Douyin/Kuaishou',ytdlp:'yt-dlp',js_runtime:'JavaScript cho YouTube'};
                // The browser check carries the name of the browser chosen in Settings.
                Object.entries(data.checks).forEach(([key,item]) => { const row=document.createElement('div'); row.className=`diagnostic-item ${item.ok?'ok':item.required?'error':''}`; const strong=document.createElement('strong'); strong.textContent=`${item.label||labels[key]||key}: ${item.ok?'OK':item.required?'Thiếu':'Tuỳ chọn'}`; const small=document.createElement('small'); small.textContent=item.detail||''; row.append(strong,small); list.appendChild(row); });
            } catch (error) { healthReadiness = {local: false, douyin: false, sources: {}}; renderQueue(); list.textContent = error.message; }
        }

        function sessionState(site, browserConnected) {
            if (!browserConnected) return {text: `${browserName} chưa mở`, css: ''};
            if (site.logged_in === true) return {text: 'Đã đăng nhập', css: 'ok'};
            if (site.logged_in === false) return {text: 'Chưa đăng nhập', css: 'warn'};
            return site.has_cookies ? {text: 'Có cookie, chưa rõ đăng nhập', css: ''} : {text: 'Chưa có cookie', css: 'warn'};
        }

        function renderSourceSessions(data) {
            const list = byId('source-sessions');
            list.replaceChildren();
            const sites = (data && data.sites) || {};
            const order = sourceSessionSites.length ? sourceSessionSites : Object.keys(sites).map(key => ({key, label: sites[key].label || key}));
            order.forEach(({key, label}) => {
                const site = sites[key] || {};
                const row = document.createElement('div'); row.className = 'source-session-row';
                const info = document.createElement('div');
                const name = document.createElement('strong'); name.textContent = label || key;
                const detail = document.createElement('small');
                detail.textContent = site.manual_cookie || sourceCookiesSet[key] ? 'Đang dùng cookie thủ công' : '';
                info.append(name, detail);
                const connected = Boolean(data && data.browser_connected);
                const state = sessionState(site, connected);
                const pill = document.createElement('span'); pill.className = `session-pill ${state.css}`; pill.textContent = state.text;
                const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-ghost';
                button.textContent = `Mở ${label || key} trên ${browserName}`;
                button.onclick = () => openSourceLogin(key, label || key);
                row.append(info, pill, button);
                list.appendChild(row);
            });
            byId('source-sessions-help').textContent = (data && data.error) || (data && data.browser_connected
                ? `Trạng thái đọc từ cookie của profile ${browserName} riêng. Xiaohongshu cho khách cookie giống người đã đăng nhập nên app không phân biệt được.`
                : `${browserName} chưa mở. Bấm "Mở … trên ${browserName}" để đăng nhập; app tự mở ${browserName} với profile riêng, không đụng ${browserName} bạn đang dùng.`);
        }

        async function loadSourceSessions() {
            try {
                renderSourceSessions(await fetchJson('/api/sources/sessions', {timeoutMs: 30000}));
            } catch (error) {
                byId('source-sessions-help').textContent = error.message;
            }
        }

        async function openSourceLogin(key, label) {
            await runOnce(`open-source-${key}`, async () => {
                try {
                    await fetchJson(`/api/sources/${encodeURIComponent(key)}/open`, {method: 'POST', timeoutMs: 90000});
                    showToast(`Đã mở ${label} trên ${browserName}. Đăng nhập xong, bấm "Kiểm tra lại đăng nhập".`);
                    checkEdge();
                } catch (error) { showToast(error.message, 'error'); }
            });
        }

        function renderSourceCookieSites() {
            const select = byId('setting-source-cookie-site');
            const previous = select.value;
            select.replaceChildren();
            sourceSessionSites.forEach(({key, label}) => {
                const option = document.createElement('option'); option.value = key; option.textContent = label;
                select.appendChild(option);
            });
            if (previous && sourceSessionSites.some(site => site.key === previous)) select.value = previous;
            else if (sourceSessionSites.length) select.value = sourceSessionSites[0].key;
            updateSourceCookieHelp();
        }

        function updateSourceCookieHelp() {
            const key = byId('setting-source-cookie-site').value;
            const label = (sourceSessionSites.find(site => site.key === key) || {}).label || key || 'nền tảng';
            const saved = Boolean(sourceCookiesSet[key]);
            byId('clear-source-cookie-btn').disabled = !saved;
            byId('setting-source-cookie').placeholder = saved
                ? `Đã lưu cookie ${label}. Dán chuỗi mới rồi bấm Lưu để thay.`
                : `Dán Cookie header của ${label}, ví dụ: name=value; name2=value2`;
        }

        async function saveSourceCookie(clear = false) {
            const key = byId('setting-source-cookie-site').value;
            if (!key) return;
            const value = clear ? null : byId('setting-source-cookie').value.trim();
            if (!clear && !value) { showToast('Hãy dán chuỗi cookie trước khi lưu.', 'error'); return; }
            if (clear && !window.confirm('Xoá cookie thủ công đã lưu cho nền tảng này?')) return;
            try {
                const saved = await fetchJson('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({source_cookies: {[key]: value}})});
                sourceCookiesSet = saved.source_cookies_set || sourceCookiesSet;
                byId('setting-source-cookie').value = '';
                updateSourceCookieHelp();
                showToast(clear ? 'Đã xoá cookie thủ công.' : 'Đã lưu cookie, mã hoá trên máy này.');
                loadSourceSessions();
            } catch (error) { showToast(error.message, 'error'); }
        }

        async function saveCookiesFile(path) {
            try {
                const saved = await fetchJson('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({cookies_file: path})});
                byId('setting-cookies-file').value = saved.cookies_file ?? path;
                showToast(path ? 'yt-dlp sẽ dùng file cookies.txt này.' : 'Đã bỏ file cookies.txt.');
            } catch (error) { showToast(error.message, 'error'); }
        }

        async function chooseCookiesFile() {
            const api = window.pywebview && window.pywebview.api;
            if (!api || typeof api.choose_cookies_file !== 'function') {
                showToast('Nút chọn file hoạt động trong cửa sổ app; ở trình duyệt hãy dán đường dẫn đầy đủ.', 'error');
                return;
            }
            try {
                const selected = await api.choose_cookies_file();
                if (selected) await saveCookiesFile(selected);
            } catch (error) { showToast(error.message || String(error), 'error'); }
        }

        async function loadHistory() {
            const field = byId('history-field');
            const list = byId('history-list');
            try {
                const data = await fetchJson('/api/history'); list.replaceChildren();
                // Only shown to people who used Douzy before v1.6.0.
                field.hidden = !data.length;
                if (!data.length) return;
                data.slice(0,8).forEach(item => { const row=document.createElement('div'); row.className='history-row'; const info=document.createElement('div'); const title=document.createElement('strong'); title.textContent=item.title||`Douyin ${item.aweme_id}`; const meta=document.createElement('small'); meta.textContent=`ID ${item.aweme_id}`; info.append(title,meta); const button=document.createElement('button'); button.className='btn-ghost'; button.textContent='Nạp'; button.onclick=()=>{invalidatePreview(); byId('url-input').value=`https://www.douyin.com/video/${item.aweme_id}`; byId('project-name').value=item.title||''; autoProjectName=item.title||''; byId('char-count').textContent=`${byId('url-input').value.length} ký tự`; updateNamePreview(); if (window.VietSubWorkbench) window.VietSubWorkbench.focusSource(); else window.scrollTo({top:0,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}; row.append(info,button); list.appendChild(row); });
            } catch (_) { field.hidden = true; }
        }

        function bootApp() {
            loadSettings(); loadHealth(); loadHistory(); loadQueue(); checkEdge(); checkForUpdate(); loadSourceSessions();
            scheduleQueuePolling(); setInterval(checkEdge, 5000);
        }

        window.addEventListener('load', () => {
            window.VietSubWorkbench?.init({request: fetchJson, notify: showToast, endCropDrag, updateSteps, refreshProject,
                projectReceived: (workspace, snapshot) => {
                    if (Object.hasOwn(workspaceProjects, workspace) && snapshot?.job_id) workspaceProjects[workspace] = snapshot.job_id;
                }});
            byId('preview-form').addEventListener('submit', preparePreview);
            byId('local-video-btn').onclick=chooseLocalVideo;
            byId('local-video-input').setAttribute('accept', 'video/mp4,video/x-matroska,.mp4,.mkv');
            byId('local-video-input').addEventListener('change', prepareLocalPreview);
            byId('batch-queue-btn').onclick = importShareLinks;
            byId('local-batch-btn').onclick = () => byId('local-batch-input').click();
            byId('local-batch-input').addEventListener('change', async () => {
                const input = byId('local-batch-input');
                try { await importLocalFiles(Array.from(input.files || [])); }
                finally { input.value = ''; }
            });
            const dropZone = byId('local-drop-zone');
            dropZone.addEventListener('dragover', event => { event.preventDefault(); if (!batchImportBusy && !localPreviewBusy && !linkPreviewBusy) dropZone.classList.add('is-dragover'); });
            dropZone.addEventListener('dragleave', event => { if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove('is-dragover'); });
            dropZone.addEventListener('drop', dropLocalFiles);
            byId('url-input').addEventListener('input', () => {
                setUrlError();
                invalidatePreview();
                refreshComposerControls();
            });
            byId('queue-select-all').addEventListener('change', event => setQueueSelection(event.target.checked ? 'all' : 'none'));
            byId('queue-select-success').onclick = () => setQueueSelection('success');
            byId('queue-select-finished').onclick = () => setQueueSelection('finished');
            byId('queue-select-old').onclick = () => setQueueSelection('old');
            byId('queue-clear-selection').onclick = () => setQueueSelection('none');
            byId('bulk-delete-btn').onclick = openBulkDelete;
            byId('bulk-delete-cancel').onclick = closeBulkDelete;
            byId('bulk-delete-confirm').onclick = deleteSelectedQueueJobs;
            for (const id of ['bulk-delete-records', 'bulk-delete-files', 'bulk-keep-srt']) byId(id).addEventListener('change', () => refreshBulkEstimate());
            byId('bulk-estimate-retry').onclick = () => refreshBulkEstimate(true);
            byId('bulk-delete-dialog').addEventListener('cancel', event => { event.preventDefault(); closeBulkDelete(); });
            for (const id of ['setting-auto-delete-source-video', 'setting-generate-publish-metadata']) byId(id).addEventListener('change', saveSettings);
            byId('project-name').addEventListener('input', () => { autoProjectName = ''; updateNamePreview(); });
            byId('add-queue-btn').addEventListener('click', addToQueue);
            byId('preview-player').addEventListener('loadedmetadata', () => {
                const player = byId('preview-player');
                if (!currentPreview || player.dataset.previewId !== currentPreview.id || !player.videoWidth || !player.videoHeight) return;
                currentPreview.video_resolution = {width: player.videoWidth, height: player.videoHeight};
                const stage = byId('video-stage'); stage.style.aspectRatio = `${player.videoWidth} / ${player.videoHeight}`; stage.style.minHeight = '0';
                byId('preview-status').value = currentPreview.source_type === 'local' ? 'Video local sẵn sàng' : 'Sẵn sàng chỉnh crop';
                refreshComposerControls();
            });
            byId('preview-player').addEventListener('error', () => {
                const player = byId('preview-player');
                if (!currentPreview || player.dataset.previewId !== currentPreview.id) return;
                if (currentPreview.video_resolution) {
                    // H.265 or some MKV files do not play in WebView2, but FFprobe already read the size.
                    byId('preview-status').value = 'Không phát được trong app; vẫn chỉnh crop và thêm vào hàng đợi được';
                    refreshComposerControls();
                    return;
                }
                byId('preview-status').value = 'Không đọc được metadata video';
                byId('add-queue-btn').disabled = true;
            });
            byId('crop-box').addEventListener('pointerdown', startCropDrag);
            window.addEventListener('pointermove', moveCrop); window.addEventListener('pointerup', endCropDrag);
            window.addEventListener('pointercancel', endCropDrag); byId('crop-box').addEventListener('lostpointercapture', endCropDrag);
            ['crop-x','crop-y','crop-width','crop-height'].forEach(id => byId(id).addEventListener('change', cropFromInputs));
            byId('full-frame-btn').onclick=()=>setCrop({crop_x:0,crop_y:0,crop_width:1,crop_height:1});
            byId('lower-third-btn').onclick=()=>setCrop({crop_x:.04,crop_y:.64,crop_width:.92,crop_height:.30});
            byId('save-crop-btn').onclick=saveDefaultCrop;
            byId('start-queue-btn').onclick=startQueue; byId('cancel-btn').onclick=cancelJob; byId('clear-finished-btn').onclick=clearFinished;
            byId('open-result-btn').onclick=openResult;
            for (const name of ['editor', 'uploader']) byId(`result-${name}-btn`).onclick = () => handoffProject(resultJobId, name);
            byId('setting-translation-provider').addEventListener('change', () => { updateTranslationProviderUI(true); saveSettings(); });
            byId('setting-translation-instructions').addEventListener('change', saveSettings);
            byId('reset-translation-instructions-btn').onclick=resetTranslationInstructions;
            byId('apply-translation-preset-btn').onclick=applyTranslationPreset;
            if (byId('setting-glossary-select')) {
                byId('settings-open-btn')?.addEventListener('click', () => { loadGlossaries(); });
                byId('setting-glossary-select').addEventListener('change', event => activateGlossary(event.target.value));
                byId('setting-glossary-new-btn').onclick = createGlossaryProfile;
                byId('setting-glossary-delete-btn').onclick = deleteCurrentGlossary;
                byId('setting-glossary-save-btn').onclick = saveCurrentGlossary;
                byId('setting-glossary-add-entry-btn').onclick = () => {
                    const list = byId('setting-glossary-entries');
                    const row = createGlossaryEntryRow();
                    list.appendChild(row);
                    row.querySelector?.('input[data-glossary-field="source"]')?.focus();
                };
                byId('setting-glossary-entries').addEventListener('click', event => {
                    const btn = event.target?.closest?.('[data-glossary-remove]') || (event.target?.dataset?.glossaryRemove ? event.target : null);
                    if (btn) btn.parentElement?.remove?.();
                });
            }
            byId('setting-notebook').addEventListener('change', saveSettings); byId('setting-output').addEventListener('change', saveSettings); byId('ocr-lang').addEventListener('change', saveSettings);
            byId('setting-api-base-url').addEventListener('change', saveSettings); byId('setting-api-model').addEventListener('change', saveSettings); byId('setting-api-key').addEventListener('change', saveSettings);
            byId('test-api-btn').onclick=testTranslationApi; byId('clear-api-key-btn').onclick=clearTranslationApiKey;
            byId('setting-ocr-speed').addEventListener('change', saveSettings); byId('setting-frames-skip').addEventListener('change', saveSettings);
            byId('choose-output-btn').onclick=chooseOutputDirectory;
            byId('setting-edge-background').addEventListener('change', saveSettings); byId('show-edge-btn').onclick=showEdgeLogin;
            byId('setting-browser-kind').addEventListener('change', () => {
                updateBrowserSettingsUI();
                if (byId('setting-browser-kind').value === 'custom' && !byId('setting-browser-path').value.trim()) {
                    byId('browser-help').textContent = 'Chọn hoặc dán đường dẫn file .exe của một trình duyệt Chromium (Chrome, Edge, Brave, Cốc Cốc…). App giữ trình duyệt cũ cho tới khi có file .exe.';
                    return;
                }
                saveSettings();
            });
            byId('setting-browser-path').addEventListener('change', saveSettings);
            byId('choose-browser-path-btn').onclick=chooseBrowserExecutable;
            byId('create-shortcut-btn').onclick=createDesktopShortcut;
            byId('health-btn').onclick=loadHealth;
            byId('refresh-sessions-btn').onclick=loadSourceSessions;
            byId('setting-source-cookie-site').addEventListener('change', updateSourceCookieHelp);
            byId('save-source-cookie-btn').onclick=()=>saveSourceCookie(false);
            byId('clear-source-cookie-btn').onclick=()=>saveSourceCookie(true);
            byId('choose-cookies-file-btn').onclick=chooseCookiesFile;
            byId('setting-cookies-file').addEventListener('change', () => saveCookiesFile(byId('setting-cookies-file').value.trim()));
            byId('update-btn').onclick=installUpdate;
            updateNamePreview(); bootApp();
        });
