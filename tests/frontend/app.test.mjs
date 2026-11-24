// Unit tests for static/app.js. Run with: node --test tests/frontend
// app.js is a classic browser script, so it is loaded into a vm context with a
// small DOM stand-in; only the behaviour under test touches the DOM.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { File } from 'node:buffer';
import { test } from 'node:test';
import vm from 'node:vm';

const APP_JS = readFileSync(new URL('../../static/app.js', import.meta.url), 'utf8');

function makeElement(id = '') {
    const el = {
        id, tagName: id.toUpperCase(), hidden: false, textContent: '', className: '', disabled: false, value: '', innerHTML: '',
        style: {}, dataset: {}, children: [], options: [], attributes: {}, onclick: null,
        checked: false, indeterminate: false, listeners: {}, isConnected: true, files: [],
        showModal() { this.open = true; }, close() { this.open = false; },
        setAttribute(name, value) { this.attributes[name] = String(value); },
        getAttribute(name) { return this.attributes[name]; },
        removeAttribute(name) { delete this.attributes[name]; },
        appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
        append(...nodes) { for (const child of nodes) this.appendChild(child); },
        replaceChildren(...nodes) { this.children = []; for (const child of nodes) this.appendChild(child); },
        addEventListener(type, listener) { (this.listeners[type] ??= []).push(listener); },
        dispatch(type, event = {}) { return Promise.all((this.listeners[type] || []).map(listener => listener({target: this, ...event}))); },
        querySelectorAll(selector) {
            const matched = [];
            const walk = node => {
                for (const child of node.children || []) {
                    if (
                        (selector === 'input[data-glossary-field]' && child.tagName === 'INPUT' && child.dataset?.glossaryField) ||
                        (selector === 'input[type="checkbox"]' && child.tagName === 'INPUT' && child.type === 'checkbox') ||
                        (selector === 'input[data-glossary-field="source"]' && child.tagName === 'INPUT' && child.dataset?.glossaryField === 'source')
                    ) matched.push(child);
                    walk(child);
                }
            };
            walk(this);
            return matched;
        },
        querySelector(selector) {
            const found = this.querySelectorAll(selector);
            return found[0] || makeElement();
        },
        classList: { toggle() {}, add() {}, remove() {} },
        pause() {}, load() {}, focus() {}, select() {}, scrollIntoView() {}, remove() {},
        click() { this.clicked = (this.clicked || 0) + 1; },
        get firstChild() { return this.children[0]; },
    };
    return el;
}

function loadApp({ fetch, confirm = () => true, pywebview, workbench, editor } = {}) {
    const elements = {}, docListeners = {}, windowListeners = {}, dispatchedEvents = [];
    const document = {
        getElementById(id) { return (elements[id] ??= makeElement(id)); },
        querySelector(selector) { return { content: selector.includes('api-token') ? 'token' : '1.6.2' }; },
        createElement(tag) { const el = makeElement(tag); el.tagName = tag.toUpperCase(); return el; },
        addEventListener(type, listener) { (docListeners[type] ??= []).push(listener); },
        dispatchEvent(event) {
            dispatchedEvents.push(event);
            for (const listener of docListeners[event.type] || []) listener(event);
            return true;
        },
    };
    class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } }
    const window = {
        addEventListener(type, listener) { (windowListeners[type] ??= []).push(listener); },
        emit(type, event = {}) { return Promise.all((windowListeners[type] || []).map(fn => fn(event))); },
        matchMedia: () => ({ matches: true }), confirm, scrollTo() {}, pywebview, VietSubWorkbench: workbench, VietSubEditor: editor,
    };
    const safeTimeout = (fn, delay, ...args) => { const t = setTimeout(fn, delay, ...args); t.unref?.(); return t; };
    const safeInterval = (fn, delay, ...args) => { const t = setInterval(fn, delay, ...args); t.unref?.(); return t; };
    const context = vm.createContext({
        document, window, fetch, console, Headers, AbortController, FormData, URL, File, CustomEvent,
        setTimeout: safeTimeout, clearTimeout, setInterval: safeInterval, clearInterval, JSON, Date, Math,
    });
    vm.runInContext(APP_JS, context, { filename: 'static/app.js' });
    return { context, elements, document, window, dispatchedEvents, run: code => vm.runInContext(code, context) };
}

test('a request that never settles fails after its timeout and shows the disconnected banner', async () => {
    const neverSettles = (_url, options) => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
            const error = new Error('aborted');
            error.name = 'AbortError';
            reject(error);
        });
    });
    const app = loadApp({ fetch: neverSettles });
    const message = await app.run("fetchJson('/api/queue', {timeoutMs: 30}).then(() => 'ok', error => error.message)");
    assert.match(message, /không phản hồi/);
    assert.equal(app.elements['connection-banner'].hidden, false);
});

test('a successful response hides the disconnected banner again', async () => {
    const ok = async () => ({ ok: true, status: 200, json: async () => ({ jobs: [] }) });
    const app = loadApp({ fetch: ok });
    app.run("setConnection(false)");
    await app.run("fetchJson('/api/queue')");
    assert.equal(app.elements['connection-banner'].hidden, true);
});

test('HTTP errors keep their status so callers can react to 409', async () => {
    const conflict = async () => ({ ok: false, status: 409, json: async () => ({ error: 'Hãy chờ job xong' }) });
    const app = loadApp({ fetch: conflict });
    const status = await app.run("fetchJson('/api/update/install', {method: 'POST'}).then(() => 0, error => error.status)");
    assert.equal(status, 409);
});

test('the cancel button is enabled again when the next job starts', () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
    app.run(`
        healthReadiness = {local: true, douyin: true};
        queueJobs = [{id: 'job-b', status: 'running', step: 'ocr', ocr_lang: 'ch', result: {}}];
        cancellingJobId = 'job-a';
        renderQueue();
    `);
    assert.equal(app.elements['cancel-btn'].disabled, false);
    assert.equal(app.elements['cancel-btn'].textContent, 'Huỷ job');
    assert.equal(app.run('cancellingJobId'), '');

    app.run("cancellingJobId = 'job-b'; byId('cancel-btn').disabled = true; renderQueue();");
    assert.equal(app.elements['cancel-btn'].disabled, true);
});

test('a second click is ignored while the same action is running', async () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
    const calls = await app.run(`
        (async () => {
            let calls = 0;
            let release;
            const slow = () => new Promise(resolve => { calls += 1; release = resolve; });
            const first = runOnce('start', slow);
            const second = runOnce('start', slow);
            release();
            await Promise.all([first, second]);
            return calls;
        })()
    `);
    assert.equal(calls, 1);
});

test('the progress caption names the configured translation provider', () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
    app.run("translationProviderName = 'DeepSeek API'; updateSteps('translate', 'running');");
    assert.equal(app.elements['process-caption'].textContent, 'DeepSeek API đang dịch');
});

const okFetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

