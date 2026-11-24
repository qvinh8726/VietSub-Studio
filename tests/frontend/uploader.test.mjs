import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/uploader.js', import.meta.url), 'utf8');
const workbench = readFileSync(new URL('../../static/workbench.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../../templates/index.html', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const ids = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
const snapshot = (job = 'job-a', overrides = {}) => ({job_id: job, workspace: 'uploader', ready: true,
    availability: {video: true, translated_srt: true}, video_url: `/api/projects/${job}/video?signature=test&asset=edited`,
    manifest: {schema_version: 1, project_id: `project-${job}`, project_name: `Project ${job}`, assets: {edited_video: 'edited.mp4'},
        crop_box: {x: .04, y: .64, width: .92, height: .3}, source_resolution: {width: 1920, height: 1080}, metadata: {title: 'Tiêu đề gốc', description: 'Mô tả gốc', hashtags: ['#vietsub']}, stages: {}}, ...overrides});
const capabilities = (overrides = {}) => ({browser_available: true, timezone: 'Asia/Saigon', scheduling: 'local',
    platforms: Object.entries({tiktok: ['public', 'draft'], youtube: ['public', 'private', 'draft'], facebook: ['public', 'private', 'draft'], douyin: ['public', 'private', 'draft'], bilibili: ['public', 'draft'], xiaohongshu: ['public', 'private', 'draft']}).map(([key, modes]) => ({key, label: key, modes, login_url: 'https://example.invalid/login'})), ...overrides});
const profile = (overrides = {}) => ({id: ids[0], platform: 'youtube', name: 'Kênh chính', session_status: 'ready', detail: '', ...overrides});
const queueTask = (overrides = {}) => ({id: 'upload-a', profile_id: ids[0], platform: 'youtube', status: 'queued', phase: 'prepare', percent: 0, error: '', screenshot_url: '', result_url: '', visibility: 'draft', scheduled_at: null, ...overrides});

function eventTarget() {
    const listeners = new Map();
    return {listeners, addEventListener(type, fn) { const items = listeners.get(type) || []; items.push(fn); listeners.set(type, items); },
        emit(type, detail = {}) {
            const event = {target: this, currentTarget: this, button: 0, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...detail};
            return {event, done: Promise.all((listeners.get(type) || []).map(fn => fn(event)))};
        }};
}
function harness({handler, caps = capabilities(), initialProfiles = [profile()], initialJobs = [], withWorkbench = false, storage = new Map(), storageError = false, confirm = true} = {}) {
    const elements = {}, calls = [], timers = new Map(), confirmations = [], handoffs = [];
    let timerId = 0, uuidCounter = 0;
    const document = Object.assign(eventTarget(), {activeElement: null, documentElement: {dataset: {}, classList: {add() {}, remove() {}}},
        getElementById(id) { assert.ok(elements[id], `Missing real markup #${id}`); return elements[id]; },
        querySelector() { return {content: 'csrf-uploader-token'}; }, createElement: tag => element('', tag)});
    function element(id, tag = 'div') {
        const attrs = {}, classes = new Set();
        const el = Object.assign(eventTarget(), {id, tagName: tag.toUpperCase(), attributes: attrs, dataset: {}, children: [], parentElement: null,
            hidden: false, inert: false, disabled: false, checked: false, value: '', textContent: '', type: '', files: [], loads: 0,
            clientWidth: 1280, clientHeight: 720, offsetHeight: 30, paused: true, currentTime: 0, duration: NaN, readyState: 0,
            style: {setProperty(key, value) { this[key] = value; }}, classList: {add: key => classes.add(key), remove: key => classes.delete(key), contains: key => classes.has(key), toggle(key, value) { if (value ?? !classes.has(key)) classes.add(key); else classes.delete(key); }},
            setAttribute(key, value) { attrs[key] = String(value); }, getAttribute: key => attrs[key] ?? null,
            removeAttribute(key) { delete attrs[key]; if (key === 'src') delete this.src; },
            appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
            replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); },
            focus() { document.activeElement = this; }, contains(other) { return this === other || this.children.some(child => child.contains(other)); },
            querySelectorAll(selector) {
                const all = []; const walk = parent => { for (const child of parent.children) { if (selector.split(',').some(tag => tag.trim().toUpperCase() === child.tagName)) all.push(child); walk(child); } }; walk(this); return all;
            }, pause() { this.paused = true; }, load() { this.loads++; }, getClientRects() { return this.hidden ? [] : [{}]; },
        });
        Object.defineProperty(el, 'options', {get() { return this.children.filter(child => child.tagName === 'OPTION'); }});
        return el;
    }
    const stack = [], voids = new Set(['input', 'meta', 'link', 'br', 'img', 'hr', 'source']);
    for (const [token] of template.matchAll(/<\/?[a-z][\w:-]*(?:[^<>"']|"[^"]*"|'[^']*')*>/gi)) {
        const tag = /^<\/?([\w:-]+)/.exec(token)[1].toLowerCase();
        if (token.startsWith('</')) { const at = stack.findLastIndex(node => node.tagName.toLowerCase() === tag); if (at >= 0) stack.splice(at); continue; }
        const attrs = {}; for (const attr of token.slice(tag.length + 1, -1).matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) attrs[attr[1]] = attr[2] ?? attr[3] ?? attr[4] ?? '';
        const node = element(attrs.id || '', tag); Object.assign(node.attributes, attrs);
        node.hidden = Object.hasOwn(attrs, 'hidden'); node.inert = Object.hasOwn(attrs, 'inert'); node.disabled = Object.hasOwn(attrs, 'disabled'); node.checked = Object.hasOwn(attrs, 'checked'); node.type = attrs.type || ''; node.value = attrs.value || '';
        for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) node.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
        if (node.id) elements[node.id] = node; stack.at(-1)?.appendChild(node); if (!voids.has(tag)) stack.push(node);
    }
    elements['uploader-profile-platform'].value = 'tiktok';
    const window = Object.assign(eventTarget(), {innerWidth: 1280, innerHeight: 720, location: {href: 'http://127.0.0.1:8123/'}, matchMedia: () => ({matches: false, addEventListener() {}}),
        confirm(text) { confirmations.push(text); return typeof confirm === 'function' ? confirm(text) : confirm; },
        VietSubWorkbench: {projectReceived(...args) { handoffs.push(args); }, workspaceState: () => ({name: 'uploader', revision: 0})}});
    const context = vm.createContext({window, document, console, URL, Headers, AbortController, FormData, Blob, TextEncoder,
        crypto: {randomUUID: () => `99999999-9999-4999-8999-${String(++uuidCounter).padStart(12, '0')}`},
        setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout: id => timers.delete(id), requestAnimationFrame() { return 1; },
        localStorage: {getItem(key) { if (storageError) throw new Error('storage unavailable'); return storage.get(key) ?? null; }, setItem(key, value) { if (storageError) throw new Error('storage full'); storage.set(key, value); }},
        fetch: async (url, options) => {
            assert.ok(url.startsWith('/api/uploader/'), `No remote platform requests in tests: ${url}`); calls.push({url, options});
            let data = handler ? await handler(url, options) : undefined;
            if (data === undefined && url === '/api/uploader/capabilities') data = caps;
            if (data === undefined && url === '/api/uploader/profiles' && options.method === 'GET') data = {profiles: initialProfiles};
            if (data === undefined && url === '/api/uploader/queue' && options.method === 'GET') data = {jobs: initialJobs};
            if (data === undefined && /^\/api\/uploader\/projects\/[^/]+$/.test(url)) {
                const snap = snapshot(decodeURIComponent(url.split('/')[4])); data = {snapshot: snap, metadata: options.method === 'POST' ? JSON.parse(options.body).metadata : snap.manifest.metadata, asset: 'edited', video_url: snap.video_url};
            }
            if (data instanceof Error) throw data;
            if (data?.response) return data.response;
            return {ok: true, status: 200, json: async () => data ?? {}};
        }});
    if (withWorkbench) vm.runInContext(workbench, context, {filename: 'static/workbench.js'});
    vm.runInContext(source, context, {filename: 'static/uploader.js'});
    const uploader = window.VietSubUploader;
    function change(id, value, type = 'input') { const control = elements[id]; if (control.type === 'checkbox') control.checked = value; else control.value = String(value); return control.emit(type); }
    const walk = root => [root, ...root.children.flatMap(walk)];
    return {uploader, window, document, elements, calls, timers, confirmations, handoffs, change, storage,
        find: (id, predicate) => walk(elements[id]).find(predicate),
        async select(id = ids[0], value = true) { const el = walk(elements['uploader-profile-list']).find(el => el.dataset.uploaderFocus === `select-${id}`); assert.ok(el); el.checked = value; await el.emit('change').done; await settle(); },
        async action(root, text) { const el = walk(elements[root]).find(el => el.tagName === 'BUTTON' && el.textContent.includes(text)); assert.ok(el, `No ${text} in ${root}`); await el.emit('click').done; await settle(); },
        async init() { await window.emit('load').done; await settle(); },
        async receive(value = snapshot()) { uploader.projectReceived(value); await settle(); },
        async poll() { const entry = [...timers].find(([, timer]) => timer.delay === 1800); assert.ok(entry, 'No pending read-only poll'); timers.delete(entry[0]); await entry[1].fn(); await settle(); },
    };
}
const helpers = harness().uploader.helpers;

test('metadata validation trims allowed text and hashtags without accepting unbounded or invalid values', () => {
    assert.deepEqual(plain(helpers.validateMetadata({title: ' Demo ', description: '<b>Plain text</b>', hashtags: '#một,hai #một'})), {errors: {}, metadata: {title: 'Demo', description: '<b>Plain text</b>', hashtags: ['#một', '#hai']}});
    for (const raw of [{title: 42}, {title: ''}, {title: 'a'.repeat(121)}, {title: 'bad\nname'}, {description: 'a'.repeat(5001)}, {hashtags: '#bad-tag'}, {hashtags: '#a '.repeat(31)}, {description: '\u0000'}]) {
        assert.ok(Object.keys(helpers.validateMetadata({title: 'ok', description: '', hashtags: '', ...raw}).errors).length, JSON.stringify(raw));
    }
});
test('scoped signed media and screenshot URLs reject cross-job, remote and extra-query capabilities', () => {
    assert.equal(helpers.safeProjectUrl('/api/projects/job-a/video?signature=x&asset=edited', 'job-a'), '/api/projects/job-a/video?signature=x&asset=edited');
    assert.equal(helpers.safeMediaUrl('/api/uploader/media/m1/video?signature=x', 'm1'), '/api/uploader/media/m1/video?signature=x');
    assert.equal(helpers.safeScreenshotUrl('/api/uploader/queue/t1/screenshot?signature=x', 't1'), '/api/uploader/queue/t1/screenshot?signature=x');
    for (const url of ['https://evil.invalid/api/projects/job-a/video?signature=x', '//evil.invalid/v', '/api/projects/job-b/video?signature=x', '/api/projects/job-a/video', '/api/projects/job-a/video?signature=x&path=C:/secret', '/api/projects/job-a/video?signature=x&signature=y', '/api/projects/job-a/video?signature=x#frag', '/api/projects/job-a/video?signature=x&asset=unknown', 'file:///C:/private.mp4', '/api/projects/job-a/video?signature=x&asset=source&url=https://evil.invalid']) assert.equal(helpers.safeProjectUrl(url, 'job-a'), '', url);
    for (const url of ['/api/uploader/queue/t2/screenshot?signature=x', '/api/uploader/queue/t1/screenshot?signature=x&path=secret', 'https://evil.invalid/a', '/api/uploader/queue/t1/screenshot?signature=x#fragment']) assert.equal(helpers.safeScreenshotUrl(url, 't1'), '');
});
test('schedule datetime is validated and serialized in Asia/Saigon independently of browser timezone', () => {
    const now = Date.parse('2026-11-01T00:00:00Z');
    assert.equal(helpers.scheduleValue('local', '2026-11-02T09:30', now).scheduled_at, '2026-11-02T09:30:00+07:00');
    for (const raw of ['2026-02-30T09:00', '2026-11-01T06:00', '2026-11-02 09:00', 'bad', '2026-11-02T25:00']) assert.ok(helpers.scheduleValue('local', raw, now).error, raw);
    assert.equal(helpers.scheduleValue('now', 'ignored', now).scheduled_at, null);
});
test('boot initializes exactly once, reads only capabilities/profiles/queue, and keeps actions disabled without a video', async () => {
    const h = harness(); assert.equal(h.calls.length, 0); await h.init(); h.uploader.init(); await h.window.emit('load').done;
    assert.deepEqual(h.calls.map(call => call.url), ['/api/uploader/capabilities', '/api/uploader/profiles', '/api/uploader/queue']);
    assert.ok(h.calls.every(call => call.options.method === 'GET')); assert.equal(h.elements['uploader-queue-submit'].disabled, true);
    assert.equal(h.elements['uploader-profile-form'].listeners.get('submit').length, 1);
    assert.equal(h.elements['uploader-refresh-project'].listeners.size, 0, 'only app / Workbench owns recheck; the module must not double-fetch');
    assert.equal(h.elements['uploader-capability-matrix'].children.length, 6);
});
test('pre-load handoff queues a snapshot and Workbench calls the uploader sink once without recursion', async () => {
    const h = harness({withWorkbench: true}); h.uploader.projectReceived(snapshot()); assert.equal(h.calls.length, 0);
    await h.init(); assert.equal(h.uploader.state().job_id, 'job-a');
    const refreshes = [];
    h.window.VietSubWorkbench.init({request: async () => ({}), notify() {}, endCropDrag() {}, refreshProject(name, id) { refreshes.push([name, id]); h.window.VietSubWorkbench.projectReceived(name, snapshot(id)); }});
    let hooked = 0; const received = h.uploader.projectReceived;
    h.uploader.projectReceived = value => { hooked++; received(value); };
    h.window.VietSubWorkbench.projectReceived('uploader', snapshot()); await settle(); assert.equal(hooked, 1);
    await h.change('uploader-title-input', 'Giữ bản sửa').done;
    await h.elements['uploader-refresh-project'].emit('click').done; await settle();
    assert.deepEqual(refreshes, [['uploader', 'job-a']]); assert.equal(hooked, 2); assert.equal(h.uploader.state().metadata.title, 'Giữ bản sửa');
    assert.equal(h.elements['uploader-refresh-project'].listeners.get('click').length, 1);
});
test('project reads are stale-guarded, inherit edited video, and preserve edited metadata over refresh and project changes', async () => {
    const slow = deferred(); let reads = 0;
    const h = harness({handler: (url, options) => url === '/api/uploader/projects/job-a' && options.method === 'GET' && ++reads === 1 ? slow.promise : undefined});
    await h.init(); h.uploader.projectReceived(snapshot()); await settle();
    await h.receive(snapshot('job-b')); await h.change('uploader-title-input', 'B tự sửa').done;
    slow.resolve({snapshot: snapshot(), metadata: {title: 'A trả muộn'}, asset: 'edited', video_url: snapshot().video_url}); await settle();
    assert.equal(h.uploader.state().job_id, 'job-b'); assert.equal(h.elements['uploader-title-input'].value, 'B tự sửa');
    await h.receive(); await h.change('uploader-title-input', 'A tự sửa').done;
    await h.receive(); assert.equal(h.elements['uploader-title-input'].value, 'A tự sửa');
    await h.receive(snapshot('job-b')); assert.equal(h.elements['uploader-title-input'].value, 'B tự sửa');
    assert.equal(h.uploader.state().asset, 'edited'); assert.match(h.elements['uploader-video'].src, /job-b\/video\?signature=test&asset=edited$/);
    assert.equal(h.calls.some(call => call.options.method !== 'GET'), false, 'handoff never auto-saves, opens login or queues a post');
});
test('versioned per-job drafts survive reload, validate storage, cap ten jobs, and never persist signed URLs', async () => {
    const h = harness(); await h.init(); await h.receive();
    await h.change('uploader-title-input', 'Tiêu đề riêng').done;
    await h.change('uploader-description-input', 'Nội dung riêng').done;
    await h.change('uploader-schedule-mode', 'local', 'change').done;
    await h.change('uploader-scheduled-at', '2030-01-02T08:00').done;
    const raw = h.storage.get(helpers.draftStorageKey); assert.ok(raw); assert.doesNotMatch(raw, /signature|video_url|cookie|csrf/);
    const reload = harness({storage: h.storage}); await reload.init(); await reload.receive();
    assert.equal(reload.elements['uploader-title-input'].value, 'Tiêu đề riêng'); assert.equal(reload.uploader.state().scheduled_local, '2030-01-02T08:00');
    for (let i = 0; i < 12; i++) { await h.receive(snapshot('draft-' + i)); await h.change('uploader-title-input', 'Nháp ' + i).done; }
    const data = JSON.parse(h.storage.get(helpers.draftStorageKey)); assert.equal(data.version, 1); assert.equal(data.projects.length, 10); assert.ok(Buffer.byteLength(h.storage.get(helpers.draftStorageKey)) <= 262144);
    const valid = data.projects[0]; assert.ok(helpers.validatedDraft(valid));
    for (const altered of [{revision: -1}, {profile_ids: ['not-uuid']}, {scheduled_local: '<bad>'}, {metadata: {...valid.metadata, title: '\u0000'}}, {pending: {request_id: 'bad'}}]) assert.equal(helpers.validatedDraft({...valid, ...altered}), null);
    const corrupt = harness({storage: new Map([[helpers.draftStorageKey, '{broken']])}); await corrupt.init(); await corrupt.receive(); assert.equal(corrupt.uploader.state().metadata.title, 'Tiêu đề gốc');
    const future = harness({storage: new Map([[helpers.draftStorageKey, JSON.stringify({version: 99, projects: [valid]})]])}); await future.init(); await future.receive(); assert.equal(future.uploader.state().metadata.title, 'Tiêu đề gốc');
    const full = harness({storageError: true}); await full.init(); await full.receive(); await full.change('uploader-title-input', 'Không mất nội dung').done;
    assert.equal(full.uploader.state().persisted, false); assert.match(full.elements['uploader-draft-status'].textContent, /đừng đóng/);
});
test('manual profile creation, login, finish, cancel and probe are explicit, local and CSRF-protected', async () => {
    let created = false, ready = false, currentAction = 'login';
    const session = overrides => ({id: 'session-a', profile_id: ids[0], action: currentAction, status: 'running', error: '', ...overrides});
    const h = harness({initialProfiles: [], handler: (url, options) => {
        if (url === '/api/uploader/profiles' && options.method === 'POST') { created = true; return profile({session_status: 'unknown'}); }
        if (url === '/api/uploader/profiles' && options.method === 'GET') return {profiles: created ? [profile({session_status: ready ? 'ready' : 'unknown'})] : []};
        if (url.endsWith('/login')) { currentAction = 'login'; return session(); }
        if (url.endsWith('/check')) { currentAction = 'check'; return session(); }
        if (url.endsWith('/finish')) { ready = true; return session({status: 'success'}); }
        if (url.endsWith('/cancel')) return session({status: 'cancelled'});
    }});
    await h.init(); h.elements['uploader-profile-platform'].value = 'youtube'; await h.change('uploader-profile-name', 'Kênh chính').done;
    await h.elements['uploader-profile-form'].emit('submit').done; await settle();
    assert.equal(h.calls.filter(call => call.url.endsWith('/login')).length, 0, 'create must not automatically open login');
    await h.action('uploader-profile-list', 'Đăng nhập thủ công'); assert.equal(h.uploader.state().sessions[0].status, 'running');
    const mutations = () => h.calls.filter(call => call.options.method !== 'GET'); assert.equal(mutations().filter(call => call.url.endsWith('/finish')).length, 0);
    await h.action('uploader-profile-list', 'Tôi đã đăng nhập xong'); assert.equal(h.uploader.state().profiles[0].session_status, 'ready');
    await h.action('uploader-profile-list', 'Kiểm tra phiên'); assert.equal(h.uploader.state().sessions[0].action, 'check');
    await h.action('uploader-profile-list', 'Huỷ phiên thao tác'); assert.equal(h.uploader.state().sessions[0].status, 'cancelled');
    for (const call of mutations()) { assert.equal(call.options.headers.get('X-VietSub-Token'), 'csrf-uploader-token'); assert.equal(call.options.credentials, 'same-origin'); }
    assert.equal(h.calls.some(call => call.url.includes('example.invalid')), false);
});
test('multi-account selection respects the capability matrix and requires deliberate public acknowledgement', async () => {
    const h = harness({initialProfiles: [profile(), profile({id: ids[1], platform: 'tiktok', name: 'Kênh TikTok'})], handler: (url, options) => {
        if (url === '/api/uploader/queue' && options.method === 'POST') { const body = JSON.parse(options.body); return {jobs: body.profile_ids.map((id, index) => queueTask({id: 'q-' + index, profile_id: id, platform: index ? 'tiktok' : 'youtube', visibility: body.visibility}))}; }
    }});
    await h.init(); await h.receive(); await h.select(); await h.select(ids[1]);
    await h.change('uploader-visibility', 'private', 'change').done;
    await h.elements['uploader-publish-form'].emit('submit').done; assert.match(h.elements['uploader-submit-error'].textContent, /TikTok không hỗ trợ/);
    await h.change('uploader-visibility', 'public', 'change').done;
    await h.elements['uploader-publish-form'].emit('submit').done; assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 0);
    await h.change('uploader-confirm-public', true, 'change').done;
    await h.change('uploader-title-input', 'Đổi nội dung sau xác nhận').done; assert.equal(h.elements['uploader-confirm-public'].checked, false);
    await h.change('uploader-confirm-public', true, 'change').done;
    await h.elements['uploader-publish-form'].emit('submit').done; await settle();
    const call = h.calls.find(call => call.url === '/api/uploader/queue' && call.options.method === 'POST'), body = JSON.parse(call.options.body);
    assert.deepEqual(body.profile_ids, ids); assert.equal(body.confirm_public, true); assert.equal(body.job_id, 'job-a'); assert.equal(body.visibility, 'public'); assert.equal(body.scheduled_at, null);
    assert.match(body.request_id, /^[a-f\d-]{36}$/); assert.equal(body.metadata.title, 'Đổi nội dung sau xác nhận');
    assert.equal(h.uploader.state().jobs.length, 2); assert.match(h.elements['uploader-status'].textContent, /không phải xác nhận đăng thành công/);
});
test('source fallback must be acknowledged; local scheduling has an explicit +07:00 payload', async () => {
    const snap = snapshot('job-a', {video_url: '/api/projects/job-a/video?signature=x&asset=source'});
    const h = harness({handler: (url, options) => {
        if (url === '/api/uploader/projects/job-a') return {snapshot: snap, metadata: snap.manifest.metadata, asset: 'source', video_url: snap.video_url};
        if (url === '/api/uploader/queue' && options.method === 'POST') return {jobs: [queueTask({status: 'scheduled', scheduled_at: JSON.parse(options.body).scheduled_at})]};
    }});
    await h.init(); await h.receive(snap); await h.select();
    await h.elements['uploader-publish-form'].emit('submit').done; assert.match(h.elements['uploader-submit-error'].textContent, /chủ động dùng video nguồn/);
    await h.change('uploader-source-ack', true, 'change').done;
    const tomorrow = new Date(Date.now() + 2 * 86400000).toISOString().slice(0, 10) + 'T09:30';
    await h.change('uploader-schedule-mode', 'local', 'change').done; await h.change('uploader-scheduled-at', tomorrow).done;
    await h.elements['uploader-publish-form'].emit('submit').done;
    const call = h.calls.find(call => call.options.method === 'POST'); assert.equal(JSON.parse(call.options.body).scheduled_at, tomorrow + ':00+07:00');
});
test('lost queue POST keeps an identical idempotency key and payload, including across reload, and never auto-resubmits', async () => {
    let submits = 0;
    const handler = (url, options) => {
        if (url === '/api/uploader/queue' && options.method === 'POST') return ++submits === 1 ? new Error('Response lost') : {jobs: [queueTask()]};
    };
    const h = harness({handler}); await h.init(); await h.receive(); await h.select();
    await h.elements['uploader-publish-form'].emit('submit').done; await settle();
    assert.equal(h.uploader.state().uncertain, true); const first = h.calls.find(call => call.options.method === 'POST');
    const requestId = JSON.parse(first.options.body).request_id;
    assert.equal(h.elements['uploader-content-fields'].disabled, true); assert.match(h.elements['uploader-submit-error'].textContent, /cùng request ID/);
    const stored = JSON.parse(h.storage.get(helpers.draftStorageKey)); assert.equal(stored.projects[0].pending.request_id, requestId);
    const reload = harness({storage: h.storage, handler}); await reload.init(); await reload.receive();
    assert.equal(submits, 1, 'restoring a pending request must never retry it automatically');
    assert.equal(reload.uploader.state().request_id, requestId); assert.equal(reload.uploader.state().uncertain, true);
    await reload.elements['uploader-publish-form'].emit('submit').done;
    const second = reload.calls.find(call => call.options.method === 'POST'); assert.equal(second.options.body, first.options.body);
    assert.equal(reload.uploader.state().uncertain, false); assert.equal(reload.uploader.state().jobs[0].status, 'queued');
});
test('queue submission is single-flight and malformed acceptance stays uncertain, not success', async () => {
    const delayed = deferred();
    const h = harness({handler: (url, options) => url === '/api/uploader/queue' && options.method === 'POST' ? delayed.promise : undefined});
    await h.init(); await h.receive(); await h.select();
    const first = h.elements['uploader-publish-form'].emit('submit'); await settle();
    await h.elements['uploader-publish-form'].emit('submit').done; assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 1);
    delayed.resolve({jobs: [queueTask({profile_id: ids[1]})]}); await first.done; await settle();
    assert.equal(h.uploader.state().jobs.length, 0); assert.equal(h.uploader.state().uncertain, true); assert.match(h.elements['uploader-submit-error'].textContent, /không khớp/);
});
test('metadata save is explicit and AI completion does not overwrite later edits or switch projects', async () => {
    const ai = {id: 'metadata-a', status: 'queued', error: ''}; let resultTitle = 'AI title';
    const h = harness({handler: (url, options) => {
        if (url.endsWith('/projects/job-a/metadata')) return ai;
        if (url.endsWith('/metadata/metadata-a')) return {...ai, status: 'success', metadata: {title: resultTitle, description: 'AI description', hashtags: ['#ai']}};
    }});
    await h.init(); await h.receive(); await h.change('uploader-title-input', 'Nội dung duyệt').done;
    assert.equal(h.calls.some(call => call.options.method === 'POST'), false);
    await h.elements['uploader-save-metadata'].emit('click').done;
    const save = h.calls.find(call => call.options.method === 'POST'); assert.equal(save.url, '/api/uploader/projects/job-a'); assert.equal(JSON.parse(save.options.body).metadata.title, 'Nội dung duyệt'); assert.equal(h.uploader.state().dirty, false);
    await h.elements['uploader-generate-metadata'].emit('click').done; await h.change('uploader-title-input', 'Chỉnh trong lúc chờ AI').done;
    await h.poll(); assert.equal(h.uploader.state().metadata.title, 'Chỉnh trong lúc chờ AI'); assert.match(h.elements['uploader-status'].textContent, /không tự ghi đè/);
    await h.elements['uploader-generate-metadata'].emit('click').done; await h.receive(snapshot('job-b')); resultTitle = 'AI cho A'; await h.poll();
    assert.equal(h.uploader.state().job_id, 'job-b'); assert.equal(h.uploader.state().metadata.title, 'Tiêu đề gốc');
    await h.receive(); assert.equal(h.uploader.state().metadata.title, 'AI cho A');
    assert.equal(h.calls.some(call => call.url === '/api/uploader/queue' && call.options.method === 'POST'), false);
});
test('arbitrary MP4 upload is multipart/local, preview uses only its signed media URL, and AI stays disabled without SRT', async () => {
    const h = harness({handler: (url, options) => {
        if (url === '/api/uploader/media') return {id: 'media-a', name: 'local.mp4', size_bytes: 12, metadata: {title: 'local', description: '', hashtags: []}, video_url: '/api/uploader/media/media-a/video?signature=test'};
        if (url === '/api/uploader/queue' && options.method === 'POST') return {jobs: [queueTask()]};
    }});
    await h.init(); h.elements['uploader-media-file'].files = [Object.assign(new Blob(['fake-mp4'], {type: 'video/mp4'}), {name: 'local.mp4'})];
    await h.elements['uploader-media-file'].emit('change').done; assert.equal(h.elements['uploader-media-upload'].disabled, false);
    await h.elements['uploader-media-form'].emit('submit').done; await settle();
    const upload = h.calls.find(call => call.url === '/api/uploader/media'); assert.ok(upload.options.body instanceof FormData); assert.equal(upload.options.headers.get('Content-Type'), null); assert.equal(upload.options.headers.get('X-VietSub-Token'), 'csrf-uploader-token');
    assert.equal(h.uploader.state().media_id, 'media-a'); assert.equal(h.elements['uploader-video'].src, '/api/uploader/media/media-a/video?signature=test');
    assert.equal(h.elements['uploader-generate-metadata'].disabled, true); await h.elements['uploader-generate-metadata'].emit('click').done;
    assert.equal(h.calls.some(call => call.url.includes('/metadata')), false); await h.select(); await h.elements['uploader-publish-form'].emit('submit').done;
    const body = JSON.parse(h.calls.find(call => call.url === '/api/uploader/queue' && call.options.method === 'POST').options.body); assert.equal(body.media_id, 'media-a'); assert.equal(Object.hasOwn(body, 'job_id'), false);
});
test('invalid MP4 and a late media response cannot replace a new project or trigger platform work', async () => {
    const slow = deferred(); const h = harness({handler: url => url === '/api/uploader/media' ? slow.promise : undefined}); await h.init();
    h.elements['uploader-media-file'].files = [Object.assign(new Blob(['x'], {type: 'video/x-matroska'}), {name: 'bad.mkv'})];
    await h.elements['uploader-media-form'].emit('submit').done; assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 0);
    h.elements['uploader-media-file'].files = [Object.assign(new Blob(['x'], {type: 'video/mp4'}), {name: 'ok.mp4'})];
    const pending = h.elements['uploader-media-form'].emit('submit'); await settle(); await h.receive(snapshot('job-new'));
    slow.resolve({id: 'media-a', name: 'ok.mp4', size_bytes: 1, metadata: {}, video_url: '/api/uploader/media/media-a/video?signature=test'}); await pending.done;
    assert.equal(h.uploader.state().job_id, 'job-new'); assert.equal(h.uploader.state().media_id, ''); assert.match(h.elements['uploader-media-status'].textContent, /Không tự thay project/);
});
test('queue progress/errors are backend-only, screenshot links stay exact and local, retry/cancel show duplicate warnings', async () => {
    const uncertain = queueTask({status: 'uncertain', phase: 'verify', percent: 73, error: 'Could not verify', screenshot_url: '/api/uploader/queue/upload-a/screenshot?signature=test', result_url: 'https://remote.invalid/post'});
    const h = harness({initialJobs: [uncertain], handler: (url, options) => {
        if (url.endsWith('/retry')) return queueTask({status: 'queued'});
        if (url.endsWith('/cancel')) return queueTask({status: 'cancelled', percent: 73});
    }});
    await h.init(); let link = h.find('uploader-publish-jobs', el => el.tagName === 'A'); assert.equal(link.href, uncertain.screenshot_url);
    assert.equal(h.find('uploader-publish-jobs', el => el.href === uncertain.result_url), undefined);
    assert.match(h.find('uploader-publish-jobs', el => el.className === 'uploader-job-error').textContent, /Could not verify/);
    assert.equal(h.find('uploader-publish-jobs', el => el.attributes.role === 'progressbar').attributes['aria-valuenow'], '73');
    await h.action('uploader-publish-jobs', 'Thử lại'); assert.match(h.confirmations[0], /NGUY CƠ ĐĂNG TRÙNG/);
    const retry = h.calls.find(call => call.url.endsWith('/retry')); assert.deepEqual(JSON.parse(retry.options.body), {confirm_uncertain: true});
    assert.equal(h.uploader.state().jobs[0].status, 'queued');
    await h.action('uploader-publish-jobs', 'Huỷ tác vụ'); assert.match(h.confirmations[1], /không xoá được bài đăng/); assert.equal(h.uploader.state().jobs[0].status, 'cancelled');
    assert.ok(h.calls.every(call => call.options.headers.get('X-VietSub-Token') === 'csrf-uploader-token'));
});
test('declined retry/delete confirmations cause no mutation, and absent runtime never fakes readiness', async () => {
    const h = harness({initialJobs: [queueTask({status: 'error'})], confirm: false, caps: capabilities({browser_available: false})});
    await h.init(); await h.receive(); await h.action('uploader-publish-jobs', 'Thử lại'); await h.action('uploader-profile-list', 'Xoá profile');
    assert.equal(h.calls.some(call => call.options.method !== 'GET'), false); assert.equal(h.elements['uploader-queue-submit'].disabled, true); assert.match(h.elements['uploader-runtime-status'].textContent, /Chưa có trình duyệt/);
    assert.equal(h.find('uploader-profile-list', el => el.tagName === 'BUTTON' && el.textContent === 'Đăng nhập thủ công').disabled, true);
});
test('three failed read-only polls stop until explicit refresh, without resubmitting or inventing success', async () => {
    let reads = 0;
    const h = harness({initialJobs: [queueTask({status: 'running', percent: 41})], handler: (url, options) => {
        if (url === '/api/uploader/queue' && options.method === 'GET' && ++reads > 1) return new Error('Local app unavailable');
    }});
    await h.init(); await h.receive(); await h.change('uploader-title-input', 'Bản sửa không mất').done;
    await h.poll(); await h.poll(); await h.poll();
    assert.equal([...h.timers.values()].some(timer => timer.delay === 1800), false);
    assert.equal(h.uploader.state().jobs[0].status, 'running'); assert.equal(h.uploader.state().jobs[0].percent, 41);
    assert.equal(h.elements['uploader-title-input'].value, 'Bản sửa không mất'); assert.match(h.elements['uploader-status'].textContent, /3 lỗi liên tiếp/);
    assert.ok(h.calls.every(call => call.options.method === 'GET'));
});
test('wrong-job project responses block queueing rather than loading another asset or metadata', async () => {
    const h = harness({handler: url => url === '/api/uploader/projects/job-a' ? {snapshot: snapshot('wrong-job'), metadata: {title: 'wrong'}, asset: 'edited', video_url: snapshot('wrong-job').video_url} : undefined});
    await h.init(); await h.receive(); assert.equal(h.uploader.state().job_id, 'job-a'); assert.equal(h.uploader.state().loadFailed, true);
    assert.equal(h.elements['uploader-queue-submit'].disabled, true); assert.notEqual(h.elements['uploader-title-input'].value, 'wrong'); assert.match(h.elements['uploader-status'].textContent, /sai project/);
});

