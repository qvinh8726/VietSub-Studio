import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/editor.js', import.meta.url), 'utf8');
const workbench = readFileSync(new URL('../../static/workbench.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../../templates/index.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../static/style.css', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const approx = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-6, `${actual} != ${expected}`);
const snapshot = (job = 'job-a', overrides = {}) => ({job_id: job, workspace: 'editor', ready: true,
    availability: {video: true, translated_srt: true}, video_url: `/api/projects/${job}/video?signature=test&asset=source`,
    manifest: {schema_version: 1, project_id: `project-${job}`, project_name: `Project ${job}`,
        crop_box: {x: .04, y: .64, width: .92, height: .3}, source_resolution: {width: 1920, height: 1080},
        assets: {}, metadata: {title: 'Tiêu đề gốc'}, stages: {}}, ...overrides});
const task = (overrides = {}) => ({id: 'task-a', job_id: 'job-a', action: 'render', status: 'queued', phase: 'prepare',
    percent: 0, fps: null, error: '', warnings: [], output_url: '', output_kind: 'video', output_path: '', ...overrides});
const caps = (overrides = {}) => ({ffmpeg: true, ffprobe: true, encoders: {libx264: true, h264_nvenc: false},
    optional: {edge_tts: {available: false, detail: 'not installed', install_command: 'python -m pip install edge-tts'},
        audio_separator: {available: false, detail: 'not installed', install_command: 'python -m pip install audio-separator'}},
    presets: ['standard', 'anime', 'social'], fonts: ['Be Vietnam Pro', 'Noto Sans SC', 'JetBrains Mono'], ...overrides});

function eventTarget() {
    const listeners = new Map();
    return {listeners,
        addEventListener(type, fn) { const items = listeners.get(type) || []; items.push(fn); listeners.set(type, items); },
        emit(type, detail = {}) {
            const event = {target: this, currentTarget: this, button: 0, pointerId: 1, defaultPrevented: false,
                preventDefault() { this.defaultPrevented = true; }, ...detail};
            return {event, done: Promise.all((listeners.get(type) || []).map(fn => fn(event)))};
        }};
}
function harness({handler, capabilities = caps(), withWorkbench = false, storage = new Map(), storageError = false} = {}) {
    const elements = {}, calls = [], timers = new Map(), copies = [], handoffs = [], observers = [];
    let timerId = 0, navigation = 0;
    const document = Object.assign(eventTarget(), {activeElement: null, documentElement: {dataset: {}, classList: {add() {}, remove() {}}},
        getElementById(id) { assert.ok(elements[id], `Missing real markup #${id}`); return elements[id]; },
        querySelector(selector) { return {content: selector.includes('api-token') ? 'csrf-editor-token' : '1.8.0'}; },
        createElement: tag => element('', tag),
    });
    function element(id, tag = 'div') {
        const attrs = {}, captures = new Set(), classes = new Set();
        const el = Object.assign(eventTarget(), {id, tagName: tag.toUpperCase(), attributes: attrs, dataset: {}, children: [], parentElement: null,
            hidden: false, disabled: false, checked: false, value: '', textContent: '', type: '', labels: [],
            clientWidth: 800, clientHeight: 400, offsetHeight: 30, videoWidth: 0, videoHeight: 0,
            paused: true, currentTime: 0, duration: NaN, readyState: 0, isConnected: true, loads: 0, selections: 0,
            style: {setProperty(key, value) { this[key] = value; }},
            classList: {add: key => classes.add(key), remove: key => classes.delete(key), contains: key => classes.has(key),
                toggle(key, value) { if (value ?? !classes.has(key)) classes.add(key); else classes.delete(key); }},
            setAttribute(key, value) { attrs[key] = String(value); }, getAttribute: key => attrs[key] ?? null,
            removeAttribute(key) { delete attrs[key]; if (key === 'src') delete this.src; },
            appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
            replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); },
            get options() { return this.children.filter(child => child.tagName === 'OPTION'); },
            focus() { document.activeElement = this; }, select() { this.selections++; },
            contains(other) { return this === other || this.children.some(child => child.contains(other)); },
            querySelectorAll(selector) {
                const all = [];
                const walk = parent => { for (const child of parent.children) { if (selector === '[data-editor-option]' ? child.dataset.editorOption : true) all.push(child); walk(child); } };
                walk(this); return all;
            },
            getBoundingClientRect() { return this.rect || {left: 0, top: 0, width: this.clientWidth, height: this.clientHeight}; },
            getClientRects() { return this.hidden ? [] : [this.getBoundingClientRect()]; },
            pause() { this.paused = true; }, load() { this.loads++; },
            setPointerCapture(id) { captures.add(id); }, hasPointerCapture: id => captures.has(id), releasePointerCapture(id) { captures.delete(id); },
        });
        Object.defineProperty(el, 'options', {get() { return this.children.filter(child => child.tagName === 'OPTION'); }});
        return el;
    }
    const stack = [], voids = new Set(['input', 'meta', 'link', 'br', 'img', 'hr', 'source']);
    for (const match of template.matchAll(/<\/?[a-z][\w:-]*(?:[^<>"']|"[^"]*"|'[^']*')*>/gi)) {
        const token = match[0], tag = /^<\/?([\w:-]+)/.exec(token)[1].toLowerCase();
        if (token.startsWith('</')) { const at = stack.findLastIndex(node => node.tagName.toLowerCase() === tag); if (at >= 0) stack.splice(at); continue; }
        const attrs = {};
        for (const attr of token.slice(tag.length + 1, -1).matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) attrs[attr[1]] = attr[2] ?? attr[3] ?? attr[4] ?? '';
        const node = element(attrs.id || '', tag);
        Object.assign(node.attributes, attrs);
        node.hidden = Object.hasOwn(attrs, 'hidden'); node.disabled = Object.hasOwn(attrs, 'disabled'); node.checked = Object.hasOwn(attrs, 'checked');
        node.inert = Object.hasOwn(attrs, 'inert'); node.type = attrs.type || ''; node.value = attrs.value || '';
        for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) node.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
        if (node.id) elements[node.id] = node;
        stack.at(-1)?.appendChild(node);
        if (!voids.has(tag)) stack.push(node);
    }
    const window = Object.assign(eventTarget(), {innerWidth: 1280, innerHeight: 720, location: {href: 'http://127.0.0.1:8123/'},
        matchMedia: () => ({matches: false, addEventListener() {}}),
        VietSubWorkbench: {projectReceived(...args) { handoffs.push(args); }, selectWorkspace(name) { navigation++; handoffs.push(['select', name]); }, workspaceState: () => ({name: 'editor', revision: navigation})},
    });
    const context = vm.createContext({window, document, console, URL, Headers, AbortController, FormData, Blob,
        navigator: {clipboard: {async writeText(text) { copies.push(text); }}},
        ResizeObserver: class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} },
        setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout: id => timers.delete(id),
        requestAnimationFrame() { return 1; }, localStorage: {
            getItem(key) { if (storageError) throw new Error('storage unavailable'); return storage.get(key) ?? null; },
            setItem(key, value) { if (storageError) throw new Error('storage full'); storage.set(key, value); },
        },
        fetch: async (url, options) => {
            calls.push({url, options});
            let data;
            if (handler) data = await handler(url, options);
            if (data === undefined && url === '/api/editor/capabilities') data = capabilities;
            if (data === undefined && /^\/api\/editor\/projects\//.test(url)) {
                const job = decodeURIComponent(url.split('/')[4]);
                data = {snapshot: snapshot(job), options: options.method === 'POST' ? JSON.parse(options.body).options : {...plain(window.VietSubEditor.helpers.defaults), crop_box: snapshot(job).manifest.crop_box}, active_task: null};
            }
            if (data instanceof Error) throw data;
            if (data?.response) return data.response;
            return {ok: true, status: 200, json: async () => data ?? {}};
        },
    });
    if (withWorkbench) vm.runInContext(workbench, context, {filename: 'static/workbench.js'});
    vm.runInContext(source, context, {filename: 'static/editor.js'});
    const editor = window.VietSubEditor;
    function change(id, value, type = 'input') {
        const control = elements[id];
        if (control.type === 'checkbox') control.checked = value; else control.value = String(value);
        return control.emit(type);
    }
    async function runTimer(delay) {
        const entry = [...timers].find(([, timer]) => timer.delay === delay);
        assert.ok(entry, `No pending timer at ${delay}ms; have ${[...timers.values()].map(timer => timer.delay)}`);
        timers.delete(entry[0]); await entry[1].fn(); await settle();
    }
    return {editor, window, document, elements, calls, timers, copies, handoffs, observers, change, runTimer, storage,
        async init() { await window.emit('load').done; await settle(); },
        async receive(value = snapshot()) { editor.projectReceived(value); await settle(); },
        navigate() { navigation++; },
    };
}
const helpers = harness().editor.helpers;

