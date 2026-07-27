        const byId = id => document.getElementById(id);
        const apiToken = document.querySelector('meta[name="api-token"]').content;
        const appVersion = document.querySelector('meta[name="app-version"]').content;
        const steps = ['resolve', 'download', 'ocr', 'translate', 'done'];
        const statusLabels = { queued: 'Đang chờ', running: 'Đang chạy', success: 'Hoàn tất', error: 'Lỗi', cancelled: 'Đã huỷ' };
        const stepIndex = { resolve: 0, download: 1, ocr: 2, translate: 3, done: 4 };
        let currentPreview = null;
        let previewRequestId = 0;
        let autoProjectName = '';
        let healthReadiness = {local: false, douyin: false};
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

        function showToast(message, type = 'success') {
            clearTimeout(toastTimer);
            const toast = byId('toast');
            toast.textContent = message;
            toast.className = `toast ${type === 'error' ? 'error' : ''} show`;
            toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3500);
        }

        async function fetchJson(url, options = {}) {
            const method = (options.method || 'GET').toUpperCase();
            const headers = new Headers(options.headers || {});
            headers.set('X-VietSub-Token', apiToken);
            const nextOptions = {...options, headers};
            if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && !headers.has('Content-Type') && !(options.body instanceof FormData)) {
                headers.set('Content-Type', 'application/json');
                if (options.body == null) nextOptions.body = '{}';
            }
            const response = await fetch(url, nextOptions);
            let data = {};
            try { data = await response.json(); } catch (_) {}
            if (!response.ok) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }
            return data;
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
                const data = await fetchJson('/api/update/install', {method: 'POST'});
                button.textContent = 'Đang khởi động lại…';
                byId('update-copy').textContent = data.message || 'Đã tải xong. App đang cài bản mới và sẽ tự mở lại.';
                showToast(`Đang cài VietSub Studio v${data.version}.`);
            } catch (error) {
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

        async function preparePreview(event) {
            event.preventDefault();
            const url = byId('url-input').value.trim();
            if (!url) { setUrlError('Hãy dán link hoặc đoạn share Douyin.'); return; }
            setUrlError();
            invalidatePreview('Đang chuẩn bị…');
            const requestId = previewRequestId;
            const button = byId('prepare-btn');
            button.disabled = true;
            button.innerHTML = '<span style="display:inline-flex;gap:8px;align-items:center"><i class="spinner"></i> Đang tải preview</span>';
            try {
                const data = await fetchJson('/api/previews', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url}) });
                if (requestId !== previewRequestId) return;
                applyPreview(data);
                showToast('Preview đã sẵn sàng. Kéo khung vàng vào vùng phụ đề.');
            } catch (error) {
                if (requestId !== previewRequestId) return;
                byId('preview-status').value = 'Chuẩn bị thất bại';
                showToast(error.message, 'error');
            } finally {
                button.disabled = false;
                button.textContent = 'Tải preview';
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
            invalidatePreview('Đang copy video local…');
            const requestId = previewRequestId;
            const button = byId('local-video-btn');
            button.disabled = true;
            button.innerHTML = '<span style="display:inline-flex;gap:8px;align-items:center"><i class="spinner"></i> Đang nạp MP4</span>';
            byId('preview-status').value = 'Đang copy video local…';
            const form = new FormData(); form.append('video', file);
            try {
                const data = await fetchJson('/api/previews/local', {method:'POST', body:form});
                if (requestId !== previewRequestId) return;
                applyPreview(data);
                showToast('MP4 local đã sẵn sàng để chỉnh vùng OCR.');
            } catch (error) {
                if (requestId !== previewRequestId) return;
                byId('preview-status').value = 'Không nạp được MP4';
                showToast(error.message, 'error');
            } finally {
                button.disabled = false;
                button.textContent = 'Chọn MP4';
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
            const queueReady = [...queuedSources].every(source => source === 'local' ? healthReadiness.local : healthReadiness.douyin);
            byId('start-queue-btn').disabled = !queuedCount || running || !queueReady;
            byId('start-queue-btn').textContent = running ? 'Đang chạy tuần tự…' : !queueReady ? 'Thiếu thành phần bắt buộc' : `Chạy hàng đợi${queuedCount ? ` (${queuedCount})` : ''}`;
            const cancelButton = byId('cancel-btn');
            cancelButton.hidden = !running;
            if (!running) { cancelButton.disabled = false; cancelButton.textContent = 'Huỷ job'; }
            if (!queueJobs.length) { list.innerHTML = '<div class="queue-empty">Hàng đợi đang trống.<br>Chuẩn bị preview đầu tiên để bắt đầu.</div>'; return; }
            list.replaceChildren();
            queueJobs.forEach((job, index) => {
                const row = document.createElement('article'); row.className = `queue-item ${job.status}`;
                const top = document.createElement('div'); top.className = 'queue-item-top';
                const info = document.createElement('div'); info.style.minWidth = '0';
                const sourceMeta = job.source_type === 'local' ? 'LOCAL' : `ID ${job.video_id || 'đang chờ giải mã'}`;
                const queueIndex = document.createElement('div'); queueIndex.className = 'queue-index'; queueIndex.textContent = `JOB ${String(index + 1).padStart(2,'0')}`;
                const queueName = document.createElement('div'); queueName.className = 'queue-name'; queueName.textContent = job.project_name || job.video_title || (job.source_type === 'local' ? 'Video local' : 'Tên tự động từ Douyin');
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
                if (job.needs_edge_login) { const login = document.createElement('button'); login.className = 'btn-ghost'; login.textContent = 'Mở Edge đăng nhập'; login.onclick = showEdgeLogin; actions.appendChild(login); }
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
                const [data] = await Promise.all([fetchJson(`/api/queue?revision=${queueRevision}`), pollProgress()]);
                queueRevision = Number.isInteger(data.revision) ? data.revision : queueRevision;
                if (Array.isArray(data.jobs)) {
                    queueJobs = data.jobs;
                    const signature = JSON.stringify(queueJobs);
                    if (signature !== lastQueueSignature) { lastQueueSignature = signature; renderQueue(); }
                }
            }
            catch (error) { console.warn(error); }
            finally { queueLoadInFlight = false; byId('queue-list').setAttribute('aria-busy', 'false'); }
        }

        function scheduleQueuePolling() {
            const busy = queueJobs.some(job => job.status === 'running' || job.status === 'queued');
            setTimeout(async () => { await loadQueue(); scheduleQueuePolling(); }, busy ? 1000 : 4000);
        }

        async function startQueue() {
            try { await fetchJson('/api/queue/start', {method: 'POST'}); hideResult(); displayedLogCount = 0; progressJobId = ''; byId('console-box').replaceChildren(); await loadQueue(); showToast('Hàng đợi đã bắt đầu chạy tuần tự.'); }
            catch (error) { showToast(error.message, 'error'); }
        }

        async function removeQueueJob(id, status = 'queued') {
            if (status !== 'queued' && !window.confirm('Xoá job này khỏi lịch sử? File project đã tạo sẽ không bị xoá.')) return;
            try { await fetchJson(`/api/queue/${id}`, {method: 'DELETE'}); queueRevision = -1; await loadQueue(); }
            catch (error) { showToast(error.message, 'error'); }
        }

        async function retryQueueJob(id) {
            try { await fetchJson(`/api/queue/${id}/retry`, {method:'POST'}); hideResult(); await loadQueue(); showToast('Job đã được đưa lại vào hàng đợi.'); }
            catch (error) { showToast(error.message, 'error'); }
        }

        async function showEdgeLogin() {
            try { await fetchJson('/api/edge/show', {method:'POST'}); showToast('Đã mở Edge. Đăng nhập xong rồi bấm chạy lại job.'); }
            catch (error) { showToast(error.message, 'error'); }
        }

        async function clearFinished() {
            if (!window.confirm('Xoá toàn bộ job đã hoàn tất khỏi lịch sử? File project sẽ được giữ nguyên.')) return;
            try { await fetchJson('/api/queue/finished', {method: 'DELETE'}); hideResult(); queueRevision = -1; await loadQueue(); }
            catch (error) { showToast(error.message, 'error'); }
        }

        async function cancelJob() {
            const button = byId('cancel-btn'); button.disabled = true; button.textContent = 'Đang huỷ…';
            try { await fetchJson('/api/cancel', {method:'POST'}); showToast('Đã gửi yêu cầu huỷ job hiện tại.'); }
            catch (error) { button.disabled = false; button.textContent = 'Huỷ job'; showToast(error.message, 'error'); }
        }

        function updateSteps(step, status) {
            const activeIndex = stepIndex[step] ?? 0;
            steps.forEach((name, index) => {
                const item = byId(`step-${name}`);
                item.className = index < activeIndex || status === 'success' ? 'step completed' : index === activeIndex ? `step ${status === 'error' || status === 'cancelled' ? 'error' : 'active'}` : 'step';
            });
            byId('workflow-progress').setAttribute('aria-valuenow', String(progressWidth(step, status)));
            const captions = {resolve:'Đang giải mã link',download:'Đang chuẩn bị video',ocr:'VideOCR đang quét',translate:'Gemini đang dịch',done:'Đã hoàn tất'};
            const errorCaptions = {resolve:'Lỗi giải mã link',download:'Lỗi chuẩn bị video',ocr:'VideOCR bị lỗi',translate:'Dịch bằng Gemini bị lỗi',done:'Hoàn tất không hợp lệ'};
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

        async function checkEdge() {
            try { const data = await fetchJson('/api/check-edge'); byId('edge-pill').className = `status-pill ${data.connected ? 'ready' : 'error'}`; byId('edge-pill').querySelector('span').textContent = data.connected ? 'Edge sẵn sàng' : 'Edge chưa kết nối'; }
            catch (_) {}
        }

        async function loadSettings() {
            try {
                const data = await fetchJson('/api/settings');
                byId('setting-notebook').value = data.notebook_url || ''; byId('setting-output').value = data.output_dir || '';
                byId('setting-output').placeholder = data.default_output_dir || 'Documents\\VietSub Studio';
                const syncWarning = data.output_dir_synced ? ' ⚠ Thư mục này đang được OneDrive đồng bộ: mỗi video xuất sẽ bị upload lên cloud và có thể chậm hơn — cân nhắc chọn thư mục ngoài OneDrive.' : '';
                byId('output-help').textContent = `Để trống sẽ lưu tại ${data.default_output_dir}. Mỗi project có folder riêng chứa đủ video, SRT và thumbnail.${syncWarning}`;
                byId('setting-crop').value = data.crop_coords ? JSON.stringify(data.crop_coords) : 'Toàn khung hình';
                byId('setting-download').value = data.download_dir || 'Chưa xác định'; byId('ocr-lang').value = data.ocr_lang || 'ch';
                byId('setting-ocr-speed').value = data.ocr_model_mode || 'auto';
                byId('setting-frames-skip').value = data.ocr_frames_to_skip == null ? '' : String(data.ocr_frames_to_skip);
                byId('setting-edge-background').checked = data.edge_background !== false;
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
        async function saveSettingsNow() {
            try {
                await fetchJson('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({notebook_url:byId('setting-notebook').value.trim(), output_dir:byId('setting-output').value.trim(), ocr_lang:byId('ocr-lang').value, ocr_model_mode:byId('setting-ocr-speed').value, ocr_frames_to_skip:byId('setting-frames-skip').value === '' ? null : Number(byId('setting-frames-skip').value), edge_background:byId('setting-edge-background').checked})});
                showToast('Đã lưu thiết lập.');
            } catch (error) { showToast(error.message, 'error'); }
        }

        function saveSettings() {
            clearTimeout(saveSettingsTimer);
            saveSettingsTimer = setTimeout(saveSettingsNow, 250);
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
                healthReadiness = {local: Boolean(data.ready_local), douyin: Boolean(data.ready_douyin)};
                renderQueue();
                const labels = {douzy_sidecar:'Douzy sidecar',douzy_config:'Cấu hình Douzy',douzy_database:'Database Douzy',videocr:'VideOCR',ffprobe:'FFprobe',notebook:'Gemini Notebook',edge:'Microsoft Edge'};
                Object.entries(data.checks).forEach(([key,item]) => { const row=document.createElement('div'); row.className=`diagnostic-item ${item.ok?'ok':item.required?'error':''}`; const strong=document.createElement('strong'); strong.textContent=`${labels[key]||key}: ${item.ok?'OK':item.required?'Thiếu':'Tuỳ chọn'}`; const small=document.createElement('small'); small.textContent=item.detail||''; row.append(strong,small); list.appendChild(row); });
            } catch (error) { healthReadiness = {local: false, douyin: false}; renderQueue(); list.textContent = error.message; }
        }

        async function loadHistory() {
            const list = byId('history-list');
            try {
                const data = await fetchJson('/api/history'); list.replaceChildren();
                if (!data.length) { list.textContent = 'Chưa có video trong lịch sử Douzy.'; return; }
                data.slice(0,8).forEach(item => { const row=document.createElement('div'); row.className='history-row'; const info=document.createElement('div'); const title=document.createElement('strong'); title.textContent=item.title||`Douyin ${item.aweme_id}`; const meta=document.createElement('small'); meta.textContent=`ID ${item.aweme_id}`; info.append(title,meta); const button=document.createElement('button'); button.className='btn-ghost'; button.textContent='Nạp'; button.onclick=()=>{invalidatePreview(); byId('url-input').value=`https://www.douyin.com/video/${item.aweme_id}`; byId('project-name').value=item.title||''; autoProjectName=item.title||''; byId('char-count').textContent=`${byId('url-input').value.length} ký tự`; updateNamePreview(); window.scrollTo({top:0,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}; row.append(info,button); list.appendChild(row); });
            } catch (error) { list.textContent = error.message; }
        }

        function bootApp() {
            loadSettings(); loadHealth(); loadHistory(); loadQueue(); checkEdge(); checkForUpdate();
            scheduleQueuePolling(); setInterval(checkEdge, 5000);
        }

        window.addEventListener('load', () => {
            byId('preview-form').addEventListener('submit', preparePreview);
            byId('local-video-btn').onclick=()=>byId('local-video-input').click(); byId('local-video-input').addEventListener('change', prepareLocalPreview);
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
            byId('setting-notebook').addEventListener('change', saveSettings); byId('setting-output').addEventListener('change', saveSettings); byId('ocr-lang').addEventListener('change', saveSettings);
            byId('setting-ocr-speed').addEventListener('change', saveSettings); byId('setting-frames-skip').addEventListener('change', saveSettings);
            byId('choose-output-btn').onclick=chooseOutputDirectory;
            byId('setting-edge-background').addEventListener('change', saveSettings); byId('show-edge-btn').onclick=showEdgeLogin;
            byId('create-shortcut-btn').onclick=createDesktopShortcut;
            byId('health-btn').onclick=loadHealth;
            byId('update-btn').onclick=installUpdate;
            updateNamePreview(); bootApp();
        });