test('download progress shows percent, size, speed and the captcha notice', () => {
    const app = loadApp({ fetch: okFetch });
    app.run(`renderDownloadProgress({active: true, source: 'douyin', phase: 'download', downloaded: 52428800,
        total: 104857600, percent: 50, speed: 3145728, notice: ''}, 'preview')`);
    assert.equal(app.elements['preview-download'].hidden, false);
    assert.equal(app.elements['preview-download-stats'].textContent, '50% · 50,0/100,0 MB · 3,0 MB/s');
    assert.equal(app.elements['preview-download-fill'].style.width, '50%');
    assert.equal(app.elements['preview-download-bar'].attributes['aria-valuenow'], '50');
    assert.equal(app.elements['preview-download-notice'].hidden, true);

    app.run(`renderDownloadProgress({active: true, source: 'douyin', phase: 'captcha', downloaded: 0, total: null,
        percent: null, speed: null, notice: 'Vui lòng kéo captcha trên trình duyệt Chrome để tiếp tục tải'}, 'preview')`);
    assert.equal(app.elements['preview-download-notice'].hidden, false);
    assert.match(app.elements['preview-download-notice'].textContent, /captcha/);
    assert.match(app.elements['preview-download-label'].textContent, /Chrome/);

    app.run("renderDownloadProgress({active: false}, 'preview')");
    assert.equal(app.elements['preview-download'].hidden, true);
});

test('choosing a video uses the native dialog instead of the upload input', async () => {
    const picked = {
        id: 'p1', source_url: 'local://p1', video_id: 'local-p1', video_title: 'clip', crop_coords: null,
        source_type: 'local', video_url: '/api/previews/p1/video', video_resolution: { width: 1920, height: 1080 },
    };
    const app = loadApp({ fetch: okFetch, pywebview: { api: { choose_local_video: async () => picked } } });
    await app.run('chooseLocalVideo()');
    assert.equal(app.run('currentPreview.id'), 'p1');
    assert.equal(app.run('currentPreview.video_resolution.width'), 1920);
    // The upload input is never touched (elements are created lazily on first lookup).
    assert.equal(app.elements['local-video-input']?.clicked, undefined);

    const browserApp = loadApp({ fetch: okFetch });
    await browserApp.run('chooseLocalVideo()');
    assert.equal(browserApp.elements['local-video-input'].clicked, 1);
});

test('a queued source waits until its own downloader is ready', () => {
    const app = loadApp({ fetch: okFetch });
    app.run(`
        healthReadiness = {local: true, douyin: true, sources: {local: true, douyin: true, youtube: false}};
        queueJobs = [{id: 'a', status: 'queued', step: 'resolve', source_type: 'youtube', ocr_lang: 'en', result: {}}];
        renderQueue();
    `);
    assert.equal(app.elements['start-queue-btn'].disabled, true);
    assert.equal(app.elements['start-queue-btn'].textContent, 'Thiếu thành phần bắt buộc');
    app.run('healthReadiness.sources.youtube = true; renderQueue();');
    assert.equal(app.elements['start-queue-btn'].disabled, false);
});

test('platform sessions show the login state of each platform', () => {
    const app = loadApp({ fetch: okFetch });
    app.run(`
        sourceSessionSites = [{key: 'douyin', label: 'Douyin'}, {key: 'xiaohongshu', label: 'Xiaohongshu'},
            {key: 'kuaishou', label: 'Kuaishou'}];
        renderSourceSessions({browser_connected: true, sites: {
            douyin: {logged_in: true, has_cookies: true},
            xiaohongshu: {logged_in: null, has_cookies: true},
            kuaishou: {logged_in: false, has_cookies: false, manual_cookie: true}}});
    `);
    const rows = app.elements['source-sessions'].children;
    assert.equal(rows.length, 3);
    assert.equal(rows[0].children[1].textContent, 'Đã đăng nhập');
    assert.equal(rows[1].children[1].textContent, 'Có cookie, chưa rõ đăng nhập');
    assert.equal(rows[2].children[1].textContent, 'Chưa đăng nhập');
    assert.equal(rows[2].children[2].textContent, 'Mở Kuaishou trên Chrome');
    assert.match(rows[2].children[0].children[1].textContent, /cookie thủ công/);

    app.run("renderSourceSessions({browser_connected: false, sites: {}})");
    assert.equal(app.elements['source-sessions'].children[0].children[1].textContent, 'Chrome chưa mở');
});

test('session buttons, the captcha label and the pill name the selected automation browser', async () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({ connected: false }) }) });
    app.run(`
        applyBrowserName('Edge');
        sourceSessionSites = [{key: 'douyin', label: 'Douyin'}];
        renderSourceSessions({browser_connected: false, sites: {}});
        renderDownloadProgress({active: true, source: 'douyin', phase: 'captcha', notice: 'x'}, 'preview');
    `);
    const row = app.elements['source-sessions'].children[0];
    assert.equal(row.children[2].textContent, 'Mở Douyin trên Edge');
    assert.equal(row.children[1].textContent, 'Edge chưa mở');
    assert.match(app.elements['preview-download-label'].textContent, /trên Edge/);
    assert.match(app.elements['source-sessions-intro'].textContent, /Edge/);
    // Lazy start: a browser that has not been opened yet is normal, not an error.
    await app.run('checkEdge()');
    assert.equal(app.elements['edge-pill'].className, 'status-pill');
    assert.equal(app.elements['edge-pill-text'].textContent, 'Edge chưa mở');
});

test('the custom browser path only shows for the custom choice', () => {
    const app = loadApp({ fetch: okFetch });
    app.run("byId('setting-browser-kind').value = 'custom'; updateBrowserSettingsUI();");
    assert.equal(app.elements['browser-path-field'].hidden, false);
    app.run("byId('setting-browser-kind').value = 'chrome'; updateBrowserSettingsUI();");
    assert.equal(app.elements['browser-path-field'].hidden, true);
});

const jsonResponse = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));

test('share parser extracts all supported sources from multiline share text, preserving signed queries', () => {
    const app = loadApp({fetch: okFetch});
    const text = `复制链接https://v.douyin.com/AbC123/复制此链接，打开Dou音\n` +
        `Clip (https://www.youtube.com/watch?v=abc123xyz99&list=keep).\n` +
        `http://xhslink.com/a/AbCd1234，复制\nhttps://www.xiaohongshu.com/explore/abc?xsec_token=a%2Bb%3D&xsec_source=pc_share\n` +
        `https://v.kuaishou.com/example/; https://www.chenzhongtech.com/s/a123\n` +
        `https://www.gifshow.com/s/b123 https://www.bilibili.com/video/BV1234567890/\n` +
        `https://b23.tv/abc123 https://www.tiktok.com/@name/video/123456789\n` +
        `https://youtu.be/abc123xyz99 https://www.iesdouyin.com/share/video/123/`;
    const result = plain(app.run(`parseShareLinks(${JSON.stringify(text)})`));
    assert.equal(result.urls.length, 12);
    assert.equal(result.urls[0], 'https://v.douyin.com/AbC123/');
    assert.equal(result.urls[1], 'https://www.youtube.com/watch?v=abc123xyz99&list=keep');
    assert.equal(result.urls[3], 'https://www.xiaohongshu.com/explore/abc?xsec_token=a%2Bb%3D&xsec_source=pc_share');
    assert.deepEqual(result.unsupported, []);
});