test('queue polling performs GET only, preserves form edits, and suppresses an older GET that races a successful submit', async () => {
    const old = deferred(); let reads = 0;
    const h = harness({handler: (url, options) => {
        if (url === '/api/uploader/queue' && options.method === 'GET' && ++reads === 2) return old.promise;
        if (url === '/api/uploader/queue' && options.method === 'POST') return {jobs: [queueTask({status: 'running', percent: 12})]};
    }});
    await h.init(); await h.receive(); await h.select(); await h.change('uploader-title-input', 'Giữ qua poll').done;
    const reading = h.elements['uploader-queue-refresh'].emit('click'); await settle();
    await h.elements['uploader-publish-form'].emit('submit').done; old.resolve({jobs: []}); await reading.done;
    assert.equal(h.uploader.state().jobs[0].status, 'running'); assert.equal(h.elements['uploader-title-input'].value, 'Giữ qua poll');
    const mutationCount = h.calls.filter(call => call.options.method !== 'GET').length; await h.poll();
    assert.equal(h.calls.filter(call => call.options.method !== 'GET').length, mutationCount); assert.equal(h.elements['uploader-title-input'].value, 'Giữ qua poll');
});

test('UI defaults to assisted upload mode, toggles auto/debug_mode, and validates signed trace URLs with privacy warning', async () => {
    assert.equal(helpers.safeTraceUrl('/api/uploader/queue/t1/trace?signature=sig', 't1'), '/api/uploader/queue/t1/trace?signature=sig');
    for (const bad of ['/api/uploader/queue/t2/trace?signature=sig', '/api/uploader/queue/t1/trace', 'https://evil.invalid/api/uploader/queue/t1/trace?signature=sig', '/api/uploader/queue/t1/trace?signature=sig&extra=1']) {
        assert.equal(helpers.safeTraceUrl(bad, 't1'), '');
    }
    const awaiting = queueTask({id: 'upload-assisted', status: 'awaiting_user_submit', phase: 'awaiting_user_submit', step: 'submit', percent: 88, upload_mode: 'assisted', awaiting_deadline: Date.now() / 1000 + 300});
    const failed = queueTask({id: 'upload-trace', status: 'error', phase: 'error', failed_step: 'fill_metadata', percent: 50, debug_mode: true, error: 'Selector changed', trace_url: '/api/uploader/queue/upload-trace/trace?signature=tracesig'});
    const h = harness({initialJobs: [awaiting, failed], handler: (url, options) => {
        if (url === '/api/uploader/queue' && options.method === 'POST') return {jobs: [queueTask({id: 'q-new', upload_mode: JSON.parse(options.body).upload_mode, debug_mode: JSON.parse(options.body).debug_mode})]};
        if (url.endsWith('/confirm-submitted')) return queueTask({id: 'upload-assisted', status: 'success', phase: 'done', percent: 100});
    }});
    await h.init(); await h.receive();
    assert.equal(h.uploader.state().upload_mode, 'assisted');
    assert.equal(h.uploader.state().debug_mode, false);
    assert.equal(h.elements['uploader-upload-mode'].value, 'assisted');
    assert.equal(h.elements['uploader-debug-mode'].checked, false);

    // Assisted job renders guidance + Xác nhận đã đăng button
    assert.ok(h.find('uploader-publish-jobs', el => el.className === 'uploader-job-assisted'));
    await h.action('uploader-publish-jobs', 'Xác nhận đã đăng');
    const confirmCall = h.calls.find(call => call.url === '/api/uploader/queue/upload-assisted/confirm-submitted');
    assert.ok(confirmCall);
    assert.equal(confirmCall.options.headers.get('X-VietSub-Token'), 'csrf-uploader-token');

    // Trace download link + privacy warning + failed_step indicator
    const traceLink = h.find('uploader-publish-jobs', el => el.tagName === 'A' && el.textContent.includes('Playwright Trace'));
    assert.equal(traceLink.href, '/api/uploader/queue/upload-trace/trace?signature=tracesig');
    const traceWarn = h.find('uploader-publish-jobs', el => el.className === 'uploader-trace-warning');
    assert.match(traceWarn.textContent, /không chia sẻ công khai/i);
    const failedStepNode = h.find('uploader-publish-jobs', el => el.className === 'uploader-job-step-failed');
    assert.match(failedStepNode.textContent, /4\/7/);

    // Switch to auto + debug_mode=true and verify payload
    await h.change('uploader-upload-mode', 'auto', 'change').done;
    await h.change('uploader-debug-mode', true, 'change').done;
    await h.select();
    await h.elements['uploader-publish-form'].emit('submit').done; await settle();
    const post = h.calls.find(call => call.url === '/api/uploader/queue' && call.options.method === 'POST');
    const body = JSON.parse(post.options.body);
    assert.equal(body.upload_mode, 'auto');
    assert.equal(body.debug_mode, true);
});

