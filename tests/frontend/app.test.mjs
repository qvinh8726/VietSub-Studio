// Unit tests for static/app.js. Run with: node --test tests/frontend
// app.js is a classic browser script, so it is loaded into a vm context with a
// small DOM stand-in; only the behaviour under test touches the DOM.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const APP_JS = readFileSync(new URL('../../static/app.js', import.meta.url), 'utf8');

function makeElement(id = '') {
    return {
        id, hidden: false, textContent: '', className: '', disabled: false, value: '', innerHTML: '',
        style: {}, dataset: {}, children: [], options: [], attributes: {}, onclick: null,
        setAttribute(name, value) { this.attributes[name] = String(value); },
        getAttribute(name) { return this.attributes[name]; },
        removeAttribute(name) { delete this.attributes[name]; },
        appendChild(child) { this.children.push(child); return child; },
        append(...nodes) { this.children.push(...nodes); },
        replaceChildren(...nodes) { this.children = [...nodes]; },
        addEventListener() {},
        querySelector() { return makeElement(); },
        classList: { toggle() {}, add() {}, remove() {} },
        pause() {}, load() {}, focus() {}, scrollIntoView() {}, remove() {},
        click() { this.clicked = (this.clicked || 0) + 1; },
        get firstChild() { return this.children[0]; },
    };
}

function loadApp({ fetch, confirm = () => true, pywebview } = {}) {
    const elements = {};
    const document = {
        getElementById(id) { return (elements[id] ??= makeElement(id)); },
        querySelector(selector) { return { content: selector.includes('api-token') ? 'token' : '1.6.1' }; },
        createElement(tag) { return makeElement(tag); },
    };
    const window = { addEventListener() {}, matchMedia: () => ({ matches: true }), confirm, scrollTo() {}, pywebview };
    const context = vm.createContext({
        document, window, fetch, console, Headers, AbortController, FormData,
        setTimeout, clearTimeout, setInterval, clearInterval, JSON, Date, Math,
    });
    vm.runInContext(APP_JS, context, { filename: 'static/app.js' });
    return { context, elements, run: code => vm.runInContext(code, context) };
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