test('share parser deduplicates in order and rejects lookalike hosts, credentials and non-web URLs', () => {
    const app = loadApp({fetch: okFetch});
    const result = plain(app.run(`parseShareLinks('https://v.douyin.com/abc/ https://v.douyin.com/abc/ https://douyin.com.evil.test/video/123 https://notyoutube.com/watch?v=x https://douyin.com@evil.test/a https://user:pass@youtube.com/watch?v=x javascript:alert(1) file:///tmp/a.mp4 https://youtu.be/second')`));
    assert.deepEqual(result.urls, ['https://v.douyin.com/abc/', 'https://youtu.be/second']);
    assert.equal(result.duplicates, 1);
    assert.equal(result.unsupported.length, 4);
    assert.deepEqual(plain(app.run('parseShareLinks(null)')).urls, []);
});

test('sequential batch waits for each request, records failures and keeps the original order', async () => {
    const app = loadApp({fetch: okFetch});
    const result = await app.run(`(async () => {
        const starts = []; let release;
        const pending = enqueueSequentially(['one', 'bad', 'three'], async item => {
            starts.push(item);
            if (item === 'one') await new Promise(resolve => { release = resolve; });
            if (item === 'bad') throw new Error('rejected');
        });
        const before = starts.slice(); release();
        return {before, starts, result: await pending};
    })()`);
    assert.deepEqual(plain(result.before), ['one']);
    assert.deepEqual(plain(result.starts), ['one', 'bad', 'three']);
    assert.equal(result.result.added, 2);
    assert.deepEqual(plain(result.result.failures), [{item: 'bad', error: 'rejected'}]);
});

test('batch links enqueue one URL per request, retain failures, and never call preview', async () => {
    const calls = [];
    const app = loadApp({fetch: async (url, options) => {
        calls.push([url, options.method, options.body]);
        if (url === '/api/queue') {
            const payload = JSON.parse(options.body);
            return payload.url.includes('bad') ? jsonResponse({error: 'Link hỏng'}, 400) : jsonResponse({id: 'ok'}, 201);
        }
        return jsonResponse(url.startsWith('/api/progress') ? {status: 'idle'} : {jobs: [], revision: 3});
    }});
    app.run("byId('url-input').value = 'https://youtu.be/first https://youtu.be/bad https://youtu.be/last'; byId('ocr-lang').value = 'ja';");
    await app.run('importShareLinks()');
    const posts = calls.filter(([, method]) => method === 'POST');
    assert.deepEqual(posts.map(([url]) => url), ['/api/queue', '/api/queue', '/api/queue']);
    assert.deepEqual(posts.map(([, , body]) => JSON.parse(body)), [
        {url: 'https://youtu.be/first', lang: 'ja'}, {url: 'https://youtu.be/bad', lang: 'ja'}, {url: 'https://youtu.be/last', lang: 'ja'},
    ]);
    assert.equal(app.elements['url-input'].value, 'https://youtu.be/bad');
    assert.match(app.elements['batch-import-status'].textContent, /2 video · 1 lỗi/);
    assert.match(app.elements['batch-import-errors'].children[0].textContent, /Link hỏng/);
    assert.equal(app.run('batchImportBusy'), false);
});

test('multiple local files upload and enqueue sequentially without replacing a crop preview', async () => {
    const calls = [];
    let uploads = 0;
    const app = loadApp({fetch: async (url, options) => {
        if (url === '/api/previews/local') {
            const file = options.body.get('video'); calls.push(`upload:${file.name}`); uploads += 1;
            return jsonResponse({id: `local-${uploads}`, source_url: `local://${uploads}`, crop_coords: {crop_y: .7}, video_resolution: {width: 1280, height: 720}}, 201);
        }
        if (url === '/api/queue') { calls.push(JSON.parse(options.body)); return jsonResponse({id: 'queued'}, 201); }
        return jsonResponse(url.startsWith('/api/progress') ? {status: 'idle'} : {jobs: [], revision: 3});
    }});
    app.run("currentPreview = {id: 'keep-this-preview', video_resolution: {width: 1920, height: 1080}}; crop = {crop_x: .2, crop_y: .5, crop_width: .7, crop_height: .3}; byId('ocr-lang').value = 'ko';");
    await app.run("importLocalFiles([new File(['a'], 'one.mp4'), new File(['x'], 'bad.txt'), new File(['b'], 'two.mkv')])");
    assert.equal(calls.length, 4);
    assert.equal(calls[0], 'upload:one.mp4');
    assert.deepEqual(calls[1], {url: 'local://1', preview_id: 'local-1', lang: 'ko', crop_coords: {crop_y: .7}, video_resolution: {width: 1280, height: 720}});
    assert.equal(calls[2], 'upload:two.mkv');
    assert.equal(calls[3].preview_id, 'local-2');
    assert.equal(app.run('currentPreview.id'), 'keep-this-preview');
    assert.equal(app.run('crop.crop_y'), .5);
    assert.match(app.elements['batch-import-errors'].children[0].textContent, /bad.txt/);
});

test('one selected or dropped file keeps the crop-preview flow without enqueueing', async () => {
    const calls = [];
    const app = loadApp({fetch: async url => {
        calls.push(url);
        return jsonResponse({id: 'one-preview', source_type: 'local', source_url: 'local://one-preview', video_url: '/api/previews/one-preview/video', video_resolution: {width: 1280, height: 720}});
    }});
    await app.run("dropLocalFiles({preventDefault() {}, dataTransfer: {files: [new File(['a'], 'one.mp4')]}})");
    assert.deepEqual(calls, ['/api/previews/local']);
    assert.equal(app.run('currentPreview.id'), 'one-preview');
    assert.equal(app.elements['preview-player'].hidden, false);
});

test('finished and older selections include errors/cancellations but exclude running and queued jobs', async () => {
    const app = loadApp({fetch: async () => jsonResponse({selected_count: 3, reclaimable_bytes: 0, failures: []})});
    app.run(`queueJobs = [
        {id: 'success', status: 'success', finished_at: (Date.now() - 4 * 86400000) / 1000},
        {id: 'error', status: 'error', created_at: (Date.now() - 5 * 86400000) / 1000},
        {id: 'cancel', status: 'cancelled', finished_at: Date.now() / 1000},
        {id: 'running', status: 'running', created_at: 1}, {id: 'queued', status: 'queued', created_at: 1}
    ]; setQueueSelection('finished');`);
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['success', 'error', 'cancel']);
    app.run("setQueueSelection('old')");
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['success', 'error']);
    app.run("setQueueSelection('all')");
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['success', 'error', 'cancel', 'queued']);
    assert.equal(app.elements['queue-select-all'].checked, true);
    assert.equal(app.elements['queue-select-all'].indeterminate, false);
    const now = 1800000000000;
    assert.equal(app.run(`isOlderFinished({status: 'success', finished_at: ${(now - 3 * 86400000) / 1000}}, ${now})`), false);
    assert.equal(app.run(`isOlderFinished({status: 'success', finished_at: 'invalid'}, ${now})`), false);
    await flush();
});