test('options validation keeps flat documented defaults, rejects unsafe values, and strips unknown keys', () => {
    const valid = helpers.validateOptions({...plain(helpers.defaults), unexpected: '/arbitrary/path'});
    assert.deepEqual(plain(valid.errors), {});
    assert.equal(Object.hasOwn(valid.options, 'unexpected'), false);
    for (const [field, values] of Object.entries({blur_strength: [0, 41, true, '8', Infinity], padding_px: [-1, 101], font_size: [11, 121],
        outline: [-1, 11], shadow: [11], tts_speed: [.49, 2.1], tts_pitch: [-13, 13], solid_opacity: [-1, 1.1], bgm_volume: [2.1],
        watermark_opacity: [1.1], subtitle_enabled: ['true'], encoder: ['shell'], output_height: [1080, '1280'],
        solid_color: ['red', '#fff', '#abcdef;'], font: ['remote font'], banner_text: ['a'.repeat(201), '<text>\nline'], tts_voice: ['bad\u0000id']})) {
        for (const value of values) assert.ok(helpers.validateOptions({...plain(helpers.defaults), [field]: value}).errors[field], `${field}=${value}`);
    }
    assert.ok(helpers.validateOptions({...plain(helpers.defaults), watermark_enabled: true}).errors.watermark_path);
    assert.deepEqual(plain(helpers.validateOptions({banner_text: '<img src=x>', text_color: '#ABCDEF'}).errors), {});
    assert.equal(helpers.validateOptions({text_color: '#ABCDEF'}).options.text_color, '#abcdef');
});

test('crop validation and pointer adjustment stay normalized, bounded, and independent of DPR', () => {
    for (const invalid of [null, {}, {x: '0', y: 0, width: 1, height: 1}, {x: .9, y: 0, width: .2, height: 1},
        {x: 0, y: 0, width: 0, height: 1}, {x: 0, y: NaN, width: 1, height: 1}]) assert.equal(helpers.validCrop(invalid), false);
    const box = {x: .2, y: .3, width: .5, height: .2};
    assert.deepEqual(plain(helpers.adjustCrop(box, 'move', 10, -10)), {x: .5, y: 0, width: .5, height: .2});
    assert.deepEqual(plain(helpers.adjustCrop(box, 'nw', -.3, -.4)), {x: 0, y: 0, width: .7, height: .5});
    const tiny = helpers.adjustCrop(box, 'se', -10, -10); approx(tiny.width, .001); approx(tiny.height, .001);
    for (const sw of [1920, 1080, 720]) for (const sh of [1080, 1920, 720]) {
        const fit = helpers.containRect(800, 400, sw, sh);
        approx(fit.width / fit.height, sw / sh); assert.ok(fit.x >= 0 && fit.y >= 0);
        approx(fit.x * 2 + fit.width, 800); approx(fit.y * 2 + fit.height, 400);
    }
    assert.equal(helpers.containRect(0, 100, 1920, 1080), null);
});

test('boot initializes once on window load, checks runtime only, and never lists remote voices', async () => {
    const h = harness(); assert.equal(h.calls.length, 0);
    await h.init(); h.editor.init(); await h.window.emit('load').done;
    assert.deepEqual(h.calls.map(call => call.url), ['/api/editor/capabilities']);
    assert.equal(h.elements['editor-save-btn'].disabled, true);
    assert.equal(h.elements['editor-render-btn'].disabled, true);
    assert.equal(h.elements['editor-cancel-btn'].disabled, true);
    assert.equal(h.elements['editor-options-fieldset'].disabled, true);
    assert.equal(h.elements['editor-runtime-edge-command'].value, 'python -m pip install edge-tts');
    await h.elements['editor-runtime-edge-copy'].emit('click').done;
    assert.deepEqual(h.copies, ['python -m pip install edge-tts']);
    assert.equal(h.calls.length, 1, 'copy must never execute an installer API');
    assert.equal(h.elements['editor-render-btn'].listeners.get('click').length, 1);
});

