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
        let displayedLogCount = 0;
        let progressJobId = '';
        let resultOpenPath = '';
        let toastTimer = null;
        let automaticUpdateSupported = false;
        let pollFailureCount = 0;
        let translationProviderPresets = {};
        let translationApiKeySet = false;
        let lastTranslationProvider = 'notebook';
        let defaultTranslationInstructions = '';
        let translationInstructionPresets = {};
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
            byId('video-stage').style.aspectRatio = ''; byId('video-stage').style.minHeight = '';
            byId('preview-placeholder').hidden = false; byId('crop-box').hidden = true; byId('crop-tools').hidden = true;
            byId('preview-title').textContent = 'Chưa có video'; byId('crop-summary').textContent = 'Crop: —';
            byId('preview-status').value = status; byId('add-queue-btn').disabled = true;
        }

        function formatMegabytes(bytes) {
            return (Number(bytes || 0) / 1048576).toFixed(1).replace('.', ',');
        }

        // prefix 'preview' is the panel under the composer, 'job' the one in the job progress card.
        function renderDownloadProgress(download, prefix = 'job') {
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

        async function preparePreview(event) {
            event.preventDefault();
            const url = byId('url-input').value.trim();
            if (!url) { setUrlError('Hãy dán link video hoặc đoạn chia sẻ.'); return; }
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
                button.disabled = false;
                button.textContent = 'Tải preview';
            }
        }

        // In the desktop window the native dialog gives the real path: the video is used in
        // place (no upload, no %TEMP% copy). The browser fallback still uploads the MP4.
        async function chooseLocalVideo() {
            const api = window.pywebview && window.pywebview.api;
            if (!api || typeof api.choose_local_video !== 'function') { byId('local-video-input').click(); return; }
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
                button.disabled = false;
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
        }

        async function prepareLocalPreview() {
            const input = byId('local-video-input');
            const file = input.files?.[0];
            if (!file) return;
            invalidatePreview('Đang tải MP4 lên app…');
            const requestId = previewRequestId;
            const button = byId('local-video-btn');
            button.disabled = true;
            button.innerHTML = '<span style="display:inline-flex;gap:8px;align-items:center"><i class="spinner"></i> Đang nạp MP4</span>';
            byId('preview-status').value = 'Đang tải MP4 lên app…';
            const form = new FormData(); form.append('video', file);
            try {
                const data = await fetchJson('/api/previews/local', {method:'POST', body:form, timeoutMs: 0});
                if (requestId !== previewRequestId) return;
                applyPreview(data);
                showToast('MP4 local đã sẵn sàng để chỉnh vùng OCR.');
            } catch (error) {
                if (requestId !== previewRequestId) return;
                byId('preview-status').value = 'Không nạp được MP4';
                showToast(error.message, 'error');
            } finally {
                button.disabled = false;
                button.textContent = 'Chọn video';
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
            if (!currentPreview) return;
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
            byId('char-count').textContent = '0 ký tự'; updateNamePreview();
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

        function renderQueue() {
            const list = byId('queue-list');
            byId('queue-count').textContent = queueJobs.length;
            const queuedCount = queueJobs.filter(job => job.status === 'queued').length;
            const running = queueJobs.some(job => job.status === 'running');
            const queuedSources = new Set(queueJobs.filter(job => job.status === 'queued').map(job => job.source_type || 'douyin'));
            const queueReady = [...queuedSources].every(sourceReady);
            byId('start-queue-btn').disabled = !queuedCount || running || !queueReady;
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
            if (!queueJobs.length) { list.innerHTML = '<div class="queue-empty">Hàng đợi đang trống.<br>Chuẩn bị preview đầu tiên để bắt đầu.</div>'; return; }
            list.replaceChildren();
            queueJobs.forEach((job, index) => {
                const row = document.createElement('article'); row.className = `queue-item ${job.status}`;
                const top = document.createElement('div'); top.className = 'queue-item-top';
                const info = document.createElement('div'); info.style.minWidth = '0';
                const sourceLabel = sourceLabels[job.source_type] || 'Douyin';
                const sourceMeta = job.source_type === 'local' ? 'VIDEO TRÊN MÁY' : `${sourceLabel.toUpperCase()} · ID ${job.video_id || 'đang chờ giải mã'}`;
                const queueIndex = document.createElement('div'); queueIndex.className = 'queue-index'; queueIndex.textContent = `JOB ${String(index + 1).padStart(2,'0')}`;
                const queueName = document.createElement('div'); queueName.className = 'queue-name'; queueName.textContent = job.project_name || job.video_title || (job.source_type === 'local' ? 'Video trên máy' : `Tên tự động từ ${sourceLabel}`);
                const queueId = document.createElement('div'); queueId.className = 'queue-id'; queueId.textContent = `${sourceMeta} · ${String(job.ocr_lang || '').toUpperCase()}`;
                info.append(queueIndex, queueName, queueId);
                const badge = document.createElement('span'); badge.className = `badge ${job.status}`; badge.textContent = statusLabels[job.status] || job.status;
                top.append(info, badge); row.appendChild(top);
                const progress = document.createElement('div'); progress.className = 'queue-progress'; progress.setAttribute('role', 'progressbar'); progress.setAttribute('aria-valuemin', '0'); progress.setAttribute('aria-valuemax', '100');
                const progressValue = progressWidth(job.step, job.status); progress.setAttribute('aria-valuenow', String(progressValue)); const bar = document.createElement('i'); bar.style.width = `${progressValue}%`; progress.appendChild(bar); row.appendChild(progress);
                if (job.error) { const error = document.createElement('div'); error.className = 'queue-error'; error.textContent = job.error; row.appendChild(error); }
                const actions = document.createElement('div'); actions.className = 'queue-item-actions';
                if (job.status !== 'running') { const remove = document.createElement('button'); remove.className = 'btn-ghost'; remove.textContent = job.status === 'queued' ? 'Xoá khỏi hàng đợi' : 'Xoá job'; remove.onclick = () => removeQueueJob(job.id, job.status); actions.appendChild(remove); }
                if (job.status === 'error' || job.status === 'cancelled') { const retry = document.createElement('button'); retry.className = 'btn-secondary'; retry.textContent = job.retry_step === 'translate' ? 'Chạy lại bước dịch' : job.retry_step === 'ocr' ? 'Chạy lại từ OCR' : 'Chạy lại từ video'; retry.onclick = () => retryQueueJob(job.id); actions.appendChild(retry); }
                if (job.needs_edge_login) { const login = document.createElement('button'); login.className = 'btn-ghost'; login.textContent = `Mở ${browserName} đăng nhập`; login.onclick = showEdgeLogin; actions.appendChild(login); }
                if (job.status === 'success' && job.result_ready) { const open = document.createElement('button'); open.className = 'btn-ghost'; open.textContent = 'Mở kết quả'; open.onclick = () => showResult(job.result, true); actions.appendChild(open); }
                else if ((job.status === 'error' || job.status === 'cancelled') && job.result?.project_dir) { const open = document.createElement('button'); open.className = 'btn-ghost'; open.textContent = 'Mở thư mục làm dở'; open.onclick = () => openResultPath(job.result.project_dir); actions.appendChild(open); }
                if (actions.children.length) row.appendChild(actions);
                list.appendChild(row);
            });
        }

        async function loadQueue() {
            if (queueLoadInFlight) return;
            queueLoadInFlight = true;
            byId('queue-list').setAttribute('aria-busy', 'true');
            try {
                // Handle each response on its own, so a failed progress poll never drops a good queue update.
                const [queueResult, progressResult] = await Promise.allSettled([
                    fetchJson(`/api/queue?revision=${queueRevision}`),
                    pollProgress(),
                ]);
                if (queueResult.status === 'fulfilled') {
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
            await runOnce(`remove-${id}`, async () => {
                try { await fetchJson(`/api/queue/${id}`, {method: 'DELETE'}); queueRevision = -1; await loadQueue(); }
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
                try { await fetchJson(`/api/queue/${id}/retry`, {method:'POST'}); hideResult(); await loadQueue(); showToast('Job đã được đưa lại vào hàng đợi.'); }
                catch (error) { showToast(error.message, 'error'); }
            });
        }

        async function showEdgeLogin() {
            try { await fetchJson('/api/edge/show', {method:'POST', timeoutMs: 90000}); showToast(`Đã mở ${browserName}. Đăng nhập xong rồi bấm chạy lại job.`); }
            catch (error) { showToast(error.message, 'error'); }
        }

        async function clearFinished() {
            if (!window.confirm('Xoá toàn bộ job đã hoàn tất khỏi lịch sử? File project sẽ được giữ nguyên.')) return;
            await runOnce('clear-finished', async () => {
                try { await fetchJson('/api/queue/finished', {method: 'DELETE'}); hideResult(); queueRevision = -1; await loadQueue(); }
                catch (error) { showToast(error.message, 'error'); }
            });
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
            try {
                const data = await fetchJson(`/api/progress?after=${displayedLogCount}`);
                if (data.job_id && data.job_id !== progressJobId) { hideResult(); progressJobId = data.job_id; displayedLogCount = 0; byId('console-box').replaceChildren(); return pollProgress(); }
                (data.logs || []).forEach(addLog); displayedLogCount = data.next_log_index ?? displayedLogCount;
                // A preview download has its own panel; otherwise the live download belongs to the job.
                if (!downloadWatchTimer) renderDownloadProgress(data.download, 'job');
                if (data.status !== 'idle') updateSteps(data.step, data.status);
                if (data.status === 'success' && data.result_ready) showResult(data.result, false);
                else if (['running', 'error', 'cancelled'].includes(data.status)) hideResult();
                pollFailureCount = 0;
            } catch (error) {
                pollFailureCount += 1;
                if (pollFailureCount >= 3) byId('process-caption').textContent = 'Mất kết nối cục bộ · đang thử lại';
                throw error;
            }
        }

        function hideResult() {
            resultOpenPath = '';
            byId('res-video').textContent = ''; byId('res-thumbnail').textContent = ''; byId('res-thumbnail-row').hidden = true; byId('res-raw').textContent = ''; byId('res-vi').textContent = '';
            byId('result-box').hidden = true;
        }

        function showResult(result, scroll = false) {
            resultOpenPath = result.project_dir || result.translated_srt || '';
            byId('result-title').textContent = `${result.project_name || 'Bộ file'} đã sẵn sàng`;
            byId('res-video').textContent = result.video || ''; byId('res-thumbnail').textContent = result.thumbnail || ''; byId('res-thumbnail-row').hidden = !result.thumbnail; byId('res-raw').textContent = result.raw_srt || ''; byId('res-vi').textContent = result.translated_srt || '';
            byId('result-box').hidden = false;
            if (scroll) byId('result-box').scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth', block:'center'});
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
                data.slice(0,8).forEach(item => { const row=document.createElement('div'); row.className='history-row'; const info=document.createElement('div'); const title=document.createElement('strong'); title.textContent=item.title||`Douyin ${item.aweme_id}`; const meta=document.createElement('small'); meta.textContent=`ID ${item.aweme_id}`; info.append(title,meta); const button=document.createElement('button'); button.className='btn-ghost'; button.textContent='Nạp'; button.onclick=()=>{invalidatePreview(); byId('url-input').value=`https://www.douyin.com/video/${item.aweme_id}`; byId('project-name').value=item.title||''; autoProjectName=item.title||''; byId('char-count').textContent=`${byId('url-input').value.length} ký tự`; updateNamePreview(); window.scrollTo({top:0,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}; row.append(info,button); list.appendChild(row); });
            } catch (_) { field.hidden = true; }
        }

        function bootApp() {
            loadSettings(); loadHealth(); loadHistory(); loadQueue(); checkEdge(); checkForUpdate(); loadSourceSessions();
            scheduleQueuePolling(); setInterval(checkEdge, 5000);
        }

        window.addEventListener('load', () => {
            byId('preview-form').addEventListener('submit', preparePreview);
            byId('local-video-btn').onclick=chooseLocalVideo; byId('local-video-input').addEventListener('change', prepareLocalPreview);
            byId('url-input').addEventListener('input', () => {
                byId('char-count').textContent=`${byId('url-input').value.length} ký tự`;
                setUrlError();
                invalidatePreview();
            });
            byId('project-name').addEventListener('input', () => { autoProjectName = ''; updateNamePreview(); });
            byId('add-queue-btn').addEventListener('click', addToQueue);
            byId('preview-player').addEventListener('loadedmetadata', () => {
                const player = byId('preview-player');
                if (!currentPreview || player.dataset.previewId !== currentPreview.id || !player.videoWidth || !player.videoHeight) return;
                currentPreview.video_resolution = {width: player.videoWidth, height: player.videoHeight};
                const stage = byId('video-stage'); stage.style.aspectRatio = `${player.videoWidth} / ${player.videoHeight}`; stage.style.minHeight = '0';
                byId('preview-status').value = currentPreview.source_type === 'local' ? 'MP4 local sẵn sàng' : 'Sẵn sàng chỉnh crop';
                byId('add-queue-btn').disabled = false;
            });
            byId('preview-player').addEventListener('error', () => {
                const player = byId('preview-player');
                if (!currentPreview || player.dataset.previewId !== currentPreview.id) return;
                if (currentPreview.video_resolution) {
                    // H.265 or some MKV files do not play in WebView2, but FFprobe already read the size.
                    byId('preview-status').value = 'Không phát được trong app; vẫn chỉnh crop và thêm vào hàng đợi được';
                    byId('add-queue-btn').disabled = false;
                    return;
                }
                byId('preview-status').value = 'Không đọc được metadata video';
                byId('add-queue-btn').disabled = true;
            });
            byId('crop-box').addEventListener('pointerdown', startCropDrag);
            window.addEventListener('pointermove', moveCrop); window.addEventListener('pointerup', endCropDrag);
            ['crop-x','crop-y','crop-width','crop-height'].forEach(id => byId(id).addEventListener('change', cropFromInputs));
            byId('full-frame-btn').onclick=()=>setCrop({crop_x:0,crop_y:0,crop_width:1,crop_height:1});
            byId('lower-third-btn').onclick=()=>setCrop({crop_x:.04,crop_y:.64,crop_width:.92,crop_height:.30});
            byId('save-crop-btn').onclick=saveDefaultCrop;
            byId('start-queue-btn').onclick=startQueue; byId('cancel-btn').onclick=cancelJob; byId('clear-finished-btn').onclick=clearFinished;
            byId('open-result-btn').onclick=openResult;
            byId('setting-translation-provider').addEventListener('change', () => { updateTranslationProviderUI(true); saveSettings(); });
            byId('setting-translation-instructions').addEventListener('change', saveSettings);
            byId('reset-translation-instructions-btn').onclick=resetTranslationInstructions;
            byId('apply-translation-preset-btn').onclick=applyTranslationPreset;
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