test('selection survives polling and is pruned when a job starts or disappears', async () => {
    const app = loadApp({fetch: async () => jsonResponse({selected_count: 1, reclaimable_bytes: 10, failures: []})});
    app.run("queueJobs = [{id: 'a', status: 'success'}, {id: 'b', status: 'queued'}]; setQueueSelection('all'); queueJobs = [{id: 'a', status: 'success'}, {id: 'b', status: 'running'}]; renderQueue();");
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['a']);
    assert.equal(app.elements['queue-select-all'].checked, true);
    const row = app.elements['queue-list'].children[0], checkbox = row.children[0].children[0].children[0];
    assert.equal(checkbox.checked, true);
    checkbox.checked = false; checkbox.dispatch('change');
    assert.equal(app.run('selectedQueueIds.size'), 0);
    app.run("selectedQueueIds.add('gone'); renderQueue()");
    assert.equal(app.run('selectedQueueIds.size'), 0);
    await flush();
});

test('disk badge uses decimal MB and GB with locale-stable numeric boundaries', () => {
    const app = loadApp();
    assert.equal(app.run('formatDiskBytes(1000000)'), '1 MB');
    assert.equal(app.run('formatDiskBytes(2500000000)'), '2,5 GB');
    assert.equal(app.run('formatDiskBytes(0)'), '0 B');
});

test('bulk disk badge uses authoritative dry-run bytes rather than summing shared job estimates', async () => {
    const calls = [];
    const app = loadApp({fetch: async (url, options) => {
        calls.push([url, JSON.parse(options.body)]);
        return jsonResponse({selected_count: 2, reclaimable_bytes: 1024, failures: []});
    }});
    app.run("queueJobs = [{id: 'a', status: 'success', disk_bytes: 99999}, {id: 'b', status: 'success', disk_bytes: 99999}]; selectedQueueIds.add('a'); selectedQueueIds.add('b');");
    await app.run('refreshBulkEstimate()');
    assert.deepEqual(calls, [['/api/queue/bulk-delete', {ids: ['a', 'b'], delete_files: true, keep_srt: true, dry_run: true}]]);
    assert.equal(app.elements['queue-disk-badge'].textContent, '1 KB có thể dọn');
});

test('out-of-order estimates cannot overwrite a changed selection or deletion mode', async () => {
    const pending = [];
    const app = loadApp({fetch: (_url, options) => new Promise(resolve => pending.push({resolve, payload: JSON.parse(options.body)}))});
    app.run("queueJobs = [{id: 'a', status: 'success'}, {id: 'b', status: 'success'}]; selectedQueueIds.add('a');");
    const first = app.run('refreshBulkEstimate()');
    app.run("selectedQueueIds.clear(); selectedQueueIds.add('b');");
    const second = app.run('refreshBulkEstimate()');
    pending[1].resolve(jsonResponse({selected_count: 1, reclaimable_bytes: 2048, failures: []})); await second;
    pending[0].resolve(jsonResponse({selected_count: 1, reclaimable_bytes: 999999, failures: []})); await first;
    assert.equal(app.elements['queue-disk-badge'].textContent, '2 KB có thể dọn');
    app.run('openBulkDelete()'); // record-only dry run
    assert.equal(app.elements['bulk-delete-records'].checked, true);
    assert.equal(app.elements['bulk-keep-srt'].checked, true);
    assert.equal(app.elements['bulk-delete-confirm'].disabled, true);
    app.run("byId('bulk-delete-files').checked = true; byId('bulk-delete-records').checked = false;");
    const files = app.run('refreshBulkEstimate()');
    pending[2].resolve(jsonResponse({selected_count: 1, reclaimable_bytes: 0, failures: []})); await flush();
    assert.equal(app.elements['bulk-delete-confirm'].disabled, true, 'old record-only estimate must not enable file deletion');
    pending[3].resolve(jsonResponse({selected_count: 1, reclaimable_bytes: 4096, failures: []})); await files;
    assert.equal(app.elements['bulk-delete-confirm'].disabled, false);
    assert.match(app.elements['bulk-delete-estimate'].textContent, /4,1 KB/);
    assert.equal(pending[3].payload.keep_srt, true);
});

test('estimate failure blocks deletion and a retry can recover without mutating', async () => {
    let fail = true;
    const calls = [];
    const app = loadApp({fetch: async (_url, options) => {
        calls.push(JSON.parse(options.body));
        return fail ? jsonResponse({error: 'Không đọc được file'}, 500) : jsonResponse({selected_count: 1, reclaimable_bytes: 50, failures: []});
    }});
    app.run("queueJobs = [{id: 'a', status: 'success'}]; selectedQueueIds.add('a'); bulkDialogOpen = true; byId('bulk-delete-files').checked = true; byId('bulk-keep-srt').checked = true;");
    await app.run('refreshBulkEstimate()');
    await app.run('deleteSelectedQueueJobs()');
    assert.equal(calls.length, 1);
    assert.equal(app.elements['bulk-delete-confirm'].disabled, true);
    assert.equal(app.elements['bulk-estimate-retry'].hidden, false);
    fail = false; await app.run('refreshBulkEstimate(true)');
    assert.equal(app.elements['bulk-delete-confirm'].disabled, false);
    assert.ok(calls.every(item => item.dry_run));
});

test('bulk mutation sends explicit mode flags once and leaves failed IDs selected with errors', async () => {
    const writes = [];
    let release;
    const app = loadApp({fetch: async (url, options) => {
        if (url === '/api/queue/bulk-delete') {
            const payload = JSON.parse(options.body);
            if (payload.dry_run) return jsonResponse({selected_count: 2, reclaimable_bytes: 800, failures: []});
            writes.push(payload);
            return new Promise(resolve => { release = () => resolve(jsonResponse({deleted_ids: ['a'], freed_bytes: 500, failures: [{id: 'b', error: 'File đang mở'}], jobs: [{id: 'b', status: 'error'}]})); });
        }
        return jsonResponse(url.startsWith('/api/progress') ? {status: 'idle'} : {jobs: [{id: 'b', status: 'error'}], revision: 8});
    }});
    app.run("queueJobs = [{id: 'a', status: 'success'}, {id: 'b', status: 'error'}]; selectedQueueIds.add('a'); selectedQueueIds.add('b'); bulkDialogOpen = true; byId('bulk-delete-files').checked = true; byId('bulk-keep-srt').checked = false;");
    await app.run('refreshBulkEstimate()');
    const deleting = app.run('deleteSelectedQueueJobs()');
    await app.run('deleteSelectedQueueJobs()');
    assert.equal(writes.length, 1);
    assert.equal(app.elements['bulk-delete-confirm'].disabled, true);
    app.run('closeBulkDelete()');
    assert.equal(app.run('bulkDialogOpen'), true);
    release(); await deleting;
    assert.deepEqual(writes[0], {ids: ['a', 'b'], delete_files: true, keep_srt: false, dry_run: false});
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['b']);
    assert.match(app.elements['queue-bulk-status'].textContent, /500 B/);
    assert.match(app.elements['bulk-delete-failures'].children[0].textContent, /File đang mở/);
    assert.equal(app.elements['bulk-delete-confirm'].disabled, true);
});