test('pre-load handoff queues snapshot and the workbench hook calls the editor once without recursion', async () => {
    const h = harness({withWorkbench: true}); h.editor.projectReceived(snapshot());
    await h.init(); assert.equal(h.editor.state().job_id, 'job-a');
    let calls = 0; h.window.VietSubEditor = {projectReceived(value) { calls++; assert.equal(value.job_id, 'job-a'); }};
    h.window.VietSubWorkbench.init({endCropDrag() {}, request: async () => ({}), notify() {}, updateSteps() {}});
    h.window.VietSubWorkbench.projectReceived('editor', snapshot());
    assert.equal(calls, 1);
    h.window.VietSubWorkbench.projectReceived('uploader', {...snapshot(), workspace: 'uploader'});
    assert.equal(calls, 1);
});

test('project load fills controls while repeated handoff preserves dirty inputs, crop and focus', async () => {
    const h = harness(); await h.init(); await h.receive();
    assert.equal(h.elements['editor-options-fieldset'].disabled, false);
    assert.equal(h.elements['editor-font-size'].value, '48');
    h.change('editor-font-size', 66); h.change('editor-crop-x', 5);
    h.elements['editor-banner-text'].focus(); h.change('editor-banner-text', '<unsafe html is text>');
    const count = h.calls.length, state = h.editor.state();
    h.editor.projectReceived(snapshot()); await settle();
    assert.equal(h.calls.length, count, 'same-project handoff must not refetch or repaint draft');
    assert.equal(h.elements['editor-font-size'].value, '66');
    assert.equal(h.elements['editor-crop-x'].value, '5');
    assert.equal(h.document.activeElement, h.elements['editor-banner-text']);
    assert.deepEqual(plain(h.editor.state().options), plain(state.options)); assert.equal(h.editor.state().dirty, true);
    await h.receive(snapshot('job-b')); await h.receive();
    assert.equal(h.elements['editor-font-size'].value, '66', 'project switching restores cached draft');
    approx(h.editor.state().options.crop_box.x, .05);
});

test('invalid numeric crop reports ARIA error, preserves last valid overlay and blocks saving/rendering', async () => {
    const h = harness(); await h.init(); await h.receive();
    h.change('editor-crop-x', 90);
    assert.equal(h.elements['editor-crop-x'].getAttribute('aria-invalid'), 'true');
    assert.match(h.elements['editor-crop-error'].textContent, /100%/);
    assert.equal(h.elements['editor-save-btn'].disabled, true); assert.equal(h.elements['editor-render-btn'].disabled, true);
    approx(h.editor.state().options.crop_box.x, .04);
    h.change('editor-crop-x', 4);
    assert.equal(h.elements['editor-crop-error'].textContent, '');
    assert.equal(h.elements['editor-save-btn'].disabled, false);
    h.change('editor-blur-strength', '');
    assert.equal(h.elements['editor-blur-strength'].getAttribute('aria-invalid'), 'true');
    assert.equal(h.elements['editor-save-btn'].disabled, true);
    h.change('editor-blur-strength', 8);
    assert.equal(h.elements['editor-save-btn'].disabled, false);
});

test('overlay fits displayed source content, ignores letterboxing, supports numeric, pointer and keyboard adjustment', async () => {
    const h = harness(); await h.init(); await h.receive();
    const video = h.elements['editor-video']; video.hidden = false; video.rect = {left: 0, top: 0, width: 800, height: 400};
    video.videoWidth = 1080; video.videoHeight = 1920; video.emit('loadedmetadata');
    const layer = h.elements['editor-crop-layer'], box = h.elements['editor-crop-box'];
    assert.equal(layer.style.left, '287.5px'); assert.equal(layer.style.width, '225px'); assert.equal(layer.style.height, '400px');
    assert.equal(layer.hidden, false); assert.equal(box.style.left, '4%');
    box.emit('pointerdown', {clientX: 300, clientY: 280});
    h.window.emit('pointermove', {clientX: 304.5, clientY: 284}); h.window.emit('pointerup');
    approx(h.editor.state().options.crop_box.x, .06); approx(h.editor.state().options.crop_box.y, .65);
    assert.equal(box.hasPointerCapture(1), false);
    const before = h.editor.state().options.crop_box.width;
    box.emit('keydown', {key: 'ArrowLeft', shiftKey: true}); approx(h.editor.state().options.crop_box.width, before - .01);
    assert.match(box.getAttribute('aria-label'), /Vùng che: X/);
    const corner = box.children.find(child => child.dataset.editorHandle === 'nw');
    box.emit('pointerdown', {target: corner, clientX: 300, clientY: 280});
    h.window.emit('pointermove', {clientX: 297.75, clientY: 276}); h.window.emit('pointercancel');
    approx(h.editor.state().options.crop_box.x, .05); approx(h.editor.state().options.crop_box.y, .64);
    h.change('editor-crop-enabled', false, 'change'); assert.equal(layer.hidden, true); assert.equal(h.editor.state().options.crop_box, null);
    h.change('editor-crop-enabled', true, 'change'); assert.equal(layer.hidden, false);
    assert.match(css, /\.editor-source-surface > video\s*\{[^}]*object-fit: contain/);
});

test('saves use CSRF header, normalized flat options and preserve edits made during the request', async () => {
    const save = deferred();
    const h = harness({handler: (url, options) => options.method === 'POST' ? save.promise : undefined});
    await h.init(); await h.receive(); h.change('editor-font-size', 64);
    const saving = h.elements['editor-save-btn'].emit('click'); await settle();
    const call = h.calls.at(-1), sent = JSON.parse(call.options.body).options;
    assert.equal(call.url, '/api/editor/projects/job-a'); assert.equal(call.options.headers.get('X-VietSub-Token'), 'csrf-editor-token');
    assert.equal(call.options.headers.get('Content-Type'), 'application/json'); assert.equal(call.options.credentials, 'same-origin');
    assert.equal(sent.font_size, 64); assert.equal(sent.crop_box.y, .64);
    assert.equal(h.elements['editor-save-btn'].disabled, true);
    h.change('editor-font-size', 72); save.resolve({options: sent}); await saving.done; await settle();
    assert.equal(h.elements['editor-font-size'].value, '72'); assert.equal(h.editor.state().dirty, true);
});

