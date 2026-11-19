/* Presentation-only adapter. Business handlers, API requests and crop state stay in app.js. */
(() => {
    'use strict';
    const STORAGE_KEY = 'vietsub.studio.workbench.v1';
    const $ = id => document.getElementById(id);
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
    function validLayout(value) {
        return Boolean(value && typeof value === 'object' && Object.keys(value).length === 4 && value.version === 1 &&
            positive(value.sidebar_ratio) && value.sidebar_ratio < 1 &&
            positive(value.console_ratio) && value.console_ratio < 1 &&
            typeof value.console_collapsed === 'boolean');
    }
    function layoutPixels(layout, width, height) {
        // Keep a useful centre stage at every desktop width, including restored older layouts.
        const minSidebar = width * .16;
        const maxSidebar = width * .185;
        const maxConsole = Math.max(36, height * .4);
        return {
            sidebar: clamp(width * layout.sidebar_ratio, minSidebar, maxSidebar),
            console: layout.console_collapsed ? 36 : clamp(height * layout.console_ratio, Math.min(112, maxConsole), maxConsole),
            minSidebar, maxSidebar, maxConsole,
        };
    }
    function fitRect(width, height, sourceWidth, sourceHeight) {
        if (![width, height, sourceWidth, sourceHeight].every(positive)) return {width: 0, height: 0};
        const scale = Math.min(width / sourceWidth, height / sourceHeight);
        return {width: sourceWidth * scale, height: sourceHeight * scale};
    }
    function formatTime(seconds) {
        const ms = Math.max(0, Math.round((Number.isFinite(seconds) ? seconds : 0) * 1000));
        const hours = Math.floor(ms / 3600000);
        const minutes = Math.floor(ms / 60000) % 60, secs = Math.floor(ms / 1000) % 60;
        return `${hours ? String(hours).padStart(2, '0') + ':' : ''}${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`;
    }
    function frameTime(time, direction, fps, duration) {
        if (!positive(fps) || !positive(duration)) return null;
        return clamp((Math.round(time * fps) + direction) / fps, 0, Math.max(0, duration - 1 / fps));
    }
    let initialized = false, adapter = {}, layout = null, layoutDirty = false, hasStoredLayout = false;
    let saveChain = Promise.resolve(), saveSequence = 0, fitRaf = 0, resizeObserver = null;
    let sourceMode = 'link', consoleTab = 'log', preview = null, mediaFailed = false;
    let drawerOpen = false, drawerTimer = 0, drawerAnimation = null, restoreFocus = null;
    let splitDrag = null, motionSaved = true, motionSaving = false, lastResult = '';
    let workspace = 'vietsub', workspaceRevision = 0;
    const workspaceNames = ['vietsub', 'editor', 'uploader'];
    const projects = {editor: null, uploader: null};
    const downloads = {preview: null, job: null};
    function dimensions() {
        const shell = $('workbench-shell');
        return {width: shell.clientWidth, height: Math.max(100, shell.clientHeight - 52 - ($('update-banner').hidden ? 0 : $('update-banner').offsetHeight))};
    }
    function readLayout() {
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
            if (validLayout(saved)) { hasStoredLayout = true; return saved; }
        } catch (_) { /* Private browsing, unavailable storage or an older layout: use defaults. */ }
        return {version: 1, sidebar_ratio: .18, console_ratio: .14, console_collapsed: false};
    }
    function localSave() {
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout)); } catch (_) {}
    }
    function applyLayout() {
        if (!initialized) return;
        const {width, height} = dimensions(), px = layoutPixels(layout, width, height);
        const shell = $('workbench-shell');
        shell.style.setProperty('--sidebar-width', `${px.sidebar}px`);
        shell.style.setProperty('--console-height', `${px.console}px`);
        shell.classList.toggle('console-collapsed', layout.console_collapsed);
        const toggle = $('console-toggle-btn');
        toggle.setAttribute('aria-expanded', String(!layout.console_collapsed));
        toggle.setAttribute('aria-label', layout.console_collapsed ? 'Mở rộng bảng nhật ký' : 'Thu gọn bảng nhật ký');
        toggle.title = layout.console_collapsed ? 'Mở rộng nhật ký' : 'Thu gọn nhật ký';
        toggle.textContent = layout.console_collapsed ? 'Mở rộng' : 'Thu gọn';
        const side = $('sidebar-resizer'), bottom = $('console-resizer');
        for (const [el, min, max, value] of [[side, px.minSidebar, px.maxSidebar, px.sidebar], [bottom, 36, px.maxConsole, px.console]]) {
            el.setAttribute('aria-valuemin', String(Math.round(min)));
            el.setAttribute('aria-valuemax', String(Math.round(max)));
            el.setAttribute('aria-valuenow', String(Math.round(value)));
            el.setAttribute('aria-valuetext', `${Math.round(value)} pixel`);
        }
        scheduleFit();
    }
    function saveLayout() {
        layoutDirty = true; localSave();
        const saved = {...layout}, sequence = ++saveSequence;
        // Serialize preference writes so a slow earlier request cannot overwrite the last drag.
        saveChain = saveChain.then(() => adapter.request('/api/settings', {
            method: 'POST', body: JSON.stringify({workbench_layout: saved}),
        })).catch(error => {
            if (sequence === saveSequence) adapter.notify(`Bố cục đã lưu trên trình duyệt; chưa lưu được vào app: ${error.message}`, 'error');
        });
        return saveChain;
    }
    function setCollapsed(value, persist = true) {
        if (value && $('console-body').contains(document.activeElement)) $('console-toggle-btn').focus();
        layout.console_collapsed = value; applyLayout();
        if (persist) saveLayout();
    }
    function selectConsole(tab, expand = false) {
        consoleTab = tab === 'result' ? 'result' : 'log';
        const moveFocus = ['log', 'result'].some(name => name !== consoleTab && $(`console-${name}-panel`).contains(document.activeElement));
        for (const name of ['log', 'result']) {
            const selected = name === consoleTab, button = $(`console-${name}-tab`), panel = $(`console-${name}-panel`);
            button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
            panel.hidden = !selected; panel.inert = !selected;
        }
        if (moveFocus && !drawerOpen) $(`console-${consoleTab}-tab`).focus();
        if (expand) setCollapsed(false, false);
    }
    function selectSource(mode, animate = false) {
        sourceMode = mode === 'local' ? 'local' : 'link';
        for (const name of ['link', 'local']) {
            const selected = name === sourceMode, button = $(`source-${name}-tab`), panel = $(`source-${name}-panel`);
            if (!selected && panel.contains(document.activeElement) && workspace === 'vietsub' && !drawerOpen) $(`source-${sourceMode}-tab`).focus();
            button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
            panel.hidden = !selected; panel.inert = !selected;
        }
        if (animate) window.VietSubVFX?.smoke($(`source-${sourceMode}-tab`));
    }
    function bindTabs(names, prefix, choose) {
        names.forEach((name, index) => {
            const el = $(`${prefix}-${name}-tab`);
            el.addEventListener('click', () => choose(name, true));
            el.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? names.length - 1 :
                    (index + (event.key === 'ArrowRight' ? 1 : -1) + names.length) % names.length;
                choose(names[next], true); $(`${prefix}-${names[next]}-tab`).focus();
            });
        });
    }
    const inspectorNames = {editor: ['frame', 'audio', 'subtitle', 'export'], uploader: ['accounts', 'publish']};
    const inspectorSelection = {editor: 'frame', uploader: 'accounts'};
    function selectInspector(name, panel, focus = false) {
        if (!initialized || !inspectorNames[name]?.includes(panel)) return false;
        const previous = inspectorSelection[name], prefix = `${name}-inspector`;
        const previousPanel = $(`${prefix}-${previous}-panel`);
        const moveFocus = focus || previousPanel.contains(document.activeElement);
        inspectorSelection[name] = panel;
        for (const item of inspectorNames[name]) {
            const selected = item === panel, tab = $(`${prefix}-${item}-tab`), content = $(`${prefix}-${item}-panel`);
            tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
            content.hidden = !selected; content.inert = !selected;
        }
        if (moveFocus && workspace === name && !drawerOpen) $(`${prefix}-${panel}-tab`).focus({preventScroll: true});
        if (previous !== panel) document.dispatchEvent(new CustomEvent('vietsub:inspector-change', {
            detail: {panel: `${prefix}-${panel}-panel`, previous: `${prefix}-${previous}-panel`},
        }));
        return true;
    }
    function bindInspectors() {
        for (const [name, items] of Object.entries(inspectorNames)) {
            bindTabs(items, `${name}-inspector`, (panel, focus) => selectInspector(name, panel, focus));
            selectInspector(name, inspectorSelection[name]);
        }
    }
    function workspacePanel(name) { return $(name === 'vietsub' ? 'workbench' : `workspace-${name}`); }
    function canFocus(element) {
        if (!element?.isConnected || element.disabled || !element.getClientRects().length) return false;
        for (let node = element; node; node = node.parentElement) if (node.hidden || node.inert) return false;
        return true;
    }
    function selectWorkspace(name, focus = false) {
        if (!initialized || !workspaceNames.includes(name)) return false;
        const previous = workspacePanel(workspace), changed = name !== workspace;
        const moveFocus = focus || (changed && previous.contains(document.activeElement));
        if (changed || focus) workspaceRevision++;
        if (changed) {
            finishSplit(); adapter.endCropDrag();
            // Do not touch VietSub's src, currentTime, crop or source mode. Only staging media pauses.
            if (workspace !== 'vietsub') $(`${workspace}-video`).pause();
        }
        workspace = name;
        $('workbench-shell').dataset.workspace = name;
        for (const item of workspaceNames) {
            const selected = item === name, panel = workspacePanel(item), button = $(`workspace-${item}-tab`);
            panel.hidden = !selected; panel.inert = !selected;
            button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
        }
        if (moveFocus && !drawerOpen) $(`workspace-${name}-tab`).focus({preventScroll: true});
        scheduleFit();
        if (changed) document.dispatchEvent(new CustomEvent('vietsub:workspace-change', {detail: {panel: workspacePanel(name).id, previous: previous.id === 'workbench' ? 'vietsub' : previous.id.replace('workspace-', '')}}));
        return true;
    }
    function bindWorkspaces() {
        workspaceNames.forEach((name, index) => {
            const button = $(`workspace-${name}-tab`);
            button.addEventListener('click', () => selectWorkspace(name, true));
            button.addEventListener('keydown', event => {
                if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? workspaceNames.length - 1 :
                    (index + (event.key === 'ArrowDown' ? 1 : -1) + workspaceNames.length) % workspaceNames.length;
                selectWorkspace(workspaceNames[next], true);
            });
        });
        $('preview-skip-link').addEventListener('click', event => {
            event.preventDefault(); selectWorkspace('vietsub'); $('preview-viewport').focus();
        });
        for (const name of ['editor', 'uploader']) {
            $(`${name}-back-source`).addEventListener('click', () => selectWorkspace('vietsub', true));
            $(`${name}-refresh-project`).addEventListener('click', () => adapter.refreshProject?.(name, projects[name]?.job_id));
            $(`${name}-video`).addEventListener('error', () => {
                $(`${name}-video-status`).textContent = 'Không phát được video trong trình duyệt này. File và thông tin project vẫn được giữ; thử kiểm tra lại file.';
            });
        }
    }
    function cropText(box) {
        if (!box) return 'Chưa có vùng OCR trong manifest';
        const {x, y, width, height} = box;
        if (![x, y, width, height].every(Number.isFinite) || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1.000001 || y + height > 1.000001) return 'Crop không hợp lệ (cần toạ độ nguồn 0–1)';
        return `x ${x.toFixed(4)} · y ${y.toFixed(4)} · rộng ${width.toFixed(4)} · cao ${height.toFixed(4)}`;
    }
    function projectVideoUrl(value, jobId) {
        if (typeof value !== 'string' || !value || !jobId) return '';
        try {
            const base = new URL(window.location?.href || 'http://localhost/'), url = new URL(value, base);
            if (url.origin !== base.origin || url.username || url.password || url.hash ||
                url.pathname !== `/api/projects/${encodeURIComponent(jobId)}/video`) return '';
            return url.pathname + url.search;
        } catch (_) { return ''; }
    }
    function projectStatus(name, message, state = '') {
        if (!initialized || !projects.hasOwnProperty(name)) return;
        const status = $(`${name}-project-status`);
        status.textContent = message; status.dataset.state = state;
        workspacePanel(name).setAttribute('aria-busy', String(state === 'loading'));
        $(`${name}-refresh-project`).disabled = state === 'loading' || !projects[name];
    }
    function projectReceived(name, snapshot) {
        if (!initialized || !projects.hasOwnProperty(name) || !snapshot?.manifest) return;
        projects[name] = snapshot;
        const manifest = snapshot.manifest, assets = manifest.assets || {}, metadata = manifest.metadata || {}, source = manifest.source || {};
        const text = (field, value) => { $(`${name}-${field}`).textContent = typeof value === 'string' && value ? value : 'Chưa có'; };
        text('project-name', manifest.project_name || manifest.project_id);
        text('project-id', `${snapshot.job_id} / ${manifest.project_id || '—'}`);
        text('source', [source.url, source.video_id, source.original_video].filter(value => typeof value === 'string' && value).join(' · '));
        const selectedVideo = name === 'uploader' && assets.edited_video ? assets.edited_video : assets.video || assets.source_video;
        text('video-path', snapshot.availability?.video === true ? selectedVideo : 'Video không còn khả dụng · không thể edit / upload');
        text('srt', snapshot.availability?.translated_srt === true ? assets.translated_srt : 'Phụ đề Việt không còn khả dụng');
        text('crop', cropText(manifest.crop_box));
        const resolution = manifest.source_resolution;
        text('resolution', positive(resolution?.width) && positive(resolution?.height) ? `${resolution.width} × ${resolution.height}` : 'Chưa có độ phân giải nguồn');
        text('manifest', snapshot.manifest_path);
        text('title', metadata.title); text('description', metadata.description);
        text('hashtags', Array.isArray(metadata.hashtags) ? metadata.hashtags.filter(value => typeof value === 'string').join(' ') : metadata.hashtags);
        const stageNames = {vietsub: 'VietSub', editor: 'Auto Edit', uploader: 'Đăng đa nền tảng'};
        const statuses = {success: 'Hoàn tất', not_started: 'Chưa bắt đầu', ready: 'Sẵn sàng', queued: 'Đang chờ', running: 'Đang chạy', error: 'Lỗi', cancelled: 'Đã huỷ'};
        text('stages', Object.entries(stageNames).map(([key, label]) => `${label}: ${statuses[manifest.stages?.[key]?.status] || manifest.stages?.[key]?.status || 'Chưa bắt đầu'}`).join(' · '));
        const player = $(`${name}-video`), url = snapshot.availability?.video === true ? projectVideoUrl(snapshot.video_url, snapshot.job_id) : '';
        if (player.dataset.projectUrl !== url) {
            player.pause(); player.dataset.projectUrl = url;
            if (url) player.src = url; else player.removeAttribute('src');
            player.load();
        }
        player.hidden = !url; $(`${name}-video-placeholder`).hidden = Boolean(url);
        $(`${name}-video-status`).textContent = url ? 'Preview video của đúng job; vùng OCR dùng toạ độ nguồn trong manifest, không lấy từ kích thước preview.' : 'Không có video preview khả dụng. Không tự mở đường dẫn file trong manifest.';
        $(`${name}-empty`).hidden = true; $(`${name}-project`).hidden = false;
        projectStatus(name, snapshot.ready === true ? 'Đã nhận project · chỉ chuẩn bị, chưa render / upload.' : 'Project thiếu video hoặc SRT; hãy khôi phục file trước khi tiếp tục.', snapshot.ready === true ? 'ready' : 'error');
        // Auto Edit owns its draft and task state; never repaint its form from queue polling.
        if (name === 'editor') window.VietSubEditor?.projectReceived(snapshot);
        if (name === 'uploader') window.VietSubUploader?.projectReceived(snapshot);
        adapter.projectReceived?.(name, snapshot);
    }
    function setDrawer(open) {
        clearTimeout(drawerTimer); drawerAnimation?.cancel(); drawerOpen = open;
        const drawer = $('settings-drawer'), shell = $('workbench-shell');
        $('settings-open-btn').setAttribute('aria-expanded', String(open));
        $('settings-backdrop').hidden = !open; shell.inert = open; drawer.inert = !open;
        if (open) {
            restoreFocus = document.activeElement; drawer.hidden = false;
            $('settings-close-btn').focus();
        } else {
            (canFocus(restoreFocus) ? restoreFocus : $(`workspace-${workspace}-tab`)).focus();
        }
        const motion = document.documentElement.dataset.motion === 'on';
        if (motion && drawer.animate) {
            drawerAnimation = drawer.animate(open ?
                [{transform: 'translateX(28px)', opacity: 0}, {transform: 'translateX(0)', opacity: 1}] :
                [{transform: 'translateX(0)', opacity: 1}, {transform: 'translateX(28px)', opacity: 0}],
            {duration: open ? 220 : 150, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'both'});
        }
        if (!open) {
            if (motion) drawerTimer = setTimeout(() => { if (!drawerOpen) drawer.hidden = true; }, 150);
            else drawer.hidden = true;
        }
    }
    function drawerKeys(event) {
        if (!drawerOpen) return;
        if (event.key === 'Escape') { event.preventDefault(); setDrawer(false); return; }
        if (event.key !== 'Tab') return;
        const controls = [...$('settings-drawer').querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')]
            .filter(el => !el.disabled && el.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    function finishSplit() {
        if (!splitDrag) return;
        const drag = splitDrag; splitDrag = null;
        if (drag.el.hasPointerCapture?.(drag.pointerId)) drag.el.releasePointerCapture(drag.pointerId);
        document.documentElement.classList.remove('is-resizing'); saveLayout();
    }
    function bindSplitter(id, axis) {
        const el = $(id);
        el.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            event.preventDefault(); adapter.endCropDrag();
            const {width, height} = dimensions(), px = layoutPixels(layout, width, height);
            splitDrag = {el, pointerId: event.pointerId, axis, x: event.clientX, y: event.clientY, width, height, px};
            el.setPointerCapture(event.pointerId); el.focus(); document.documentElement.classList.add('is-resizing');
        });
        el.addEventListener('pointermove', event => {
            const d = splitDrag;
            if (!d || d.el !== el || d.pointerId !== event.pointerId) return;
            if (axis === 'x') layout.sidebar_ratio = clamp(d.px.sidebar + event.clientX - d.x, d.px.minSidebar, d.px.maxSidebar) / d.width;
            else {
                layout.console_collapsed = false;
                layout.console_ratio = clamp(d.px.console + d.y - event.clientY, Math.min(112, d.px.maxConsole), d.px.maxConsole) / d.height;
            }
            layoutDirty = true; applyLayout();
        });
        for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) el.addEventListener(type, finishSplit);
        el.addEventListener('dblclick', () => {
            if (axis === 'x') layout.sidebar_ratio = .18;
            else { layout.console_ratio = .14; layout.console_collapsed = false; }
            applyLayout(); saveLayout();
        });
        el.addEventListener('keydown', event => {
            const keys = axis === 'x' ? ['ArrowLeft', 'ArrowRight'] : ['ArrowDown', 'ArrowUp'];
            if (![...keys, 'Home', 'End'].includes(event.key)) return;
            event.preventDefault(); adapter.endCropDrag();
            const {width, height} = dimensions(), px = layoutPixels(layout, width, height);
            const min = axis === 'x' ? px.minSidebar : Math.min(112, px.maxConsole), max = axis === 'x' ? px.maxSidebar : px.maxConsole;
            const current = axis === 'x' ? px.sidebar : px.console;
            const value = event.key === 'Home' ? min : event.key === 'End' ? max : clamp(current + (event.key === keys[1] ? 1 : -1) * (event.shiftKey ? 32 : 8), min, max);
            if (axis === 'x') layout.sidebar_ratio = value / width;
            else { layout.console_collapsed = false; layout.console_ratio = value / height; }
            applyLayout(); saveLayout();
        });
    }
    function fitVideo() {
        fitRaf = 0;
        if (workspace !== 'vietsub') return;
        const viewport = $('preview-viewport'), stage = $('video-stage'), player = $('preview-player');
        const width = player.videoWidth || preview?.video_resolution?.width || 16;
        const height = player.videoHeight || preview?.video_resolution?.height || 9;
        const fit = fitRect(viewport.clientWidth, viewport.clientHeight, width, height);
        if (!fit.width || !fit.height) return;
        const w = `${fit.width.toFixed(3)}px`, h = `${fit.height.toFixed(3)}px`;
        if (stage.style.width !== w || stage.style.height !== h) {
            adapter.endCropDrag(); stage.style.width = w; stage.style.height = h;
        }
    }
    function scheduleFit() { if (!fitRaf && initialized) fitRaf = requestAnimationFrame(fitVideo); }
    function refreshTransport() {
        if (!initialized) return;
        const player = $('preview-player'), dataMatches = preview && player.dataset.previewId === preview.id;
        const duration = player.duration, ready = dataMatches && !mediaFailed && player.readyState >= 1 && positive(duration);
        const fps = preview?.video_metadata?.frame_rate;
        $('play-pause-btn').disabled = !ready;
        $('play-pause-btn').textContent = player.paused ? 'Phát' : 'Tạm dừng';
        $('play-pause-btn').setAttribute('aria-label', player.paused ? 'Phát video preview' : 'Tạm dừng video preview');
        for (const id of ['frame-back-btn', 'frame-forward-btn']) {
            $(id).disabled = !ready || !positive(fps);
            $(id).title = positive(fps) ? `Một khung theo ${fps.toFixed(3)} fps; ước lượng với video VFR` : 'Chưa có FPS nguồn để tua từng khung';
        }
        const seek = $('video-seek'); seek.disabled = !ready; seek.max = ready ? String(duration) : '1';
        seek.value = ready ? String(player.currentTime) : '0';
        seek.setAttribute('aria-valuetext', formatTime(player.currentTime));
        $('video-timecode').textContent = `${formatTime(ready ? player.currentTime : 0)} / ${formatTime(ready ? duration : preview?.video_metadata?.duration)}`;
        const width = player.videoWidth || preview?.video_resolution?.width, height = player.videoHeight || preview?.video_resolution?.height;
        const details = [];
        if (width && height) details.push(`${width} × ${height}`);
        if (positive(fps)) details.push(`${Number(fps.toFixed(2))} fps`);
        $('video-metadata').textContent = details.join(' · ') || 'Video preview';
        scheduleFit();
    }
    async function togglePlayback() {
        const player = $('preview-player');
        if ($('play-pause-btn').disabled) return;
        if (!player.paused) { player.pause(); return; }
        try { await player.play(); } catch (error) { adapter.notify(`Không phát được preview: ${error.message}`, 'error'); }
    }
    function stepFrame(direction) {
        const player = $('preview-player');
        if ($('frame-forward-btn').disabled) return;
        const next = frameTime(player.currentTime, direction, preview?.video_metadata?.frame_rate, player.duration);
        if (next !== null) { player.pause(); player.currentTime = next; refreshTransport(); }
    }
    function previewChanged(data) {
        if (!initialized) return;
        preview = data || null; mediaFailed = false;
        if (preview) selectSource(preview.source_type === 'local' ? 'local' : 'link');
        refreshTransport(); refreshMetrics();
    }
    function refreshMetrics() {
        const download = downloads.preview?.active ? downloads.preview : downloads.job?.active ? downloads.job : null;
        $('metric-connections').textContent = Number.isInteger(download?.connections) && download.connections > 0 ? String(download.connections) : '—';
        $('metric-speed').textContent = positive(download?.speed) ? (download.speed / 1048576).toLocaleString('vi-VN', {minimumFractionDigits: 1, maximumFractionDigits: 1}) : '—';
        const bitrate = preview?.video_metadata?.bit_rate;
        $('metric-bitrate').textContent = positive(bitrate) ? `${(bitrate / 1000000).toLocaleString('vi-VN', {maximumFractionDigits: 2})} Mb/s` : '—';
        $('metric-bitrate').title = 'Bitrate của video preview';
    }
    function downloadChanged(download, prefix) {
        if (!initialized) return;
        downloads[prefix] = download; refreshMetrics();
        if (download?.active && download.notice && layout.console_collapsed) setCollapsed(false, false);
    }
    function progressChanged(data) {
        if (!initialized) return;
        // Reconcile the OS preference on the existing progress tick as well as
        // its change event: Windows/headless browsers may defer that event while
        // an offscreen settings drawer is closing. No new animation/poll loop.
        if (!motionSaving) window.VietSubVFX?.setEnabled(motionSaved);
        motionHelp();
        $('workflow-progress').dataset.status = data.status;
        if (data.status === 'idle') {
            if (data.download?.active) {
                $('workflow-progress').dataset.status = 'running';
                adapter.updateSteps(data.download.phase === 'download' ? 'download' : 'resolve', 'running');
            } else {
                for (const name of ['resolve', 'download', 'ocr', 'translate', 'done']) $(`step-${name}`).className = 'step';
                $('workflow-progress').setAttribute('aria-valuenow', '0');
                if ($('connection-banner').hidden) $('process-caption').textContent = 'Sẵn sàng';
            }
        }
        window.VietSubVFX?.pipeline({jobId: data.job_id, step: data.step, status: data.status,
            resultReady: data.result_ready, translatedSrt: data.result?.translated_srt});
    }
    function resultShown(result, scroll) {
        if (!initialized) return;
        const key = result.translated_srt || result.project_dir || '';
        if (scroll || (key && key !== lastResult)) selectConsole('result', true);
        lastResult = key;
    }
    function resultHidden() { if (initialized && consoleTab === 'result') selectConsole('log'); lastResult = ''; }
    function focusSource() {
        if (!initialized) return;
        if (drawerOpen) setDrawer(false);
        selectWorkspace('vietsub'); selectSource('link'); $('url-input').focus();
    }
    function motionHelp() {
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        $('motion-help').textContent = reduced ? 'Windows đang yêu cầu giảm chuyển động; hiệu ứng được tạm tắt.' : 'Phản hồi nhẹ khi thao tác. Tự tắt khi Windows yêu cầu giảm chuyển động.';
    }
    function applySettings(data) {
        if (!initialized) return;
        if (!motionSaving) {
            motionSaved = data.sakuga_vfx_enabled !== false;
            $('setting-sakuga-vfx').checked = motionSaved;
            window.VietSubVFX?.setEnabled(motionSaved); motionHelp();
        }
        if (!layoutDirty && validLayout(data.workbench_layout)) {
            layout = {...data.workbench_layout}; applyLayout(); localSave();
        } else if (!layoutDirty && data.workbench_layout == null && hasStoredLayout) saveLayout();
    }
    async function saveMotion() {
        const checkbox = $('setting-sakuga-vfx');
        if (motionSaving) return;
        motionSaving = true; checkbox.disabled = true;
        const next = checkbox.checked; window.VietSubVFX?.setEnabled(next);
        try {
            await adapter.request('/api/settings', {method: 'POST', body: JSON.stringify({sakuga_vfx_enabled: next})});
            motionSaved = next; adapter.notify(next ? 'Đã bật hiệu ứng chuyển động.' : 'Đã tắt hiệu ứng chuyển động.');
        } catch (error) {
            checkbox.checked = motionSaved; window.VietSubVFX?.setEnabled(motionSaved); adapter.notify(error.message, 'error');
        } finally { motionSaving = false; checkbox.disabled = false; motionHelp(); }
    }
    function init(options) {
        if (initialized || !$('workbench-shell')) return;
        adapter = options; initialized = true; layout = readLayout();
        window.VietSubVFX?.init();
        bindTabs(['link', 'local'], 'source', selectSource);
        bindTabs(['log', 'result'], 'console', selectConsole);
        bindWorkspaces(); selectWorkspace('vietsub'); bindInspectors();
        selectSource('link'); selectConsole('log');
        $('preview-form').addEventListener('submit', event => {
            if (sourceMode === 'local') { event.preventDefault(); event.stopImmediatePropagation(); }
        });
        $('console-toggle-btn').addEventListener('click', () => setCollapsed(!layout.console_collapsed));
        $('settings-open-btn').addEventListener('click', () => setDrawer(true));
        $('settings-close-btn').addEventListener('click', () => setDrawer(false));
        $('settings-backdrop').addEventListener('click', () => setDrawer(false));
        document.addEventListener('keydown', drawerKeys);
        $('setting-sakuga-vfx').addEventListener('change', saveMotion);
        $('settings-panel').addEventListener('change', event => {
            if (event.target.matches('select, input[type="checkbox"]')) window.VietSubVFX?.smoke(event.target);
        });
        window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener?.('change', motionHelp);
        bindSplitter('sidebar-resizer', 'x'); bindSplitter('console-resizer', 'y');
        const player = $('preview-player');
        $('play-pause-btn').addEventListener('click', togglePlayback);
        $('frame-back-btn').addEventListener('click', () => stepFrame(-1));
        $('frame-forward-btn').addEventListener('click', () => stepFrame(1));
        $('video-seek').addEventListener('input', event => {
            if (!event.target.disabled) { player.currentTime = clamp(Number(event.target.value), 0, player.duration); refreshTransport(); }
        });
        player.tabIndex = 0; player.setAttribute('aria-label', 'Video preview, dùng Space để phát hoặc dừng');
        player.addEventListener('keydown', event => {
            if (event.key === ' ') { event.preventDefault(); togglePlayback(); }
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); stepFrame(event.key === 'ArrowLeft' ? -1 : 1); }
        });
        for (const type of ['loadedmetadata', 'loadeddata', 'durationchange', 'timeupdate', 'seeked', 'play', 'pause', 'emptied']) player.addEventListener(type, refreshTransport);
        player.addEventListener('error', () => { mediaFailed = true; refreshTransport(); });
        player.controls = false;
        window.addEventListener('resize', () => { finishSplit(); adapter.endCropDrag(); applyLayout(); });
        window.addEventListener('blur', () => { finishSplit(); adapter.endCropDrag(); });
        if (typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(scheduleFit); resizeObserver.observe($('preview-viewport'));
        }
        applyLayout(); refreshTransport(); refreshMetrics(); motionHelp();
    }
    window.VietSubWorkbench = {init, applySettings, previewChanged, downloadChanged, progressChanged,
        resultShown, resultHidden, focusSource, refreshTransport, selectWorkspace, selectInspector, projectReceived, projectStatus,
        workspaceState: () => ({name: workspace, revision: workspaceRevision}),
        helpers: {validLayout, layoutPixels, fitRect, formatTime, frameTime, cropText, projectVideoUrl}};
})();