test('dry run can still confirm eligible jobs when selected_count excludes a failed job', async () => {
    const app = loadApp({fetch: async () => jsonResponse({selected_count: 1, reclaimable_bytes: 0, failures: [{id: 'b', error: 'Job đang được xử lý'}]})});
    app.run("queueJobs = [{id: 'a', status: 'success'}, {id: 'b', status: 'queued'}]; selectedQueueIds.add('a'); selectedQueueIds.add('b'); bulkDialogOpen = true;");
    await app.run('refreshBulkEstimate()');
    assert.equal(app.elements['bulk-delete-confirm'].disabled, false);
    assert.match(app.elements['bulk-delete-failures'].children[0].textContent, /đang được xử lý/);
});

test('record-only deletion uses explicit defaults and old polls cannot resurrect deleted jobs or results', async () => {
    let resolveQueue, resolveProgress;
    const mutations = [];
    const app = loadApp({fetch: async (url, options) => {
        if (url === '/api/queue/bulk-delete') {
            const payload = JSON.parse(options.body);
            if (payload.dry_run) return jsonResponse({selected_count: 1, reclaimable_bytes: 0, failures: []});
            mutations.push(payload);
            return jsonResponse({deleted_ids: ['a'], freed_bytes: 0, failures: [], jobs: []});
        }
        if (url.startsWith('/api/queue?')) return new Promise(resolve => { resolveQueue = resolve; });
        if (url.startsWith('/api/progress?')) return new Promise(resolve => { resolveProgress = resolve; });
        throw new Error(`unexpected ${url}`);
    }});
    app.run("queueJobs = [{id: 'a', status: 'success'}]; selectedQueueIds.add('a'); openBulkDelete();");
    await flush();
    const oldPoll = app.run('loadQueue()');
    await app.run('deleteSelectedQueueJobs()');
    assert.deepEqual(mutations, [{ids: ['a'], delete_files: false, keep_srt: true, dry_run: false}]);
    resolveQueue(jsonResponse({jobs: [{id: 'a', status: 'success'}], revision: 1}));
    resolveProgress(jsonResponse({status: 'success', result_ready: true, result: {project_name: 'deleted', video: 'old.mp4'}}));
    await oldPoll;
    assert.equal(app.run('queueJobs.length'), 0);
    assert.equal(app.run('selectedQueueIds.size'), 0);
    assert.equal(app.elements['bulk-delete-dialog'].open, false);
    assert.equal(app.elements['result-box'].hidden, true);
});

test('rejected bulk mutation restores row controls and keeps selection for retry', async () => {
    const app = loadApp({fetch: async (url, options) => {
        if (url === '/api/queue/bulk-delete') {
            return JSON.parse(options.body).dry_run ? jsonResponse({selected_count: 1, reclaimable_bytes: 64, failures: []}) : jsonResponse({error: 'Bận, thử lại'}, 409);
        }
        return jsonResponse(url.startsWith('/api/progress') ? {status: 'idle'} : {jobs: [{id: 'a', status: 'success'}], revision: 3});
    }});
    app.run("queueJobs = [{id: 'a', status: 'success'}]; selectedQueueIds.add('a'); bulkDialogOpen = true;");
    await app.run('refreshBulkEstimate()');
    await app.run('deleteSelectedQueueJobs()');
    assert.equal(app.run('bulkDeleteBusy'), false);
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['a']);
    assert.equal(app.elements['queue-list'].children[0].children[0].children[0].children[0].disabled, false);
    assert.equal(app.elements['bulk-delete-cancel'].disabled, false);
    assert.equal(app.elements['bulk-delete-confirm'].disabled, true);
    assert.equal(app.elements['bulk-estimate-retry'].hidden, false);
});

test('result identifies cleaned video and exposes optional publish pack', () => {
    const app = loadApp();
    app.run("showResult({project_name:'Clip', source_video_deleted:true, video:'gone.mp4', translated_srt:'clip.vi.srt', publish_metadata:'clip.publish.json'})");
    assert.match(app.elements['res-video'].textContent, /Đã dọn video/);
    assert.equal(app.elements['res-publish'].textContent, 'clip.publish.json');
    assert.equal(app.elements['res-publish-row'].hidden, false);
    app.run("showResult({video:'kept.mp4'})");
    assert.equal(app.elements['res-video'].textContent, 'kept.mp4');
    assert.equal(app.elements['res-publish-row'].hidden, true);
});

test('new settings default to false and persist as explicit booleans', async () => {
    const saved = [];
    let settings = {};
    const app = loadApp({fetch: async (url, options) => {
        if (url === '/api/settings') {
            if (options.method === 'POST') { saved.push(JSON.parse(options.body)); return jsonResponse({}); }
            return jsonResponse(settings);
        }
        return jsonResponse({ready_local: true, ready_douyin: true, checks: {}});
    }});
    await app.run('loadSettings()');
    assert.equal(app.elements['setting-auto-delete-source-video'].checked, false);
    assert.equal(app.elements['setting-generate-publish-metadata'].checked, false);
    await app.run('saveSettingsNow({silent: true})');
    assert.equal(saved[0].auto_delete_source_video, false);
    assert.equal(saved[0].generate_publish_metadata, false);
    settings = {auto_delete_source_video: true, generate_publish_metadata: true};
    await app.run('loadSettings()');
    await app.run('saveSettingsNow({silent: true})');
    assert.equal(saved[1].auto_delete_source_video, true);
    assert.equal(saved[1].generate_publish_metadata, true);
});

function workbenchSpy() {
    const received = [], switches = [], statuses = [];
    return {received, switches, statuses, workspaceState: () => ({name: 'vietsub', revision: 0}),
        projectReceived: (...args) => received.push(args), selectWorkspace: name => switches.push(name),
        projectStatus: (...args) => statuses.push(args), resultShown() {}, resultHidden() {}};
}
const handoffSnapshot = (jobId, workspace, overrides = {}) => ({workspace, job_id: jobId, manifest_path: `${jobId}.project.json`,
    manifest: {schema_version: 1, project_id: jobId, assets: {}}, video_url: `/api/projects/${jobId}/video`,
    availability: {video: true, translated_srt: true}, ready: true, ...overrides});