test('task polling updates only task/output nodes and never loses an unsaved draft or source player', async () => {
    let reads = 0;
    const h = harness({handler: (url, options) => {
        if (url === '/api/editor/jobs' && options.method === 'POST') return task();
        if (url === '/api/editor/jobs/task-a') return ++reads === 1 ? task({status: 'running', phase: 'encode', percent: 47.3, fps: 39.2,
            warnings: ['CPU fallback <text>']}) : task({status: 'success', phase: 'done', percent: 100, output_url: '/api/editor/jobs/task-a/output?token=test', output_path: 'project/out.mp4'});
    }});
    await h.init(); await h.receive();
    const video = h.elements['editor-video']; video.src = snapshot().video_url; video.currentTime = 7; const loads = video.loads;
    await h.elements['editor-render-btn'].emit('click').done;
    assert.equal(h.elements['editor-render-btn'].disabled, true); assert.equal(h.elements['editor-cancel-btn'].disabled, false);
    h.change('editor-font-size', 70); h.elements['editor-banner-text'].focus(); h.change('editor-banner-text', 'Keep my draft');
    await h.runTimer(700);
    assert.equal(h.elements['editor-task-progress'].getAttribute('aria-valuenow'), '47.3');
    assert.equal(h.elements['editor-task-panel'].getAttribute('aria-busy'), 'true');
    assert.equal(h.elements['editor-task-fps'].textContent, '39.2 fps');
    assert.equal(h.elements['editor-task-warnings'].children[0].textContent, 'CPU fallback <text>');
    await h.runTimer(1000);
    assert.equal(h.elements['editor-font-size'].value, '70'); assert.equal(h.elements['editor-banner-text'].value, 'Keep my draft');
    assert.equal(h.document.activeElement, h.elements['editor-banner-text']); assert.equal(h.editor.state().dirty, true);
    assert.equal(video.currentTime, 7); assert.equal(video.src, snapshot().video_url); assert.equal(video.loads, loads);
    assert.equal(h.elements['editor-output-video'].src, '/api/editor/jobs/task-a/output?token=test');
    assert.equal(h.elements['editor-output-video'].hidden, false); assert.equal(h.elements['editor-output-audio'].hidden, true);
    assert.equal(h.elements['editor-send-publish-btn'].disabled, false); assert.equal(h.elements['editor-cancel-btn'].disabled, true);
    await h.receive(snapshot('job-b'));
    assert.equal(h.elements['editor-output'].hidden, true, 'a new project must not show the previous project output');
    assert.equal(h.elements['editor-output-video'].hidden, true);
    assert.equal(h.elements['editor-send-publish-btn'].disabled, true);
});

test('stale project and poll responses cannot overwrite a newer active project', async () => {
    const pendingPoll = deferred();
    const h = harness({handler: (url, options) => {
        if (url === '/api/editor/jobs' && options.method === 'POST') return task();
        if (url === '/api/editor/jobs/task-a') return pendingPoll.promise;
    }});
    await h.init(); await h.receive(); await h.elements['editor-render-btn'].emit('click').done;
    const polling = h.runTimer(700); await settle();
    await h.receive(snapshot('job-b')); h.change('editor-font-size', 89);
    pendingPoll.resolve(task({status: 'success', output_url: '/api/editor/jobs/task-a/output'})); await polling;
    assert.equal(h.editor.state().job_id, 'job-b'); assert.equal(h.editor.state().task, null);
    assert.equal(h.elements['editor-font-size'].value, '89'); assert.equal(h.elements['editor-output'].hidden, true);
    const load = deferred();
    const next = harness({handler: url => url === '/api/editor/projects/job-a' ? load.promise : undefined});
    await next.init(); next.editor.projectReceived(snapshot()); await settle(); await next.receive(snapshot('job-b'));
    load.resolve({snapshot: snapshot(), options: plain(helpers.defaults), active_task: null}); await settle();
    assert.equal(next.editor.state().job_id, 'job-b'); assert.equal(next.elements['editor-crop-y'].value, '64');
});

test('poll failures stop after three attempts and retry tracks the old task rather than submitting another', async () => {
    const h = harness({handler: (url, options) => {
        if (url === '/api/editor/jobs' && options.method === 'POST') return task();
        if (url === '/api/editor/jobs/task-a') throw new Error('poll unavailable');
    }});
    await h.init(); await h.receive(); await h.elements['editor-render-btn'].emit('click').done;
    await h.runTimer(700); await h.runTimer(2500); await h.runTimer(2500);
    assert.equal(h.elements['editor-task-retry'].hidden, false);
    assert.equal([...h.timers.values()].some(timer => [700, 1000, 2500].includes(timer.delay)), false);
    await h.elements['editor-task-retry'].emit('click').done; await h.runTimer(700);
    assert.equal(h.calls.filter(call => call.url === '/api/editor/jobs').length, 1);
});

test('cancel, voice loading and preview are explicit, protected, and preserve draft options', async () => {
    let pollCount = 0;
    const h = harness({capabilities: caps({optional: {edge_tts: {available: true}, audio_separator: {available: false}}}), handler: (url, options) => {
        if (url.startsWith('/api/editor/voices')) return {voices: [{id: 'vi-custom', name: 'Giọng Việt', language: 'vi', provider: 'capcut_internal'}], warnings: ['Remote listing requested']};
        if (url === '/api/editor/tts/preview') return task({id: 'preview-a', job_id: '', action: 'tts', output_kind: 'audio'});
        if (url === '/api/editor/jobs/preview-a') return task({id: 'preview-a', job_id: '', action: 'tts', status: 'success', output_kind: 'audio', output_url: '/api/editor/jobs/preview-a/output'});
        if (url === '/api/editor/jobs') return task({id: 'tts-a', action: 'tts'});
        if (url === '/api/editor/jobs/tts-a/cancel') return task({id: 'tts-a', action: 'tts', status: 'cancelled'});
        if (url === '/api/editor/jobs/tts-a') { pollCount++; return task({id: 'tts-a', action: 'tts', status: 'running'}); }
    }});
    await h.init(); await h.receive();
    assert.equal(h.calls.some(call => call.url.includes('/voices')), false);
    await h.elements['editor-load-voices-btn'].emit('click').done;
    assert.equal(h.elements['editor-tts-voice'].options.at(-1)?.textContent, 'Giọng Việt', h.elements['editor-status'].textContent);
    h.change('editor-tts-voice', 'vi-custom', 'change'); h.elements['editor-tts-preview-text'].value = 'Xin chào Việt Nam';
    await h.elements['editor-tts-preview-btn'].emit('click').done;
    const preview = h.calls.find(call => call.url === '/api/editor/tts/preview');
    assert.deepEqual(JSON.parse(preview.options.body), {text: 'Xin chào Việt Nam', voice: 'vi-custom', provider: 'capcut_internal', speed: 1, pitch: 0, allow_fallback: true});
    await h.runTimer(700);
    assert.equal(h.elements['editor-output-audio'].hidden, false); assert.equal(h.elements['editor-output-video'].hidden, true);
    assert.equal(h.elements['editor-output-audio'].paused, true, 'preview must not autoplay');
    await h.elements['editor-tts-generate-btn'].emit('click').done;
    await h.elements['editor-cancel-btn'].emit('click').done;
    assert.equal(h.editor.state().task.status, 'cancelled'); assert.equal(h.elements['editor-cancel-btn'].disabled, true);
    assert.equal(pollCount, 0); assert.equal(h.editor.state().options.tts_voice, 'vi-custom');
    h.change('editor-tts-provider', 'edge_tts', 'change');
    assert.equal(h.elements['editor-tts-voice'].options.some(option => option.value === 'vi-VN-HoaiMyNeural'), true);
    assert.equal(h.calls.filter(call => call.url.includes('/voices')).length, 1, 'provider changes never list remotely');
});

