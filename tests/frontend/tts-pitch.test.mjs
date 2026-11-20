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

// Independent TTS-scope coverage; legacy regression assertions remain untouched.
const helpers = harness().editor.helpers;
const scopedOptions = (changes = {}) => ({...plain(helpers.defaults), crop_box: snapshot().manifest.crop_box,
    tts_change_pitch: false, ...changes});
const scopedResponse = (changes = {}) => ({snapshot: snapshot(), options: scopedOptions(changes), active_task: null});

test('the optional pitch alias is strict, roundtrips and preserves legacy validation shape', () => {
    const legacy = helpers.validateOptions({...plain(helpers.defaults), change_audio_pitch: true});
    assert.deepEqual(plain(legacy.errors), {});
    assert.equal(Object.hasOwn(legacy.options, 'tts_change_pitch'), false);
    for (const linked of [false, true]) {
        const value = helpers.validateOptions({tts_change_pitch: linked, pitch_semitones: 4});
        assert.deepEqual(plain(value.errors), {});
        assert.equal(value.options.tts_change_pitch, linked);
        assert.equal(value.options.change_audio_pitch, linked);
        assert.deepEqual(plain(helpers.validateOptions(value.options).options), plain(value.options));
    }
    for (const invalid of [null, 0, 1, 'false', [], {}]) {
        assert.ok(helpers.validateOptions({tts_change_pitch: invalid}).errors.tts_change_pitch);
    }
    assert.ok(helpers.validateOptions({tts_change_pitch: true, change_audio_pitch: false}).errors.tts_change_pitch);
});

test('new project source transport preserves BGM pitch with the voice switch on', async () => {
    const h = harness({handler: url => url === '/api/editor/projects/job-a' ? scopedResponse() : undefined});
    await h.init(); await h.receive();
    h.change('editor-speed-range', '2'); h.change('editor-change-audio-pitch', true);
    assert.equal(h.elements['editor-video'].playbackRate, 2);
    assert.equal(h.elements['editor-video'].preservesPitch, true);
    assert.equal(h.editor.state().options.tts_change_pitch, true);
    assert.equal(h.editor.state().options.change_audio_pitch, true);
    assert.match(h.elements['editor-pitch-help'].textContent, /chỉ áp dụng giọng đọc phụ đề/);
    assert.doesNotMatch(h.elements['editor-pitch-help'].textContent, /Cấu hình cũ/);
});

test('all pitch chips change voice pitch while leaving source transport pitch and speed intact', async () => {
    const h = harness({handler: url => url === '/api/editor/projects/job-a' ? scopedResponse({speed: 1.5}) : undefined});
    await h.init(); await h.receive();
    const form = h.elements['editor-options-form'];
    const chips = form.querySelectorAll('[data-pitch-preset]').filter(node => node.dataset.pitchPreset !== undefined);
    assert.equal(chips.length, 4);
    for (const chip of chips) {
        await form.emit('click', {target: {closest: selector => selector === '[data-pitch-preset]' ? chip : null}}).done;
        assert.equal(h.editor.state().options.pitch_semitones, Number(chip.dataset.pitchPreset));
        assert.equal(h.elements['editor-pitch-semitones'].value, chip.dataset.pitchPreset);
        assert.equal(h.elements['editor-video'].preservesPitch, true);
        assert.equal(h.elements['editor-video'].playbackRate, 1.5);
        assert.equal(chip.getAttribute('aria-pressed'), 'true');
    }
});

test('every project action opts into TTS-only pitch alongside unchanged legacy keys and routes', async () => {
    const actions = [
        ['editor-save-btn', '/api/editor/projects/job-a', null],
        ['editor-render-btn', '/api/editor/jobs', 'render'],
        ['editor-extract-btn', '/api/editor/jobs', 'extract_audio'],
        ['editor-tts-generate-btn', '/api/editor/jobs', 'tts'],
        ['editor-audio-preview', '/api/editor/projects/job-a/audio-preview', 'audio_preview'],
        ['editor-draft-btn', '/api/editor/projects/job-a/capcut-draft', 'capcut_draft'],
    ];
    for (const [button, route, action] of actions) {
        const h = harness({handler: (url, request) => {
            if (url === route && request.method === 'POST' && action) return task({action});
            return undefined;
        }});
        await h.init(); await h.receive();
        h.change('editor-change-audio-pitch', true);
        h.change('editor-pitch-semitones', 4); h.change('editor-speed', 1.5);
        h.change('editor-tts-voice', 'fixture', 'change');
        await h.elements[button].emit('click').done;
        const call = h.calls.find(call => call.url === route && call.options.method === 'POST');
        assert.ok(call, button);
        const sent = JSON.parse(call.options.body).options;
        assert.equal(sent.tts_change_pitch, true, button);
        assert.equal(sent.change_audio_pitch, true);
        assert.equal(sent.pitch_semitones, 4);
        assert.equal(sent.speed, 1.5);
    }
});

test('legacy loaded options migrate explicitly on save and then preserve source pitch', async () => {
    const h = harness(); await h.init(); await h.receive();
    h.change('editor-speed', 2); h.change('editor-change-audio-pitch', true);
    assert.equal(h.elements['editor-video'].preservesPitch, false, 'old server response keeps legacy preview contract');
    assert.match(h.elements['editor-pitch-help'].textContent, /Cấu hình cũ/);
    await h.elements['editor-save-btn'].emit('click').done;
    assert.equal(h.editor.state().options.tts_change_pitch, true);
    assert.equal(h.elements['editor-video'].preservesPitch, true);
    assert.doesNotMatch(h.elements['editor-pitch-help'].textContent, /Cấu hình cũ/);
});

test('voice pitch alias survives a local draft reload without replacing unsaved controls', async () => {
    const storage = new Map();
    const handler = url => url === '/api/editor/projects/job-a' ? scopedResponse() : undefined;
    const first = harness({storage, handler}); await first.init(); await first.receive();
    first.change('editor-change-audio-pitch', true); first.change('editor-pitch-semitones', -2);
    const second = harness({storage, handler}); await second.init(); await second.receive();
    assert.equal(second.editor.state().options.tts_change_pitch, true);
    assert.equal(second.editor.state().options.change_audio_pitch, true);
    assert.equal(second.editor.state().options.pitch_semitones, -2);
    assert.equal(second.elements['editor-video'].preservesPitch, true);
    assert.equal(second.editor.state().dirty, true);
});

test('voice pitch labels retain associated helper text after repeated validation', async () => {
    assert.match(template, /Cao độ &amp; Tốc độ giọng đọc phụ đề/);
    assert.match(template, /aria-label="Cao độ giọng đọc phụ đề"/);
    assert.match(template, /aria-label="Mẫu cao độ giọng đọc phụ đề"/);
    const h = harness(); await h.init(); await h.receive();
    h.change('editor-pitch-semitones', 13); h.change('editor-pitch-semitones', 4);
    for (const id of ['editor-pitch-semitones', 'editor-change-audio-pitch', 'editor-tts-pitch']) {
        assert.equal(h.elements[id].getAttribute('aria-describedby'), 'editor-pitch-help editor-form-error');
    }
});