const descendants = element => [element, ...element.children.flatMap(descendants)];

test('success-only selection is additive while the finished shortcut keeps legacy semantics', async () => {
    const app = loadApp({fetch: async () => jsonResponse({selected_count: 1, reclaimable_bytes: 0, failures: []})});
    app.run("queueJobs = [{id:'ok',status:'success'},{id:'bad',status:'error'},{id:'cancel',status:'cancelled'},{id:'wait',status:'queued'}]; setQueueSelection('success')");
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['ok']);
    assert.equal(app.elements['queue-select-success'].disabled, false);
    app.run("setQueueSelection('finished')");
    assert.deepEqual(plain(app.run('[...selectedQueueIds]')), ['ok', 'bad', 'cancel']);
    await flush();
});

test('historical successful queue actions capture job ID even when old result_ready is missing', async () => {
    const calls = [], wb = workbenchSpy();
    const app = loadApp({workbench: wb, fetch: async (url, options) => {
        calls.push([url, options]);
        return jsonResponse(handoffSnapshot('historical', 'editor'));
    }});
    app.run("queueJobs = [{id:'historical',status:'success',result:{video:'clip.mp4',translated_srt:'clip.vi.srt'}},{id:'failed',status:'error',result:{}}]; progressJobId='different-current-job'; renderQueue()");
    const rows = app.elements['queue-list'].children;
    const editor = descendants(rows[0]).find(node => node.textContent === 'Chuyển sang Auto Edit →');
    assert.ok(editor); assert.equal(editor.disabled, false);
    assert.ok(descendants(rows[0]).some(node => node.textContent === 'Đẩy sang Đăng đa nền tảng →'));
    assert.equal(descendants(rows[1]).some(node => node.textContent === 'Chuyển sang Auto Edit →'), false);
    await editor.onclick();
    assert.equal(calls[0][0], '/api/projects/historical/handoff');
    assert.deepEqual(JSON.parse(calls[0][1].body), {workspace: 'editor'});
    assert.equal(calls[0][1].headers.get('X-VietSub-Token'), 'token');
    assert.deepEqual(wb.switches, ['editor']);
});

test('result handoff ID prefers captured job/progress identity and falls back to a unique matching old result', () => {
    const app = loadApp();
    app.run("queueJobs = [{id:'old',status:'success',result:{project_dir:'old',translated_srt:'old.vi.srt'}},{id:'live',status:'success',result:{project_dir:'live',translated_srt:'live.vi.srt'}}]; progressJobId='live'; showResult({project_dir:'old',translated_srt:'old.vi.srt'},true,'old')");
    assert.equal(app.run('resultJobId'), 'old');
    app.run("showResult({project_dir:'live',translated_srt:'live.vi.srt'})");
    assert.equal(app.run('resultJobId'), 'live');
    app.run("progressJobId=''; showResult({translated_srt:'old.vi.srt'})");
    assert.equal(app.run('resultJobId'), 'old');
    app.run("progressJobId='new-progress-job'; showResult({translated_srt:'old.vi.srt'})");
    assert.equal(app.run('resultJobId'), 'new-progress-job', 'explicit progress identity takes priority over shared output paths');
    app.run("progressJobId=''; showResult({translated_srt:'old.vi.srt'})");
    assert.equal(app.elements['result-editor-btn'].disabled, false);
    app.run("showResult({video:'unknown.mp4'})");
    assert.equal(app.run('resultJobId'), '');
    assert.equal(app.elements['result-editor-btn'].disabled, true);
    assert.match(app.elements['result-handoff-status'].textContent, /Chưa xác định/);
    app.run('hideResult()');
    assert.equal(app.elements['result-handoff-actions'].hidden, true);
});

test('handoff loading is explicit, ignores duplicate clicks and only the latest response can switch staging', async () => {
    const pending = [], wb = workbenchSpy();
    const app = loadApp({workbench: wb, fetch: (url, options) => new Promise(resolve => pending.push({url, options, resolve}))});
    app.run("showResult({translated_srt:'one.vi.srt'},false,'one')");
    const first = app.run("handoffProject('one','editor')");
    await app.run("handoffProject('one','editor')");
    assert.equal(pending.length, 1);
    assert.equal(app.elements['result-editor-btn'].disabled, true);
    assert.match(app.elements['result-editor-btn'].textContent, /Đang bàn giao/);
    assert.equal(app.elements['result-editor-btn'].attributes['aria-busy'], 'true');
    const second = app.run("handoffProject('two','uploader')");
    pending[1].resolve(jsonResponse(handoffSnapshot('two', 'uploader'))); await second;
    pending[0].resolve(jsonResponse(handoffSnapshot('one', 'editor'))); await first;
    assert.deepEqual(wb.switches, ['uploader']);
    assert.equal(wb.received.length, 1); assert.equal(wb.received[0][1].job_id, 'two');
    assert.equal(app.elements['result-editor-btn'].disabled, false);
    assert.equal(app.elements['result-editor-btn'].textContent, 'Chuyển sang Auto Edit →');
    assert.equal(app.run('pendingHandoffs.size'), 0);
});

test('handoff conflicts, wrong-job responses and missing media never switch workspace and stay retryable', async () => {
    for (const response of [jsonResponse({error:'Video đã bị xoá'}, 409),
        jsonResponse(handoffSnapshot('wrong', 'editor')),
        jsonResponse(handoffSnapshot('one', 'editor', {ready: false, availability: {video: false, translated_srt: true}}))]) {
        const wb = workbenchSpy(), app = loadApp({workbench: wb, fetch: async () => response});
        app.run("showResult({translated_srt:'one.vi.srt'},false,'one')");
        await app.run("handoffProject('one','editor')");
        assert.deepEqual(wb.switches, []); assert.deepEqual(wb.received, []);
        assert.equal(app.elements['result-handoff-status'].dataset.state, 'error');
        assert.ok(app.elements['result-handoff-status'].textContent.length);
        assert.equal(app.elements['result-editor-btn'].disabled, false);
        assert.equal(app.elements['result-editor-btn'].attributes['aria-busy'], 'false');
    }
});

test('cleaned video results keep quick action labels with a downstream warning and no request', async () => {
    let calls = 0;
    const app = loadApp({fetch: async () => { calls++; return jsonResponse({}); }});
    app.run("queueJobs=[{id:'gone',status:'success',result:{source_video_deleted:true,translated_srt:'gone.vi.srt'}}]; renderQueue(); showResult(queueJobs[0].result,true,'gone')");
    const action = descendants(app.elements['queue-list'].children[0]).find(node => node.textContent === 'Chuyển sang Auto Edit →');
    assert.equal(action.disabled, true);
    assert.equal(app.elements['result-editor-btn'].disabled, true);
    assert.match(app.elements['result-handoff-status'].textContent, /không thể bàn giao/);
    await action.onclick();
    assert.equal(calls, 0);
});