test('missing core/optional runtime disables only processing and keeps save and unrelated core actions usable', async () => {
    const h = harness({capabilities: caps({ffmpeg: false})}); await h.init(); await h.receive();
    assert.equal(h.elements['editor-save-btn'].disabled, false); assert.equal(h.elements['editor-render-btn'].disabled, true);
    assert.equal(h.elements['editor-extract-btn'].disabled, true);
    const core = harness(); await core.init(); await core.receive();
    assert.equal(core.elements['editor-render-btn'].disabled, false); assert.equal(core.elements['editor-extract-btn'].disabled, false);
    core.change('editor-audio-mode', 'instrumental', 'change'); assert.equal(core.elements['editor-render-btn'].disabled, true);
    core.change('editor-audio-mode', 'original', 'change'); core.change('editor-tts-provider', 'edge_tts', 'change');
    assert.equal(core.elements['editor-tts-preview-btn'].disabled, true);
    assert.equal(core.elements['editor-render-btn'].disabled, false, 'unused optional TTS must not block core render');
    core.change('editor-tts-enabled', true, 'change'); assert.equal(core.elements['editor-render-btn'].disabled, true);
    assert.equal(core.elements['editor-save-btn'].disabled, true, 'TTS enabled without voice must be invalid');
    core.change('editor-tts-voice', 'vi-VN-HoaiMyNeural', 'change');
    assert.equal(core.elements['editor-save-btn'].disabled, false);
});

test('watermark sends only PNG multipart image and cannot overwrite other dirty options', async () => {
    const h = harness({handler: url => url.endsWith('/watermark') ? {watermark_path: 'project/logo.png', options: {...plain(helpers.defaults), font_size: 48}} : undefined});
    await h.init(); await h.receive(); h.change('editor-font-size', 75);
    h.elements['editor-watermark-file'].files = [new File(['\x89PNG'], 'brand.png', {type: 'image/png'})];
    await h.elements['editor-watermark-file'].emit('change').done;
    const call = h.calls.at(-1); assert.equal(call.url, '/api/editor/projects/job-a/watermark');
    assert.ok(call.options.body instanceof FormData); assert.equal(call.options.body.get('image').name, 'brand.png');
    assert.equal(call.options.headers.has('Content-Type'), false, 'browser sets multipart boundary');
    assert.equal(h.editor.state().options.font_size, 75); assert.equal(h.editor.state().options.watermark_path, 'project/logo.png');
    assert.equal(h.editor.state().options.watermark_enabled, true); assert.equal(h.elements['editor-watermark-file'].value, '');
});

test('render result handoff uses old job-ID endpoint and never performs an upload', async () => {
    const h = harness({handler: (url, options) => {
        if (url === '/api/editor/jobs') return task({status: 'success', output_url: '/api/editor/jobs/task-a/output', output_path: 'out.mp4'});
        if (url.endsWith('/handoff')) return {...snapshot(), workspace: 'uploader'};
    }});
    await h.init(); await h.receive(); await h.elements['editor-render-btn'].emit('click').done;
    await h.elements['editor-send-publish-btn'].emit('click').done;
    const call = h.calls.at(-1); assert.equal(call.url, '/api/projects/job-a/handoff'); assert.deepEqual(JSON.parse(call.options.body), {workspace: 'uploader'});
    assert.equal(h.handoffs[0][0], 'uploader'); assert.deepEqual(h.handoffs.at(-1), ['select', 'uploader']);
    assert.equal(h.calls.some(call => /upload/.test(call.url)), false);
});

test('safe media URLs require same origin and the exact task output route; task identity rejects mismatched projects', () => {
    for (const url of ['/api/editor/jobs/task-a/output?token=x', 'http://127.0.0.1:8123/api/editor/jobs/task-a/output?token=x']) {
        assert.equal(helpers.safeOutputUrl(url, 'job-a', 'task-a'), '/api/editor/jobs/task-a/output?token=x');
    }
    for (const url of ['https://evil.invalid/api/editor/jobs/task-a/output', 'javascript:alert(1)', '/static/secret', '/api/projects/job-a/video',
        '/api/projects/job-b/video', '/api/editor/jobs/task-a/output#hash', '/api/editor/capabilities', '/api/editor/projects/job-a',
        '/api/editor/jobs/task-b/output', '/api/editor/jobs/task-a', '/api/editor/jobs/task-a/cancel', '/api/editor/jobs/task-a/output/extra',
        'http://user:password@127.0.0.1:8123/api/editor/jobs/task-a/output']) {
        assert.equal(helpers.safeOutputUrl(url, 'job-a', 'task-a'), '', url);
    }
    assert.equal(helpers.safeOutputUrl('/api/editor/jobs/task-a/output', 'job-a'), '', 'task identity is mandatory');
    assert.equal(helpers.validTask(task(), 'job-a'), true); assert.equal(helpers.validTask(task(), 'job-b'), false);
});

test('rejected save and task responses retain dirty state and re-enable actions with editor errors', async () => {
    const h = harness({handler: (url, options) => options.method === 'POST' ? {response: {ok: false, status: 409, json: async () => ({error: 'Project locked by another task'})}} : undefined});
    await h.init(); await h.receive(); h.change('editor-font-size', 61);
    await h.elements['editor-save-btn'].emit('click').done;
    assert.equal(h.editor.state().dirty, true); assert.equal(h.editor.state().options.font_size, 61);
    assert.equal(h.elements['editor-save-btn'].disabled, false);
    assert.match(h.elements['editor-status'].textContent, /Project locked/);
    assert.equal(h.elements['editor-status'].dataset.state, 'error');
    await h.elements['editor-render-btn'].emit('click').done;
    assert.equal(h.editor.state().task, null); assert.equal(h.elements['editor-render-btn'].disabled, false);
});

