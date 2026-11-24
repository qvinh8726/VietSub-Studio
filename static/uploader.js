/* Publish Hub owns only its workspace. All platform work is explicit and goes through the local app. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id), clone = value => JSON.parse(JSON.stringify(value));
    const platformLabels = Object.freeze({ tiktok: 'TikTok', youtube: 'YouTube', facebook: 'Facebook', douyin: 'Douyin', bilibili: 'Bilibili', xiaohongshu: 'Xiaohongshu' });
    const modes = ['public', 'private', 'draft'], modeLabels = { public: 'Công khai', private: 'Riêng tư', draft: 'Bản nháp' };
    const uploadModes = ['assisted', 'auto'], uploadModeLabels = { assisted: 'Bán tự động', auto: 'Tự động hoàn toàn' };
    const stepLabels = Object.freeze({
        open_studio: '1/7 Mở trang đăng',
        check_session: '2/7 Kiểm tra phiên',
        upload_file: '3/7 Tải tệp video',
        fill_metadata: '4/7 Điền tiêu đề & mô tả',
        set_visibility: '5/7 Chọn chế độ hiển thị',
        submit: '6/7 Chờ / Gửi đăng',
        confirm: '7/7 Xác nhận hoàn tất',
    });
    const statuses = {
        queued: 'Đang chờ',
        scheduled: 'Đã xếp lịch cục bộ',
        running: 'Đang đăng',
        awaiting_user_submit: 'Chờ bạn bấm Đăng trong trình duyệt',
        success: 'Thành công',
        error: 'Lỗi',
        needs_verification: 'Cần xác minh thủ công',
        cancelled: 'Đã huỷ',
        uncertain: 'Chưa rõ kết quả · nguy cơ đăng trùng',
    };
    const sessionLabels = { unknown: 'Chưa kiểm tra', ready: 'Sẵn sàng', alive: 'Sẵn sàng', expired: 'Hết phiên', needs_verification: 'Cần xác minh', error: 'Lỗi kiểm tra' };
    const activeQueue = new Set(['queued', 'scheduled', 'running', 'awaiting_user_submit']);
    const activeSessions = new Set(['queued', 'running', 'needs_verification']);
    const sessionStatuses = new Set(['queued', 'running', 'needs_verification', 'success', 'error', 'cancelled']);
    const draftStorageKey = 'vietsub.uploader.drafts.v1', maxStoredDrafts = 10, maxDraftBytes = 262144;
    const records = new Map(), storedDrafts = new Map(), sessions = new Map(), profileBusy = new Set(), queueBusy = new Set(), ytDrafts = new Map(), profileCardCache = new Map();
    let initialized = false, pendingSnapshot = null, current = null, lastProject = null, capabilities = null, profiles = [], jobs = [];
    let profilesLoading = false, profilesLoaded = false, queueLoading = false, mediaBusy = false, createBusy = false, capabilitySequence = 0, profileSequence = 0, queueSequence = 0;
    let navigation = 0, mediaSequence = 0, pollTimer = 0, pollFailures = 0, queueMutation = 0, profilesError = '', queueError = '', lastPresetSequence = 0, paintedProfilesEmpty = null;
    const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 256 && !/[\u0000-\u0020\u007f/\\?#]/.test(value) && value !== '.' && value !== '..';
    const uuid = value => typeof value === 'string' && /^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i.test(value);
    const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
    const byteLength = value => new TextEncoder().encode(value).length;
    const isYoutubeApiProfile = profile => profile?.platform === 'youtube' && profile?.upload_method === 'youtube_api';
    function normalizeMetadata(value) {
        return {
            title: typeof value?.title === 'string' ? value.title : '', description: typeof value?.description === 'string' ? value.description : '',
            hashtags: Array.isArray(value?.hashtags) ? value.hashtags.filter(item => typeof item === 'string').join(' ') : typeof value?.hashtags === 'string' ? value.hashtags : ''
        };
    }
    function validateMetadata(value) {
        const errors = {}, raw = object(value) ? value : {};
        for (const [key, max] of [['title', 120], ['description', 5000], ['hashtags', 5000]]) {
            if (typeof raw[key] !== 'string' || raw[key].length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(raw[key]) || (key === 'title' && /[\r\n]/.test(raw[key]))) errors[key] = `${key}: văn bản không hợp lệ hoặc quá ${max} ký tự.`;
        }
        const hashtags = typeof raw.hashtags === 'string' ? raw.hashtags.split(/[\s,]+/u).filter(Boolean).map(tag => tag.startsWith('#') ? tag : '#' + tag) : [];
        if (typeof raw.title === 'string' && !raw.title.trim()) errors.title = 'Cần tiêu đề video trước khi xếp hàng đăng.';
        if (hashtags.length > 30 || hashtags.some(tag => tag.length > 81 || !/^#[\p{L}\p{M}\p{N}_]+$/u.test(tag))) errors.hashtags = 'Tối đa 30 hashtag; chỉ dùng chữ, số và gạch dưới, mỗi hashtag tối đa 80 ký tự sau #.';
        return { errors, metadata: { title: typeof raw.title === 'string' ? raw.title.trim() : '', description: typeof raw.description === 'string' ? raw.description.trim() : '', hashtags: [...new Set(hashtags)] } };
    }
    function parseYoutubeClientSecretJson(raw) {
        if (typeof raw !== 'string' || !raw.trim()) return { error: 'Dán nội dung client_secret.json (loại Desktop app) trước khi nạp.' };
        if (byteLength(raw) > 65536) return { error: 'Tệp client_secret.json vượt quá giới hạn 64 KiB.' };
        let parsed;
        try { parsed = JSON.parse(raw); } catch (_) { return { error: 'Nội dung không phải JSON hợp lệ.' }; }
        if (!object(parsed)) return { error: 'Cấu trúc client_secret.json không hợp lệ.' };
        if (object(parsed.web) && !object(parsed.installed)) return { error: 'JSON này là loại Web application; hãy tạo OAuth Client loại Desktop app trên Google Cloud.' };
        const block = object(parsed.installed) ? parsed.installed : parsed;
        const client_id = typeof block.client_id === 'string' ? block.client_id.trim() : '';
        const client_secret = typeof block.client_secret === 'string' ? block.client_secret.trim() : '';
        if (!client_id || !client_id.endsWith('.apps.googleusercontent.com') || /[\s\u0000-\u001f\u007f]/.test(client_id)) {
            return { error: 'client_id trong JSON không hợp lệ (cần dạng *.apps.googleusercontent.com).' };
        }
        if (!client_secret || client_secret.length < 6 || client_secret.length > 256 || /[\s\u0000-\u001f\u007f]/.test(client_secret)) {
            return { error: 'client_secret trong JSON không hợp lệ.' };
        }
        return { client_id, client_secret, error: '' };
    }
    // Exact same-origin capability URLs only. No manifest paths, arbitrary query keys, credentials or fragments.
    function scopedUrl(value, path, allowed, required = []) {
        if (typeof value !== 'string' || !value || value.includes('\\') || /[\u0000-\u0020\u007f]/.test(value)) return '';
        try {
            const base = new URL(window.location.href), url = new URL(value, base);
            if (url.origin !== base.origin || url.username || url.password || url.hash || url.pathname !== path) return '';
            const keys = [...url.searchParams.keys()];
            if (new Set(keys).size !== keys.length || keys.some(key => !allowed.includes(key)) || required.some(key => !url.searchParams.get(key))) return '';
            return url.pathname + url.search;
        } catch (_) { return ''; }
    }
    function safeProjectUrl(value, jobId) {
        if (!validId(jobId)) return '';
        const url = scopedUrl(value, `/api/projects/${encodeURIComponent(jobId)}/video`, ['signature', 'asset'], ['signature']);
        if (!url) return '';
        const asset = new URL(url, window.location.href).searchParams.get('asset');
        return asset === null || ['source', 'edited'].includes(asset) ? url : '';
    }
    function safeMediaUrl(value, id) { return validId(id) ? scopedUrl(value, `/api/uploader/media/${encodeURIComponent(id)}/video`, ['signature'], ['signature']) : ''; }
    function safeScreenshotUrl(value, id) { return validId(id) ? scopedUrl(value, `/api/uploader/queue/${encodeURIComponent(id)}/screenshot`, ['signature'], ['signature']) : ''; }
    function safeTraceUrl(value, id) { return validId(id) ? scopedUrl(value, `/api/uploader/queue/${encodeURIComponent(id)}/trace`, ['signature'], ['signature']) : ''; }
    function scheduleValue(mode, raw, now = Date.now()) {
        if (mode === 'now') return { scheduled_at: null, error: '' };
        if (mode !== 'local' || typeof raw !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(raw)) return { error: 'Chọn ngày giờ hợp lệ theo Asia/Saigon.' };
        const iso = raw + ':00+07:00', parsed = Date.parse(iso);
        if (!Number.isFinite(parsed) || new Date(parsed + 7 * 3600000).toISOString().slice(0, 16) !== raw) return { error: 'Ngày giờ không tồn tại.' };
        if (parsed <= now + 1000) return { error: 'Giờ đăng phải ở tương lai (Asia/Saigon, UTC+07:00).' };
        if (parsed > now + 366 * 86400000) return { error: 'Lịch đăng chỉ hỗ trợ trong vòng 366 ngày.' };
        return { scheduled_at: iso, error: '' };
    }
    function validSnapshot(value) { return object(value) && validId(value.job_id) && object(value.manifest) && (!value.workspace || value.workspace === 'uploader'); }
    function validatedPending(value, jobId) {
        if (value === null || value === undefined) return null;
        if (!object(value) || value.job_id !== jobId || !uuid(value.request_id) || !Array.isArray(value.profile_ids) || !value.profile_ids.length || value.profile_ids.length > 100 || !value.profile_ids.every(uuid) ||
            !modes.includes(value.visibility) || typeof value.confirm_public !== 'boolean' || (value.visibility === 'public' && !value.confirm_public) ||
            (value.upload_mode !== undefined && !uploadModes.includes(value.upload_mode)) ||
            (value.debug_mode !== undefined && typeof value.debug_mode !== 'boolean') ||
            !(value.scheduled_at === null || typeof value.scheduled_at === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\+07:00$/.test(value.scheduled_at) && Number.isFinite(Date.parse(value.scheduled_at))) || !object(value.metadata) || !Array.isArray(value.metadata.hashtags)) return null;
        const checked = validateMetadata(normalizeMetadata(value.metadata));
        if (Object.keys(checked.errors).length) return null;
        const result = {
            job_id: jobId, profile_ids: [...new Set(value.profile_ids)], metadata: checked.metadata, visibility: value.visibility,
            scheduled_at: value.scheduled_at, request_id: value.request_id, confirm_public: value.confirm_public
        };
        if (value.upload_mode !== undefined) result.upload_mode = value.upload_mode;
        if (value.debug_mode !== undefined) result.debug_mode = value.debug_mode;
        return result;
    }
    function validatedDraft(value) {
        if (!object(value) || !validId(value.job_id) || !object(value.metadata) || !modes.includes(value.visibility) || !['now', 'local'].includes(value.schedule_mode) ||
            (value.upload_mode !== undefined && !uploadModes.includes(value.upload_mode)) ||
            (value.debug_mode !== undefined && typeof value.debug_mode !== 'boolean') ||
            typeof value.scheduled_local !== 'string' || (value.scheduled_local && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value.scheduled_local)) ||
            !Array.isArray(value.profile_ids) || value.profile_ids.length > 100 || !value.profile_ids.every(uuid) || typeof value.source_confirmed !== 'boolean' ||
            typeof value.dirty !== 'boolean' || !Number.isSafeInteger(value.revision) || value.revision < 0 || value.revision >= Number.MAX_SAFE_INTEGER) return null;
        // Recoverable hashtag mistakes may be kept, but unbounded or control-character storage is rejected.
        for (const [key, max] of [['title', 120], ['description', 5000], ['hashtags', 5000]]) if (typeof value.metadata[key] !== 'string' || value.metadata[key].length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value.metadata[key])) return null;
        const pending = validatedPending(value.pending, value.job_id);
        if (value.pending && !pending) return null;
        return {
            job_id: value.job_id, metadata: Object.fromEntries(['title', 'description', 'hashtags'].map(key => [key, value.metadata[key]])), visibility: value.visibility, schedule_mode: value.schedule_mode, scheduled_local: value.scheduled_local,
            upload_mode: value.upload_mode || 'assisted', debug_mode: Boolean(value.debug_mode),
            profile_ids: [...new Set(value.profile_ids)], source_confirmed: value.source_confirmed, dirty: value.dirty, revision: value.revision, pending
        };
    }
    function readDrafts() {
        try {
            const raw = localStorage.getItem(draftStorageKey);
            if (typeof raw !== 'string' || byteLength(raw) > maxDraftBytes) return;
            const data = JSON.parse(raw);
            if (data?.version !== 1 || !Array.isArray(data.projects) || data.projects.length > maxStoredDrafts) return;
            for (const value of data.projects) { const draft = validatedDraft(value); if (draft) storedDrafts.set(draft.job_id, draft); }
        } catch (_) { /* Disabled, full, future-version or corrupt storage is nonfatal. */ }
    }
    function persist(record) {
        if (record.kind !== 'project') return;
        const draft = validatedDraft({
            job_id: record.id, metadata: record.metadata, visibility: record.visibility, schedule_mode: record.scheduleMode,
            scheduled_local: record.scheduledLocal, upload_mode: record.uploadMode, debug_mode: record.debugMode,
            profile_ids: [...record.selected], source_confirmed: record.sourceConfirmed, dirty: record.dirty, revision: record.revision, pending: record.pending?.body || null
        });
        storedDrafts.delete(record.id); if (draft) storedDrafts.set(record.id, draft);
        const evictOldest = () => { const id = storedDrafts.keys().next().value; storedDrafts.delete(id); const previous = records.get('project:' + id); if (previous) previous.persisted = false; };
        while (storedDrafts.size > maxStoredDrafts) evictOldest();
        try {
            let raw = JSON.stringify({ version: 1, projects: [...storedDrafts.values()] });
            while (byteLength(raw) > maxDraftBytes && storedDrafts.size > 1) { evictOldest(); raw = JSON.stringify({ version: 1, projects: [...storedDrafts.values()] }); }
            if (byteLength(raw) > maxDraftBytes) throw new Error('Draft limit');
            localStorage.setItem(draftStorageKey, raw); record.persisted = Boolean(draft);
        } catch (_) { record.persisted = false; }
    }
    function newRecord(kind, id, metadata) {
        const record = {
            kind, id, metadata: normalizeMetadata(metadata), selected: new Set(), visibility: 'draft', scheduleMode: 'now', scheduledLocal: '',
            uploadMode: 'assisted', debugMode: false,
            sourceConfirmed: false, revision: 0, dirty: false, persisted: true, busy: '', loading: false, loaded: false, loadSequence: 0, loadFailed: false,
            snapshot: null, videoUrl: '', asset: '', aiTask: null, aiRevision: 0, pending: null, uncertain: false
        };
        const draft = kind === 'project' && storedDrafts.get(id);
        if (draft) Object.assign(record, {
            metadata: { ...draft.metadata }, selected: new Set(draft.profile_ids), visibility: draft.visibility, scheduleMode: draft.schedule_mode,
            scheduledLocal: draft.scheduled_local, uploadMode: draft.upload_mode || 'assisted', debugMode: Boolean(draft.debug_mode),
            sourceConfirmed: draft.source_confirmed, revision: draft.revision, dirty: draft.dirty, restored: true,
            pending: draft.pending ? { body: draft.pending, revision: draft.revision } : null, uncertain: Boolean(draft.pending)
        });
        return record;
    }
    async function request(url, options = {}) {
        const { timeoutMs = 20000, ...rest } = options, method = (rest.method || 'GET').toUpperCase(), headers = new Headers(rest.headers || {});
        headers.set('X-VietSub-Token', document.querySelector('meta[name="api-token"]')?.content || '');
        if (method !== 'GET' && !(rest.body instanceof FormData)) headers.set('Content-Type', 'application/json');
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, { ...rest, method, headers, credentials: 'same-origin', signal: controller.signal });
            let data; try { data = await response.json(); } catch (_) { throw new Error('Publish Hub nhận phản hồi không phải JSON.'); }
            if (!response.ok) { const error = new Error(typeof data?.error === 'string' ? data.error : `Publish Hub: HTTP ${response.status}`); error.status = response.status; throw error; }
            if (!object(data)) throw new Error('Publish Hub nhận dữ liệu không hợp lệ.');
            return data;
        } catch (error) {
            const failure = error.name === 'AbortError' ? new Error('App không phản hồi đúng hạn; kiểm tra lại trước khi gửi thêm tác vụ.') : error instanceof TypeError ? new Error('Không kết nối được Publish Hub trên máy này.') : error;
            if (method !== 'GET' && (!failure.status || failure.status >= 500)) failure.uncertainMutation = true;
            throw failure;
        } finally { clearTimeout(timer); }
    }
    function notify(message, state = '') { $('uploader-status').textContent = message; $('uploader-status').dataset.state = state; }
    function node(tag, text = '', className = '') { const el = document.createElement(tag); el.textContent = text; if (className) el.className = className; return el; }
    function button(text, action, disabled = false, className = 'btn-ghost') { const el = node('button', text, className); el.type = 'button'; el.disabled = disabled; el.addEventListener('click', action); return el; }
    function touched(record, metadata = false) {
        record.revision++; record.dirty = record.dirty || metadata; record.pending = null; record.uncertain = false;
        $('uploader-confirm-public').checked = false; persist(record); refreshControls();
    }
    function currentReady() { return current && current.loaded && !current.loading && !current.loadFailed && Boolean(current.videoUrl); }
    function profileLocked(id) { return profileBusy.has(id) || [...sessions.values()].some(task => task.profile_id === id && activeSessions.has(task.status)) || jobs.some(task => task.profile_id === id && activeQueue.has(task.status)) || [...records.values()].some(record => record.pending?.body.profile_ids.includes(id)); }
    function platform(key) { return capabilities?.platforms.find(item => item.key === key); }
    function validation(record, confirmation = true) {
        const issues = [], checked = validateMetadata(record?.metadata);
        if (!record || !currentReady()) issues.push('Chọn video khả dụng và kiểm tra lại project nếu có lỗi.');
        const selectedProfiles = record ? [...record.selected].map(id => profiles.find(item => item.id === id)) : [];
        const allSelectedYoutubeApi = selectedProfiles.length > 0 && selectedProfiles.every(p => isYoutubeApiProfile(p) && p?.youtube_api?.connected === true);
        if (!capabilities?.browser_available && !allSelectedYoutubeApi) {
            issues.push('Trình duyệt tự động hoá chưa khả dụng.');
        }
        if (profilesError) issues.push('Đọc lại danh sách tài khoản trước khi đăng.');
        if (Object.keys(checked.errors).length) issues.push(...Object.values(checked.errors));
        if (record) {
            if (record.aiTask && ['queued', 'running'].includes(record.aiTask.status)) issues.push('Chờ metadata AI hoàn tất trước khi duyệt và xếp hàng đăng.');
            if (record.selected.size > 30) issues.push('Mỗi yêu cầu chỉ hỗ trợ tối đa 30 tài khoản.');
            if (!record.selected.size) issues.push('Chọn ít nhất một tài khoản.');
            for (const id of record.selected) {
                const profile = profiles.find(item => item.id === id);
                if (!profile || profile.session_status !== 'ready' || profileLocked(id)) issues.push(`Tài khoản ${profile?.name || id} chưa sẵn sàng hoặc đang bận.`);
                if (profile && isYoutubeApiProfile(profile) && profile.youtube_api?.connected !== true) issues.push(`Tài khoản ${profile.name} chưa kết nối YouTube Data API v3 (OAuth2).`);
                if (profile && !platform(profile.platform)?.modes.includes(record.visibility)) issues.push(`${platformLabels[profile.platform]} không hỗ trợ ${modeLabels[record.visibility]}. Chọn chế độ chung hoặc bỏ tài khoản đó.`);
            }
            if (record.kind === 'project' && record.asset === 'source' && !record.sourceConfirmed) issues.push('Xác nhận chủ động dùng video nguồn chưa Auto Edit.');
            const schedule = scheduleValue(record.scheduleMode, record.scheduledLocal); if (schedule.error) issues.push(schedule.error);
            if (confirmation && record.visibility === 'public' && !$('uploader-confirm-public').checked) issues.push('Cần xác nhận rõ trước khi đăng công khai.');
        }
        return { issues, metadata: checked.metadata, scheduled_at: record ? scheduleValue(record.scheduleMode, record.scheduledLocal).scheduled_at : null };
    }
    function paintMetadata() {
        if (!current) return;
        for (const key of ['title', 'description', 'hashtags']) {
            $(`uploader-${key}-input`).value = current.metadata[key];
            $(`uploader-${key}`).textContent = current.metadata[key] || 'Chưa có';
        }
        $('uploader-visibility').value = current.visibility; $('uploader-schedule-mode').value = current.scheduleMode;
        $('uploader-upload-mode').value = current.uploadMode || 'assisted';
        $('uploader-debug-mode').checked = Boolean(current.debugMode);
        $('uploader-scheduled-at').value = current.scheduledLocal; $('uploader-source-ack').checked = current.sourceConfirmed;
    }
    function refreshControls() {
        if (!initialized) return;
        const ready = currentReady(), busy = Boolean(current?.busy), pending = Boolean(current?.uncertain);
        $('uploader-content-fields').disabled = !ready || busy || pending;
        $('uploader-save-metadata').disabled = !ready || busy || pending || current?.kind !== 'project';
        $('uploader-generate-metadata').disabled = !ready || busy || pending || current?.kind !== 'project' || current?.snapshot?.availability?.translated_srt !== true || Boolean(current?.aiTask && ['queued', 'running'].includes(current.aiTask.status));
        $('uploader-generate-metadata').title = current?.kind === 'media' ? 'MP4 riêng không có SRT; nhập metadata thủ công.' : 'Dùng provider dịch đã cấu hình để tạo metadata từ SRT; chỉ chạy khi bấm.';
        $('uploader-scheduled-at').disabled = !ready || busy || pending || current?.scheduleMode !== 'local';
        $('uploader-public-confirm-row').hidden = current?.visibility !== 'public';
        $('uploader-source-ack-row').hidden = current?.kind !== 'project' || current?.asset !== 'source';
        $('uploader-confirm-public').disabled = busy || pending;
        $('uploader-source-ack').disabled = busy || pending;
        $('uploader-media-upload').disabled = mediaBusy || !$('uploader-media-file').files?.length;
        $('uploader-media-file').disabled = mediaBusy;
        $('uploader-use-project').hidden = !lastProject || current?.kind === 'project';
        $('uploader-refresh-project').disabled = current?.kind !== 'project' || current.loading;
        $('uploader-profile-platform').disabled = !capabilities || createBusy;
        $('uploader-profile-create').disabled = !capabilities || createBusy;
        $('uploader-profiles-refresh').disabled = profilesLoading; $('uploader-queue-refresh').disabled = queueLoading;
        $('uploader-queue-submit').disabled = !ready || busy || (!pending && validation(current).issues.length > 0);
        $('uploader-queue-submit').textContent = pending ? 'Kiểm tra / gửi lại đúng request đã mất phản hồi' : current?.busy === 'queue' ? 'Đang gửi hàng đợi…' : 'Thêm vào hàng đợi đăng →';
        const blockers = current && !pending ? validation(current, false).issues : [];
        const modeSummary = uploadModeLabels[current?.uploadMode || 'assisted'] || 'Bán tự động';
        const debugSummary = current?.debugMode ? ' · Debug Trace' : '';
        $('uploader-submit-summary').textContent = current ? `${current.selected.size} tài khoản · ${modeLabels[current.visibility]} · ${modeSummary}${debugSummary} · ${current.scheduleMode === 'local' ? 'Hẹn giờ cục bộ Asia/Saigon' : 'Ngay khi tới lượt'}. ${blockers.length ? blockers.join(' ') : 'Sẵn sàng duyệt và chủ động xếp hàng đăng.'}` : 'Chưa chọn tài khoản. Không có thao tác đăng tự động.';
        $('uploader-draft-status').textContent = current ? current.dirty ? current.persisted ? 'Đã giữ nháp cục bộ' : 'Nháp chưa lưu được · đừng đóng app' : 'Nội dung đang duyệt' : 'Bản nháp';
        if (current) {
            const error = validateMetadata(current.metadata).errors;
            $('uploader-metadata-error').textContent = Object.values(error).join(' ');
            for (const key of ['title', 'description', 'hashtags']) $(`uploader-${key}-input`).setAttribute('aria-invalid', String(Boolean(error[key])));
            // Unsupported modes remain visible rather than silently changing the user's intended visibility.
            for (const option of $('uploader-visibility').options) option.disabled = [...current.selected].some(id => {
                const item = profiles.find(value => value.id === id); return item && !platform(item.platform)?.modes.includes(option.value);
            });
        }
    }
    function showVideo(record) {
        const player = $('uploader-video'), url = record?.videoUrl || '';
        if (player.dataset.projectUrl !== url) { player.pause(); player.dataset.projectUrl = url; if (url) player.src = url; else player.removeAttribute('src'); player.load(); }
        player.hidden = !url; $('uploader-video-placeholder').hidden = Boolean(url);
        $('uploader-empty').hidden = Boolean(record); $('uploader-project').hidden = !record;
        $('uploader-asset-badge').textContent = !record ? 'Chưa chọn' : record.kind === 'media' ? 'MP4 riêng' : record.asset === 'edited' ? 'Video đã Auto Edit' : 'Video nguồn';
        $('uploader-video-status').textContent = !url ? 'Không có URL video an toàn của đúng project/file. Không dùng đường dẫn manifest để phát.' : record.kind === 'media' ? 'MP4 đã tải vào app cục bộ; chưa đăng. Metadata AI không khả dụng vì không có SRT.' : record.asset === 'edited' ? 'Đang dùng video Auto Edit đã kế thừa; chưa đăng lên nền tảng.' : 'Đang xem video nguồn. Cần xác nhận chủ động dùng nguồn trước khi xếp hàng đăng.';
    }
    function paintCurrent() {
        showVideo(current); paintMetadata(); refreshControls(); paintProfiles();
        if (current?.aiTask) $('uploader-ai-status').textContent = `Metadata AI: ${statuses[current.aiTask.status] || current.aiTask.status}${current.aiTask.error ? ' · ' + current.aiTask.error : ''}`;
        else $('uploader-ai-status').textContent = current?.kind === 'media' ? 'MP4 riêng không có SRT. Nhập nội dung thủ công.' : '';
    }
    async function loadCapabilities() {
        const sequence = ++capabilitySequence;
        try {
            const data = await request('/api/uploader/capabilities');
            if (sequence !== capabilitySequence) return;
            if (typeof data.browser_available !== 'boolean' || !Array.isArray(data.platforms) || data.timezone !== 'Asia/Saigon' || data.scheduling !== 'local' ||
                data.platforms.length !== 6 || new Set(data.platforms.map(item => item.key)).size !== 6 || data.platforms.some(item => !Object.hasOwn(platformLabels, item.key) || !Array.isArray(item.modes) || item.modes.some(mode => !modes.includes(mode)))) throw new Error('Ma trận khả năng Publish Hub không hợp lệ.');
            capabilities = data;
            let runtimeNote = data.browser_available
                ? 'Trình duyệt cục bộ khả dụng · 6 nền tảng · profile tách biệt. Chưa mở đăng nhập hoặc gửi video lên nền tảng.'
                : 'Chưa có trình duyệt tự động hoá. Có thể soạn nội dung hoặc dùng YouTube Data API v3; đăng nhập trình duyệt bị khoá. Kiểm tra cấu hình trình duyệt trong Thiết lập.';
            if (data.selector_override?.override_active) runtimeNote += ' Đang áp dụng selectors.override.json cục bộ.';
            else if (typeof data.selector_override?.warning === 'string' && data.selector_override.warning) runtimeNote += ' ' + data.selector_override.warning;
            $('uploader-runtime-status').textContent = runtimeNote;
            $('uploader-runtime-status').dataset.state = data.browser_available ? '' : 'error';
            const rows = [];
            for (const [key, label] of Object.entries(platformLabels)) {
                const row = node('tr'); row.appendChild(node('th', label));
                for (const mode of modes) row.appendChild(node('td', platform(key)?.modes.includes(mode) ? 'Có' : 'Không'));
                rows.push(row);
            }
            $('uploader-capability-matrix').replaceChildren(...rows);
        } catch (error) { if (sequence === capabilitySequence) { capabilities = null; $('uploader-runtime-status').textContent = error.message; $('uploader-runtime-status').dataset.state = 'error'; } }
        refreshControls(); paintProfiles();
    }
    function validProfile(profile) { return object(profile) && uuid(profile.id) && Object.hasOwn(platformLabels, profile.platform) && typeof profile.name === 'string' && profile.name.length > 0 && profile.name.length <= 80 && Object.hasOwn(sessionLabels, profile.session_status); }
    async function loadProfiles() {
        const sequence = ++profileSequence; profilesLoading = true; refreshControls();
        try {
            const data = await request('/api/uploader/profiles'); if (sequence !== profileSequence) return;
            if (!Array.isArray(data.profiles) || !data.profiles.every(validProfile) || new Set(data.profiles.map(item => item.id)).size !== data.profiles.length) throw new Error('Danh sách tài khoản không hợp lệ.');
            profiles = data.profiles.map(item => ({ ...item, session_status: item.session_status === 'alive' ? 'ready' : item.session_status }));
            profilesLoaded = true;
            profilesError = '';
            const knownIds = new Set(profiles.map(item => item.id));
            for (const record of records.values()) {
                if (!record.pendingPresetProfiles) continue;
                record.pendingPresetProfiles = false;
                const filtered = [...record.selected].filter(id => knownIds.has(id));
                if (filtered.length !== record.selected.size) {
                    record.selected = new Set(filtered);
                    persist(record);
                }
            }
            $('uploader-profiles-status').textContent = `${profiles.length} profile cục bộ. Chỉ profile sẵn sàng mới được xếp hàng đăng.`;
        } catch (error) { if (sequence === profileSequence) { profilesError = error.message; $('uploader-profiles-status').textContent = error.message; } }
        finally { if (sequence === profileSequence) { profilesLoading = false; paintProfiles(); refreshControls(); } }
    }
    function getYtDraft(profileId) {
        let draft = ytDrafts.get(profileId);
        if (!draft) { draft = { client_id: '', client_secret: '', raw_json: '', open: null, message: '', version: 0 }; ytDrafts.set(profileId, draft); }
        return draft;
    }
    async function updateProfileMethod(profile, uploadMethod) {
        if (profileLocked(profile.id) || profile.upload_method === uploadMethod) return;
        profileBusy.add(profile.id); paintProfiles(); refreshControls();
        try {
            const updated = await request(`/api/uploader/profiles/${encodeURIComponent(profile.id)}/config`, { method: 'POST', body: JSON.stringify({ upload_method: uploadMethod }) });
            if (!validProfile(updated)) throw new Error('App chưa xác nhận cấu hình phương thức đăng.');
            if (uploadMethod === 'youtube_api') {
                const draft = getYtDraft(profile.id);
                draft.open = true;
                draft.version = (draft.version || 0) + 1;
            }
            await loadProfiles();
            notify(uploadMethod === 'youtube_api'
                ? 'Đã chuyển tài khoản YouTube sang YouTube Data API v3 (OAuth2). Hãy lưu Client ID/Secret và kết nối OAuth2.'
                : 'Đã chuyển tài khoản YouTube về phương thức Trình duyệt (Playwright).');
        } catch (error) { notify(error.message, 'error'); }
        finally { profileBusy.delete(profile.id); paintProfiles(); refreshControls(); }
    }
    async function saveYoutubeOAuthConfig(profile) {
        if (profileLocked(profile.id)) return;
        const draft = getYtDraft(profile.id);
        let payload;
        if (draft.raw_json.trim() && (!draft.client_id.trim() || !draft.client_secret.trim())) {
            const parsed = parseYoutubeClientSecretJson(draft.raw_json);
            if (parsed.error) { draft.message = parsed.error; draft.version = (draft.version || 0) + 1; paintProfiles(); return; }
            payload = { client_secret_json: draft.raw_json };
        } else {
            payload = { client_id: draft.client_id.trim(), client_secret: draft.client_secret.trim() };
            if (!payload.client_id || !payload.client_secret) {
                draft.message = 'Nhập Client ID và Client Secret, hoặc dán client_secret.json (Desktop app) rồi bấm Nạp từ JSON.';
                draft.version = (draft.version || 0) + 1;
                paintProfiles();
                return;
            }
        }
        profileBusy.add(profile.id); paintProfiles(); refreshControls();
        try {
            const updated = await request(`/api/uploader/profiles/${encodeURIComponent(profile.id)}/youtube-oauth/config`, { method: 'POST', body: JSON.stringify(payload) });
            if (!validProfile(updated)) throw new Error('App chưa xác nhận cấu hình YouTube OAuth2.');
            draft.client_id = ''; draft.client_secret = ''; draft.raw_json = ''; draft.message = 'Đã mã hoá và lưu OAuth Client cục bộ (DPAPI). Bấm “Kết nối Google OAuth2” để cấp quyền.';
            draft.open = true; draft.version = (draft.version || 0) + 1;
            await loadProfiles();
            notify('Đã lưu OAuth Client YouTube (mã hoá DPAPI). Chưa đăng video.');
        } catch (error) { draft.message = error.message; draft.version = (draft.version || 0) + 1; notify(error.message, 'error'); }
        finally { profileBusy.delete(profile.id); paintProfiles(); refreshControls(); }
    }
    async function startYoutubeOAuth(profile) {
        if (profileLocked(profile.id)) return;
        profileBusy.add(profile.id); paintProfiles(); refreshControls();
        try {
            const data = await request(`/api/uploader/profiles/${encodeURIComponent(profile.id)}/youtube-oauth/start`, { method: 'POST', body: '{}' });
            const task = data.task || data;
            if (!validSession(task, profile.id)) throw new Error('Phiên kết nối YouTube OAuth2 không hợp lệ.');
            sessions.forEach((old, id) => { if (old.profile_id === profile.id) sessions.delete(id); });
            sessions.set(task.id, task);
            notify('Đang mở trình duyệt để bạn cấp quyền YouTube Data API v3 (OAuth2 loopback cục bộ, tối đa 5 phút).');
            schedulePoll();
        } catch (error) { notify(error.message, 'error'); }
        finally { profileBusy.delete(profile.id); paintProfiles(); refreshControls(); }
    }
    async function disconnectYoutubeOAuth(profile) {
        if (profileLocked(profile.id)) return;
        if (!window.confirm(`Ngắt kết nối YouTube Data API v3 của “${profile.name}” và xoá token OAuth2 cục bộ?`)) return;
        profileBusy.add(profile.id); paintProfiles(); refreshControls();
        try {
            const updated = await request(`/api/uploader/profiles/${encodeURIComponent(profile.id)}/youtube-oauth/disconnect`, { method: 'POST', body: JSON.stringify({ keep_client: false }) });
            if (!validProfile(updated)) throw new Error('App chưa xác nhận ngắt kết nối OAuth2.');
            if (current?.selected.delete(profile.id)) touched(current);
            await loadProfiles();
            notify('Đã ngắt kết nối và xoá token YouTube OAuth2 cục bộ.');
        } catch (error) { notify(error.message, 'error'); }
        finally { profileBusy.delete(profile.id); paintProfiles(); refreshControls(); }
    }
    function buildYoutubeApiPanel(profile, locked) {
        const draft = getYtDraft(profile.id);
        const yt = object(profile.youtube_api) ? profile.youtube_api : { configured: false, connected: false, channel_title: '' };
        const details = node('details', '', 'uploader-yt-api-panel');
        details.open = draft.open === null ? isYoutubeApiProfile(profile) : Boolean(draft.open);
        details.addEventListener('toggle', () => { draft.open = details.open; });
        details.appendChild(node('summary', 'YouTube Data API v3 (tùy chọn · ổn định hơn trình duyệt)'));

        const methodRow = node('div', '', 'uploader-yt-method-row');
        methodRow.appendChild(node('span', 'Phương thức đăng:', 'field-label'));
        const select = node('select', '', 'select-input');
        select.disabled = locked;
        select.dataset.uploaderFocus = `yt-method-${profile.id}`;
        select.setAttribute('aria-label', `Phương thức đăng cho ${profile.name}`);
        for (const [val, label] of [['browser', 'Trình duyệt (Playwright)'], ['youtube_api', 'YouTube Data API v3 (OAuth2)']]) {
            const opt = node('option', label); opt.value = val; opt.selected = (profile.upload_method || 'browser') === val; select.appendChild(opt);
        }
        select.value = profile.upload_method || 'browser';
        select.addEventListener('change', () => updateProfileMethod(profile, select.value));
        methodRow.appendChild(select);
        details.appendChild(methodRow);

        const statusText = yt.connected
            ? `OAuth2: Đã kết nối${yt.channel_title ? ` · Kênh “${yt.channel_title}”` : ''}.`
            : yt.configured ? 'OAuth2: Đã lưu Client ID (DPAPI) · Chưa cấp quyền Google.' : 'OAuth2: Chưa cấu hình Client ID / Secret.';
        details.appendChild(node('p', statusText, 'uploader-yt-status'));
        details.appendChild(node('p', 'Dùng OAuth Client (Desktop app) cá nhân trên Google Cloud. Hạn mức mặc định ~10.000 units/ngày (≈ 6 video/ngày). App chưa xác minh (Unverified) sẽ bị Google tự đặt video ở chế độ Riêng tư.', 'field-help'));

        const jsonInput = node('textarea', '', 'uploader-yt-json-input');
        jsonInput.rows = 2;
        jsonInput.placeholder = 'Dán nhanh nội dung client_secret.json (loại Desktop app / installed)…';
        jsonInput.value = draft.raw_json;
        jsonInput.disabled = locked;
        jsonInput.dataset.uploaderFocus = `yt-json-${profile.id}`;
        jsonInput.setAttribute('aria-label', `Nội dung client_secret.json cho ${profile.name}`);
        jsonInput.addEventListener('input', () => { draft.raw_json = jsonInput.value; });
        details.appendChild(jsonInput);

        const idInput = node('input', '', 'text-input');
        idInput.type = 'text'; idInput.autocomplete = 'off'; idInput.placeholder = 'Client ID (xxx.apps.googleusercontent.com)';
        idInput.value = draft.client_id; idInput.disabled = locked;
        idInput.dataset.uploaderFocus = `yt-cid-${profile.id}`;
        idInput.setAttribute('aria-label', `YouTube OAuth Client ID cho ${profile.name}`);
        idInput.addEventListener('input', () => { draft.client_id = idInput.value; });
        details.appendChild(idInput);

        const secretInput = node('input', '', 'text-input');
        secretInput.type = 'password'; secretInput.autocomplete = 'off'; secretInput.placeholder = 'Client Secret (lưu mã hoá DPAPI)';
        secretInput.value = draft.client_secret; secretInput.disabled = locked;
        secretInput.dataset.uploaderFocus = `yt-csec-${profile.id}`;
        secretInput.setAttribute('aria-label', `YouTube OAuth Client Secret cho ${profile.name}`);
        secretInput.addEventListener('input', () => { draft.client_secret = secretInput.value; });
        details.appendChild(secretInput);

        if (draft.message) details.appendChild(node('p', draft.message, 'field-help'));

        const bar = node('div', '', 'uploader-profile-actions');
        const loadJsonBtn = button('Nạp từ JSON', () => {
            const parsed = parseYoutubeClientSecretJson(draft.raw_json);
            if (parsed.error) { draft.message = parsed.error; draft.version = (draft.version || 0) + 1; paintProfiles(); return; }
            draft.client_id = parsed.client_id; draft.client_secret = parsed.client_secret;
            draft.message = 'Đã trích xuất Client ID & Client Secret từ JSON. Bấm “Lưu OAuth Client” để lưu mã hoá DPAPI.';
            draft.open = true; draft.version = (draft.version || 0) + 1; paintProfiles();
        }, locked, 'btn-ghost');
        loadJsonBtn.dataset.uploaderFocus = `yt-parse-${profile.id}`;
        bar.appendChild(loadJsonBtn);

        const saveBtn = button('Lưu OAuth Client', () => saveYoutubeOAuthConfig(profile), locked, 'btn-secondary');
        saveBtn.dataset.uploaderFocus = `yt-save-${profile.id}`;
        bar.appendChild(saveBtn);

        const connectBtn = button('Kết nối Google OAuth2', () => startYoutubeOAuth(profile), locked || !yt.configured, 'btn-secondary');
        connectBtn.dataset.uploaderFocus = `yt-connect-${profile.id}`;
        bar.appendChild(connectBtn);

        if (yt.configured || yt.connected) {
            const disconnectBtn = button('Ngắt OAuth2', () => disconnectYoutubeOAuth(profile), locked, 'btn-danger');
            disconnectBtn.dataset.uploaderFocus = `yt-disconnect-${profile.id}`;
            bar.appendChild(disconnectBtn);
        }
        details.appendChild(bar);
        return details;
    }
    function profileCardSignature(profile) {
        const ytApi = isYoutubeApiProfile(profile);
        const locked = profileLocked(profile.id);
        const selected = Boolean(current?.selected.has(profile.id));
        const selectable = profile.session_status === 'ready' && (!ytApi || profile.youtube_api?.connected === true) && !locked;
        const checkboxDisabled = !current || Boolean(current.busy) || current.uncertain || (!selected && !selectable);
        const task = [...sessions.values()].find(value => value.profile_id === profile.id);
        const draft = profile.platform === 'youtube' ? ytDrafts.get(profile.id) : null;
        return JSON.stringify([
            profile.id, profile.platform, profile.name, profile.session_status, profile.detail || '',
            profile.upload_method || 'browser', profile.youtube_api || null,
            Boolean(capabilities?.browser_available), locked, profileBusy.has(profile.id),
            selected, checkboxDisabled,
            task ? [task.id, task.action, task.status, task.error || ''] : null,
            draft ? [draft.message || '', draft.version || 0] : null,
        ]);
    }
    function buildProfileCard(profile) {
        const row = node('article', '', 'uploader-profile-card'); row.dataset.selected = String(Boolean(current?.selected.has(profile.id)));
        row.dataset.profileId = profile.id;
        const label = node('label', '', 'uploader-check'), input = node('input'), description = node('span');
        input.type = 'checkbox'; input.checked = Boolean(current?.selected.has(profile.id)); input.dataset.uploaderFocus = `select-${profile.id}`;
        const ytApi = isYoutubeApiProfile(profile);
        const selectable = profile.session_status === 'ready' && (!ytApi || profile.youtube_api?.connected === true) && !profileLocked(profile.id);
        input.disabled = !current || Boolean(current.busy) || current.uncertain || (!input.checked && !selectable);
        input.addEventListener('change', () => { if (!current || input.disabled || current.busy || current.uncertain) return; input.checked ? current.selected.add(profile.id) : current.selected.delete(profile.id); touched(current); paintProfiles(); });
        description.appendChild(node('strong', profile.name));
        const methodTag = ytApi ? ' · API v3' : '';
        description.appendChild(node('span', `${platformLabels[profile.platform]}${methodTag} · ${sessionLabels[profile.session_status]}`));
        label.appendChild(input); label.appendChild(description); row.appendChild(label);
        if (typeof profile.detail === 'string' && profile.detail) row.appendChild(node('p', profile.detail, 'uploader-profile-detail'));
        const actions = node('div', '', 'uploader-profile-actions'), locked = profileLocked(profile.id);
        for (const [action, text] of [['login', 'Đăng nhập thủ công'], ['check', 'Kiểm tra phiên'], ['delete', 'Xoá profile']]) {
            const disabled = locked || (action === 'login' && !capabilities?.browser_available) || (action === 'check' && !ytApi && !capabilities?.browser_available);
            const el = button(text, () => profileAction(profile, action), disabled, action === 'delete' ? 'btn-danger' : 'btn-ghost');
            el.dataset.uploaderFocus = `${action}-${profile.id}`; actions.appendChild(el);
        }
        row.appendChild(actions);
        if (profile.platform === 'youtube') {
            row.appendChild(buildYoutubeApiPanel(profile, locked));
        }
        const task = [...sessions.values()].find(value => value.profile_id === profile.id);
        if (task) {
            const panel = node('div', '', 'uploader-session');
            const heading = task.action === 'youtube_oauth'
                ? 'Kết nối Google OAuth2 (loopback cục bộ)'
                : task.action === 'login'
                    ? 'Đăng nhập thủ công trong cửa sổ riêng của app'
                    : 'Kiểm tra phiên cục bộ';
            panel.appendChild(node('strong', heading));
            panel.appendChild(node('p', `${task.id} · ${statuses[task.status] || task.status}${task.error ? ' · ' + task.error : ''}`, 'uploader-session-detail'));
            if (activeSessions.has(task.status)) {
                const bar = node('div', '', 'uploader-profile-actions');
                if (task.action === 'login') bar.appendChild(button('Tôi đã đăng nhập xong · kiểm tra', () => sessionAction(task, 'finish'), profileBusy.has(profile.id), 'btn-secondary'));
                bar.appendChild(button('Huỷ phiên thao tác', () => sessionAction(task, 'cancel'), profileBusy.has(profile.id), 'btn-danger')); panel.appendChild(bar);
            }
            row.appendChild(panel);
        }
        return row;
    }
    function paintProfiles() {
        if (!initialized) return;
        const list = $('uploader-profile-list');
        if (!profiles.length) {
            const emptySig = profilesLoading ? 'loading' : 'empty';
            if (paintedProfilesEmpty === emptySig && list.children.length === 1) return;
            profileCardCache.clear();
            paintedProfilesEmpty = emptySig;
            list.replaceChildren(node('p', profilesLoading ? 'Đang đọc profile…' : 'Chưa có profile. Chọn nền tảng và đặt tên để tạo; app không tự mở đăng nhập.', 'field-help'));
            return;
        }
        paintedProfilesEmpty = null;
        const nextEntries = [];
        let allUnchanged = list.children.length === profiles.length;
        for (let i = 0; i < profiles.length; i++) {
            const profile = profiles[i];
            const sig = profileCardSignature(profile);
            const cached = profileCardCache.get(profile.id);
            const reused = Boolean(cached && cached.sig === sig);
            if (!reused || list.children[i] !== cached.node) allUnchanged = false;
            nextEntries.push({ profile, sig, cached, reused });
        }
        if (allUnchanged) return;

        const activeEl = document.activeElement;
        const focused = activeEl?.dataset?.uploaderFocus;
        const selStart = typeof activeEl?.selectionStart === 'number' ? activeEl.selectionStart : null;
        const selEnd = typeof activeEl?.selectionEnd === 'number' ? activeEl.selectionEnd : null;

        const nextIds = new Set(profiles.map(p => p.id));
        const keptNodes = new Set();
        const replacedOldToNew = new Map();
        const rows = [];
        for (const entry of nextEntries) {
            let cardNode;
            if (entry.reused) {
                cardNode = entry.cached.node;
                keptNodes.add(cardNode);
            } else {
                cardNode = buildProfileCard(entry.profile);
                if (entry.cached?.node) replacedOldToNew.set(entry.cached.node, cardNode);
            }
            profileCardCache.set(entry.profile.id, { sig: entry.sig, node: cardNode });
            rows.push(cardNode);
        }
        for (const id of [...profileCardCache.keys()]) {
            if (!nextIds.has(id)) profileCardCache.delete(id);
        }

        if (typeof list.insertBefore === 'function' && typeof list.replaceChild === 'function' && typeof list.removeChild === 'function') {
            for (const child of [...list.children]) {
                if (!keptNodes.has(child) && !replacedOldToNew.has(child)) list.removeChild(child);
            }
            for (let i = 0; i < rows.length; i++) {
                const desired = rows[i], existing = list.children[i];
                if (existing === desired) continue;
                if (existing && replacedOldToNew.get(existing) === desired) list.replaceChild(desired, existing);
                else if (existing) list.insertBefore(desired, existing);
                else list.appendChild(desired);
            }
            while (list.children.length > rows.length) list.removeChild(list.lastChild);
        } else {
            list.replaceChildren(...rows);
        }

        // Keep keyboard focus stable across status polls without stealing it from another workspace or interrupting unchanged inputs.
        if (focused && (!document.activeElement || !list.contains(document.activeElement) || document.activeElement.dataset?.uploaderFocus !== focused)) {
            list.querySelectorAll('input, textarea, button, select').forEach(el => {
                if (el.dataset.uploaderFocus === focused && !el.disabled) {
                    el.focus({ preventScroll: true });
                    if (selStart !== null && selEnd !== null && typeof el.setSelectionRange === 'function') {
                        try { el.setSelectionRange(selStart, selEnd); } catch (_) {}
                    }
                }
            });
        }
    }
    function validSession(task, profileId) { return object(task) && validId(task.id) && task.profile_id === profileId && ['login', 'check', 'probe', 'youtube_oauth'].includes(task.action) && sessionStatuses.has(task.status); }
    async function createProfile(event) {
        event?.preventDefault(); if (createBusy || !capabilities) return;
        const key = $('uploader-profile-platform').value, name = $('uploader-profile-name').value.trim();
        if (!platform(key) || !name || name.length > 80 || /[\u0000-\u001f\u007f]/.test(name)) { $('uploader-profiles-status').textContent = 'Chọn nền tảng và tên từ 1–80 ký tự, không có ký tự điều khiển.'; return; }
        createBusy = true; refreshControls();
        try {
            const data = await request('/api/uploader/profiles', { method: 'POST', body: JSON.stringify({ platform: key, name }) }), profile = data.profile || data;
            if (!validProfile(profile)) throw new Error('App chưa xác nhận profile hợp lệ; đọc lại danh sách trước khi thử thêm.');
            $('uploader-profile-name').value = ''; await loadProfiles(); notify('Đã tạo record profile cục bộ. Chưa đăng nhập hoặc đăng video.');
        } catch (error) { notify(error.message + (error.uncertainMutation ? ' Đọc lại danh sách trước khi tạo thêm để tránh trùng profile.' : ''), 'error'); }
        finally { createBusy = false; refreshControls(); }
    }
    async function profileAction(profile, action) {
        const ytApi = isYoutubeApiProfile(profile);
        if (profileLocked(profile.id) || (action === 'login' && !capabilities?.browser_available) || (action === 'check' && !ytApi && !capabilities?.browser_available)) return;
        if (action === 'delete' && !window.confirm(`Xoá record profile “${profile.name}”? Chỉ xoá record cục bộ, không xoá tài khoản hoặc bài đăng trên nền tảng.`)) return;
        profileBusy.add(profile.id); paintProfiles(); refreshControls();
        try {
            const data = await request(`/api/uploader/profiles/${encodeURIComponent(profile.id)}${action === 'delete' ? '' : '/' + action}`, { method: action === 'delete' ? 'DELETE' : 'POST', ...(action === 'delete' ? {} : { body: '{}' }) });
            if (action === 'delete') {
                if (data.ok !== true) throw new Error('App chưa xác nhận xoá record; đọc lại danh sách trước khi thử tiếp.');
                profiles = profiles.filter(item => item.id !== profile.id); ytDrafts.delete(profile.id);
                sessions.forEach((task, id) => { if (task.profile_id === profile.id) sessions.delete(id); });
                if (current?.selected.delete(profile.id)) touched(current); await loadProfiles(); notify('Đã xoá record profile theo phản hồi app.');
            } else {
                const task = data.task || data;
                if (!validSession(task, profile.id)) throw new Error('Phiên thao tác không hợp lệ; đọc lại trạng thái trước khi thao tác tiếp.');
                // A profile has only one active session. A new explicit action replaces its old terminal task.
                sessions.forEach((old, id) => { if (old.profile_id === profile.id) sessions.delete(id); }); sessions.set(task.id, task);
                notify(action === 'login' ? 'Đăng nhập trong cửa sổ trình duyệt riêng. Khi xong, bấm “Tôi đã đăng nhập xong”; không tự giải CAPTCHA.' : 'Đã gửi yêu cầu kiểm tra phiên. Chưa đăng video.'); schedulePoll();
            }
        } catch (error) { notify(error.message, 'error'); }
        finally { profileBusy.delete(profile.id); paintProfiles(); refreshControls(); }
    }
    async function sessionAction(task, action) {
        if (profileBusy.has(task.profile_id) || !activeSessions.has(task.status)) return;
        profileBusy.add(task.profile_id); paintProfiles();
        try {
            const data = await request(`/api/uploader/sessions/${encodeURIComponent(task.id)}/${action}`, { method: 'POST', body: '{}' }), next = data.task || data;
            if (!validSession(next, task.profile_id) || next.id !== task.id) throw new Error('Trạng thái phiên không khớp yêu cầu.');
            sessions.set(next.id, next); await loadProfiles(); schedulePoll(); notify(action === 'finish' ? 'Đã yêu cầu kiểm tra đăng nhập; chỉ dùng tài khoản khi app báo sẵn sàng.' : 'Đã gửi yêu cầu huỷ phiên thao tác.');
        } catch (error) { notify(error.message, 'error'); }
        finally { profileBusy.delete(task.profile_id); paintProfiles(); refreshControls(); }
    }
    async function loadProject(record) {
        const sequence = ++record.loadSequence, revision = record.revision; record.loading = true;
        if (current === record) { refreshControls(); notify('Đang đọc video và metadata Publish Hub của đúng project…'); }
        try {
            const data = await request(`/api/uploader/projects/${encodeURIComponent(record.id)}`);
            if (sequence !== record.loadSequence) return;
            if (!validSnapshot(data.snapshot) || data.snapshot.job_id !== record.id || !['edited', 'source'].includes(data.asset) || !object(data.metadata)) throw new Error('Publish Hub trả sai project hoặc thiếu thông tin video.');
            const url = data.snapshot.availability?.video === true ? safeProjectUrl(data.video_url, record.id) : '';
            if (!url) throw new Error('Video không khả dụng hoặc URL không thuộc đúng job / API được phép.');
            const signedAsset = new URL(url, window.location.href).searchParams.get('asset');
            if (signedAsset && signedAsset !== data.asset) throw new Error('Asset video không khớp URL đã ký.');
            if (record.asset === 'edited' && data.asset === 'source') record.sourceConfirmed = false;
            record.snapshot = data.snapshot; record.videoUrl = url; record.asset = data.asset; record.loaded = true; record.loadFailed = false;
            if (record.revision === revision && !record.dirty && !record.restored) record.metadata = normalizeMetadata(data.metadata);
            if (current === record) { showVideo(record); paintMetadata(); notify(record.dirty ? 'Đã đọc lại file. Các trường bạn đã chỉnh vẫn được giữ.' : 'Đã đọc project Publish Hub. Chưa gửi yêu cầu đăng.'); }
        } catch (error) {
            if (sequence !== record.loadSequence) return;
            record.loadFailed = true;
            if (current === record) { showVideo(record); notify(error.message + ' Bản nháp nội dung vẫn được giữ; dùng Kiểm tra lại file.', 'error'); }
        } finally { if (sequence === record.loadSequence) { record.loading = false; if (current === record) refreshControls(); } }
    }
    function projectReceived(snapshot) {
        if (!validSnapshot(snapshot)) return;
        if (!initialized) { pendingSnapshot = snapshot; return; }
        lastProject = snapshot;
        let record = records.get('project:' + snapshot.job_id);
        if (!record) { record = newRecord('project', snapshot.job_id, snapshot.manifest.metadata); records.set('project:' + snapshot.job_id, record); }
        record.snapshot = snapshot;
        // The hook is a sink: never call Workbench.projectReceived here (that would recurse).
        current = record; navigation++; $('uploader-confirm-public').checked = false;
        if (!record.loaded) { record.asset = snapshot.manifest.assets?.edited_video ? 'edited' : 'source'; record.videoUrl = snapshot.availability?.video === true ? safeProjectUrl(snapshot.video_url, snapshot.job_id) : ''; }
        paintCurrent(); loadProject(record);
    }
    async function saveMetadata() {
        const record = current; if (!record || !currentReady() || record.kind !== 'project' || record.busy || record.uncertain) return;
        const checked = validateMetadata(record.metadata);
        if (Object.keys(checked.errors).length) { refreshControls(); $('uploader-metadata-error').focus(); return; }
        record.busy = 'save'; const revision = record.revision; refreshControls();
        try {
            const data = await request(`/api/uploader/projects/${encodeURIComponent(record.id)}`, { method: 'POST', body: JSON.stringify({ metadata: checked.metadata }) });
            if (!validSnapshot(data.snapshot) || data.snapshot.job_id !== record.id || !object(data.metadata)) throw new Error('App chưa xác nhận metadata của đúng project.');
            if (record.revision === revision) { record.dirty = false; record.restored = false; persist(record); }
            if (current === record) { refreshControls(); notify('App đã lưu metadata project. Chưa đăng video.'); }
        } catch (error) { if (current === record) notify(error.message + ' Nội dung chỉnh sửa vẫn được giữ.', 'error'); }
        finally { record.busy = ''; if (current === record) refreshControls(); }
    }
    function validMetadataTask(task) { return object(task) && validId(task.id) && ['queued', 'running', 'success', 'error', 'cancelled'].includes(task.status); }
    async function generateMetadata() {
        const record = current;
        if (!record || $('uploader-generate-metadata').disabled || record.kind !== 'project' || record.uncertain) return;
        if ((record.metadata.title || record.metadata.description || record.metadata.hashtags) && !window.confirm('Tạo metadata AI từ SRT của project? Kết quả chỉ thay nội dung khi bạn chưa chỉnh thêm trong lúc chờ. Không đăng video.')) return;
        record.busy = 'metadata'; record.aiRevision = record.revision; refreshControls();
        try {
            const data = await request(`/api/uploader/projects/${encodeURIComponent(record.id)}/metadata`, { method: 'POST', body: '{}' }), task = data.task || data;
            if (!validMetadataTask(task)) throw new Error('App chưa xác nhận tác vụ metadata hợp lệ.');
            record.aiTask = task;
            if (current === record) { $('uploader-ai-status').textContent = `Metadata AI: ${statuses[task.status] || task.status}`; notify('Đã gửi tác vụ tạo metadata từ SRT; chưa đăng video.'); }
            if (task.status === 'success') applyGenerated(record, task);
            schedulePoll();
        } catch (error) { if (current === record) notify(error.message + (error.uncertainMutation ? ' Kiểm tra app trước khi tạo lại để tránh chạy AI hai lần.' : ''), 'error'); }
        finally { record.busy = ''; if (current === record) refreshControls(); }
    }
    function applyGenerated(record, task) {
        if (!object(task.metadata)) throw new Error('Tác vụ AI hoàn tất nhưng không có metadata hợp lệ.');
        const metadata = normalizeMetadata(task.metadata), checked = validateMetadata(metadata);
        if (Object.keys(checked.errors).length) throw new Error('Metadata AI vượt giới hạn; nội dung cũ vẫn được giữ.');
        if (record.revision !== record.aiRevision) {
            if (current === record) notify('AI đã hoàn tất nhưng bạn đã chỉnh nội dung sau khi gửi. Giữ nguyên bản chỉnh sửa, không tự ghi đè.');
            return;
        }
        record.metadata = metadata; record.dirty = true; record.revision++; record.pending = null; persist(record);
        if (current === record) { $('uploader-confirm-public').checked = false; paintMetadata(); refreshControls(); notify('Đã nhận metadata AI. Hãy duyệt và lưu / xếp hàng chủ động; chưa đăng video.'); }
    }
    async function uploadMedia(event) {
        event?.preventDefault(); const input = $('uploader-media-file'), file = input.files?.[0];
        if (!file || mediaBusy) return;
        if (!/\.mp4$/i.test(file.name) || (file.type && file.type !== 'video/mp4') || !Number.isFinite(file.size) || file.size <= 0) { $('uploader-media-status').textContent = 'Chọn một file MP4 không rỗng.'; return; }
        const sequence = ++mediaSequence, revision = navigation; mediaBusy = true; refreshControls(); $('uploader-media-status').textContent = 'Đang tải MP4 vào app cục bộ; chưa gửi lên nền tảng…';
        try {
            const body = new FormData(); body.append('video', file);
            const data = await request('/api/uploader/media', { method: 'POST', body, timeoutMs: 120000 });
            if (sequence !== mediaSequence) return;
            if (!validId(data.id) || typeof data.name !== 'string' || !Number.isFinite(data.size_bytes) || data.size_bytes <= 0 || !object(data.metadata) || !safeMediaUrl(data.video_url, data.id)) throw new Error('App chưa trả MP4 / URL đã ký hợp lệ.');
            const record = newRecord('media', data.id, data.metadata); record.videoUrl = safeMediaUrl(data.video_url, data.id); record.loaded = true; record.asset = 'media'; record.name = data.name;
            records.set('media:' + data.id, record);
            if (navigation !== revision) { $('uploader-media-status').textContent = 'MP4 đã vào app, nhưng project đã đổi trong lúc tải. Không tự thay project đang làm.'; return; }
            current = record; navigation++; $('uploader-confirm-public').checked = false;
            for (const [field, value] of Object.entries({ 'project-name': data.name, 'project-id': data.id, source: 'MP4 tải vào app cục bộ', 'video-path': data.name, srt: 'MP4 riêng không có SRT', crop: 'Không áp dụng', resolution: 'Xem video preview', manifest: 'Media cục bộ riêng, không có manifest project', stages: 'Chưa đăng' })) $(`uploader-${field}`).textContent = value;
            paintCurrent(); $('uploader-media-status').textContent = `Đã nhận ${data.name}. Chưa đăng lên nền tảng.`; notify('Đã nhận MP4 cục bộ. Nhập nội dung và chọn tài khoản để tiếp tục.');
        } catch (error) { if (sequence === mediaSequence) $('uploader-media-status').textContent = error.message; }
        finally { if (sequence === mediaSequence) { mediaBusy = false; input.value = ''; refreshControls(); } }
    }
    function validQueueTask(task) { return object(task) && validId(task.id) && uuid(task.profile_id) && Object.hasOwn(platformLabels, task.platform) && Object.hasOwn(statuses, task.status) && typeof task.percent === 'number' && Number.isFinite(task.percent) && task.percent >= 0 && task.percent <= 100; }
    async function loadQueue() {
        const sequence = ++queueSequence, mutation = queueMutation; queueLoading = true; refreshControls();
        try {
            const data = await request('/api/uploader/queue'); if (sequence !== queueSequence || mutation !== queueMutation || [...records.values()].some(record => record.busy === 'queue') || queueBusy.size) return;
            if (!Array.isArray(data.jobs) || !data.jobs.every(validQueueTask) || new Set(data.jobs.map(task => task.id)).size !== data.jobs.length) throw new Error('App trả hàng đợi đăng không hợp lệ.');
            const completed = data.jobs.filter(task => task.status === 'success' && jobs.some(old => old.id === task.id && activeQueue.has(old.status)));
            jobs = data.jobs; queueError = ''; $('uploader-queue-status').textContent = 'Đã đọc trạng thái từ app cục bộ. Không tự thử lại / tạo tác vụ mới.'; paintQueue();
            for (const task of completed) window.VietSubVFX?.complete?.({ kind: 'upload', id: task.id, status: 'success', target: $('uploader-publish-jobs') });
        } catch (error) { if (sequence === queueSequence) { queueError = error.message; $('uploader-queue-status').textContent = error.message + ' Giữ trạng thái cuối; bấm Đọc lại tiến độ để kết nối lại.'; } }
        finally { if (sequence === queueSequence) { queueLoading = false; paintProfiles(); refreshControls(); } }
    }
    let paintedQueueSignature = '';
    function paintQueue() {
        const signature = JSON.stringify([jobs, profiles.map(profile => [profile.id, profile.name]), [...queueBusy]]);
        if (signature === paintedQueueSignature) return;
        paintedQueueSignature = signature;
        const rows = [], focused = document.activeElement?.dataset?.uploaderFocus;
        $('uploader-queue-count').textContent = String(jobs.length);
        for (const task of jobs) {
            const row = node('article', '', 'uploader-job'); row.dataset.status = task.status;
            const head = node('div', '', 'uploader-job-head');
            head.appendChild(node('strong', `${platformLabels[task.platform]} · ${profiles.find(item => item.id === task.profile_id)?.name || task.profile_id}`));
            head.appendChild(node('span', statuses[task.status]));
            row.appendChild(head);
            const progress = node('div', '', 'uploader-job-progress'), fill = node('i');
            progress.setAttribute('role', 'progressbar'); progress.setAttribute('aria-label', `Tiến độ ${platformLabels[task.platform]} ${task.id}`);
            progress.setAttribute('aria-valuemin', '0'); progress.setAttribute('aria-valuemax', '100'); progress.setAttribute('aria-valuenow', String(task.percent));
            fill.style.width = `${task.percent}%`; progress.appendChild(fill); row.appendChild(progress);
            const stepText = typeof task.step === 'string' && Object.hasOwn(stepLabels, task.step) ? ` · ${stepLabels[task.step]}` : '';
            const modeTag = uploadModeLabels[task.upload_mode] ? ` · ${uploadModeLabels[task.upload_mode]}` : '';
            const debugTag = task.debug_mode ? ' · Debug Trace' : '';
            row.appendChild(node('p', `${typeof task.phase === 'string' ? task.phase : ''}${stepText} · ${task.percent}% · ${modeLabels[task.visibility] || 'Chế độ từ app'}${modeTag}${debugTag}\n${task.id}`));
            if (typeof task.failed_step === 'string' && Object.hasOwn(stepLabels, task.failed_step) && ['error', 'needs_verification', 'uncertain'].includes(task.status)) {
                row.appendChild(node('p', `Dừng tại bước: ${stepLabels[task.failed_step]}`, 'uploader-job-step-failed'));
            }
            if (task.status === 'awaiting_user_submit') {
                const remainMin = typeof task.awaiting_deadline === 'number' && Number.isFinite(task.awaiting_deadline)
                    ? Math.max(1, Math.ceil((task.awaiting_deadline - Date.now() / 1000) / 60))
                    : 10;
                row.appendChild(node('p', `Trình duyệt đã mở và điền sẵn nội dung. Hãy xử lý CAPTCHA (nếu có) rồi bấm nút Đăng trên trình duyệt, hoặc bấm “Xác nhận đã đăng” sau khi hoàn tất (tối đa còn ~${remainMin} phút).`, 'uploader-job-assisted'));
            }
            if (task.scheduled_at) row.appendChild(node('p', `Lịch cục bộ: ${String(task.scheduled_at)} · Asia/Saigon. App và máy tính phải đang chạy.`));
            if (typeof task.error === 'string' && task.error) row.appendChild(node('p', task.error, 'uploader-job-error'));
            if (['uncertain', 'needs_verification'].includes(task.status)) row.appendChild(node('p', 'Kiểm tra trực tiếp tài khoản trước khi thử lại. Video có thể đã được đăng; thử lại có nguy cơ đăng trùng.', 'uploader-job-error'));
            const screenshot = safeScreenshotUrl(task.screenshot_url, task.id);
            if (screenshot) { const link = node('a', 'Mở ảnh chụp kiểm tra cục bộ'); link.href = screenshot; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.appendChild(link); }
            const trace = safeTraceUrl(task.trace_url, task.id);
            if (trace) {
                const traceWrap = node('div', '', 'uploader-trace-box');
                const traceLink = node('a', 'Tải Playwright Trace (.zip)');
                traceLink.href = trace; traceLink.download = `vietsub-trace-${task.id.slice(0, 12)}.zip`; traceLink.rel = 'noopener noreferrer';
                traceWrap.appendChild(traceLink);
                traceWrap.appendChild(node('p', 'Lưu ý bảo mật: Tệp Trace có thể chứa ảnh chụp và dữ liệu phiên trình duyệt — không chia sẻ công khai tệp này.', 'uploader-trace-warning'));
                row.appendChild(traceWrap);
            }
            // result_url may point off-machine; it is never turned into a request or unchecked link.
            const actions = node('div', '', 'uploader-job-actions');
            if (task.status === 'awaiting_user_submit') {
                const confirmBtn = button('Xác nhận đã đăng', () => queueAction(task, 'confirm-submitted'), queueBusy.has(task.id), 'btn-secondary');
                confirmBtn.dataset.uploaderFocus = `confirm-${task.id}`;
                actions.appendChild(confirmBtn);
            }
            if (activeQueue.has(task.status) || task.status === 'needs_verification') { const el = button('Huỷ tác vụ đăng', () => queueAction(task, 'cancel'), queueBusy.has(task.id), 'btn-danger'); el.dataset.uploaderFocus = `cancel-${task.id}`; actions.appendChild(el); }
            if (['error', 'cancelled', 'uncertain', 'needs_verification'].includes(task.status)) { const el = button('Thử lại…', () => queueAction(task, 'retry'), queueBusy.has(task.id), 'btn-secondary'); el.dataset.uploaderFocus = `retry-${task.id}`; actions.appendChild(el); }
            row.appendChild(actions); rows.push(row);
        }
        if (!rows.length) rows.push(node('p', 'Chưa có tác vụ đăng. Chọn video, tài khoản và chủ động thêm vào hàng đợi.', 'field-help'));
        const list = $('uploader-publish-jobs'); list.replaceChildren(...rows);
        if (focused) list.querySelectorAll('button').forEach(el => { if (el.dataset.uploaderFocus === focused && !el.disabled) el.focus({ preventScroll: true }); });
    }
    async function submitQueue(event) {
        event?.preventDefault(); const record = current;
        if (!record || record.busy || !currentReady()) return;
        if (!record.uncertain) {
            const checked = validation(record);
            if (checked.issues.length) { $('uploader-submit-error').textContent = checked.issues.join(' '); return; }
            let requestId; try { requestId = crypto.randomUUID(); } catch (_) { notify('Trình duyệt không tạo được UUID an toàn; không gửi yêu cầu đăng.', 'error'); return; }
            const body = {
                ...(record.kind === 'project' ? { job_id: record.id } : { media_id: record.id }), profile_ids: [...record.selected], metadata: checked.metadata,
                visibility: record.visibility, scheduled_at: checked.scheduled_at, request_id: requestId, confirm_public: record.visibility === 'public' && $('uploader-confirm-public').checked,
                upload_mode: record.uploadMode || 'assisted', debug_mode: Boolean(record.debugMode)
            };
            record.pending = { body, revision: record.revision };
        } else if (!record.pending) return;
        // Reuse the exact payload + request_id when the POST may have reached the app. Never auto-submit from a poll.
        const pending = record.pending; record.busy = 'queue'; queueMutation++; persist(record); refreshControls(); paintProfiles(); $('uploader-submit-error').textContent = '';
        try {
            const data = await request('/api/uploader/queue', { method: 'POST', body: JSON.stringify(pending.body) });
            if (!Array.isArray(data.jobs) || !data.jobs.length || !data.jobs.every(validQueueTask) || data.jobs.length !== pending.body.profile_ids.length ||
                new Set(data.jobs.map(task => task.profile_id)).size !== pending.body.profile_ids.length || data.jobs.some(task => !pending.body.profile_ids.includes(task.profile_id))) {
                const error = new Error('Phản hồi xếp hàng không khớp tài khoản đã chọn. Kiểm tra tiến độ trước khi gửi lại.'); error.uncertainMutation = true; throw error;
            }
            for (const task of data.jobs) { const index = jobs.findIndex(item => item.id === task.id); if (index < 0) jobs.push(task); else jobs[index] = task; }
            record.pending = null; record.uncertain = false; record.revision++;
            if (current === record) { $('uploader-confirm-public').checked = false; notify(`App đã nhận ${data.jobs.length} tác vụ. Đây là trạng thái hàng đợi, không phải xác nhận đăng thành công.`); }
            paintQueue(); paintProfiles(); schedulePoll();
        } catch (error) {
            record.uncertain = Boolean(error.uncertainMutation);
            if (!record.uncertain) record.pending = null;
            if (current === record) { $('uploader-submit-error').textContent = error.message + (record.uncertain ? ' Có thể app đã nhận: chỉ dùng nút gửi lại cùng request ID, không tạo yêu cầu mới. Các trường tạm khoá để giữ payload.' : ''); notify($('uploader-submit-error').textContent, 'error'); }
        } finally { record.busy = ''; persist(record); if (current === record) { refreshControls(); paintProfiles(); } }
    }
    async function queueAction(task, action) {
        if (queueBusy.has(task.id)) return;
        const uncertain = ['uncertain', 'needs_verification'].includes(task.status);
        const warning = action === 'retry'
            ? `Thử lại tác vụ ${platformLabels[task.platform]}? NGUY CƠ ĐĂNG TRÙNG: tác vụ trước có thể đã đăng dù app báo lỗi / chưa rõ kết quả. Hãy kiểm tra tài khoản trước. Tiếp tục?`
            : action === 'confirm-submitted'
                ? `Xác nhận bạn đã bấm Đăng / Lưu bản nháp xong trong cửa sổ trình duyệt cho ${platformLabels[task.platform]}?`
                : 'Huỷ tác vụ đăng? Nếu nền tảng đã nhận video, huỷ trong app không xoá được bài đăng. Kiểm tra tài khoản để biết kết quả thực tế.';
        if (!window.confirm(warning)) return;
        queueBusy.add(task.id); queueMutation++; paintQueue(); refreshControls();
        try {
            const data = await request(`/api/uploader/queue/${encodeURIComponent(task.id)}/${action}`, { method: 'POST', body: JSON.stringify(action === 'retry' ? { confirm_uncertain: uncertain } : {}) }), next = data.job || data.task || data;
            if (!validQueueTask(next) || next.profile_id !== task.profile_id || (action === 'cancel' && next.id !== task.id)) throw new Error('Phản hồi tác vụ không khớp; đọc lại tiến độ trước khi thao tác tiếp.');
            const index = jobs.findIndex(value => value.id === next.id); if (index >= 0) jobs[index] = next; else jobs.push(next);
            notify(action === 'cancel'
                ? 'App đã nhận yêu cầu huỷ. Trạng thái thực tế hiển thị theo phản hồi app.'
                : action === 'confirm-submitted'
                    ? 'Đã gửi xác nhận đã đăng thủ công; đang hoàn tất phiên trình duyệt.'
                    : 'App đã nhận yêu cầu thử lại có cảnh báo nguy cơ trùng. Chưa xác nhận thành công.');
            schedulePoll();
        } catch (error) { notify(error.message + ' Đọc lại tiến độ trước khi thử lại lần nữa.', 'error'); }
        finally { queueBusy.delete(task.id); paintQueue(); paintProfiles(); refreshControls(); }
    }
    function needsPolling() { return jobs.some(task => activeQueue.has(task.status)) || [...sessions.values()].some(task => activeSessions.has(task.status)) || [...records.values()].some(record => record.aiTask && ['queued', 'running'].includes(record.aiTask.status)); }
    function schedulePoll() { if (!pollTimer && needsPolling() && pollFailures < 3) pollTimer = setTimeout(poll, 1800); }
    async function poll() {
        pollTimer = 0; let failed = false;
        try {
            for (const task of [...sessions.values()]) {
                if (!activeSessions.has(task.status) || profileBusy.has(task.profile_id)) continue;
                try {
                    const data = await request(`/api/uploader/sessions/${encodeURIComponent(task.id)}`), next = data.task || data;
                    if (!validSession(next, task.profile_id) || next.id !== task.id) throw new Error('Phiên thao tác trả sai ID hoặc trạng thái.');
                    if (sessions.get(task.id) !== task) continue;
                    sessions.set(task.id, next);
                    if (!activeSessions.has(next.status)) await loadProfiles();
                } catch (error) { failed = true; $('uploader-profiles-status').textContent = error.message + ' Chưa biết kết quả đăng nhập; không tự đăng lại.'; }
            }
            for (const record of records.values()) {
                const task = record.aiTask;
                if (!task || !['queued', 'running'].includes(task.status)) continue;
                try {
                    const data = await request(`/api/uploader/metadata/${encodeURIComponent(task.id)}`), next = data.task || data;
                    if (!validMetadataTask(next) || next.id !== task.id) throw new Error('Tác vụ metadata trả sai ID.');
                    if (record.aiTask !== task) continue;
                    record.aiTask = next;
                    if (current === record) $('uploader-ai-status').textContent = `Metadata AI: ${statuses[next.status] || next.status}${next.error ? ' · ' + next.error : ''}`;
                    if (next.status === 'success') applyGenerated(record, next);
                } catch (error) { failed = true; if (current === record) notify(error.message + ' Nội dung đang soạn vẫn được giữ.', 'error'); }
            }
            if (jobs.some(task => activeQueue.has(task.status)) && !queueLoading) { await loadQueue(); if (queueError) failed = true; }
        } finally {
            pollFailures = failed ? pollFailures + 1 : 0;
            if (pollFailures >= 3) notify('Đã dừng tự đọc sau 3 lỗi liên tiếp. Dùng Kiểm tra danh sách / Đọc lại tiến độ để kết nối lại; không tự thử đăng lại.', 'error');
            paintProfiles(); refreshControls(); schedulePoll();
        }
    }
    function metadataInput() {
        if (!current || $('uploader-content-fields').disabled) return;
        for (const key of ['title', 'description', 'hashtags']) { current.metadata[key] = $(`uploader-${key}-input`).value; $(`uploader-${key}`).textContent = current.metadata[key] || 'Chưa có'; }
        touched(current, true);
    }
    function beforeUnload(event) {
        const dirty = [...records.values()].filter(record => record.dirty || record.uncertain);
        for (const record of records.values()) persist(record);
        if (dirty.some(record => !record.persisted || record.uncertain || record.kind === 'media')) { event.preventDefault(); event.returnValue = ''; }
    }
    function init() {
        if (initialized || !$('workspace-uploader')) return;
        initialized = true; readDrafts();
        $('uploader-profile-form').addEventListener('submit', createProfile);
        $('uploader-profiles-refresh').addEventListener('click', async () => { pollFailures = 0; await loadProfiles(); await loadCapabilities(); schedulePoll(); });
        $('uploader-queue-refresh').addEventListener('click', async () => { pollFailures = 0; await loadQueue(); schedulePoll(); });
        $('uploader-publish-form').addEventListener('submit', submitQueue);
        $('uploader-media-form').addEventListener('submit', uploadMedia);
        $('uploader-media-file').addEventListener('change', refreshControls);
        $('uploader-use-project').addEventListener('click', () => {
            if (!lastProject || mediaBusy) return;
            // Repaint Phase1 facts through the existing Workbench sink; it calls this hook once.
            window.VietSubWorkbench?.projectReceived('uploader', lastProject);
        });
        for (const key of ['title', 'description', 'hashtags']) $(`uploader-${key}-input`).addEventListener('input', metadataInput);
        $('uploader-save-metadata').addEventListener('click', saveMetadata);
        $('uploader-generate-metadata').addEventListener('click', generateMetadata);
        $('uploader-visibility').addEventListener('change', () => { if (current && !$('uploader-content-fields').disabled) { current.visibility = $('uploader-visibility').value; touched(current); } });
        $('uploader-schedule-mode').addEventListener('change', () => { if (current && !$('uploader-content-fields').disabled) { current.scheduleMode = $('uploader-schedule-mode').value; touched(current); } });
        $('uploader-upload-mode').addEventListener('change', () => { if (current && !$('uploader-content-fields').disabled) { current.uploadMode = $('uploader-upload-mode').value; touched(current); } });
        $('uploader-debug-mode').addEventListener('change', () => { if (current && !$('uploader-content-fields').disabled) { current.debugMode = $('uploader-debug-mode').checked; touched(current); } });
        $('uploader-scheduled-at').addEventListener('input', () => { if (current && !$('uploader-scheduled-at').disabled) { current.scheduledLocal = $('uploader-scheduled-at').value; touched(current); } });
        $('uploader-confirm-public').addEventListener('change', refreshControls);
        $('uploader-source-ack').addEventListener('change', () => { if (current && !$('uploader-source-ack').disabled) { current.sourceConfirmed = $('uploader-source-ack').checked; touched(current); } });
        document.addEventListener('vietsub:apply-channel-preset', event => {
            const detail = event?.detail;
            const pub = detail?.publish_defaults;
            if (!pub || !current || current.busy || current.uncertain) return;
            if (typeof detail.apply_sequence === 'number') {
                if (detail.apply_sequence < lastPresetSequence) return;
                lastPresetSequence = detail.apply_sequence;
            }
            const rawIds = Array.isArray(pub.profile_ids) ? pub.profile_ids.filter(uuid) : [];
            if (profilesLoaded) {
                const knownIds = new Set(profiles.map(item => item.id));
                current.selected = new Set(rawIds.filter(id => knownIds.has(id)));
                current.pendingPresetProfiles = false;
            } else {
                current.selected = new Set(rawIds);
                current.pendingPresetProfiles = rawIds.length > 0;
            }
            if (uploadModes.includes(pub.upload_mode)) current.uploadMode = pub.upload_mode;
            if (modes.includes(pub.visibility)) current.visibility = pub.visibility;
            touched(current);
            paintProfiles();
            paintMetadata();
            refreshControls();
            notify(`Đã đồng bộ tài khoản và chế độ đăng từ hồ sơ "${detail.preset_name || ''}"${current.visibility === 'public' ? ' (vẫn cần xác nhận công khai thủ công trước khi đăng).' : '.'}`);
        });
        // app.js already owns this click -> Workbench refresh -> projectReceived. No second refresh listener here.
        $('uploader-video').addEventListener('error', () => { $('uploader-video-status').textContent = 'Trình duyệt không phát được video. Bản nháp vẫn giữ; kiểm tra lại file trước khi đăng.'; });
        window.addEventListener('beforeunload', beforeUnload);
        refreshControls(); paintProfiles(); paintQueue();
        loadCapabilities(); loadProfiles(); loadQueue().then(schedulePoll);
        if (pendingSnapshot) { const snapshot = pendingSnapshot; pendingSnapshot = null; projectReceived(snapshot); }
    }
    window.VietSubUploader = {
        init, projectReceived,
        state: () => ({
            job_id: current?.kind === 'project' ? current.id : '', media_id: current?.kind === 'media' ? current.id : '', metadata: current ? { ...current.metadata } : null,
            selected: current ? [...current.selected] : [], visibility: current?.visibility || 'draft', schedule_mode: current?.scheduleMode || 'now', scheduled_local: current?.scheduledLocal || '',
            upload_mode: current?.uploadMode || 'assisted', debug_mode: Boolean(current?.debugMode),
            dirty: Boolean(current?.dirty), revision: current?.revision || 0, persisted: Boolean(current?.persisted), loading: Boolean(current?.loading), busy: current?.busy || '',
            asset: current?.asset || '', video_url: current?.videoUrl || '', loadFailed: Boolean(current?.loadFailed), uncertain: Boolean(current?.uncertain),
            request_id: current?.pending?.body.request_id || '', profiles: clone(profiles), jobs: clone(jobs), sessions: clone([...sessions.values()])
        }),
        helpers: { platformLabels, stepLabels, validateMetadata, parseYoutubeClientSecretJson, scheduleValue, safeProjectUrl, safeMediaUrl, safeScreenshotUrl, safeTraceUrl, validatedDraft, draftStorageKey, maxDraftBytes }
    };
    window.addEventListener('load', init);
})();