test('project recheck uses read-only snapshot and ignores an earlier response after a new handoff', async () => {
    const pending = [], wb = workbenchSpy();
    const app = loadApp({workbench: wb, fetch: (url, options) => new Promise(resolve => pending.push({url, options, resolve}))});
    const initial = app.run("handoffProject('one','editor')");
    pending[0].resolve(jsonResponse(handoffSnapshot('one', 'editor'))); await initial;
    const recheck = app.run("refreshProject('editor','one')");
    assert.equal(pending[1].url, '/api/projects/one?workspace=editor'); assert.equal(pending[1].options.method, undefined);
    const newer = app.run("handoffProject('two','editor')");
    pending[2].resolve(jsonResponse(handoffSnapshot('two', 'editor'))); await newer;
    pending[1].resolve(jsonResponse(handoffSnapshot('one', 'editor', {ready: false}))); await recheck;
    assert.deepEqual(wb.received.map(([, data]) => data.job_id), ['one', 'two']);
});

test('stale handoff errors are ignored after a newer successful request', async () => {
    const pending = [], wb = workbenchSpy();
    const app = loadApp({workbench: wb, fetch: () => new Promise(resolve => pending.push(resolve))});
    const older = app.run("handoffProject('old','editor')");
    const newer = app.run("handoffProject('new','uploader')");
    pending[1](jsonResponse(handoffSnapshot('new', 'uploader'))); await newer;
    pending[0](jsonResponse({error:'stale failure'}, 409)); await older;
    assert.deepEqual(wb.switches, ['uploader']);
    assert.equal(wb.statuses.some(([, message]) => message.includes('stale failure')), false);
    assert.equal(app.run('handoffStates.has("old")'), false);
});

test('queue mutation invalidates an in-flight handoff without switching to removed media', async () => {
    let release;
    const wb = workbenchSpy(), app = loadApp({workbench: wb, fetch: () => new Promise(resolve => { release = resolve; })});
    const pending = app.run("handoffProject('old','editor')");
    app.run('queueEpoch++');
    release(jsonResponse(handoffSnapshot('old', 'editor'))); await pending;
    assert.deepEqual(wb.switches, []); assert.deepEqual(wb.received, []);
    assert.equal(app.run('pendingHandoffs.size'), 0);
    assert.match(wb.statuses.at(-1)[1], /Hàng đợi đã thay đổi/);
});

test('H-14: opening settings restores preset glossary_id even when channel-presets resolves before glossaries', async () => {
    let resolveGlossaries;
    const presetStore = {
        active_id: 'p1',
        presets: [{
            id: 'p1', name: 'Kênh Tiên Hiệp', description: '', glossary_id: 'g-tien-hiep',
            editor_options: {reframe: 'vertical', mask_mode: 'boxblur', crop_box: null, subtitle_animation: 'word_pop', subtitle_highlight_color: '#ffd54a', subtitle_max_words_per_chunk: 4},
            publish_defaults: {profile_ids: [], upload_mode: 'assisted', visibility: 'private', confirm_public_auto: false},
        }],
        resolved_warnings: {},
    };
    const app = loadApp({
        fetch: (url) => {
            if (url === '/api/glossaries') return new Promise(resolve => { resolveGlossaries = resolve; });
            if (url === '/api/channel-presets') return Promise.resolve(jsonResponse(presetStore));
            if (url === '/api/uploader/profiles') return Promise.resolve(jsonResponse({profiles: []}));
            return Promise.resolve(jsonResponse({}));
        },
    });
    await app.window.emit('load');
    const glossariesPending = app.run('loadGlossaries()');
    await app.run('loadChannelPresets({withProfiles: true})');
    // Before glossaries arrive, dropdown temporarily fell back to default.
    assert.equal(app.elements['setting-preset-glossary'].value, 'default');
    // When glossaries finally arrive, the listener must restore the preset's real glossary_id.
    resolveGlossaries(jsonResponse({
        active_id: 'default',
        profiles: [
            {id: 'default', name: 'Mặc định', style_note: '', entries: []},
            {id: 'g-tien-hiep', name: 'Tiên Hiệp', style_note: 'ta-ngươi', entries: [{source: '剑', target: 'Kiếm', note: ''}]},
        ],
    }));
    await glossariesPending;
    assert.equal(app.elements['setting-preset-glossary'].value, 'g-tien-hiep');

    // Manual user selection on #setting-preset-glossary survives later glossaries-updated events.
    app.elements['setting-preset-glossary'].value = 'default';
    await app.elements['setting-preset-glossary'].dispatch('change');
    app.run(`renderGlossaryStore({active_id: 'default', profiles: [
        {id: 'default', name: 'Mặc định', style_note: '', entries: []},
        {id: 'g-tien-hiep', name: 'Tiên Hiệp', style_note: '', entries: []},
        {id: 'g-game', name: 'Game', style_note: '', entries: []}
    ]})`);
    assert.equal(app.elements['setting-preset-glossary'].value, 'default');
});

test('M-15: saving channel preset preserves existing profile_ids when uploader profiles failed or did not load yet', async () => {
    const savedBodies = [];
    const presetStore = {
        active_id: 'p1',
        presets: [{
            id: 'p1', name: 'Kênh Shorts', description: '', glossary_id: 'default',
            editor_options: {reframe: 'vertical', mask_mode: 'boxblur', crop_box: null, subtitle_animation: 'word_pop', subtitle_highlight_color: '#ffd54a', subtitle_max_words_per_chunk: 4},
            publish_defaults: {profile_ids: ['11111111-1111-4111-8111-111111111111'], upload_mode: 'assisted', visibility: 'private', confirm_public_auto: false},
        }],
        resolved_warnings: {},
    };
    const app = loadApp({
        fetch: (url, opts = {}) => {
            if (url === '/api/channel-presets') return Promise.resolve(jsonResponse(presetStore));
            if (url === '/api/uploader/profiles') return Promise.reject(new Error('offline'));
            if (url === '/api/channel-presets/p1' && opts.method === 'PUT') {
                savedBodies.push(JSON.parse(opts.body));
                return Promise.resolve(jsonResponse(presetStore));
            }
            return Promise.resolve(jsonResponse({}));
        },
    });
    await app.run('loadChannelPresets({withProfiles: true})');
    assert.equal(app.elements['setting-preset-profiles'].dataset.profilesLoaded, 'false');
    await app.run('saveCurrentChannelPreset()');
    assert.equal(savedBodies.length, 1);
    assert.deepEqual(savedBodies[0].publish_defaults.profile_ids, ['11111111-1111-4111-8111-111111111111']);
});