test('a delayed Publish handoff never overrides a newer workspace navigation', async () => {
    const handoff = deferred();
    const h = harness({handler: url => url === '/api/editor/jobs' ? task({status: 'success', output_url: '/api/editor/jobs/task-a/output'}) : url.endsWith('/handoff') ? handoff.promise : undefined});
    await h.init(); await h.receive(); await h.elements['editor-render-btn'].emit('click').done;
    const sending = h.elements['editor-send-publish-btn'].emit('click'); await settle(); h.navigate();
    handoff.resolve({...snapshot(), workspace: 'uploader'}); await sending.done;
    assert.equal(h.handoffs.length, 1); assert.equal(h.handoffs[0][0], 'uploader');
});

test('request timeout reports a proper editor-only error without modifying app connection UI', async () => {
    const h = harness({handler: (url, options) => url === '/api/editor/capabilities' ? new Promise((resolve, reject) =>
        options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})))) : undefined});
    await h.init(); await h.runTimer(20000);
    assert.match(h.elements['editor-status'].textContent, /không phản hồi/);
    assert.equal(h.elements['connection-banner'].hidden, true);
});

test('lost accepted POST recovers its active task through project GET, retains newer draft/revision, and can cancel without resubmitting', async () => {
    const accepted = deferred(); let projectReads = 0;
    const h = harness({handler: (url, options) => {
        if (url === '/api/editor/projects/job-a' && options.method === 'GET' && ++projectReads > 1) return {
            snapshot: snapshot(), options: {...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box}, active_task: task({status: 'running'})};
        if (url === '/api/editor/jobs') return accepted.promise;
        if (url === '/api/editor/jobs/task-a/cancel') return task({status: 'cancelled'});
    }});
    await h.init(); await h.receive(); h.change('editor-font-size', 66);
    const launching = h.elements['editor-render-btn'].emit('click'); await settle();
    h.change('editor-font-size', 73); h.change('editor-crop-x', 90);
    const revision = h.editor.state().revision;
    accepted.reject(new Error('response lost after server accepted task')); await launching.done;
    assert.equal(h.editor.state().loadFailed, true); assert.equal(h.editor.state().recoveryRequired, true);
    assert.equal(h.elements['editor-render-btn'].disabled, true);
    assert.match(h.elements['editor-status'].textContent, /Kiểm tra lại file/);
    await h.elements['editor-render-btn'].emit('click').done;
    await h.elements['editor-refresh-project'].emit('click').done;
    const recovered = h.editor.state();
    assert.equal(projectReads, 2); assert.equal(recovered.task.id, 'task-a'); assert.equal(recovered.task.status, 'running');
    assert.equal(recovered.loadFailed, false); assert.equal(recovered.recoveryRequired, false);
    assert.equal(recovered.revision, revision); assert.equal(recovered.dirty, true);
    assert.equal(recovered.options.font_size, 73); assert.equal(h.elements['editor-crop-x'].value, '90');
    assert.equal(h.elements['editor-cancel-btn'].disabled, false);
    await h.elements['editor-cancel-btn'].emit('click').done;
    assert.equal(h.editor.state().task.status, 'cancelled');
    assert.equal(h.calls.filter(call => call.url === '/api/editor/jobs' && call.options.method === 'POST').length, 1);
});

test('timed-out accepted POST marks recovery even for a clean project and reload recovers instead of relaunching', async () => {
    const storage = new Map();
    const h = harness({storage, handler: (url, options) => url === '/api/editor/jobs' ? new Promise((resolve, reject) =>
        options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})))) : undefined});
    await h.init(); await h.receive();
    const launching = h.elements['editor-render-btn'].emit('click'); await settle(); await h.runTimer(30000); await launching.done;
    assert.equal(h.editor.state().loadFailed, true); assert.equal(h.editor.state().recoveryRequired, true);
    assert.equal(h.editor.state().dirty, false); assert.equal(h.window.emit('beforeunload').event.defaultPrevented, true);
    const reloaded = harness({storage, handler: url => url === '/api/editor/projects/job-a' ? {
        snapshot: snapshot(), options: {...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box}, active_task: task({status: 'running'})} : undefined});
    await reloaded.init(); await reloaded.receive();
    assert.equal(reloaded.editor.state().task.id, 'task-a'); assert.equal(reloaded.editor.state().recoveryRequired, false);
    assert.equal(reloaded.elements['editor-cancel-btn'].disabled, false);
    assert.equal(reloaded.calls.some(call => call.url === '/api/editor/jobs'), false);
});

test('project refresh always performs GET when healthy and concurrent responses cannot overwrite a newer refresh or dirty edit', async () => {
    const first = deferred(), second = deferred(); let reads = 0;
    const h = harness({handler: url => {
        if (url === '/api/editor/projects/job-a' && ++reads > 1) return reads === 2 ? first.promise : second.promise;
    }});
    await h.init(); await h.receive();
    const refresh1 = h.elements['editor-refresh-project'].emit('click'); await settle();
    const refresh2 = h.elements['editor-refresh-project'].emit('click'); await settle();
    assert.equal(reads, 3);
    h.change('editor-font-size', 79); const revision = h.editor.state().revision;
    second.resolve({snapshot: snapshot(), options: {...plain(helpers.defaults), font_size: 51}, active_task: task({id: 'latest', status: 'running'})});
    await refresh2.done;
    first.resolve({snapshot: snapshot(), options: plain(helpers.defaults), active_task: task({id: 'stale', status: 'error'})});
    await refresh1.done;
    assert.equal(h.editor.state().task.id, 'latest'); assert.equal(h.editor.state().options.font_size, 79);
    assert.equal(h.editor.state().revision, revision); assert.equal(h.editor.state().dirty, true);
});