test('YouTube Data API v3 JSON parser, OAuth setup, and queueing without local browser runtime', async () => {
    const okJson = JSON.stringify({installed: {client_id: '123456-abc.apps.googleusercontent.com', client_secret: 'GOCSPX-secret-12345'}});
    const parsed = helpers.parseYoutubeClientSecretJson(okJson);
    assert.equal(parsed.error, '');
    assert.equal(parsed.client_id, '123456-abc.apps.googleusercontent.com');
    assert.equal(parsed.client_secret, 'GOCSPX-secret-12345');
    assert.match(helpers.parseYoutubeClientSecretJson(JSON.stringify({web: {client_id: 'x', client_secret: 'y'}})).error, /Desktop app/);

    const ytApiProfile = profile({
        upload_method: 'youtube_api',
        session_status: 'ready',
        youtube_api: {configured: true, connected: true, channel_title: 'Kênh API', token_expires_at: 9999999999, scopes: []},
    });
    const ytApiProfile2 = profile({
        id: ids[1],
        name: 'Kênh API 2',
        upload_method: 'youtube_api',
        session_status: 'unknown',
        youtube_api: {configured: true, connected: false, channel_title: '', token_expires_at: null, scopes: []},
    });
    const h = harness({
        caps: capabilities({browser_available: false}),
        initialProfiles: [ytApiProfile, ytApiProfile2],
        handler: (url, options) => {
            if (url === '/api/uploader/queue' && options.method === 'POST') return {jobs: [queueTask({visibility: 'private'})]};
            if (url.endsWith('/youtube-oauth/start')) return {id: 'oauth-1', profile_id: ids[1], action: 'youtube_oauth', status: 'running', error: ''};
        },
    });
    await h.init(); await h.receive(); await h.select();
    await h.change('uploader-visibility', 'private', 'change').done;
    // Even with browser_available: false, connected YouTube API profile can submit!
    assert.equal(h.elements['uploader-queue-submit'].disabled, false);
    await h.elements['uploader-publish-form'].emit('submit').done; await settle();
    const qCall = h.calls.find(call => call.url === '/api/uploader/queue' && call.options.method === 'POST');
    assert.ok(qCall);
    assert.equal(JSON.parse(qCall.options.body).upload_mode, 'assisted');
    // OAuth connect button on idle configured profile triggers /youtube-oauth/start with CSRF token
    const connectBtn = h.find('uploader-profile-list', el => el.dataset.uploaderFocus === `yt-connect-${ids[1]}`);
    assert.ok(connectBtn && !connectBtn.disabled);
    await connectBtn.emit('click').done; await settle();
    const oauthCall = h.calls.find(call => call.url === `/api/uploader/profiles/${ids[1]}/youtube-oauth/start`);
    assert.ok(oauthCall);
    assert.equal(oauthCall.options.headers.get('X-VietSub-Token'), 'csrf-uploader-token');
});

test('channel preset event applies valid profiles, upload mode, and visibility while keeping public confirm unchecked', async () => {
    const h = harness({initialProfiles: [profile({id: ids[0], name: 'Kênh 1'})]});
    await h.init(); await h.receive();
    h.elements['uploader-confirm-public'].checked = true;
    await h.document.emit('vietsub:apply-channel-preset', {
        detail: {
            preset_name: 'Kênh Shorts',
            publish_defaults: {
                profile_ids: [ids[0], '33333333-3333-4333-8333-333333333333'],
                upload_mode: 'assisted',
                visibility: 'public',
            },
        },
    }).done;
    await settle();
    assert.deepEqual(plain(h.uploader.state().selected), [ids[0]]);
    assert.equal(h.uploader.state().upload_mode, 'assisted');
    assert.equal(h.uploader.state().visibility, 'public');
    assert.equal(h.elements['uploader-confirm-public'].checked, false, 'public confirmation must stay manual');
});