test('M-15: uploading preset watermark preserves unsaved form edits and capture button gates empty preset and filters NaN', async () => {
    const presetStore = {
        active_id: 'p1',
        presets: [{
            id: 'p1', name: 'Kênh Cũ', description: 'Mô tả cũ', glossary_id: 'default',
            editor_options: {reframe: 'original', mask_mode: 'boxblur', crop_box: null, font_size: 48, blur_strength: 8, subtitle_animation: 'static', subtitle_highlight_color: '#ffd54a', subtitle_max_words_per_chunk: 4, watermark_path: '', watermark_enabled: false},
            publish_defaults: {profile_ids: [], upload_mode: 'assisted', visibility: 'private', confirm_public_auto: false},
        }],
        resolved_warnings: {},
    };
    const editorMock = {
        state: () => ({options: {reframe: 'vertical', mask_mode: 'delogo', crop_box: {x: 0.1, y: 0.7, width: 0.8, height: 0.2}, font_size: NaN, blur_strength: Infinity, subtitle_animation: 'word_pop', subtitle_highlight_color: '#00ff88', subtitle_max_words_per_chunk: 5, banner_text: 'Hook mới'}}),
        helpers: {defaults: {font_size: 48, blur_strength: 8}},
    };
    const app = loadApp({
        editor: editorMock,
        fetch: (url, opts = {}) => {
            if (url === '/api/channel-presets/p1/watermark' && opts.method === 'POST') {
                const updated = JSON.parse(JSON.stringify(presetStore));
                updated.presets[0].editor_options.watermark_path = 'C:/cfg/channel-presets-assets/p1/logo.png';
                updated.presets[0].editor_options.watermark_enabled = true;
                return Promise.resolve(jsonResponse(updated));
            }
            return Promise.resolve(jsonResponse({}));
        },
    });
    // Without any preset, capture button is disabled and no-ops cleanly.
    app.run('renderChannelPresetsStore({active_id: "", presets: [], resolved_warnings: {}})');
    assert.equal(app.elements['setting-preset-capture-btn'].disabled, true);
    app.run('captureCurrentEditorOptionsIntoPreset()');
    assert.equal(app.run('presetEditorDraft'), null);

    // Load preset, edit form fields unsaved, then upload watermark PNG -> form edits survive.
    app.run(`renderChannelPresetsStore(${JSON.stringify(presetStore)})`);
    assert.equal(app.elements['setting-preset-capture-btn'].disabled, false);
    app.elements['setting-preset-name'].value = 'Tên Đang Sửa Dở';
    app.elements['setting-preset-banner'].value = 'Banner Đang Gõ';
    app.elements['setting-preset-watermark-file'].files = [new File([new Uint8Array([137, 80, 78, 71])], 'logo.png', {type: 'image/png'})];
    await app.run('uploadChannelPresetWatermark()');
    assert.equal(app.elements['setting-preset-name'].value, 'Tên Đang Sửa Dở');
    assert.equal(app.elements['setting-preset-banner'].value, 'Banner Đang Gõ');
    assert.equal(app.run('presetEditorDraft.watermark_path'), 'C:/cfg/channel-presets-assets/p1/logo.png');
    assert.match(app.elements['setting-preset-watermark-status'].textContent, /Đã gắn logo PNG/);

    // Capture Auto Edit options strips NaN/Infinity and retains preset's own watermark.
    app.run('captureCurrentEditorOptionsIntoPreset()');
    const draft = plain(app.run('presetEditorDraft'));
    assert.equal(draft.font_size, 48);
    assert.equal(draft.blur_strength, 8);
    assert.equal(draft.crop_box, null);
    assert.equal(draft.watermark_path, 'C:/cfg/channel-presets-assets/p1/logo.png');
    assert.equal(draft.reframe, 'vertical');
});

test('H-14 & M-15: applyChannelPreset attaches target_job_id, preset_crop_box, apply_sequence and ignores stale responses; glossary/preset loaders guard out-of-order responses', async () => {
    const pendingApply = [], pendingGlossary = [];
    const app = loadApp({
        fetch: (url, opts = {}) => {
            if (url === '/api/glossaries' && (!opts.method || opts.method === 'GET')) {
                return new Promise(resolve => pendingGlossary.push(resolve));
            }
            if (url.endsWith('/apply') && opts.method === 'POST') {
                return new Promise(resolve => pendingApply.push({url, body: JSON.parse(opts.body), resolve}));
            }
            return Promise.resolve(jsonResponse({}));
        },
    });
    // Out-of-order glossary GET: slow #1 cannot overwrite fast #2.
    const g1 = app.run('loadGlossaries()');
    const g2 = app.run('loadGlossaries()');
    pendingGlossary[1](jsonResponse({active_id: 'g2', profiles: [{id: 'default', name: 'Mặc định', entries: []}, {id: 'g2', name: 'Mới Nhất', entries: []}]}));
    await g2;
    pendingGlossary[0](jsonResponse({active_id: 'default', profiles: [{id: 'default', name: 'Cũ Kỹ', entries: []}]}));
    await g1;
    assert.equal(app.run('glossaryStore.active_id'), 'g2');
    assert.equal(app.run('glossaryStore.profiles[0].name'), 'Mặc định');

    // Out-of-order applyChannelPreset: only #2 dispatches event with target_job_id & apply_sequence & preset_crop_box.
    app.run("workspaceProjects.editor = 'job-a'");
    const a1 = app.run("applyChannelPreset('p1')");
    const a2 = app.run("applyChannelPreset('p2')");
    assert.equal(pendingApply.length, 2);
    assert.equal(pendingApply[0].body.job_id, 'job-a');
    pendingApply[1].resolve(jsonResponse({
        preset_id: 'p2', preset_name: 'Hồ sơ 2',
        editor_options: {reframe: 'vertical', crop_box: {x: 0.1, y: 0.7, width: 0.8, height: 0.2}},
        publish_defaults: {profile_ids: [], upload_mode: 'assisted', visibility: 'private'},
        presets_store: {active_id: 'p2', presets: [{id: 'p2', name: 'Hồ sơ 2', glossary_id: 'default', editor_options: {crop_box: null}, publish_defaults: {}}], resolved_warnings: {}},
        warnings: [],
    }));
    await a2;
    pendingApply[0].resolve(jsonResponse({
        preset_id: 'p1', preset_name: 'Hồ sơ 1 cũ',
        editor_options: {reframe: 'original', crop_box: null},
        publish_defaults: {profile_ids: [], upload_mode: 'assisted', visibility: 'private'},
        warnings: [],
    }));
    await a1;
    const applyEvents = app.dispatchedEvents.filter(e => e.type === 'vietsub:apply-channel-preset');
    assert.equal(applyEvents.length, 1);
    assert.equal(applyEvents[0].detail.preset_id, 'p2');
    assert.equal(applyEvents[0].detail.target_job_id, 'job-a');
    assert.equal(applyEvents[0].detail.apply_sequence, 2);
    assert.equal(applyEvents[0].detail.preset_crop_box, null);
});