test('dirty per-project drafts persist across reload with raw invalid crop/numeric errors and a beforeunload guard, never exporting text', async () => {
    const storage = new Map(), h = harness({storage}); await h.init(); await h.receive();
    h.change('editor-font-size', 77); h.change('editor-banner-text', '<draft stays local>');
    h.change('editor-blur-strength', ''); h.change('editor-crop-x', '090'); h.change('editor-crop-width', '');
    h.elements['editor-tts-preview-text'].value = 'Private unsubmitted preview';
    const revision = h.editor.state().revision;
    assert.equal(h.editor.state().draftPersisted, true);
    assert.equal(h.window.emit('beforeunload').event.defaultPrevented, true);
    const persisted = JSON.parse(storage.get(helpers.draftStorageKey));
    assert.equal(persisted.version, 1); assert.equal(persisted.projects[0].job_id, 'job-a');
    assert.equal(persisted.projects[0].crop_draft.x, '090'); assert.equal(persisted.projects[0].crop_draft.width, '');
    assert.equal(persisted.projects[0].numeric_draft.blur_strength, '');
    assert.equal(storage.get(helpers.draftStorageKey).includes('Private unsubmitted preview'), false);
    assert.equal(h.calls.some(call => call.options.method !== 'GET'), false, 'draft storage must not send form/preview text to any API');
    const reload = harness({storage}); await reload.init(); await reload.receive();
    assert.equal(reload.editor.state().dirty, true); assert.equal(reload.editor.state().revision, revision);
    assert.equal(reload.elements['editor-font-size'].value, '77'); assert.equal(reload.elements['editor-banner-text'].value, '<draft stays local>');
    assert.equal(reload.elements['editor-blur-strength'].value, ''); assert.equal(reload.elements['editor-blur-strength'].getAttribute('aria-invalid'), 'true');
    assert.equal(reload.elements['editor-crop-x'].value, '090'); assert.equal(reload.elements['editor-crop-width'].value, '');
    assert.equal(reload.elements['editor-crop-x'].getAttribute('aria-invalid'), 'true'); assert.match(reload.elements['editor-crop-error'].textContent, /100%/);
    approx(reload.editor.state().options.crop_box.x, .04); assert.equal(reload.elements['editor-save-btn'].disabled, true);
    await reload.receive(snapshot('job-b')); assert.equal(reload.window.emit('beforeunload').event.defaultPrevented, true, 'offscreen draft is guarded');
    await reload.receive(); assert.equal(reload.elements['editor-crop-x'].value, '090');
    await reload.elements['editor-crop-reset'].emit('click').done; reload.change('editor-blur-strength', 8);
    assert.equal(reload.editor.state().cropDraft, null); assert.equal(reload.elements['editor-crop-error'].textContent, '');
    await reload.elements['editor-save-btn'].emit('click').done;
    assert.equal(reload.editor.state().dirty, false); assert.equal(reload.window.emit('beforeunload').event.defaultPrevented, false);
    assert.equal(JSON.parse(storage.get(helpers.draftStorageKey)).projects.length, 0);
});

test('draft storage bounds the most recently edited projects to ten and isolates arbitrary project keys', async () => {
    const storage = new Map(), h = harness({storage}); await h.init();
    for (let i = 0; i < 12; i++) { await h.receive(snapshot(`job-${i}`)); h.change('editor-font-size', 60 + i); }
    const persisted = JSON.parse(storage.get(helpers.draftStorageKey));
    assert.equal(persisted.projects.length, 10);
    assert.deepEqual(persisted.projects.map(item => item.job_id), Array.from({length: 10}, (_, i) => `job-${i + 2}`));
    const reload = harness({storage}); await reload.init(); await reload.receive(snapshot('job-0'));
    assert.equal(reload.elements['editor-font-size'].value, '48');
    await reload.receive(snapshot('job-2')); assert.equal(reload.elements['editor-font-size'].value, '62');
    await reload.receive(snapshot('__proto__')); reload.change('editor-font-size', 88);
    const next = harness({storage}); await next.init(); await next.receive(snapshot('__proto__'));
    assert.equal(next.elements['editor-font-size'].value, '88');
});

test('draft restoration validates schemas and values before applying storage; storage failures leave editable drafts and an unload warning', async () => {
    const valid = {job_id: 'job-a', options: {...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box},
        numeric_draft: {}, crop_draft: null, dirty: true, recovery_required: false, revision: 2};
    for (const invalid of [{...valid, job_id: {}}, {...valid, revision: -1}, {...valid, options: {...valid.options, encoder: 'shell'}},
        {...valid, numeric_draft: {constructor: 'x'}}, {...valid, numeric_draft: {blur_strength: 'x'.repeat(129)}},
        {...valid, crop_draft: {x: '90'}}, {...valid, dirty: 'true'}, {...valid, recovery_required: 'true'}]) {
        assert.equal(helpers.validatedDraft(invalid), null);
    }
    for (const raw of ['{bad json', JSON.stringify({version: 2, projects: [valid]}), JSON.stringify({version: 1, projects: Array(11).fill(valid)}),
        JSON.stringify({version: 1, projects: [{...valid, options: {...valid.options, font_size: null}}]}), ' '.repeat(262145)]) {
        const h = harness({storage: new Map([[helpers.draftStorageKey, raw]])}); await h.init(); await h.receive();
        assert.equal(h.editor.state().dirty, false); assert.equal(h.elements['editor-font-size'].value, '48');
    }
    const h = harness({storageError: true}); await h.init(); await h.receive();
    h.change('editor-font-size', 80); h.change('editor-crop-x', '95');
    assert.equal(h.editor.state().dirty, true); assert.equal(h.editor.state().draftPersisted, false);
    assert.match(h.elements['editor-dirty'].textContent, /không lưu được/);
    assert.equal(h.window.emit('beforeunload').event.defaultPrevented, true);
    await h.receive(snapshot('job-b')); await h.receive();
    assert.equal(h.elements['editor-font-size'].value, '80'); assert.equal(h.elements['editor-crop-x'].value, '95');
});

test('raw invalid crop values and errors survive project switching until an explicit valid reset', async () => {
    const h = harness(); await h.init(); await h.receive();
    h.change('editor-crop-x', '090'); h.change('editor-crop-height', '');
    const error = h.elements['editor-crop-error'].textContent;
    await h.receive(snapshot('job-b')); await h.receive();
    assert.equal(h.elements['editor-crop-x'].value, '090'); assert.equal(h.elements['editor-crop-height'].value, '');
    assert.equal(h.elements['editor-crop-error'].textContent, error); assert.equal(h.elements['editor-save-btn'].disabled, true);
    approx(h.editor.state().options.crop_box.height, .3);
    await h.elements['editor-crop-lower'].emit('click').done;
    assert.equal(h.editor.state().cropDraft, null); assert.equal(h.elements['editor-crop-x'].value, '4');
    assert.equal(h.elements['editor-crop-height'].value, '30'); assert.equal(h.elements['editor-crop-error'].textContent, '');
    await h.receive(snapshot('job-b')); await h.receive();
    assert.equal(h.elements['editor-crop-height'].value, '30'); assert.equal(h.elements['editor-save-btn'].disabled, false);
});

