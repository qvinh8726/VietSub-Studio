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
        banner_text: '', banner_position: 'top', watermark_enabled: false, watermark_path: '', watermark_opacity: .7,
        watermark_corner: 'top_right', reframe: 'original', output_height: 1280, encoder: 'auto',
        audio_mode: 'original', audio_format: 'wav', bgm_volume: .35, tts_enabled: false,
        tts_provider: 'capcut_internal', tts_voice: '', tts_speed: 1, tts_pitch: 0, tts_fallback: true,
    });
    const choices = {
        mask_mode: ['none', 'boxblur', 'delogo', 'solid_bar'], subtitle_preset: ['standard', 'anime', 'social'],
        font: ['Be Vietnam Pro', 'Noto Sans SC', 'JetBrains Mono'], subtitle_position: ['crop', 'bottom', 'top'],
        banner_position: ['top', 'bottom'], watermark_corner: ['top_right', 'top_left', 'bottom_right', 'bottom_left'],
        reframe: ['original', 'vertical'], output_height: [720, 1280, 1920], encoder: ['auto', 'libx264', 'h264_nvenc'],
        audio_mode: ['original', 'mute', 'instrumental'], audio_format: ['wav', 'mp3'], tts_provider: ['capcut_internal', 'edge_tts'],
    };
    const ranges = {blur_strength: [1, 40], padding_px: [0, 100], solid_opacity: [0, 1], font_size: [12, 120],
        outline: [0, 10], shadow: [0, 10], watermark_opacity: [0, 1], bgm_volume: [0, 2], tts_speed: [.5, 2], tts_pitch: [-12, 12]};
    const booleans = ['subtitle_enabled', 'watermark_enabled', 'tts_enabled', 'tts_fallback'];
    const colors = ['solid_color', 'text_color', 'back_color'];
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

    function validCrop(box) {
        return Boolean(box && typeof box === 'object' && !Array.isArray(box) && cropFields.every(key => finite(box[key])) &&
            box.x >= 0 && box.y >= 0 && box.width > 0 && box.height > 0 && box.x + box.width <= 1.000001 && box.y + box.height <= 1.000001);
    }
    function validateOptions(value) {
        const options = {...defaults}, errors = {};
        if (!value || typeof value !== 'object' || Array.isArray(value)) return {options, errors: {_form: 'Cấu hình phải là một đối tượng.'}};
        for (const key of Object.keys(defaults)) if (Object.hasOwn(value, key)) options[key] = value[key];
        for (const [key, values] of Object.entries(choices)) if (!values.includes(options[key])) errors[key] = 'Lựa chọn không hợp lệ.';
        for (const [key, [min, max]] of Object.entries(ranges)) {
            if (!finite(options[key]) || options[key] < min || options[key] > max) errors[key] = `Nhập số từ ${min} đến ${max}.`;
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
        if (!drafts.length) return;
        event.preventDefault(); event.returnValue = '';
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
            ['render', 'extract_audio', 'tts', 'tts_preview', 'preview'].includes(task.action));
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
    function checkForm() {
        const validation = validateOptions(current?.options || defaults), errors = validation.errors;
        if (current?.cropError) errors.crop_box = current.cropError;
        for (const control of optionControls) {
            const error = errors[control.dataset.editorOption];
            control.setAttribute('aria-invalid', String(Boolean(error)));
            control.setAttribute('aria-describedby', 'editor-form-error');
        }
        const cropError = errors.crop_box || (current?.options.crop_box === null && current.options.mask_mode !== 'none' ? 'Bật vùng che hoặc chọn “Không che” trước khi lưu.' : '');
        if (cropError) errors.crop_box = cropError;
        $('editor-crop-error').textContent = cropError;
        for (const field of cropFields) $(`editor-crop-${field}`).setAttribute('aria-invalid', String(Boolean(cropError)));
        $('editor-form-error').textContent = Object.entries(errors).filter(([key]) => key !== 'crop_box')
            .map(([key, message]) => `${controlByKey[key]?.labels?.[0]?.textContent?.trim() || key}: ${message}`).join(' ');
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
        const content = sourceContent(), box = current?.options.crop_box, layer = $('editor-crop-layer'), rect = $('editor-crop-box');
        layer.hidden = !content || !validCrop(box) || $('editor-video').hidden;
        if (layer.hidden) return;
        for (const [key, value] of Object.entries({left: content.left, top: content.top, width: content.width, height: content.height})) layer.style[key] = `${value}px`;
        for (const [key, value] of Object.entries({left: box.x, top: box.y, width: box.width, height: box.height})) rect.style[key] = `${value * 100}%`;
        rect.setAttribute('aria-label', `Vùng che: X ${(box.x * 100).toFixed(1)}%, Y ${(box.y * 100).toFixed(1)}%, rộng ${(box.width * 100).toFixed(1)}%, cao ${(box.height * 100).toFixed(1)}%`);
        rect.dataset.maskMode = current.options.mask_mode;
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
    function optionChanged(event) {
        const control = event.target, key = control.dataset.editorOption;
        if (!canEdit() || !Object.hasOwn(defaults, key) || key === 'watermark_path') return;
        const numeric = numericKeys.has(key), raw = numeric ? control.value : '';
        const value = control.type === 'checkbox' ? control.checked : numeric ? (raw.trim() ? Number(raw) : NaN) : control.value;
        if (Object.is(current.options[key], value) && (!numeric || !Object.hasOwn(current.numericDraft, key) || current.numericDraft[key] === raw)) return;
        current.options[key] = value;
        if (numeric) {
            if (validateOptions({...current.options, [key]: value}).errors[key]) current.numericDraft[key] = raw;
            else delete current.numericDraft[key];
        }
        if (key === 'tts_provider') {
            voiceSequence++; current.voicesLoading = false; current.options.tts_voice = ''; renderVoices();
            $('editor-voices-status').textContent = value === 'edge_tts' ? 'Hai giọng Việt có sẵn; bấm nạp để kiểm tra danh sách mới.' : 'CapCut chỉ nạp giọng khi bạn chủ động bấm nút.';
        }
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
                numericDraft: {}, draftPersisted: false, lastCrop: crop, task: null, output: null, videoOutput: null, pollFailures: 0};
            restoreDraft(record); records.set(record.jobId, record);
        }
        record.snapshot = snapshot;
        if (current !== record) {
            endDrag(); stopPolling(); voiceSequence++; current = record; renderedOutputKey = null;
            writeForm(); paintTask(); paintOutput();
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
        const validation = checkForm();
        if (Object.keys(validation.errors).length) throw new Error('Kiểm tra các trường được đánh dấu trước khi tiếp tục.');
        return validation.options;
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
        const button = $(action === 'render' ? 'editor-render-btn' : action === 'tts' ? 'editor-tts-generate-btn' : 'editor-extract-btn');
        if (button.disabled) return;
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
            paintTask(); acceptOutput(record, task); paintOutput(); refreshControls();
            if (task.status === 'error') notify(task.error || 'Tác vụ Auto Edit thất bại.', 'error');
            else if (task.status === 'cancelled') notify('Đã huỷ tác vụ. Bản chỉnh sửa vẫn được giữ.');
            else if (task.status === 'success') notify('Tác vụ Auto Edit hoàn tất. Kết quả nằm riêng, không thay video nguồn.');
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
    function paintTask() {
        const task = current?.task, active = running();
        const percent = task?.status === 'success' ? 100 : finite(task?.percent) ? clamp(task.percent, 0, 100) : 0;
        $('editor-task-stage').textContent = task ? `${statuses[task.status]}${task.phase ? ` · ${String(task.phase)}` : ''}` : 'Chưa có tác vụ';
        $('editor-task-percent').textContent = `${Math.round(percent)}%`;
        $('editor-task-fps').textContent = finite(task?.fps) ? `${Number(task.fps.toFixed(1))} fps` : '— fps';
        $('editor-task-progress').setAttribute('aria-valuenow', String(Number(percent.toFixed(1))));
        $('editor-task-progress').setAttribute('aria-valuetext', `${Math.round(percent)}% · ${task?.phase || statuses[task?.status] || 'Chưa có tác vụ'}`);
        $('editor-task-fill').style.width = `${percent}%`; $('editor-task-panel').setAttribute('aria-busy', String(active));
        $('editor-task-retry').hidden = !active || (current?.pollFailures || 0) < 3;
        const warnings = Array.isArray(task?.warnings) ? task.warnings.filter(value => typeof value === 'string') : [];
        if (task?.error && typeof task.error === 'string') warnings.push(task.error);
        const items = warnings.map(message => { const li = document.createElement('li'); li.textContent = message; return li; });
        $('editor-task-warnings').replaceChildren(...items); $('editor-task-warnings').hidden = !items.length;
    }
    function acceptOutput(record, task) {
        if (task.status !== 'success' || !['video', 'audio'].includes(task.output_kind)) return;
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
        if (output.url) {
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
        for (const type of ['loadedmetadata', 'loadeddata']) $('editor-video').addEventListener(type, syncOverlay);
        for (const id of ['editor-output-video', 'editor-output-audio']) $(id).addEventListener('error', () => notify('Trình duyệt không phát được kết quả. File vẫn được giữ; dùng “Mở kết quả” để kiểm tra.', 'error'));
        $('editor-save-btn').addEventListener('click', save); $('editor-render-btn').addEventListener('click', () => launch('render'));
        $('editor-extract-btn').addEventListener('click', () => launch('extract_audio')); $('editor-tts-generate-btn').addEventListener('click', () => launch('tts'));
        $('editor-tts-preview-btn').addEventListener('click', previewTts); $('editor-load-voices-btn').addEventListener('click', loadVoices);
        $('editor-cancel-btn').addEventListener('click', cancelTask);
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
        $('editor-refresh-project').addEventListener('click', () => { if (current) return loadProject(current); });
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
            output: current?.output ? clone(current.output) : null, videoOutput: current?.videoOutput ? clone(current.videoOutput) : null}),
        helpers: {defaults, validateOptions, validCrop, containRect, adjustCrop, safeOutputUrl, validTask, draftStorageKey, validatedDraft}};
    window.addEventListener('load', init);
})();
