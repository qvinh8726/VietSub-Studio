/* Auto Edit owns only its workspace. No VietSub queue, crop or upload handlers are replaced. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const clone = value => JSON.parse(JSON.stringify(value));
    const defaults = Object.freeze({
        mask_mode: 'boxblur', crop_box: null, blur_strength: 8, padding_px: 8, solid_color: '#111318', solid_opacity: .75,
        subtitle_enabled: true, subtitle_preset: 'standard', font: 'Be Vietnam Pro', font_size: 48,
        text_color: '#ffffff', outline: 2, shadow: 1, back_color: '#000000', subtitle_position: 'crop',
        subtitle_animation: 'static', subtitle_highlight_color: '#ffd54a', subtitle_max_words_per_chunk: 4,
        banner_text: '', banner_position: 'top', watermark_enabled: false, watermark_path: '', watermark_opacity: .7,
        watermark_corner: 'top_right', reframe: 'original', output_height: 1280, encoder: 'auto',
        audio_mode: 'original', audio_format: 'wav', bgm_volume: .35, tts_enabled: false, ducking_enabled: true,
        speed: 1, change_audio_pitch: false, tts_change_pitch: false, pitch_semitones: 0,
        tts_provider: 'capcut_internal', tts_voice: '', tts_speed: 1, tts_pitch: 0, tts_fallback: true,
    });
    const choices = {
        mask_mode: ['none', 'boxblur', 'delogo', 'solid_bar'], subtitle_preset: ['standard', 'anime', 'social'],
        font: ['Be Vietnam Pro', 'Noto Sans SC', 'JetBrains Mono'], subtitle_position: ['crop', 'bottom', 'top'],
        subtitle_animation: ['static', 'karaoke_highlight', 'word_pop'],
        banner_position: ['top', 'bottom'], watermark_corner: ['top_right', 'top_left', 'bottom_right', 'bottom_left'],
        reframe: ['original', 'vertical'], output_height: [720, 1280, 1920], encoder: ['auto', 'libx264', 'h264_nvenc'],
        audio_mode: ['original', 'mute', 'instrumental'], audio_format: ['wav', 'mp3'], tts_provider: ['capcut_internal', 'edge_tts'],
    };
    const ranges = {blur_strength: [1, 40], padding_px: [0, 100], solid_opacity: [0, 1], font_size: [12, 120],
        outline: [0, 10], shadow: [0, 10], subtitle_max_words_per_chunk: [2, 8], watermark_opacity: [0, 1], bgm_volume: [0, 2], tts_speed: [.5, 2], tts_pitch: [-12, 12],
        speed: [.5, 2], pitch_semitones: [-12, 12]};
    const booleans = ['subtitle_enabled', 'watermark_enabled', 'tts_enabled', 'tts_fallback', 'change_audio_pitch', 'ducking_enabled'];
    const colors = ['solid_color', 'text_color', 'back_color', 'subtitle_highlight_color'];
    const cropFields = ['x', 'y', 'width', 'height'];
    const cropDraftError = 'X + rộng và Y + cao không được vượt 100%; rộng / cao phải lớn hơn 0.';
    const draftStorageKey = 'vietsub.editor.drafts.v1', maxStoredDrafts = 10, maxDraftStorageLength = 262144;
    const numericKeys = new Set([...Object.keys(ranges), 'output_height']);
    const storedDrafts = new Map();
    const activeStatuses = new Set(['queued', 'running']);
    const statuses = {queued: 'Đang chờ', running: 'Đang xử lý', success: 'Hoàn tất', error: 'Lỗi', cancelled: 'Đã huỷ'};
    const edgeVoices = [{id: 'vi-VN-HoaiMyNeural', name: 'Hoài My · nữ', language: 'vi-VN', provider: 'edge_tts'},
        {id: 'vi-VN-NamMinhNeural', name: 'Nam Minh · nam', language: 'vi-VN', provider: 'edge_tts'}];
    const records = new Map(), voiceCache = {edge_tts: edgeVoices, capcut_internal: []};
    let initialized = false, pendingSnapshot = null, current = null, capabilities = null;
    let optionControls = [], controlByKey = {}, capabilitySequence = 0, voiceSequence = 0;
    let pollTimer = 0, pollGeneration = 0, drag = null, observer = null, renderedOutputKey = '';
    let editorGlossaries = [{id: 'default', name: 'Mặc định'}], editorActiveGlossaryId = 'default', editorGlossariesLoaded = false;
    let hookSequence = 0;

    function validCrop(box) {
        return Boolean(box && typeof box === 'object' && !Array.isArray(box) && cropFields.every(key => finite(box[key])) &&
            box.x >= 0 && box.y >= 0 && box.width > 0 && box.height > 0 && box.x + box.width <= 1.000001 && box.y + box.height <= 1.000001);
    }
    function validateOptions(value) {
        const options = {...defaults}, errors = {};
        if (!value || typeof value !== 'object' || Array.isArray(value)) return {options, errors: {_form: 'Cấu hình phải là một đối tượng.'}};
        for (const key of Object.keys(defaults)) if (Object.hasOwn(value, key)) options[key] = value[key];
        // Optional alias negotiates TTS-only pitch while old API responses remain readable.
        if (Object.hasOwn(value, 'tts_change_pitch')) {
            options.tts_change_pitch = value.tts_change_pitch;
            if (typeof value.tts_change_pitch !== 'boolean') errors.tts_change_pitch = 'Giá trị bật / tắt không hợp lệ.';
            else if (Object.hasOwn(value, 'change_audio_pitch') && value.change_audio_pitch !== value.tts_change_pitch) {
                errors.tts_change_pitch = 'Hai giá trị cao độ giọng đọc không khớp nhau.';
            } else options.change_audio_pitch = value.tts_change_pitch;
        } else if (Object.hasOwn(value, 'change_audio_pitch')) {
            delete options.tts_change_pitch;
        }
        for (const [key, values] of Object.entries(choices)) if (!values.includes(options[key])) errors[key] = 'Lựa chọn không hợp lệ.';
        for (const [key, [min, max]] of Object.entries(ranges)) {
            if (!finite(options[key]) || options[key] < min || options[key] > max) errors[key] = `Nhập số từ ${min} đến ${max}.`;
        }
        if (!errors.subtitle_max_words_per_chunk && !Number.isInteger(options.subtitle_max_words_per_chunk)) {
            errors.subtitle_max_words_per_chunk = 'Nhập số nguyên từ 2 đến 8.';
        }
        for (const key of booleans) if (typeof options[key] !== 'boolean') errors[key] = 'Giá trị bật / tắt không hợp lệ.';
        for (const key of colors) {
            if (typeof options[key] !== 'string' || !/^#[0-9a-f]{6}$/i.test(options[key])) errors[key] = 'Màu phải có dạng #RRGGBB.';
            else options[key] = options[key].toLowerCase();
        }
        if (options.crop_box !== null && !validCrop(options.crop_box)) errors.crop_box = 'Vùng che phải nằm trong nguồn (0–1), rộng và cao lớn hơn 0.';
        if (validCrop(options.crop_box)) options.crop_box = Object.fromEntries(cropFields.map(key => [key, options.crop_box[key]]));
        for (const [key, max] of [['banner_text', 200], ['watermark_path', 2048], ['tts_voice', 200]]) {
            if (typeof options[key] !== 'string' || options[key].length > max || /[\u0000\r\n]/.test(options[key])) errors[key] = `Văn bản tối đa ${max} ký tự, không có ký tự điều khiển.`;
        }
        if (options.watermark_enabled && !options.watermark_path) errors.watermark_path = 'Chọn logo PNG của project trước khi bật logo.';
        if (options.tts_enabled && (typeof options.tts_voice !== 'string' || !options.tts_voice.trim())) errors.tts_voice = 'Chọn giọng đọc trước khi bật TTS.';
        return {options, errors};
    }
    // Only local form data is persisted: never source/transcript payloads, task payloads, URLs or preview text.
    function rawNumber(value) { return typeof value === 'string' && value.length <= 128 && !/[\u0000-\u001f\u007f]/.test(value); }
    function cropFromDraft(draft) {
        return Object.fromEntries(cropFields.map(key => [key, draft[key].trim() ? Number(draft[key]) / 100 : NaN]));
    }
    function draftOptions(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
        const tts = Object.hasOwn(value, 'tts_enabled') ? value.tts_enabled : defaults.tts_enabled;
        const watermark = Object.hasOwn(value, 'watermark_enabled') ? value.watermark_enabled : defaults.watermark_enabled;
        if (typeof tts !== 'boolean' || typeof watermark !== 'boolean') return null;
        // Missing a voice/logo is a recoverable inline error, not an unsafe storage value.
        const validation = validateOptions({...value, tts_enabled: false, watermark_enabled: false});
        if (Object.keys(validation.errors).length) return null;
        return {...validation.options, tts_enabled: tts, watermark_enabled: watermark};
    }
    function validatedDraft(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.job_id !== 'string' ||
            !value.job_id || value.job_id.length > 256 || /[\u0000-\u001f\u007f]/.test(value.job_id) ||
            typeof value.dirty !== 'boolean' || typeof value.recovery_required !== 'boolean' || (!value.dirty && !value.recovery_required) ||
            !Number.isSafeInteger(value.revision) || value.revision < 0 || value.revision >= Number.MAX_SAFE_INTEGER) return null;
        const options = draftOptions(value.options), numericDraft = value.numeric_draft, cropDraft = value.crop_draft;
        if (!options || !numericDraft || typeof numericDraft !== 'object' || Array.isArray(numericDraft) ||
            !Object.entries(numericDraft).every(([key, raw]) => numericKeys.has(key) && rawNumber(raw))) return null;
        if (cropDraft !== null && (!cropDraft || typeof cropDraft !== 'object' || Array.isArray(cropDraft) ||
            Object.keys(cropDraft).length !== cropFields.length || !cropFields.every(key => Object.hasOwn(cropDraft, key) && rawNumber(cropDraft[key])) ||
            !validCrop(options.crop_box))) return null;
        return {job_id: value.job_id, options, numeric_draft: {...numericDraft}, crop_draft: cropDraft ? {...cropDraft} : null,
            dirty: value.dirty, recovery_required: value.recovery_required, revision: value.revision};
    }
    function readDrafts() {
        try {
            const raw = localStorage.getItem(draftStorageKey);
            if (typeof raw !== 'string' || raw.length > maxDraftStorageLength) return;
            const data = JSON.parse(raw);
            if (data?.version !== 1 || !Array.isArray(data.projects) || data.projects.length > maxStoredDrafts) return;
            for (const value of data.projects) {
                const draft = validatedDraft(value);
                if (draft) storedDrafts.set(draft.job_id, draft);
            }
        } catch (_) { /* Storage may be disabled, full, or contain an older/corrupt document. */ }
    }
    function persistDraft(record) {
        const options = {...record.options};
        for (const key of Object.keys(record.numericDraft)) options[key] = defaults[key];
        const draft = record.dirty || record.recoveryRequired ? validatedDraft({job_id: record.jobId, options,
            numeric_draft: record.numericDraft, crop_draft: record.cropDraft, dirty: record.dirty,
            recovery_required: record.recoveryRequired, revision: record.revision}) : null;
        storedDrafts.delete(record.jobId);
        if (draft) storedDrafts.set(record.jobId, draft);
        while (storedDrafts.size > maxStoredDrafts) {
            const key = storedDrafts.keys().next().value;
            storedDrafts.delete(key);
            if (records.has(key)) records.get(key).draftPersisted = false;
        }
        try {
            const raw = JSON.stringify({version: 1, projects: [...storedDrafts.values()]});
            if (raw.length > maxDraftStorageLength) throw new Error('Draft storage limit');
            localStorage.setItem(draftStorageKey, raw);
            record.draftPersisted = !record.dirty || Boolean(draft);
        } catch (_) { record.draftPersisted = false; }
    }
    function restoreDraft(record) {
        const draft = storedDrafts.get(record.jobId);
        if (!draft) return;
        record.options = clone(draft.options); record.numericDraft = {...draft.numeric_draft};
        for (const [key, raw] of Object.entries(record.numericDraft)) record.options[key] = raw.trim() ? Number(raw) : NaN;
        record.cropDraft = draft.crop_draft ? {...draft.crop_draft} : null;
        if (record.cropDraft) {
            const box = cropFromDraft(record.cropDraft);
            if (validCrop(box)) { record.options.crop_box = box; record.cropDraft = null; }
            else record.cropError = cropDraftError;
        }
        record.lastCrop = record.options.crop_box;
        record.dirty = draft.dirty; record.revision = draft.revision; record.draftPersisted = true;
        record.recoveryRequired = draft.recovery_required; record.loadFailed = draft.recovery_required;
    }
    function beforeUnload(event) {
        const drafts = [...records.values()].filter(record => record.dirty || record.recoveryRequired);
        for (const record of drafts) persistDraft(record);
        const unsavedSrt = [...records.values()].some(record => record.srtDirty);
        if (!drafts.length && !unsavedSrt) return;
        event.preventDefault(); event.returnValue = '';
    }
    function formatSrtTimestamp(ms) {
        const safe = Math.max(0, Math.min(86400000, Math.round(finite(ms) ? ms : 0)));
        const hours = Math.floor(safe / 3600000);
        const minutes = Math.floor(safe / 60000) % 60;
        const seconds = Math.floor(safe / 1000) % 60;
        const millis = safe % 1000;
        return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')},${String(millis).padStart(3, '0')}`;
    }
    function parseSrtTimestamp(value) {
        if (typeof value !== 'string') return null;
        const match = /^(\d{1,3}):([0-5]\d):([0-5]\d)[,.](\d{3})$/.exec(value.trim());
        if (!match) return null;
        const ms = ((Number(match[1]) * 60 + Number(match[2])) * 60 + Number(match[3])) * 1000 + Number(match[4]);
        return ms <= 86400000 ? ms : null;
    }
    function findActiveCueIndex(cues, ms) {
        if (!Array.isArray(cues) || !cues.length || !finite(ms) || ms < 0) return -1;
        let lo = 0, hi = cues.length - 1, candidate = -1;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            if (cues[mid].start_ms <= ms) { candidate = mid; lo = mid + 1; }
            else hi = mid - 1;
        }
        for (let i = candidate; i >= 0; i--) {
            if (ms >= cues[i].start_ms && ms < cues[i].end_ms) return i;
            if (ms - cues[i].start_ms > 60000) break;
        }
        return -1;
    }
    function sliceWordsBalanced(words, maxWords) {
        const limit = clamp(Number.isInteger(maxWords) ? maxWords : 4, 2, 8);
        if (!words.length) return [];
        const chunks = [];
        let index = 0;
        while (index < words.length) {
            const remaining = words.length - index;
            if (remaining === limit + 1 && limit >= 3) {
                const split = Math.ceil(remaining / 2);
                chunks.push(words.slice(index, index + split));
                chunks.push(words.slice(index + split));
                break;
            }
            chunks.push(words.slice(index, index + limit));
            index += limit;
        }
        return chunks;
    }
    function wordWeight(word) {
        const letters = (word.match(/[0-9A-Za-z\u00C0-\u024F\u1E00-\u1EFF]/g) || []).length;
        const pause = /[.!?…]$/.test(word) ? 3.5 : /[,;:]$/.test(word) ? 1.8 : 0;
        return Math.max(1.5, letters) + pause;
    }
    // CSS object-fit: contain leaves letterboxing. Overlay coordinates are relative to this content rect, never the element box.
    function containRect(width, height, sourceWidth, sourceHeight) {
        if (![width, height, sourceWidth, sourceHeight].every(value => finite(value) && value > 0)) return null;
        const scale = Math.min(width / sourceWidth, height / sourceHeight), w = sourceWidth * scale, h = sourceHeight * scale;
        return {x: (width - w) / 2, y: (height - h) / 2, width: w, height: h};
    }
    function adjustCrop(box, handle, dx, dy) {
        if (!validCrop(box) || !finite(dx) || !finite(dy)) return null;
        const min = .001;
        let {x, y, width, height} = box;
        if (!['nw', 'ne', 'sw', 'se'].includes(handle)) {
            x = clamp(x + dx, 0, 1 - width); y = clamp(y + dy, 0, 1 - height);
        } else {
            const right = x + width, bottom = y + height;
            if (handle.includes('w')) { x = clamp(x + dx, 0, right - min); width = right - x; }
            else width = clamp(width + dx, min, 1 - x);
            if (handle.includes('n')) { y = clamp(y + dy, 0, bottom - min); height = bottom - y; }
            else height = clamp(height + dy, min, 1 - y);
        }
        return Object.fromEntries(Object.entries({x, y, width, height}).map(([key, value]) => [key, Math.round(value * 1e6) / 1e6]));
    }
    function safeOutputUrl(value, jobId, taskId) {
        if (typeof value !== 'string' || !value || typeof taskId !== 'string' || !taskId) return '';
        try {
            const base = new URL(window.location.href), url = new URL(value, base);
            if (url.origin !== base.origin || url.username || url.password || url.hash ||
                url.pathname !== `/api/editor/jobs/${encodeURIComponent(taskId)}/output`) return '';
            return url.pathname + url.search;
        } catch (_) { return ''; }
    }
    function validTask(task, jobId, preview = false) {
        return Boolean(task && typeof task === 'object' && typeof task.id === 'string' && task.id &&
            (task.job_id === jobId || (preview && !task.job_id)) && Object.hasOwn(statuses, task.status) &&
            ['render', 'extract_audio', 'tts', 'tts_preview', 'preview', 'audio_preview', 'capcut_draft'].includes(task.action));
    }
    async function request(url, options = {}) {
        const {timeoutMs = 20000, ...rest} = options, method = (rest.method || 'GET').toUpperCase();
        const headers = new Headers(rest.headers || {});
        headers.set('X-VietSub-Token', document.querySelector('meta[name="api-token"]')?.content || '');
        const multipart = rest.body instanceof FormData;
        if (method !== 'GET' && !multipart) headers.set('Content-Type', 'application/json');
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, {...rest, method, headers, credentials: 'same-origin', signal: controller.signal});
            let data;
            try { data = await response.json(); } catch (_) { throw new Error('Auto Edit nhận phản hồi không phải JSON.'); }
            if (!response.ok) {
                const error = new Error(typeof data?.error === 'string' ? data.error : `Auto Edit: HTTP ${response.status}`);
                error.status = response.status; throw error;
            }
            if (!data || typeof data !== 'object') throw new Error('Auto Edit nhận dữ liệu không hợp lệ.');
            return data;
        } catch (error) {
            let failure = error;
            if (error.name === 'AbortError') failure = new Error(`Auto Edit không phản hồi sau ${Math.round(timeoutMs / 1000)} giây; kiểm tra lại trước khi gửi thêm tác vụ.`);
            else if (error instanceof TypeError) failure = new Error('Không kết nối được Auto Edit trên máy này. Hãy kiểm tra lại máy chủ.');
            if (method !== 'GET' && (!failure.status || failure.status >= 500)) failure.uncertainMutation = true;
            throw failure;
        } finally { clearTimeout(timer); }
    }
    function notify(message, state = '') {
        $('editor-status').textContent = message; $('editor-status').dataset.state = state;
    }
    function edited(record = current) {
        if (!record) return;
        record.dirty = true; record.revision++; persistDraft(record);
    }
    function canEdit() { return Boolean(current?.loaded && current.snapshot.availability?.video === true); }
    function running(record = current) { return activeStatuses.has(record?.task?.status); }
    function coreReady() { return capabilities?.ffmpeg === true && capabilities?.ffprobe === true; }
    function ttsReady() {
        const edge = capabilities?.optional?.edge_tts?.available === true;
        const capcut = capabilities?.capcut_compat === undefined || capabilities.capcut_compat === true;
        return current?.options.tts_provider === 'edge_tts' ? edge : capcut || (current?.options.tts_fallback === true && edge);
    }
    function needsSeparator() { return current?.options.audio_mode === 'instrumental' && capabilities?.optional?.audio_separator?.available !== true; }
    function controlLabel(control, fallback) {
        const label = control?.labels?.[0];
        if (!label) return fallback;
        if (typeof label.cloneNode === 'function') {
            const copy = label.cloneNode(true);
            copy.querySelectorAll?.('select, input, textarea, button, small, [role="note"]').forEach(node => node.remove());
            const text = copy.textContent?.replace(/\s+/g, ' ').trim();
            if (text) return text;
        }
        return label.textContent?.trim() || fallback;
    }
    function checkForm() {
        const validation = validateOptions(current?.options || defaults), errors = validation.errors;
        if (current?.cropError) errors.crop_box = current.cropError;
        for (const control of optionControls) {
            const error = errors[control.dataset.editorOption];
            control.setAttribute('aria-invalid', String(Boolean(error)));
            const help = (control.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== 'editor-form-error');
            control.setAttribute('aria-describedby', [...help, 'editor-form-error'].join(' '));
        }
        const cropError = errors.crop_box || (current?.options.crop_box === null && current.options.mask_mode !== 'none' ? 'Bật vùng che hoặc chọn “Không che” trước khi lưu.' : '');
        if (cropError) errors.crop_box = cropError;
        $('editor-crop-error').textContent = cropError;
        for (const field of cropFields) $(`editor-crop-${field}`).setAttribute('aria-invalid', String(Boolean(cropError)));
        $('editor-form-error').textContent = Object.entries(errors).filter(([key]) => key !== 'crop_box')
            .map(([key, message]) => `${controlLabel(controlByKey[key], key)}: ${message}`).join(' ');
        return {options: validation.options, errors};
    }
    function refreshControls() {
        if (!initialized) return;
        const editable = canEdit(), busy = Boolean(current?.busy || current?.loading), active = running(), invalid = Object.keys(checkForm().errors).length > 0;
        const launch = editable && !busy && !active && !invalid && coreReady() && !current?.recoveryRequired;
        $('editor-options-fieldset').disabled = !editable;
        $('editor-save-btn').disabled = !editable || busy || active || invalid || Boolean(current?.recoveryRequired);
        const encoderReady = current?.options.encoder === 'auto' || capabilities?.encoders?.[current?.options.encoder] === true;
        $('editor-render-btn').disabled = !launch || !encoderReady || current?.snapshot.ready !== true || needsSeparator() || (current?.options.tts_enabled && !ttsReady());
        $('editor-extract-btn').disabled = !launch;
        $('editor-tts-generate-btn').disabled = !launch || !ttsReady() || current?.snapshot.availability?.translated_srt !== true;
        $('editor-tts-preview-btn').disabled = !launch || !ttsReady();
        if ($('editor-audio-preview')) $('editor-audio-preview').disabled = !launch || (current?.options.tts_enabled && !ttsReady());
        if ($('editor-draft-btn')) $('editor-draft-btn').disabled = $('editor-render-btn').disabled;
        syncAudioControls();
        $('editor-load-voices-btn').disabled = !editable || busy || Boolean(current?.voicesLoading);
        $('editor-watermark-file').disabled = !editable || busy || active || Boolean(current?.recoveryRequired);
        $('editor-cancel-btn').disabled = !active || busy;
        $('editor-cancel-btn').textContent = current?.busy === 'cancel' ? 'Đang huỷ…' : 'Huỷ tác vụ';
        $('editor-send-publish-btn').disabled = !current?.videoOutput || busy || active;
        $('editor-crop-enabled').disabled = !editable;
        for (const field of cropFields) $(`editor-crop-${field}`).disabled = !editable || !current?.options.crop_box;
        for (const id of ['editor-crop-reset', 'editor-crop-lower']) $(id).disabled = !editable;
        $('editor-crop-box').setAttribute('aria-disabled', String(!editable));
        $('editor-crop-box').tabIndex = editable && current?.options.crop_box ? 0 : -1;
        $('editor-dirty').textContent = !current ? 'Chưa có project' : current.loading ? 'Đang nạp cấu hình…' : current.loadFailed ? 'Chưa xác nhận cấu hình máy chủ' : current.dirty ?
            `Có thay đổi chưa lưu${current.draftPersisted ? '' : ' · không lưu được bản nháp cục bộ'}` : 'Đã đồng bộ cấu hình';
        $('editor-dirty').dataset.dirty = String(Boolean(current?.dirty));
        const reason = !current ? 'Nhận project VietSub để bật các thao tác.' : !editable ? 'Video nguồn không còn khả dụng; khôi phục file và kiểm tra lại.' :
            current.recoveryRequired ? 'Chưa xác nhận phản hồi máy chủ. Bấm “Kiểm tra lại file” để khôi phục tác vụ cũ; không gửi lại tác vụ.' :
            active ? 'Tác vụ đang dùng cấu hình đã gửi. Bạn có thể chỉnh bản nháp; lưu và logo chờ tác vụ kết thúc.' :
            invalid ? 'Sửa các trường được đánh dấu để lưu hoặc xử lý.' : !coreReady() ? 'Kiểm tra FFmpeg / FFprobe trong Runtime. Lưu cấu hình vẫn khả dụng.' :
            !encoderReady ? 'Encoder đã chọn không khả dụng; chọn Tự động hoặc CPU.' : needsSeparator() ? 'Render giữ nhạc cần runtime tách giọng; trích audio gốc vẫn khả dụng. Đổi chế độ âm thanh hoặc kiểm tra Runtime.' :
            current.options.tts_enabled && !ttsReady() ? 'Provider TTS đã chọn chưa khả dụng; bật CapCut compatibility hoặc Edge fallback phù hợp, tắt TTS hoặc kiểm tra Runtime.' : '';
        $('editor-action-help').textContent = reason;
        $('editor-render-btn').title = reason || 'Render video bằng cấu hình hiện tại';
        $('editor-options-form').setAttribute('aria-busy', String(current?.busy === 'save' || current?.loading === true));
        if ($('editor-suggest-hooks-btn')) {
            $('editor-suggest-hooks-btn').disabled = !editable || busy || Boolean(current?.hooksBusy) || current?.snapshot?.availability?.translated_srt !== true;
            $('editor-suggest-hooks-btn').textContent = current?.hooksBusy ? 'Đang gợi ý Hook…' : 'AI gợi ý Hook';
        }
        refreshSrtControls();
    }
    function writeForm() {
        if (!current) return;
        for (const control of optionControls) {
            const value = current.options[control.dataset.editorOption];
            if (control.type === 'checkbox') control.checked = value;
            else if (control.dataset.editorOption !== 'tts_voice') control.value = current.numericDraft[control.dataset.editorOption] ?? String(value);
        }
        renderVoices(); writeCropInputs(); refreshControls(); syncOverlay();
    }
    function estimatedDuration(duration, speed) {
        if (!finite(duration) || duration <= 0 || !finite(speed) || speed < .5 || speed > 2) return '—';
        const seconds = duration / speed, minutes = Math.floor(seconds / 60);
        return `${String(minutes).padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
    }
    function syncAudioControls() {
        const options = current?.options || defaults, video = $('editor-video');
        for (const [id, key] of [['editor-speed-range', 'speed'], ['editor-pitch-range', 'pitch_semitones']]) {
            const input = $(id);
            if (input && finite(options[key])) input.value = String(options[key]);
        }
        const output = $('editor-speed-duration');
        if (output) output.textContent = `Thời lượng dự kiến: ${estimatedDuration(video.duration || current?.snapshot.manifest?.source_duration, options.speed)}`;
        if ($('editor-isolate-vocals')) $('editor-isolate-vocals').checked = options.audio_mode === 'instrumental';
        for (const chip of $('editor-options-form').querySelectorAll('[data-editor-preset]')) if (chip.dataset.editorPreset) chip.setAttribute('aria-pressed', String(options[chip.dataset.editorPreset] === chip.dataset.presetValue));
        const chips = $('editor-options-form').querySelectorAll('[data-pitch-preset]');
        for (const chip of chips) if (chip.dataset.pitchPreset !== undefined) chip.setAttribute('aria-pressed', String(Number(chip.dataset.pitchPreset) === options.pitch_semitones));
        // The source player contains no generated speech. New options always
        // preserve its pitch; only legacy API responses retain tape-style preview.
        const scoped = Object.hasOwn(options, 'tts_change_pitch');
        if (finite(options.speed) && options.speed >= .5 && options.speed <= 2) video.playbackRate = options.speed;
        video.preservesPitch = scoped || !options.change_audio_pitch;
        const help = $('editor-pitch-help');
        if (help) help.textContent = (scoped ? '' : 'Cấu hình cũ: video nguồn đang xem dùng liên kết cao độ cũ. Lần lưu hoặc xử lý tiếp theo chuyển sang cao độ riêng TTS. ') +
            'Cao độ và các chip chỉ áp dụng giọng đọc phụ đề (TTS). Bật công tắc để cao độ TTS đổi theo tốc độ video; tắt để giữ tông. Nhạc nền luôn giữ tông khi đổi tốc độ. Nghe thử 3 giây để kiểm tra bản phối; khi tắt TTS, mẫu chỉ có âm thanh gốc.';
    }
    function setAudioOption(key, value) {
        const control = controlByKey[key];
        if (!control || !canEdit()) return;
        control.value = String(value); optionChanged({target: control});
    }
    function writeCropInputs() {
        const box = current?.options.crop_box;
        $('editor-crop-enabled').checked = Boolean(box);
        for (const field of cropFields) $(`editor-crop-${field}`).value = current?.cropDraft?.[field] ?? (box ? String(Number((box[field] * 100).toFixed(3))) : '');
        updateCropSummary();
    }
    function updateCropSummary() {
        const box = current?.options.crop_box;
        if (current) $('editor-crop').textContent = validCrop(box) ? `x ${box.x.toFixed(4)} · y ${box.y.toFixed(4)} · rộng ${box.width.toFixed(4)} · cao ${box.height.toFixed(4)}` : 'Không dùng vùng che';
    }
    function setCrop(box, writeInputs = true) {
        if (!canEdit() || (box !== null && !validCrop(box))) return;
        current.options.crop_box = box ? {...box} : null; current.cropError = ''; current.cropDraft = null;
        if (box) current.lastCrop = {...box};
        edited();
        if (writeInputs) writeCropInputs(); else updateCropSummary();
        syncOverlay(); refreshControls();
    }
    function cropInputChanged() {
        if (!canEdit()) return;
        current.cropDraft = Object.fromEntries(cropFields.map(key => [key, $(`editor-crop-${key}`).value]));
        const box = cropFromDraft(current.cropDraft);
        if (!validCrop(box)) {
            current.cropError = cropDraftError;
            edited(); refreshControls(); return;
        }
        setCrop(box, false);
    }
    function sourceContent() {
        const video = $('editor-video'), surface = $('editor-source-surface');
        const vr = video.getBoundingClientRect(), sr = surface.getBoundingClientRect(), resolution = current?.snapshot.manifest?.source_resolution;
        const fit = containRect(vr.width, vr.height, video.videoWidth || resolution?.width, video.videoHeight || resolution?.height);
        return fit ? {...fit, left: vr.left - sr.left - (surface.clientLeft || 0) + fit.x, top: vr.top - sr.top - (surface.clientTop || 0) + fit.y} : null;
    }
    function syncOverlay() {
        if (!initialized) return;
        const content = drag?.record === current ? drag.content : sourceContent(), box = current?.options.crop_box, layer = $('editor-crop-layer'), rect = $('editor-crop-box');
        layer.hidden = !content || !validCrop(box) || $('editor-video').hidden;
        if (!layer.hidden) {
            for (const [key, value] of Object.entries({left: content.left, top: content.top, width: content.width, height: content.height})) layer.style[key] = `${value}px`;
            for (const [key, value] of Object.entries({left: box.x, top: box.y, width: box.width, height: box.height})) rect.style[key] = `${value * 100}%`;
            rect.setAttribute('aria-label', `Vùng che: X ${(box.x * 100).toFixed(1)}%, Y ${(box.y * 100).toFixed(1)}%, rộng ${(box.width * 100).toFixed(1)}%, cao ${(box.height * 100).toFixed(1)}%`);
            rect.dataset.maskMode = current.options.mask_mode;
        }
        renderLiveSubtitle(content);
        renderLiveBanner(content);
    }
    function renderLiveBanner(cachedContent = null) {
        if (!initialized) return;
        const overlay = $('editor-live-banner'), video = $('editor-video');
        if (!overlay) return;
        const record = current, content = cachedContent || sourceContent();
        const text = (record?.options?.banner_text || '').trim();
        if (!record || !text || video.hidden || !content) {
            overlay.hidden = true;
            return;
        }
        const opts = record.options;
        const resolution = record.snapshot.manifest?.source_resolution;
        const sourceH = video.videoHeight || resolution?.height || 1080;
        const scaledSize = Math.max(11, Math.min(34, Math.round((opts.font_size || 48) * 0.72 * (content.height / sourceH))));
        overlay.hidden = false;
        for (const [key, value] of Object.entries({left: content.left, top: content.top, width: content.width, height: content.height})) overlay.style[key] = `${value}px`;
        overlay.dataset.position = opts.banner_position === 'bottom' ? 'bottom' : 'top';
        const bar = document.createElement('span');
        bar.className = 'editor-live-banner-box';
        bar.style.fontFamily = `'${opts.font || 'Be Vietnam Pro'}', sans-serif`;
        bar.style.fontSize = `${scaledSize}px`;
        bar.style.color = opts.subtitle_highlight_color || '#ffd54a';
        bar.textContent = text;
        overlay.replaceChildren(bar);
    }
    function renderHooksList(record = current) {
        const list = $('editor-hooks-list');
        if (!list) return;
        const hooks = Array.isArray(record?.hooks) ? record.hooks : [];
        if (!hooks.length) {
            list.replaceChildren();
            list.hidden = true;
            return;
        }
        const nodes = hooks.map(text => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'hook-chip';
            chip.textContent = text;
            chip.dataset.hookText = text;
            chip.title = 'Bấm để điền vào Top Banner';
            return chip;
        });
        list.replaceChildren(...nodes);
        list.hidden = false;
    }
    async function suggestHooks() {
        const record = current;
        if (!record || !canEdit() || record.hooksBusy || record.busy) return;
        const sequence = ++hookSequence;
        record.hooksBusy = true;
        refreshControls();
        if ($('editor-hooks-status')) {
            $('editor-hooks-status').textContent = 'Đang tạo 3–5 câu Hook từ phụ đề SRT…';
            $('editor-hooks-status').dataset.state = '';
        }
        try {
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/suggest-hooks`, {
                method: 'POST', body: '{}', timeoutMs: 65000,
            });
            if (sequence !== hookSequence || current !== record) return;
            if (!Array.isArray(data.hooks) || !data.hooks.length) throw new Error('AI chưa trả về danh sách câu Hook hợp lệ.');
            record.hooks = data.hooks.filter(item => typeof item === 'string' && item.trim()).slice(0, 5);
            renderHooksList(record);
            if ($('editor-hooks-status')) {
                $('editor-hooks-status').textContent = `Đã gợi ý ${record.hooks.length} câu Hook (${data.provider || 'AI'}). Bấm để điền vào Top Banner.`;
            }
        } catch (error) {
            if (sequence !== hookSequence || current !== record) return;
            if ($('editor-hooks-status')) {
                $('editor-hooks-status').textContent = error.message;
                $('editor-hooks-status').dataset.state = 'error';
            }
        } finally {
            record.hooksBusy = false;
            if (current === record) refreshControls();
        }
    }
    function applyChannelPreset(detail) {
        if (!detail || !detail.editor_options || !current || !canEdit()) return;
        const merged = {...detail.editor_options};
        if (merged.crop_box === null && validCrop(current.options.crop_box)) {
            merged.crop_box = {...current.options.crop_box};
        }
        const validation = validateOptions(merged);
        if (Object.keys(validation.errors).length) {
            notify('Một số tuỳ chọn trong hồ sơ kênh chưa tương thích với dự án hiện tại.', 'error');
            return;
        }
        current.options = validation.options;
        current.numericDraft = {};
        current.cropDraft = null;
        current.cropError = '';
        if (current.options.crop_box) current.lastCrop = {...current.options.crop_box};
        edited(current);
        writeForm();
        notify(`Đã áp dụng cấu hình Auto Edit từ hồ sơ "${detail.preset_name || ''}". Bấm Lưu cấu hình hoặc Render.`);
    }
    function renderLiveSubtitle(cachedContent = null) {
        if (!initialized) return;
        const overlay = $('editor-live-subtitle'), video = $('editor-video');
        if (!overlay) return;
        const record = current, content = cachedContent || sourceContent();
        if (!record || !record.options.subtitle_enabled || video.hidden || !content || !record.srtCues?.length) {
            overlay.hidden = true;
            if (record) record.lastLiveSubtitleKey = '';
            return;
        }
        const nowMs = Math.round((finite(video.currentTime) ? video.currentTime : 0) * 1000);
        const idx = record.activeCueIndex >= 0 && record.srtCues[record.activeCueIndex] &&
            nowMs >= record.srtCues[record.activeCueIndex].start_ms && nowMs < record.srtCues[record.activeCueIndex].end_ms
            ? record.activeCueIndex : findActiveCueIndex(record.srtCues, nowMs);
        if (idx < 0 || !record.srtCues[idx]) {
            overlay.hidden = true;
            record.lastLiveSubtitleKey = '';
            return;
        }
        const cue = record.srtCues[idx], opts = record.options;
        const resolution = record.snapshot.manifest?.source_resolution;
        const sourceH = video.videoHeight || resolution?.height || 1080;
        const scaledSize = Math.max(11, Math.min(44, Math.round((opts.font_size || 48) * (content.height / sourceH))));
        overlay.hidden = false;
        for (const [key, value] of Object.entries({left: content.left, top: content.top, width: content.width, height: content.height})) overlay.style[key] = `${value}px`;
        overlay.dataset.position = opts.subtitle_position === 'top' ? 'top' : 'bottom';
        overlay.style.paddingBottom = opts.subtitle_position === 'crop' && validCrop(opts.crop_box)
            ? `${Math.max(2, Math.round((1 - (opts.crop_box.y + opts.crop_box.height)) * 100))}%` : '4%';
        const mode = opts.subtitle_animation || 'static';
        const duration = Math.max(1, cue.end_ms - cue.start_ms);
        const ratio = clamp((nowMs - cue.start_ms) / duration, 0, 1);
        const words = cue.text.replace(/\s+/g, ' ').trim().split(' ').filter(Boolean);
        let activeWord = -1, chunkIndex = 0;
        if (mode !== 'static' && words.length) {
            const weights = words.map(wordWeight), total = weights.reduce((a, b) => a + b, 0) || 1;
            let acc = 0;
            activeWord = words.length - 1;
            for (let i = 0; i < weights.length; i++) {
                acc += weights[i];
                if (ratio <= acc / total) { activeWord = i; break; }
            }
        }
        let chunks = [words];
        if (mode === 'word_pop' && words.length) {
            chunks = sliceWordsBalanced(words, opts.subtitle_max_words_per_chunk);
            let seen = 0;
            for (let ci = 0; ci < chunks.length; ci++) {
                if (activeWord < seen + chunks[ci].length || ci === chunks.length - 1) {
                    chunkIndex = ci;
                    activeWord = Math.max(0, activeWord - seen);
                    break;
                }
                seen += chunks[ci].length;
            }
        }
        const renderKey = `${idx}|${cue.text}|${mode}|${opts.font}|${scaledSize}|${opts.text_color}|${opts.subtitle_highlight_color}|${opts.back_color}|${opts.subtitle_preset}|${opts.outline}|${opts.shadow}|${chunkIndex}|${activeWord}`;
        if (record.lastLiveSubtitleKey === renderKey) return;
        record.lastLiveSubtitleKey = renderKey;
        const box = document.createElement('span');
        box.className = 'editor-live-subtitle-box';
        box.style.fontFamily = `'${opts.font || 'Be Vietnam Pro'}', sans-serif`;
        box.style.fontSize = `${scaledSize}px`;
        box.style.color = opts.text_color || '#ffffff';
        const stroke = Math.max(1, Math.round(opts.outline || 2));
        const shadow = Math.max(0, Math.round(opts.shadow || 1));
        box.style.textShadow = `0 0 ${stroke + 1}px #000, ${shadow}px ${shadow}px ${shadow + 1}px #000`;
        box.style.backgroundColor = opts.subtitle_preset === 'social' ? `${opts.back_color || '#000000'}cc` : 'rgba(0, 0, 0, 0.35)';
        if (mode === 'static' || !words.length) {
            box.textContent = cue.text;
        } else if (mode === 'karaoke_highlight') {
            words.forEach((word, wi) => {
                const span = document.createElement('span');
                span.className = 'editor-live-subtitle-word';
                span.textContent = wi < words.length - 1 ? `${word} ` : word;
                if (wi <= activeWord) span.style.color = opts.subtitle_highlight_color || '#ffd54a';
                box.appendChild(span);
            });
        } else {
            const visible = chunks[chunkIndex] || words;
            visible.forEach((word, wi) => {
                const span = document.createElement('span');
                span.className = wi === activeWord ? 'editor-live-subtitle-word is-pop' : 'editor-live-subtitle-word';
                span.textContent = wi < visible.length - 1 ? `${word} ` : word;
                if (wi === activeWord) span.style.color = opts.subtitle_highlight_color || '#ffd54a';
                box.appendChild(span);
            });
        }
        overlay.replaceChildren(box);
    }
    function setSrtStatus(message, state = '') {
        const el = $('editor-srt-status');
        if (!el) return;
        el.textContent = message;
        el.dataset.state = state;
    }
    function refreshSrtControls() {
        if (!initialized) return;
        const card = $('editor-srt-card');
        if (!card) return;
        const record = current;
        const hasCues = Boolean(record?.srtCues?.length);
        card.hidden = !record || (!hasCues && !record.srtLoading);
        const locked = Boolean(record?.srtLocked || running(record) || record?.srtSaving || record?.busy || record?.recoveryRequired);
        const editable = Boolean(record && hasCues && !locked);
        if ($('editor-srt-count')) $('editor-srt-count').textContent = `${record?.srtCues?.length || 0} câu`;
        const dirtyEl = $('editor-srt-dirty');
        if (dirtyEl) {
            dirtyEl.textContent = !record ? 'Chưa có SRT' : record.srtLoading ? 'Đang nạp SRT…' :
                record.srtSaving ? 'Đang lưu SRT…' : record.srtDirty ? 'SRT chưa lưu' : 'Đã đồng bộ SRT';
            dirtyEl.dataset.dirty = String(Boolean(record?.srtDirty));
        }
        $('editor-srt-add-btn').disabled = !editable;
        $('editor-srt-save-btn').disabled = !editable || !record.srtDirty || record.srtInvalidCount > 0;
        $('editor-srt-restore-btn').disabled = !editable || !record.srtHasBackup;
        $('editor-srt-replace-all-btn').disabled = !editable || !($('editor-srt-search')?.value || '').trim();
        $('editor-srt-apply-glossary-btn').disabled = !editable;
        const rowLocked = Boolean(record?.srtLocked || running(record) || record?.srtSaving);
        if (record && hasCues && record._lastRowLocked !== rowLocked) {
            record._lastRowLocked = rowLocked;
            const onlyOne = record.srtCues.length <= 1;
            for (const row of (record.srtRowByIndex || [])) {
                if (!row) continue;
                const head = row.children?.[0];
                if (head?.children) {
                    if (head.children[1]) head.children[1].disabled = rowLocked;
                    if (head.children[2]) head.children[2].disabled = rowLocked;
                    if (head.children[3]) head.children[3].disabled = rowLocked || onlyOne;
                }
                const last = row.children?.[row.children.length - 1];
                if (last?.dataset?.srtField === 'text') last.disabled = rowLocked;
            }
        }
    }
    function populateEditorGlossaries(store) {
        if (store && Array.isArray(store.profiles) && store.profiles.length) {
            editorGlossaries = store.profiles;
            editorActiveGlossaryId = store.active_id || store.profiles[0].id;
            editorGlossariesLoaded = true;
        }
        const select = $('editor-srt-glossary-select');
        if (!select) return;
        const previous = select.value;
        const nodes = editorGlossaries.map(profile => {
            const option = document.createElement('option');
            option.value = profile.id;
            const count = Array.isArray(profile.entries) ? profile.entries.length : 0;
            option.textContent = `${profile.name}${count ? ` (${count})` : ''}`;
            return option;
        });
        select.replaceChildren(...nodes);
        select.value = editorGlossaries.some(p => p.id === previous) ? previous : editorActiveGlossaryId;
    }
    async function loadEditorGlossaries() {
        if (editorGlossariesLoaded) { populateEditorGlossaries(); return; }
        try {
            const data = await request('/api/glossaries');
            if (Array.isArray(data?.profiles) && data.profiles.length) populateEditorGlossaries(data);
        } catch (_) { /* Non-fatal in offline/mock tests. */ }
    }
    function renderSrtList() {
        if (!initialized) return;
        const list = $('editor-srt-list');
        if (!list) return;
        const record = current;
        if (!record || !record.srtCues?.length) {
            list.replaceChildren();
            if (record) record.srtRowByIndex = [];
            refreshSrtControls();
            return;
        }
        const query = (record.srtSearch || '').trim().toLowerCase();
        const locked = Boolean(record.srtLocked || running(record));
        const onlyOne = record.srtCues.length <= 1;
        const rows = [];
        const byIndex = new Array(record.srtCues.length).fill(null);
        for (let i = 0; i < record.srtCues.length; i++) {
            const cue = record.srtCues[i];
            if (query && !cue.text.toLowerCase().includes(query) && !(cue.raw_text || '').toLowerCase().includes(query)) continue;
            const row = document.createElement('div');
            row.className = 'editor-srt-row';
            row.setAttribute('role', 'listitem');
            row.dataset.cueIndex = String(i);
            if (i === record.activeCueIndex) row.dataset.active = 'true';

            const head = document.createElement('div');
            head.className = 'editor-srt-row-head';
            const seek = document.createElement('button');
            seek.type = 'button'; seek.className = 'btn-ghost editor-srt-seek';
            seek.textContent = `#${i + 1}`; seek.title = 'Tua video tới câu này';
            seek.dataset.srtAction = 'seek'; seek.dataset.cueIndex = String(i);

            const startInput = document.createElement('input');
            startInput.type = 'text'; startInput.className = 'editor-srt-time';
            startInput.value = formatSrtTimestamp(cue.start_ms);
            startInput.disabled = locked;
            startInput.dataset.srtField = 'start'; startInput.dataset.cueIndex = String(i);
            startInput.setAttribute('aria-label', `Mốc bắt đầu câu ${i + 1}`);

            const endInput = document.createElement('input');
            endInput.type = 'text'; endInput.className = 'editor-srt-time';
            endInput.value = formatSrtTimestamp(cue.end_ms);
            endInput.disabled = locked;
            endInput.dataset.srtField = 'end'; endInput.dataset.cueIndex = String(i);
            endInput.setAttribute('aria-label', `Mốc kết thúc câu ${i + 1}`);

            const del = document.createElement('button');
            del.type = 'button'; del.className = 'btn-ghost editor-srt-delete';
            del.textContent = 'Xoá'; del.title = onlyOne ? 'Cần giữ ít nhất 1 câu phụ đề' : 'Xoá dòng phụ đề này';
            del.disabled = locked || onlyOne;
            del.dataset.srtAction = 'delete'; del.dataset.cueIndex = String(i);
            head.appendChild(seek); head.appendChild(startInput); head.appendChild(endInput); head.appendChild(del);
            row.appendChild(head);

            if (cue.raw_text) {
                const raw = document.createElement('div');
                raw.className = 'editor-srt-raw';
                raw.textContent = cue.raw_text;
                row.appendChild(raw);
            }
            const textarea = document.createElement('textarea');
            textarea.className = 'editor-srt-text';
            textarea.rows = cue.text.includes('\n') ? 2 : 1;
            textarea.value = cue.text;
            textarea.disabled = locked;
            textarea.dataset.srtField = 'text'; textarea.dataset.cueIndex = String(i);
            textarea.setAttribute('aria-label', `Nội dung câu ${i + 1}`);
            row.appendChild(textarea);

            rows.push(row);
            byIndex[i] = row;
        }
        record.srtRowByIndex = byIndex;
        record._lastRowLocked = locked;
        list.replaceChildren(...rows);
        refreshSrtControls();
    }
    function syncVideoCueTime(scrollIfIdle = true) {
        const record = current;
        if (!record || !record.srtCues?.length) { renderLiveSubtitle(); return; }
        const video = $('editor-video'), list = $('editor-srt-list');
        const ms = Math.round((finite(video.currentTime) ? video.currentTime : 0) * 1000);
        const nextIdx = findActiveCueIndex(record.srtCues, ms);
        if (nextIdx !== record.activeCueIndex) {
            const oldRow = record.activeCueIndex >= 0 ? record.srtRowByIndex?.[record.activeCueIndex] : null;
            if (oldRow) delete oldRow.dataset.active;
            const newRow = nextIdx >= 0 ? record.srtRowByIndex?.[nextIdx] : null;
            if (newRow) {
                newRow.dataset.active = 'true';
                if (scrollIfIdle && list && !list.contains(document.activeElement)) {
                    newRow.scrollIntoView?.({block: 'nearest', behavior: 'smooth'});
                }
            }
            record.activeCueIndex = nextIdx;
        }
        renderLiveSubtitle();
    }
    async function loadSrt(record, {force = false} = {}) {
        if (!record || (record.srtDirty && !force) || record.srtLoading) return;
        const sequence = record.srtSequence = (record.srtSequence || 0) + 1;
        record.srtLoading = true;
        if (current === record) refreshSrtControls();
        try {
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/srt`);
            if (record.srtSequence !== sequence) return;
            record.srtLoaded = true;
            if (!Array.isArray(data?.cues)) {
                record.srtCues = [];
                if (current === record) renderSrtList();
                return;
            }
            if (record.srtDirty && !force) return;
            record.srtCues = data.cues.map((cue, idx) => ({
                index: idx + 1,
                start_ms: Number(cue.start_ms) || 0,
                end_ms: Number(cue.end_ms) || 1000,
                text: String(cue.text || ''),
                raw_text: typeof cue.raw_text === 'string' ? cue.raw_text : '',
            }));
            record.srtHasRaw = Boolean(data.has_raw);
            record.srtHasBackup = Boolean(data.has_backup);
            record.srtLocked = Boolean(data.locked);
            record.srtLockReason = typeof data.lock_reason === 'string' ? data.lock_reason : '';
            record.srtDirty = false;
            record.srtInvalidCount = 0;
            record.activeCueIndex = -1;
            record.lastLiveSubtitleKey = '';
            if (current === record) {
                setSrtStatus(record.srtLocked ? record.srtLockReason : `Đã nạp ${record.srtCues.length} câu phụ đề.`);
                renderSrtList();
                syncVideoCueTime(false);
            }
        } catch (error) {
            if (record.srtSequence !== sequence) return;
            record.srtLoaded = true;
            if (current === record) setSrtStatus(error.message, 'error');
        } finally {
            if (record.srtSequence === sequence) {
                record.srtLoading = false;
                if (current === record) refreshSrtControls();
            }
        }
    }
    function insertSrtCue() {
        const record = current;
        if (!record || !record.srtCues?.length || record.srtLocked || running(record)) return;
        const cues = record.srtCues;
        const afterIdx = record.selectedCueIndex >= 0 && record.selectedCueIndex < cues.length
            ? record.selectedCueIndex : (record.activeCueIndex >= 0 && record.activeCueIndex < cues.length ? record.activeCueIndex : cues.length - 1);
        const prev = cues[afterIdx], next = cues[afterIdx + 1];
        let start_ms, end_ms;
        if (next) {
            const gap = next.start_ms - prev.end_ms;
            if (gap >= 100) {
                start_ms = prev.end_ms;
                end_ms = Math.min(prev.end_ms + 2000, next.start_ms);
            } else {
                start_ms = next.start_ms > prev.start_ms ? Math.max(prev.start_ms, Math.min(prev.end_ms, next.start_ms)) : prev.start_ms;
                end_ms = Math.min(86400000, Math.max(start_ms + 500, next.start_ms));
            }
        } else {
            start_ms = Math.min(86399000, prev.end_ms);
            end_ms = Math.min(86400000, start_ms + 2000);
        }
        const insertAt = afterIdx + 1;
        cues.splice(insertAt, 0, {index: insertAt + 1, start_ms, end_ms, text: 'Phụ đề mới', raw_text: ''});
        cues.forEach((cue, i) => { cue.index = i + 1; });
        record.srtDirty = true;
        record.selectedCueIndex = insertAt;
        record.lastLiveSubtitleKey = '';
        setSrtStatus(`Đã thêm dòng #${insertAt + 1}. Bấm “Lưu SRT” để ghi xuống đĩa.`);
        renderSrtList();
        const newRow = record.srtRowByIndex?.[insertAt];
        const textarea = newRow?.children?.find?.(node => node.dataset?.srtField === 'text');
        textarea?.focus?.(); textarea?.select?.();
        renderLiveSubtitle();
    }
    function deleteSrtCue(index) {
        const record = current;
        if (!record || !record.srtCues?.length || record.srtLocked || running(record)) return;
        if (record.srtCues.length <= 1) {
            setSrtStatus('Không thể xoá dòng phụ đề cuối cùng (cần ít nhất 1 câu).', 'error');
            return;
        }
        if (index < 0 || index >= record.srtCues.length) return;
        record.srtCues.splice(index, 1);
        record.srtCues.forEach((cue, i) => { cue.index = i + 1; });
        record.srtDirty = true;
        record.activeCueIndex = -1;
        record.selectedCueIndex = Math.min(index, record.srtCues.length - 1);
        record.lastLiveSubtitleKey = '';
        setSrtStatus(`Đã xoá dòng #${index + 1}. Bấm “Lưu SRT” để ghi xuống đĩa.`);
        renderSrtList();
        syncVideoCueTime(false);
    }
    function replaceAllInSrt() {
        const record = current;
        if (!record || !record.srtCues?.length || record.srtLocked || running(record)) return;
        const needle = ($('editor-srt-search')?.value || '');
        if (!needle.trim()) return;
        const replacement = $('editor-srt-replace')?.value || '';
        const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(escaped, 'gi');
        let changedCues = 0, hits = 0;
        for (const cue of record.srtCues) {
            const updated = cue.text.replace(regex, () => { hits += 1; return replacement; });
            if (updated !== cue.text) { cue.text = updated; changedCues += 1; }
        }
        if (changedCues > 0) {
            record.srtDirty = true;
            record.lastLiveSubtitleKey = '';
            renderSrtList();
            renderLiveSubtitle();
            setSrtStatus(`Đã thay ${hits} vị trí ở ${changedCues} câu. Bấm “Lưu SRT” để áp dụng.`);
        } else {
            setSrtStatus('Không tìm thấy chuỗi khớp trong phụ đề.');
        }
    }
    function validateLocalSrt(cues) {
        if (!Array.isArray(cues) || !cues.length) return 'Danh sách phụ đề phải có ít nhất 1 câu.';
        let prevStart = -1;
        for (let i = 0; i < cues.length; i++) {
            const cue = cues[i];
            if (!Number.isInteger(cue.start_ms) || !Number.isInteger(cue.end_ms) || cue.start_ms < 0 || cue.end_ms <= cue.start_ms) {
                return `Câu #${i + 1}: mốc thời gian không hợp lệ.`;
            }
            if (cue.start_ms < prevStart) return `Câu #${i + 1}: mốc bắt đầu phải không nhỏ hơn câu trước.`;
            if (!String(cue.text || '').trim()) return `Câu #${i + 1}: nội dung không được để trống.`;
            prevStart = cue.start_ms;
        }
        return '';
    }
    async function saveSrt(record = current) {
        if (!record || !record.srtCues?.length || record.srtSaving || record.srtLocked || running(record)) return false;
        const localError = validateLocalSrt(record.srtCues);
        if (localError) { setSrtStatus(localError, 'error'); return false; }
        record.srtSaving = true;
        if (current === record) refreshSrtControls();
        try {
            const payload = record.srtCues.map((cue, idx) => ({
                index: idx + 1, start_ms: cue.start_ms, end_ms: cue.end_ms, text: cue.text,
            }));
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/srt`, {
                method: 'PUT', body: JSON.stringify({cues: payload}),
            });
            if (Array.isArray(data.cues)) {
                record.srtCues = data.cues.map((cue, idx) => ({
                    index: idx + 1, start_ms: Number(cue.start_ms) || 0, end_ms: Number(cue.end_ms) || 1000,
                    text: String(cue.text || ''), raw_text: typeof cue.raw_text === 'string' ? cue.raw_text : '',
                }));
            }
            record.srtHasBackup = Boolean(data.has_backup);
            record.srtDirty = false;
            record.srtInvalidCount = 0;
            record.lastLiveSubtitleKey = '';
            if (current === record) {
                setSrtStatus(`Đã lưu ${record.srtCues.length} câu phụ đề (đã sao lưu .bak).`);
                renderSrtList();
                renderLiveSubtitle();
                notify('Đã lưu phụ đề SRT. Các thao tác Render / TTS tiếp theo sẽ dùng bản SRT mới.');
            }
            return true;
        } catch (error) {
            if (current === record) { setSrtStatus(error.message, 'error'); notify(error.message, 'error'); }
            return false;
        } finally {
            record.srtSaving = false;
            if (current === record) refreshSrtControls();
        }
    }
    async function applyGlossaryToSrt() {
        const record = current;
        if (!record || !record.srtCues?.length || record.srtSaving || record.srtLocked || running(record)) return;
        const localError = validateLocalSrt(record.srtCues);
        if (localError) { setSrtStatus(localError, 'error'); return; }
        const glossaryId = $('editor-srt-glossary-select')?.value || editorActiveGlossaryId || 'default';
        record.srtSaving = true;
        refreshSrtControls();
        try {
            const payload = record.srtCues.map((cue, idx) => ({
                index: idx + 1, start_ms: cue.start_ms, end_ms: cue.end_ms, text: cue.text,
            }));
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/srt/apply-glossary`, {
                method: 'POST', body: JSON.stringify({glossary_id: glossaryId, cues: payload, save: false}),
            });
            if (Array.isArray(data.cues)) {
                record.srtCues = data.cues.map((cue, idx) => ({
                    index: idx + 1, start_ms: Number(cue.start_ms) || 0, end_ms: Number(cue.end_ms) || 1000,
                    text: String(cue.text || ''), raw_text: typeof cue.raw_text === 'string' ? cue.raw_text : '',
                }));
            }
            const changed = Number(data.changed_cues) || 0, reps = Number(data.replacements) || 0;
            if (changed > 0) record.srtDirty = true;
            record.lastLiveSubtitleKey = '';
            if (current === record) {
                renderSrtList();
                renderLiveSubtitle();
                setSrtStatus(changed > 0
                    ? `Đã áp dụng "${data.glossary_name || glossaryId}": sửa ${reps} thuật ngữ ở ${changed} câu. Bấm “Lưu SRT” để ghi xuống đĩa.`
                    : `Bộ từ điển "${data.glossary_name || glossaryId}" không có thuật ngữ nào cần thay đổi.`);
            }
        } catch (error) {
            if (current === record) setSrtStatus(error.message, 'error');
        } finally {
            record.srtSaving = false;
            if (current === record) refreshSrtControls();
        }
    }
    async function restoreSrtBackup() {
        const record = current;
        if (!record || !record.srtHasBackup || record.srtSaving || record.srtLocked || running(record)) return;
        if (typeof window.confirm === 'function' && !window.confirm('Hoàn tác phụ đề SRT về bản sao lưu (.bak) trước lần lưu gần nhất?')) return;
        record.srtSaving = true;
        refreshSrtControls();
        try {
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/srt/restore-backup`, {
                method: 'POST', body: '{}',
            });
            if (Array.isArray(data.cues)) {
                record.srtCues = data.cues.map((cue, idx) => ({
                    index: idx + 1, start_ms: Number(cue.start_ms) || 0, end_ms: Number(cue.end_ms) || 1000,
                    text: String(cue.text || ''), raw_text: typeof cue.raw_text === 'string' ? cue.raw_text : '',
                }));
            }
            record.srtDirty = false;
            record.srtInvalidCount = 0;
            record.activeCueIndex = -1;
            record.lastLiveSubtitleKey = '';
            if (current === record) {
                renderSrtList();
                syncVideoCueTime(false);
                setSrtStatus('Đã khôi phục phụ đề SRT từ bản sao lưu .bak.');
                notify('Đã hoàn tác phụ đề SRT từ bản sao lưu .bak.');
            }
        } catch (error) {
            if (current === record) setSrtStatus(error.message, 'error');
        } finally {
            record.srtSaving = false;
            if (current === record) refreshSrtControls();
        }
    }
    async function ensureSrtSaved(record) {
        if (!record?.srtDirty) return true;
        const ok = typeof window.confirm === 'function'
            ? window.confirm('Phụ đề SRT đang có thay đổi chưa lưu. Bấm OK để lưu SRT rồi chạy tiếp, hoặc Cancel để quay lại kiểm tra.')
            : false;
        if (!ok) {
            setSrtStatus('Hãy bấm “Lưu SRT” trước khi tạo TTS, Render video hoặc xuất CapCut Draft.', 'error');
            notify('Phụ đề SRT chưa được lưu; đã tạm dừng thao tác để tránh dùng bản phụ đề cũ.', 'error');
            return false;
        }
        return await saveSrt(record);
    }
    function endDrag() {
        if (!drag) return;
        const pointerId = drag.pointerId; drag = null;
        const rect = $('editor-crop-box');
        if (rect.hasPointerCapture?.(pointerId)) rect.releasePointerCapture(pointerId);
    }
    function pointerDown(event) {
        if (event.button !== 0 || !canEdit() || !validCrop(current.options.crop_box)) return;
        const content = sourceContent(); if (!content) return;
        event.preventDefault();
        drag = {record: current, pointerId: event.pointerId, x: event.clientX, y: event.clientY,
            box: {...current.options.crop_box}, handle: event.target.dataset.editorHandle || 'move', content};
        $('editor-crop-box').setPointerCapture?.(event.pointerId); $('editor-crop-box').focus({preventScroll: true});
    }
    function pointerMove(event) {
        if (!drag || drag.record !== current || drag.pointerId !== event.pointerId) return;
        const box = adjustCrop(drag.box, drag.handle, (event.clientX - drag.x) / drag.content.width, (event.clientY - drag.y) / drag.content.height);
        if (box) setCrop(box);
    }
    function cropKeys(event) {
        if (!canEdit() || !validCrop(current.options.crop_box) || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        const step = event.altKey ? .001 : .01, dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
        const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
        setCrop(adjustCrop(current.options.crop_box, event.shiftKey ? 'se' : 'move', dx, dy));
    }
    function readOption(control) {
        const key = control.dataset.editorOption;
        if (!canEdit() || !Object.hasOwn(defaults, key) || key === 'watermark_path') return false;
        const numeric = numericKeys.has(key), raw = numeric ? control.value : '';
        const value = control.type === 'checkbox' ? control.checked : numeric ? (raw.trim() ? Number(raw) : NaN) : control.value;
        if (Object.is(current.options[key], value) && (!numeric || !Object.hasOwn(current.numericDraft, key) || current.numericDraft[key] === raw)) return false;
        current.options[key] = value;
        if (key === 'change_audio_pitch' && Object.hasOwn(current.options, 'tts_change_pitch')) current.options.tts_change_pitch = value;
        if (numeric) {
            if (validateOptions({...current.options, [key]: value}).errors[key]) current.numericDraft[key] = raw;
            else delete current.numericDraft[key];
        }
        return true;
    }
    function providerChanged(clearVoice = true) {
        voiceSequence++; current.voicesLoading = false;
        if (clearVoice) current.options.tts_voice = '';
        renderVoices();
        $('editor-voices-status').textContent = current.options.tts_provider === 'edge_tts' ? 'Hai giọng Việt có sẵn; bấm nạp để kiểm tra danh sách mới.' : 'CapCut chỉ nạp giọng khi bạn chủ động bấm nút.';
    }
    function optionChanged(event) {
        if (!readOption(event.target)) return;
        if (event.target.dataset.editorOption === 'tts_provider') providerChanged();
        edited(); syncOverlay(); refreshControls();
    }
    function syncFormOptions() {
        if (!canEdit()) return;
        const provider = current.options.tts_provider;
        let changed = false;
        // Capture the visible form atomically, including browser-restored values without input/change.
        // Do not repaint the voice select until its current selection has also been read.
        for (const control of optionControls) if (readOption(control)) changed = true;
        if (!changed) return;
        if (provider !== current.options.tts_provider) providerChanged(false);
        edited(); syncOverlay(); refreshControls();
    }
    function renderVoices() {
        const select = $('editor-tts-voice'), provider = current?.options.tts_provider || defaults.tts_provider, chosen = current?.options.tts_voice || '';
        const rows = voiceCache[provider] || [], nodes = [];
        const add = (value, label) => { const node = document.createElement('option'); node.value = value; node.textContent = label; nodes.push(node); };
        add('', rows.length ? 'Chọn giọng đọc' : 'Bấm nạp danh sách giọng');
        for (const row of rows) add(row.id, row.name || row.id);
        if (chosen && !rows.some(row => row.id === chosen)) add(chosen, `${chosen} · đã lưu`);
        select.replaceChildren(...nodes); select.value = chosen;
    }
    async function loadVoices() {
        const record = current; if (!canEdit() || record.voicesLoading || record.busy) return;
        const provider = record.options.tts_provider, sequence = ++voiceSequence;
        const language = $('editor-tts-language')?.value || 'vi';
        record.voicesLoading = true; record.voiceRequest = sequence;
        refreshControls(); $('editor-voices-status').textContent = 'Đang nạp danh sách giọng theo yêu cầu…';
        try {
            const data = await request(`/api/editor/voices?provider=${encodeURIComponent(provider)}&language=${encodeURIComponent(language)}`, {timeoutMs: 30000});
            if (current !== record || sequence !== voiceSequence || record.options.tts_provider !== provider) return;
            if (!Array.isArray(data.voices)) throw new Error('Danh sách giọng không hợp lệ.');
            const seen = new Set();
            voiceCache[provider] = data.voices.filter(row => row && typeof row.id === 'string' && row.id && !seen.has(row.id) &&
                typeof row.name === 'string' && (!row.provider || row.provider === provider) && (seen.add(row.id), true)).slice(0, 1000);
            renderVoices();
            $('editor-voices-status').textContent = [`Đã nạp ${voiceCache[provider].length} giọng.`, ...(data.warnings || []).filter(value => typeof value === 'string')].join(' ');
        } catch (error) {
            if (current === record && sequence === voiceSequence) { $('editor-voices-status').textContent = error.message; notify(error.message, 'error'); }
        } finally {
            if (record.voiceRequest === sequence) record.voicesLoading = false;
            if (current === record) refreshControls();
        }
    }
    async function loadCapabilities() {
        const sequence = ++capabilitySequence; $('editor-runtime-refresh').disabled = true;
        try {
            const data = await request('/api/editor/capabilities');
            if (sequence !== capabilitySequence) return;
            if (typeof data.ffmpeg !== 'boolean' || typeof data.ffprobe !== 'boolean' || !data.encoders || !data.optional) throw new Error('Khả năng runtime Auto Edit không hợp lệ.');
            capabilities = data;
            if ($('editor-runtime-python')) $('editor-runtime-python').value = data.runtime_python || '';
            if ($('editor-model-dir')) $('editor-model-dir').value = data.separation_model_dir || '';
            if ($('editor-model-filename')) $('editor-model-filename').value = data.separation_model || 'UVR-MDX-NET-Inst_HQ_3.onnx';
            if ($('editor-capcut-compat')) $('editor-capcut-compat').checked = data.capcut_compat === true;
            if ($('editor-capcut-detail') && data.capcut?.detail) $('editor-capcut-detail').textContent = `${data.capcut.detail} Không tự lấy cookie hoặc bỏ qua xác minh; chỉ gửi text khi bạn yêu cầu tạo giọng.`;
            const encoders = Object.entries(data.encoders).filter(([, available]) => available === true).map(([name]) => name);
            $('editor-capabilities-summary').textContent = coreReady() ? 'FFmpeg sẵn sàng' : 'Thiếu FFmpeg / FFprobe';
            $('editor-runtime-core').textContent = `FFmpeg: ${data.ffmpeg ? 'có' : 'thiếu'} · FFprobe: ${data.ffprobe ? 'có' : 'thiếu'} · Encoder: ${encoders.join(', ') || 'không khả dụng'}`;
            for (const [key, prefix] of [['edge_tts', 'edge'], ['audio_separator', 'separator']]) {
                const runtime = data.optional[key] || {}, command = typeof runtime.install_command === 'string' ? runtime.install_command : '';
                $(`editor-runtime-${prefix}-detail`).textContent = `${runtime.available ? 'Sẵn sàng' : 'Chưa khả dụng'}${runtime.detail ? ` · ${String(runtime.detail)}` : ''}`;
                $(`editor-runtime-${prefix}-command`).value = command; $(`editor-runtime-${prefix}-copy`).disabled = !command;
            }
            for (const option of $('editor-encoder').options) if (['libx264', 'h264_nvenc'].includes(option.value)) option.disabled = data.encoders[option.value] !== true;
            if (!coreReady()) notify('Thiếu FFmpeg / FFprobe. Có thể lưu cấu hình; kiểm tra runtime trước khi xử lý.', 'error');
        } catch (error) {
            if (sequence !== capabilitySequence) return;
            capabilities = null; $('editor-capabilities-summary').textContent = 'Chưa kiểm tra được'; $('editor-runtime-core').textContent = error.message; notify(error.message, 'error');
        } finally {
            if (sequence === capabilitySequence) { $('editor-runtime-refresh').disabled = false; refreshControls(); }
        }
    }
    async function saveRuntime() {
        const button = $('editor-runtime-save'); if (!button || button.disabled) return;
        button.disabled = true;
        try {
            await request('/api/editor/runtime', {method: 'POST', body: JSON.stringify({
                python_path: $('editor-runtime-python').value.trim(), model_dir: $('editor-model-dir').value.trim(),
                model_filename: $('editor-model-filename').value.trim(), capcut_compat: $('editor-capcut-compat').checked === true,
            })});
            await loadCapabilities(); notify('Đã lưu runtime. Chưa cài package hoặc tải model; dịch vụ CapCut vẫn cần kiểm tra thực tế.');
        } catch (error) { notify(error.message, 'error'); }
        finally { button.disabled = false; }
    }
    async function copyCommand(prefix) {
        const input = $(`editor-runtime-${prefix}-command`); if (!input.value) return;
        try { await navigator.clipboard.writeText(input.value); notify('Đã sao chép lệnh. App không tự chạy hoặc cài đặt gói.'); }
        catch (_) { input.focus(); input.select(); notify('Không truy cập được clipboard. Lệnh đã được chọn; nhấn Ctrl+C để sao chép.', 'error'); }
    }
    function projectReceived(snapshot) {
        if (!snapshot?.manifest || typeof snapshot.job_id !== 'string' || !snapshot.job_id) return;
        if (!initialized) { pendingSnapshot = snapshot; return; }
        let record = records.get(snapshot.job_id);
        const changed = current !== record;
        if (!record) {
            const crop = validCrop(snapshot.manifest.crop_box) ? {...snapshot.manifest.crop_box} : null;
            record = {jobId: snapshot.job_id, snapshot, options: {...defaults, crop_box: crop}, dirty: false, revision: 0,
                loaded: false, loading: false, loadFailed: false, recoveryRequired: false, busy: '', cropError: '', cropDraft: null,
                numericDraft: {}, draftPersisted: false, lastCrop: crop, task: null, output: null, videoOutput: null, pollFailures: 0,
                srtCues: [], srtDirty: false, srtLoaded: false, srtLoading: false, srtSaving: false, srtHasRaw: false,
                srtHasBackup: false, srtLocked: false, srtLockReason: '', srtSearch: '', srtInvalidCount: 0,
                activeCueIndex: -1, selectedCueIndex: -1, srtRowByIndex: [], lastLiveSubtitleKey: '',
                hooks: [], hooksBusy: false};
            restoreDraft(record); records.set(record.jobId, record);
        }
        record.snapshot = snapshot;
        if (current !== record) {
            endDrag(); stopPolling(); voiceSequence++; hookSequence++; current = record; renderedOutputKey = null;
            if ($('editor-srt-search')) $('editor-srt-search').value = record.srtSearch || '';
            if ($('editor-hooks-status')) $('editor-hooks-status').textContent = '';
            writeForm(); paintTask(); paintOutput(); renderSrtList(); renderHooksList(record); syncVideoCueTime(false);
            if (record.task && running(record)) startPolling(record);
        } else { updateCropSummary(); refreshControls(); syncOverlay(); }
        $('editor-empty').hidden = true; $('editor-project').hidden = false;
        $('editor-project-status').textContent = snapshot.ready ? 'Project sẵn sàng cho Auto Edit.' : 'Project thiếu video hoặc phụ đề Việt.';
        if (!record.loaded && !record.loading) loadProject(record);
        else if (changed) notify(record.dirty ? 'Đã khôi phục bản chỉnh sửa chưa lưu của project.' : 'Đã nhận cấu hình Auto Edit của project.');
    }
    async function loadProject(record) {
        const sequence = record.loadSequence = (record.loadSequence || 0) + 1;
        record.loading = true; const revision = record.revision;
        if (current === record) refreshControls();
        try {
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}`);
            if (record.loadSequence !== sequence) return;
            if (data.snapshot?.job_id !== record.jobId || !data.snapshot.manifest) throw new Error('Cấu hình Auto Edit trả về không đúng project.');
            const validation = validateOptions(data.options), task = data.active_task ?? null, lastRender = data.last_render ?? null;
            if (Object.keys(validation.errors).length) throw new Error('Cấu hình Auto Edit đã lưu không hợp lệ. Hãy kiểm tra lại project.');
            if (task && !validTask(task, record.jobId)) throw new Error('Tác vụ đang chạy không đúng project.');
            if (lastRender && (!validTask(lastRender, record.jobId) || lastRender.action !== 'render' ||
                lastRender.status !== 'success' || lastRender.output_kind !== 'video')) throw new Error('Video đã render không đúng project.');
            record.snapshot = data.snapshot;
            if (record.revision === revision && !record.dirty) {
                record.options = validation.options; record.lastCrop = validation.options.crop_box;
                record.cropError = ''; record.cropDraft = null; record.numericDraft = {};
            }
            record.loaded = true; record.loadFailed = false; record.recoveryRequired = false; record.pollFailures = 0;
            // Preserve offscreen results too; painting alone depends on which project is visible.
            if (lastRender) acceptOutput(record, lastRender);
            if (task || !record.previewTask) { record.task = task; record.previewTask = false; }
            if (record.task) acceptOutput(record, record.task);
            persistDraft(record);
            if (current === record) {
                if (record.revision === revision && !record.dirty) writeForm();
                paintTask(); paintOutput();
                if (running(record)) startPolling(record); else stopPolling();
                notify(record.dirty ? 'Đã kiểm tra máy chủ và khôi phục tác vụ; bản chỉnh sửa chưa lưu vẫn được giữ.' :
                    'Đã nạp cấu hình. Render dùng các tuỳ chọn hiện tại; video nguồn không bị thay thế.');
            }
            if (record.snapshot.availability?.translated_srt === true && !record.srtLoaded) {
                loadSrt(record);
                loadEditorGlossaries();
            }
        } catch (error) {
            if (record.loadSequence !== sequence) return;
            // A network failure must not destroy a draft. Local defaults remain editable.
            record.loaded = true; record.loadFailed = true;
            if (current === record) notify(`${error.message} Bản chỉnh sửa hiện tại được giữ; bấm “Kiểm tra lại file” để thử lại.`, 'error');
        } finally {
            if (record.loadSequence === sequence) { record.loading = false; if (current === record) refreshControls(); }
        }
    }
    async function withAction(kind, action) {
        const record = current; if (!record || record.busy) return;
        record.busy = kind; refreshControls();
        try { await action(record); }
        catch (error) {
            if (error.uncertainMutation) {
                record.loadFailed = true; record.recoveryRequired = true; persistDraft(record);
            }
            if (current === record) notify(`${error.message}${error.uncertainMutation ? ' Phản hồi có thể đã mất sau khi máy chủ nhận; bấm “Kiểm tra lại file” để khôi phục tác vụ, không gửi lại.' : ''}`, 'error');
        }
        finally { record.busy = ''; if (current === record) refreshControls(); }
    }
    function checkedOptions() {
        syncFormOptions();
        const validation = checkForm();
        if (Object.keys(validation.errors).length) throw new Error('Kiểm tra các trường được đánh dấu trước khi tiếp tục.');
        // Every new UI request opts into voice-only pitch. Keep legacy DOM/data
        // keys for integrations and write the explicit alias alongside them.
        return {...validation.options, tts_change_pitch: validation.options.change_audio_pitch};
    }
    async function save() {
        if ($('editor-save-btn').disabled) return;
        return withAction('save', async record => {
            const options = checkedOptions(), revision = record.revision;
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}`, {method: 'POST', body: JSON.stringify({options})});
            const validation = validateOptions(data.options);
            if (Object.keys(validation.errors).length) throw new Error('Máy chủ trả về cấu hình đã lưu không hợp lệ.');
            if (record.revision === revision) {
                record.options = validation.options; record.dirty = false; record.cropError = ''; record.cropDraft = null; record.numericDraft = {};
                if (current === record) writeForm();
            }
            record.loadFailed = false; record.recoveryRequired = false; persistDraft(record);
            if (current === record) notify(record.dirty ? 'Đã lưu cấu hình trước đó; các thay đổi mới hơn vẫn chưa lưu.' : 'Đã lưu cấu hình Auto Edit.');
        });
    }
    async function launch(action) {
        syncFormOptions();
        const button = $(action === 'render' ? 'editor-render-btn' : action === 'tts' ? 'editor-tts-generate-btn' : 'editor-extract-btn');
        if (button.disabled) return;
        if ((action === 'render' || action === 'tts') && !(await ensureSrtSaved(current))) return;
        return withAction(action, async record => {
            const options = checkedOptions();
            if (action === 'tts' || (action === 'render' && options.tts_enabled)) requireVoice(options);
            const data = await request('/api/editor/jobs', {method: 'POST', body: JSON.stringify({job_id: record.jobId, action, options}), timeoutMs: 30000});
            const task = data.task || data;
            if (!validTask(task, record.jobId) || task.action !== action) throw new Error('Máy chủ trả về tác vụ Auto Edit không hợp lệ.');
            record.task = task; record.pollFailures = 0; record.previewTask = false; acceptOutput(record, task);
            if (current === record) { paintTask(); paintOutput(); startPolling(record); notify('Đã gửi tác vụ. Bạn có thể tiếp tục chỉnh bản nháp mà không ảnh hưởng tác vụ đang chạy.'); }
        });
    }
    function requireVoice(options) {
        if (typeof options.tts_voice !== 'string' || !options.tts_voice.trim()) { $('editor-tts-voice').focus(); throw new Error('Chọn giọng đọc. Với CapCut, bấm “Nạp danh sách giọng” trước.'); }
    }
    async function previewTts() {
        syncFormOptions();
        if ($('editor-tts-preview-btn').disabled) return;
        return withAction('preview', async record => {
            const options = checkedOptions(); requireVoice(options);
            const text = $('editor-tts-preview-text').value.trim();
            if (!text || text.length > 250) throw new Error('Nội dung nghe thử cần từ 1 đến 250 ký tự.');
            const data = await request('/api/editor/tts/preview', {method: 'POST', timeoutMs: 30000, body: JSON.stringify({text,
                voice: options.tts_voice, provider: options.tts_provider, speed: options.tts_speed, pitch: options.tts_pitch, allow_fallback: options.tts_fallback})});
            const task = data.task || data;
            if (!validTask(task, record.jobId, true)) throw new Error('Tác vụ nghe thử không hợp lệ.');
            record.task = task; record.previewTask = true; record.pollFailures = 0; acceptOutput(record, task);
            if (current === record) { paintTask(); paintOutput(); startPolling(record); notify('Đang tạo mẫu giọng; audio không tự phát.'); }
        });
    }
    async function previewAudio() {
        if ($('editor-audio-preview').disabled) return;
        if (current?.options?.tts_enabled && !(await ensureSrtSaved(current))) return;
        return withAction('audio_preview', async record => {
            const options = checkedOptions();
            if (options.tts_enabled) requireVoice(options);
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/audio-preview`, {
                method: 'POST', timeoutMs: 30000, body: JSON.stringify({options})});
            const task = data.task || data;
            if (!validTask(task, record.jobId) || task.action !== 'audio_preview') throw new Error('Mẫu nghe thử không đúng project.');
            record.task = task; record.previewTask = true; record.pollFailures = 0;
            if (current === record) { paintTask(); startPolling(record); notify(options.tts_enabled ? 'Đang tạo mẫu TTS 3 giây với cao độ và tốc độ hiện tại…' : 'Đang tạo mẫu âm thanh gốc 3 giây, giữ tông khi đổi tốc độ…'); }
        });
    }
    async function exportDraft() {
        if ($('editor-draft-btn').disabled) return;
        if (!(await ensureSrtSaved(current))) return;
        return withAction('capcut_draft', async record => {
            const options = checkedOptions();
            if (options.tts_enabled) requireVoice(options);
            const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/capcut-draft`, {
                method: 'POST', timeoutMs: 30000, body: JSON.stringify({options})});
            const task = data.task || data;
            if (!validTask(task, record.jobId) || task.action !== 'capcut_draft') throw new Error('Tác vụ Draft không hợp lệ.');
            record.task = task; record.previewTask = false; record.pollFailures = 0;
            if (current === record) { paintTask(); startPolling(record); notify('Đang chuẩn bị CapCut Draft với cấu hình đã chọn…'); }
        });
    }
    function stopPolling() { clearTimeout(pollTimer); pollTimer = 0; pollGeneration++; }
    function startPolling(record) {
        stopPolling();
        if (current !== record || !running(record)) return;
        const generation = pollGeneration, id = record.task.id;
        pollTimer = setTimeout(() => poll(record, id, generation), 700);
    }
    async function poll(record, id, generation) {
        pollTimer = 0;
        if (current !== record || generation !== pollGeneration || record.task?.id !== id || !running(record)) return;
        try {
            const data = await request(`/api/editor/jobs/${encodeURIComponent(id)}`), task = data.task || data;
            if (current !== record || generation !== pollGeneration || record.task?.id !== id) return;
            if (!validTask(task, record.jobId, record.previewTask) || task.id !== id) throw new Error('Tiến độ tác vụ không đúng project hoặc ID.');
            record.task = task; record.pollFailures = 0;
            if (!running(record) && record.srtLocked) {
                record.srtLocked = false; record.srtLockReason = ''; renderSrtList();
            }
            paintTask(); acceptOutput(record, task); paintOutput(); refreshControls();
            if (task.status === 'error') notify(task.error || 'Tác vụ Auto Edit thất bại.', 'error');
            else if (task.status === 'cancelled') notify('Đã huỷ tác vụ. Bản chỉnh sửa vẫn được giữ.');
            else if (task.status === 'success') {
                notify('Tác vụ Auto Edit hoàn tất. Kết quả nằm riêng, không thay video nguồn.');
                window.VietSubVFX?.complete?.({kind: task.action === 'render' || task.action === 'capcut_draft' ? 'render' : 'audio',
                    id: task.id, status: 'success', target: $('editor-output')});
                if (task.action === 'audio_preview') {
                    const playback = $('editor-output-audio').play?.();
                    playback?.catch?.(() => notify('Mẫu 3 giây đã sẵn sàng. Bấm Phát ở kết quả để nghe thử.'));
                }
            }
        } catch (error) {
            if (current !== record || generation !== pollGeneration || record.task?.id !== id) return;
            record.pollFailures++;
            notify(`${error.message} Không tạo lại tác vụ; ${record.pollFailures < 3 ? 'đang thử theo dõi lại.' : 'bấm “Theo dõi lại” để kiểm tra tác vụ cũ.'}`, 'error');
            $('editor-task-retry').hidden = record.pollFailures < 3;
        }
        if (current === record && generation === pollGeneration && record.task?.id === id && running(record) && record.pollFailures < 3) {
            pollTimer = setTimeout(() => poll(record, id, generation), record.pollFailures ? 2500 : 1000);
        }
    }
    // Old saved tasks may still carry provider boilerplate. Real errors/fallbacks remain visible.
    function actionableWarnings(values) {
        return Array.isArray(values) ? values.filter(value => typeof value === 'string' &&
            !/^CapCut uses an unofficial internal API; live entitlement and protocol stability are not guaranteed\.$/.test(value) &&
            !/^Edge TTS is an optional unofficial online provider; service availability is not guaranteed\.$/.test(value)) : [];
    }
    function paintTask() {
        const task = current?.task, active = running();
        const percent = task?.status === 'success' ? 100 : finite(task?.percent) ? clamp(task.percent, 0, 100) : 0;
        const phase = task?.phase || task?.stage || '';
        // Callback messages belong to synthesis; later FFmpeg phases can retain the last message in the task snapshot.
        const detail = active && String(phase).startsWith('tts') && phase !== 'tts_audio' && typeof task?.message === 'string' && task.message.trim() ? task.message : phase;
        $('editor-task-stage').textContent = task ? `${statuses[task.status]}${detail ? ` · ${String(detail)}` : ''}` : 'Chưa có tác vụ';
        $('editor-task-percent').textContent = `${Math.round(percent)}%`;
        $('editor-task-fps').textContent = finite(task?.fps) ? `${Number(task.fps.toFixed(1))} fps` : '— fps';
        $('editor-task-progress').setAttribute('aria-valuenow', String(Number(percent.toFixed(1))));
        $('editor-task-progress').setAttribute('aria-valuetext', `${Math.round(percent)}% · ${detail || statuses[task?.status] || 'Chưa có tác vụ'}`);
        $('editor-task-fill').style.width = `${percent}%`; $('editor-task-panel').setAttribute('aria-busy', String(active));
        $('editor-task-retry').hidden = !active || (current?.pollFailures || 0) < 3;
        const warnings = actionableWarnings(task?.warnings);
        if (task?.error && typeof task.error === 'string') warnings.push(task.error);
        const items = warnings.map(message => { const li = document.createElement('li'); li.textContent = message; return li; });
        $('editor-task-warnings').replaceChildren(...items); $('editor-task-warnings').hidden = !items.length;
    }
    function acceptOutput(record, task) {
        if (task.status !== 'success' || !['video', 'audio', 'archive'].includes(task.output_kind)) return;
        const url = safeOutputUrl(task.output_url, record.jobId, task.id);
        record.output = {...task, url};
        if (task.action === 'render' && task.output_kind === 'video') record.videoOutput = {...task, url};
    }
    function paintOutput() {
        const output = current?.output, key = output ? `${current.jobId}:${output.id}:${output.url}:${output.output_kind}` : '';
        if (key === renderedOutputKey) return;
        renderedOutputKey = key;
        $('editor-output').hidden = !output;
        const video = $('editor-output-video'), audio = $('editor-output-audio'), link = $('editor-output-link');
        for (const player of [video, audio]) { player.pause(); player.removeAttribute('src'); player.hidden = true; }
        link.hidden = !output?.url; link.removeAttribute('href');
        if (!output) return;
        if (output.url) link.href = output.url;
        if (output.url && output.output_kind !== 'archive') {
            const player = output.output_kind === 'video' ? video : audio;
            player.src = output.url; player.hidden = false; player.load(); link.href = output.url;
        }
        $('editor-output-path').textContent = `${output.output_path || 'Đã tạo kết quả.'}${output.url ? '' : ' Máy chủ không cung cấp URL xem kết quả an toàn.'}`;
    }
    async function cancelTask() {
        if ($('editor-cancel-btn').disabled) return;
        return withAction('cancel', async record => {
            const id = record.task.id;
            const data = await request(`/api/editor/jobs/${encodeURIComponent(id)}/cancel`, {method: 'POST', body: '{}'}), task = data.task || data;
            if (!validTask(task, record.jobId, record.previewTask) || task.id !== id) throw new Error('Phản hồi huỷ tác vụ không hợp lệ.');
            if (record.task?.id !== id) return;
            record.task = task;
            if (current === record) { paintTask(); startPolling(record); notify(task.status === 'cancelled' ? 'Đã huỷ tác vụ.' : 'Đã yêu cầu huỷ; đang chờ tác vụ dừng.'); }
        });
    }
    async function uploadWatermark() {
        const input = $('editor-watermark-file'), file = input.files?.[0];
        if (!file || input.disabled || !canEdit() || current.busy || running()) return;
        return withAction('watermark', async record => {
            try {
                if (!/\.png$/i.test(file.name) || (file.type && file.type !== 'image/png')) throw new Error('Chọn ảnh PNG cho logo.');
                const body = new FormData(); body.append('image', file);
                const data = await request(`/api/editor/projects/${encodeURIComponent(record.jobId)}/watermark`, {method: 'POST', body, timeoutMs: 60000});
                if (typeof data.watermark_path !== 'string' || !data.watermark_path || data.watermark_path.length > 4096) throw new Error('Máy chủ không trả về logo PNG hợp lệ.');
                record.options.watermark_path = data.watermark_path; record.options.watermark_enabled = true; edited(record);
                if (current === record) {
                    $('editor-watermark-path').value = data.watermark_path; $('editor-watermark-enabled').checked = true;
                    notify('Đã nhận logo PNG. Các tuỳ chọn khác và bản chỉnh sửa vẫn được giữ.');
                }
            } finally { input.value = ''; }
        });
    }
    async function sendToPublish() {
        if ($('editor-send-publish-btn').disabled) return;
        return withAction('handoff', async record => {
            const navigation = window.VietSubWorkbench?.workspaceState?.().revision;
            const snapshot = await request(`/api/projects/${encodeURIComponent(record.jobId)}/handoff`, {method: 'POST', body: JSON.stringify({workspace: 'uploader'})});
            if (snapshot.job_id !== record.jobId || !snapshot.manifest || snapshot.workspace !== 'uploader' || snapshot.ready !== true) throw new Error('Bàn giao Publish Hub không đúng project hoặc thiếu file.');
            window.VietSubWorkbench?.projectReceived('uploader', snapshot);
            if (current === record && navigation === window.VietSubWorkbench?.workspaceState?.().revision && !$('workbench-shell').inert) window.VietSubWorkbench?.selectWorkspace('uploader', true);
            if (current === record) notify('Đã bàn giao video đã render sang Publish Hub. Chưa upload lên nền tảng.');
        });
    }
    function init() {
        if (initialized || !$('workspace-editor')) return;
        initialized = true; readDrafts();
        window.addEventListener('beforeunload', beforeUnload);
        optionControls = [...$('editor-options-form').querySelectorAll('[data-editor-option]')];
        controlByKey = Object.fromEntries(optionControls.map(control => [control.dataset.editorOption, control]));
        for (const control of optionControls) for (const type of ['input', 'change']) control.addEventListener(type, optionChanged);
        $('editor-options-form').addEventListener('submit', event => { event.preventDefault(); save(); });
        for (const field of cropFields) for (const type of ['input', 'change']) $(`editor-crop-${field}`).addEventListener(type, cropInputChanged);
        $('editor-crop-enabled').addEventListener('change', event => setCrop(event.target.checked ? current?.lastCrop || {x: .04, y: .64, width: .92, height: .30} : null));
        $('editor-crop-reset').addEventListener('click', () => setCrop(validCrop(current?.snapshot.manifest.crop_box) ? current.snapshot.manifest.crop_box : null));
        $('editor-crop-lower').addEventListener('click', () => setCrop({x: .04, y: .64, width: .92, height: .30}));
        $('editor-crop-box').addEventListener('pointerdown', pointerDown); $('editor-crop-box').addEventListener('keydown', cropKeys);
        $('editor-crop-box').addEventListener('lostpointercapture', endDrag);
        window.addEventListener('pointermove', pointerMove); window.addEventListener('pointerup', endDrag); window.addEventListener('pointercancel', endDrag);
        window.addEventListener('blur', endDrag); window.addEventListener('resize', () => { endDrag(); syncOverlay(); });
        for (const type of ['loadedmetadata', 'loadeddata']) $('editor-video').addEventListener(type, () => { syncOverlay(); syncAudioControls(); syncVideoCueTime(false); });
        for (const type of ['timeupdate', 'seeked']) $('editor-video').addEventListener(type, () => syncVideoCueTime(true));
        $('editor-srt-add-btn')?.addEventListener('click', insertSrtCue);
        $('editor-srt-save-btn')?.addEventListener('click', () => saveSrt(current));
        $('editor-srt-restore-btn')?.addEventListener('click', restoreSrtBackup);
        $('editor-srt-replace-all-btn')?.addEventListener('click', replaceAllInSrt);
        $('editor-srt-apply-glossary-btn')?.addEventListener('click', applyGlossaryToSrt);
        $('editor-srt-search')?.addEventListener('input', event => {
            if (!current) return;
            current.srtSearch = event.target.value || '';
            renderSrtList();
        });
        document.addEventListener('vietsub:glossaries-updated', event => {
            if (event?.detail) populateEditorGlossaries(event.detail);
        });
        document.addEventListener('vietsub:apply-channel-preset', event => {
            if (event?.detail) applyChannelPreset(event.detail);
        });
        $('editor-suggest-hooks-btn')?.addEventListener('click', suggestHooks);
        $('editor-hooks-list')?.addEventListener('click', event => {
            const chip = event.target?.closest?.('[data-hook-text]') || (event.target?.dataset?.hookText ? event.target : null);
            if (!chip || !canEdit()) return;
            const input = $('editor-banner-text');
            input.value = chip.dataset.hookText;
            optionChanged({target: input});
            if ($('editor-hooks-status')) $('editor-hooks-status').textContent = 'Đã điền câu Hook vào Top Banner và bật xem trước.';
        });
        const srtList = $('editor-srt-list');
        if (srtList) {
            srtList.addEventListener('click', event => {
                const target = event.target?.closest?.('[data-srt-action]') || (event.target?.dataset?.srtAction ? event.target : null);
                if (!target || !current) return;
                const idx = Number(target.dataset.cueIndex);
                if (!Number.isInteger(idx) || idx < 0 || idx >= current.srtCues.length) return;
                if (target.dataset.srtAction === 'seek') {
                    current.selectedCueIndex = idx;
                    const video = $('editor-video');
                    video.currentTime = current.srtCues[idx].start_ms / 1000;
                    syncVideoCueTime(false);
                } else if (target.dataset.srtAction === 'delete') {
                    deleteSrtCue(idx);
                }
            });
            srtList.addEventListener('focusin', event => {
                const idx = Number(event.target?.dataset?.cueIndex);
                if (current && Number.isInteger(idx) && idx >= 0 && idx < current.srtCues.length) current.selectedCueIndex = idx;
            });
            srtList.addEventListener('input', event => {
                const target = event.target;
                if (!current || target?.dataset?.srtField !== 'text') return;
                const idx = Number(target.dataset.cueIndex);
                if (!Number.isInteger(idx) || idx < 0 || idx >= current.srtCues.length) return;
                current.srtCues[idx].text = target.value;
                current.srtDirty = true;
                current.lastLiveSubtitleKey = '';
                refreshSrtControls();
                renderLiveSubtitle();
            });
            srtList.addEventListener('change', event => {
                const target = event.target;
                const field = target?.dataset?.srtField;
                if (!current || (field !== 'start' && field !== 'end')) return;
                const idx = Number(target.dataset.cueIndex);
                if (!Number.isInteger(idx) || idx < 0 || idx >= current.srtCues.length) return;
                const parsed = parseSrtTimestamp(target.value);
                const cue = current.srtCues[idx];
                const prev = current.srtCues[idx - 1], next = current.srtCues[idx + 1];
                let error = '';
                if (parsed === null) error = `Câu #${idx + 1}: mốc thời gian phải có dạng HH:MM:SS,mmm.`;
                else if (field === 'start' && parsed >= cue.end_ms) error = `Câu #${idx + 1}: mốc bắt đầu phải nhỏ hơn mốc kết thúc.`;
                else if (field === 'end' && parsed <= cue.start_ms) error = `Câu #${idx + 1}: mốc kết thúc phải lớn hơn mốc bắt đầu.`;
                else if (field === 'start' && prev && parsed < prev.start_ms) error = `Câu #${idx + 1}: mốc bắt đầu phải không nhỏ hơn câu trước.`;
                else if (field === 'start' && next && parsed > next.start_ms) error = `Câu #${idx + 1}: mốc bắt đầu không được vượt quá câu kế tiếp.`;
                const wasInvalid = target.getAttribute('aria-invalid') === 'true';
                if (error) {
                    target.setAttribute('aria-invalid', 'true');
                    if (!wasInvalid) current.srtInvalidCount += 1;
                    setSrtStatus(error, 'error');
                    refreshSrtControls();
                    return;
                }
                if (wasInvalid) current.srtInvalidCount = Math.max(0, current.srtInvalidCount - 1);
                target.setAttribute('aria-invalid', 'false');
                if (field === 'start') cue.start_ms = parsed; else cue.end_ms = parsed;
                target.value = formatSrtTimestamp(parsed);
                current.srtDirty = true;
                current.lastLiveSubtitleKey = '';
                setSrtStatus(`Đã cập nhật mốc thời gian câu #${idx + 1}. Bấm “Lưu SRT” để ghi xuống đĩa.`);
                refreshSrtControls();
                syncVideoCueTime(false);
            });
        }
        for (const id of ['editor-output-video', 'editor-output-audio']) $(id).addEventListener('error', () => notify('Trình duyệt không phát được kết quả. File vẫn được giữ; dùng “Mở kết quả” để kiểm tra.', 'error'));
        $('editor-save-btn').addEventListener('click', save); $('editor-render-btn').addEventListener('click', () => launch('render'));
        $('editor-extract-btn').addEventListener('click', () => launch('extract_audio')); $('editor-tts-generate-btn').addEventListener('click', () => launch('tts'));
        $('editor-tts-preview-btn').addEventListener('click', previewTts); $('editor-load-voices-btn').addEventListener('click', loadVoices);
        $('editor-cancel-btn').addEventListener('click', cancelTask);
        $('editor-audio-preview')?.addEventListener('click', previewAudio);
        $('editor-draft-btn')?.addEventListener('click', exportDraft);
        for (const [id, key] of [['editor-speed-range', 'speed'], ['editor-pitch-range', 'pitch_semitones']]) {
            $(id)?.addEventListener('input', event => setAudioOption(key, Number(event.target.value)));
        }
        $('editor-isolate-vocals')?.addEventListener('change', event => setAudioOption('audio_mode', event.target.checked ? 'instrumental' : 'original'));
        $('editor-options-form').addEventListener('click', event => {
            const chip = event.target.closest?.('[data-pitch-preset]');
            if (chip && !chip.disabled) setAudioOption('pitch_semitones', Number(chip.dataset.pitchPreset));
            const preset = event.target.closest?.('[data-editor-preset]');
            if (preset && !preset.disabled && Object.hasOwn(choices, preset.dataset.editorPreset)) setAudioOption(preset.dataset.editorPreset, preset.dataset.presetValue);
        });
        $('editor-task-retry').addEventListener('click', () => { if (current) { current.pollFailures = 0; paintTask(); startPolling(current); } });
        $('editor-watermark-file').addEventListener('change', uploadWatermark); $('editor-send-publish-btn').addEventListener('click', sendToPublish);
        $('settings-editor-runtime-btn')?.addEventListener('click', () => {
            $('settings-close-btn').click();
            window.VietSubWorkbench?.selectWorkspace('editor', true);
            $('editor-runtime').open = true;
            $('editor-runtime').scrollIntoView({block: 'nearest'});
            $('editor-runtime-python')?.focus({preventScroll: true});
        });
        $('editor-runtime-refresh').addEventListener('click', loadCapabilities);
        $('editor-runtime-save')?.addEventListener('click', saveRuntime);
        for (const prefix of ['edge', 'separator']) $(`editor-runtime-${prefix}-copy`).addEventListener('click', () => copyCommand(prefix));
        $('editor-refresh-project').addEventListener('click', () => {
            if (!current) return;
            const promise = loadProject(current);
            if (!current.srtDirty && current.snapshot?.availability?.translated_srt === true) loadSrt(current, {force: true});
            return promise;
        });
        if (typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(() => { endDrag(); syncOverlay(); }); observer.observe($('editor-source-surface')); }
        refreshControls(); loadCapabilities();
        if (pendingSnapshot) { const snapshot = pendingSnapshot; pendingSnapshot = null; projectReceived(snapshot); }
    }
    window.VietSubEditor = {init, projectReceived,
        state: () => ({job_id: current?.jobId || '', options: current ? clone(current.options) : null, dirty: Boolean(current?.dirty),
            task: current?.task ? clone(current.task) : null, loading: Boolean(current?.loading), busy: current?.busy || '',
            revision: current?.revision || 0, loadFailed: Boolean(current?.loadFailed), recoveryRequired: Boolean(current?.recoveryRequired),
            cropDraft: current?.cropDraft ? {...current.cropDraft} : null, cropError: current?.cropError || '',
            numericDraft: current ? {...current.numericDraft} : {}, draftPersisted: Boolean(current?.draftPersisted),
            output: current?.output ? clone(current.output) : null, videoOutput: current?.videoOutput ? clone(current.videoOutput) : null,
            srtDirty: Boolean(current?.srtDirty), srtCount: current?.srtCues?.length || 0,
            activeCueIndex: current ? current.activeCueIndex : -1, srtHasBackup: Boolean(current?.srtHasBackup)}),
        helpers: {actionableWarnings, estimatedDuration, defaults, validateOptions, validCrop, containRect, adjustCrop, safeOutputUrl, validTask, draftStorageKey, validatedDraft,
            formatSrtTimestamp, parseSrtTimestamp, findActiveCueIndex, sliceWordsBalanced}};
    window.addEventListener('load', init);
})();