test('an offscreen project GET records its completed output before the visible-project guard', async () => {
    const load = deferred();
    const h = harness({handler: url => url === '/api/editor/projects/job-a' ? load.promise : undefined});
    await h.init(); h.editor.projectReceived(snapshot()); await settle(); await h.receive(snapshot('job-b'));
    load.resolve({snapshot: snapshot(), options: {...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box},
        active_task: task({status: 'success', output_url: '/api/editor/jobs/task-a/output', output_path: 'out.mp4'})}); await settle();
    assert.equal(h.editor.state().job_id, 'job-b'); assert.equal(h.elements['editor-output'].hidden, true);
    await h.receive();
    assert.equal(h.elements['editor-output-video'].src, '/api/editor/jobs/task-a/output');
    assert.equal(h.elements['editor-output-video'].hidden, false); assert.equal(h.elements['editor-send-publish-btn'].disabled, false);
    assert.equal(h.calls.filter(call => call.url === '/api/editor/projects/job-a').length, 1);
});

test('last_render restores a publishable video when latest task is audio, error, or cancelled without losing audio output', async () => {
    const lastRender = task({id: 'last-video', status: 'success', output_url: '/api/editor/jobs/last-video/output', output_path: 'last-good.mp4'});
    for (const status of ['success', 'error', 'cancelled']) {
        const latest = task({id: 'new-audio', action: 'extract_audio', output_kind: 'audio', status,
            output_url: status === 'success' ? '/api/editor/jobs/new-audio/output' : '', error: status === 'error' ? 'extract failed' : ''});
        const h = harness({handler: url => url === '/api/editor/projects/job-a' ? {
            snapshot: snapshot(), options: {...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box}, active_task: latest, last_render: lastRender} : undefined});
        await h.init(); await h.receive();
        assert.equal(h.editor.state().videoOutput.id, 'last-video'); assert.equal(h.elements['editor-send-publish-btn'].disabled, false);
        assert.equal(h.editor.state().output.id, status === 'success' ? 'new-audio' : 'last-video');
        assert.equal(h.elements['editor-output-audio'].hidden, status !== 'success');
        if (status !== 'success') assert.equal(h.elements['editor-output-video'].src, '/api/editor/jobs/last-video/output');
    }
});

test('last_render is accepted offscreen independently of a newer audio task and rejects cross-project records', async () => {
    const load = deferred(), lastRender = task({id: 'last-video', status: 'success', output_url: '/api/editor/jobs/last-video/output'});
    const h = harness({handler: url => url === '/api/editor/projects/job-a' ? load.promise : undefined});
    await h.init(); h.editor.projectReceived(snapshot()); await settle(); await h.receive(snapshot('job-b'));
    load.resolve({snapshot: snapshot(), options: {...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box},
        active_task: task({id: 'audio', action: 'tts', status: 'success', output_kind: 'audio', output_url: '/api/editor/jobs/audio/output'}), last_render: lastRender});
    await settle(); assert.equal(h.editor.state().videoOutput, null);
    await h.receive(); assert.equal(h.editor.state().videoOutput.id, 'last-video'); assert.equal(h.editor.state().output.id, 'audio');
    assert.equal(h.elements['editor-send-publish-btn'].disabled, false); assert.equal(h.elements['editor-output-audio'].hidden, false);
    const bad = harness({handler: url => url === '/api/editor/projects/job-a' ? {
        snapshot: snapshot(), options: plain(helpers.defaults), active_task: null, last_render: {...lastRender, job_id: 'job-b'}} : undefined});
    await bad.init(); await bad.receive(); assert.equal(bad.editor.state().loadFailed, true);
    assert.equal(bad.editor.state().videoOutput, null); assert.equal(bad.elements['editor-output'].hidden, true);
});

test('acceptOutput checks task identity in URLs instead of trusting any editor route', async () => {
    for (const output_url of ['/api/editor/jobs/wrong-task/output', '/api/editor/capabilities', '/api/editor/projects/job-a']) {
        const h = harness({handler: url => url === '/api/editor/jobs' ? task({status: 'success', output_url}) : undefined});
        await h.init(); await h.receive(); await h.elements['editor-render-btn'].emit('click').done;
        assert.equal(h.editor.state().output.url, ''); assert.equal(h.elements['editor-output-link'].hidden, true);
        assert.equal(h.elements['editor-output-video'].hidden, true); assert.equal(h.elements['editor-output-video'].src, undefined);
    }
});

test('CapCut compatibility and allowed Edge fallback gate TTS only while core extract ignores instrumental dependencies', async () => {
    for (const [capcut, edge, fallback, expected] of [[undefined, false, true, true], [true, false, false, true],
        [false, false, true, false], [false, true, true, true], [false, true, false, false]]) {
        const options = {optional: {edge_tts: {available: edge}, audio_separator: {available: false}}};
        if (capcut !== undefined) options.capcut_compat = capcut;
        const h = harness({capabilities: caps(options)}); await h.init(); await h.receive();
        h.change('editor-tts-voice', 'saved-capcut-voice', 'change'); h.change('editor-tts-fallback', fallback, 'change');
        assert.equal(h.elements['editor-tts-preview-btn'].disabled, !expected, `${capcut}/${edge}/${fallback}`);
        assert.equal(h.elements['editor-tts-generate-btn'].disabled, !expected);
        assert.equal(h.elements['editor-save-btn'].disabled, false); assert.equal(h.elements['editor-render-btn'].disabled, false);
        assert.equal(h.elements['editor-extract-btn'].disabled, false);
        h.change('editor-tts-enabled', true, 'change'); assert.equal(h.elements['editor-render-btn'].disabled, !expected);
        assert.equal(h.elements['editor-extract-btn'].disabled, false, 'unused TTS availability must not gate extraction');
        h.change('editor-audio-mode', 'instrumental', 'change');
        assert.equal(h.elements['editor-render-btn'].disabled, true); assert.equal(h.elements['editor-extract-btn'].disabled, false);
    }
});

test('an independent extract remains actionable and posts its own action when optional TTS/separator runtimes are unavailable', async () => {
    const h = harness({capabilities: caps({capcut_compat: false}), handler: url =>
        url === '/api/editor/jobs' ? task({action: 'extract_audio', output_kind: 'audio'}) : undefined});
    await h.init(); await h.receive(); h.change('editor-audio-mode', 'instrumental', 'change');
    assert.equal(h.elements['editor-render-btn'].disabled, true); assert.equal(h.elements['editor-tts-preview-btn'].disabled, true);
    assert.equal(h.elements['editor-extract-btn'].disabled, false);
    await h.elements['editor-extract-btn'].emit('click').done;
    assert.equal(JSON.parse(h.calls.at(-1).options.body).action, 'extract_audio');
    assert.equal(h.editor.state().task.action, 'extract_audio'); assert.equal(h.elements['editor-cancel-btn'].disabled, false);
});
