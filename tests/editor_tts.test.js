'use strict';
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const editorSource = readFileSync(path.join(__dirname, '../static/editor.js'), 'utf8');
const template = readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const snapshot = (job = 'job-a') => ({job_id: job, workspace: 'editor', ready: true,
    availability: {video: true, translated_srt: true},
    manifest: {project_id: job, crop_box: {x: .04, y: .64, width: .92, height: .3},
        source_resolution: {width: 1920, height: 1080}, assets: {}, stages: {}}});
const task = (overrides = {}) => ({id: 'tts-a', job_id: 'job-a', action: 'tts', status: 'queued',
    phase: 'prepare', percent: 0, fps: 0, warnings: [], output_kind: 'audio', ...overrides});
const savedTts = {tts_provider: 'capcut_internal', tts_voice: 'saved-capcut', tts_speed: .8, tts_pitch: 0, tts_fallback: true};

function eventTarget() {
    const listeners = new Map();
    return {addEventListener(type, fn) { const list = listeners.get(type) || []; list.push(fn); listeners.set(type, list); },
        async emit(type, detail = {}) {
            const event = {target: this, currentTarget: this, preventDefault() {}, ...detail};
            await Promise.all((listeners.get(type) || []).map(fn => fn(event)));
        }};
}

// Exercise the actual controls and registered click/input/change handlers, with no live TTS requests.
function harness({saved = savedTts, handler, storage = new Map(), capcut = true} = {}) {
    const elements = {}, calls = [], timers = new Map();
    let timerId = 0;
    const document = Object.assign(eventTarget(), {
        getElementById(id) { assert.ok(elements[id], `Missing markup #${id}`); return elements[id]; },
        querySelector() { return {content: 'csrf-tts-test'}; },
        createElement: tag => element('', tag),
    });
    function element(id, tag) {
        const attrs = {};
        return Object.assign(eventTarget(), {id, tagName: tag.toUpperCase(), attributes: attrs, dataset: {}, children: [],
            hidden: false, disabled: false, checked: false, value: '', type: '', textContent: '', labels: [],
            style: {}, duration: NaN, videoWidth: 0, videoHeight: 0,
            setAttribute(key, value) { attrs[key] = String(value); }, getAttribute: key => attrs[key] ?? null,
            removeAttribute(key) { delete attrs[key]; if (key === 'src') delete this.src; },
            appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
            replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); },
            querySelectorAll(selector) {
                const result = [];
                const walk = node => { for (const child of node.children) {
                    if (selector === '[data-editor-option]' && child.dataset.editorOption) result.push(child);
                    walk(child);
                }};
                walk(this); return result;
            },
            getBoundingClientRect: () => ({left: 0, top: 0, width: 800, height: 400}),
            focus() { document.activeElement = this; }, pause() {}, load() {},
        });
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
        node.type = attrs.type || ''; node.value = attrs.value || '';
        Object.defineProperty(node, 'options', {get() { return this.children.filter(child => child.tagName === 'OPTION'); }});
        for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) node.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
        if (node.id) elements[node.id] = node;
        stack.at(-1)?.appendChild(node);
        if (!voids.has(tag)) stack.push(node);
    }
    const window = Object.assign(eventTarget(), {location: {href: 'http://127.0.0.1:8123/'}});
    const context = vm.createContext({window, document, console, URL, Headers, FormData, AbortController,
        localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
        setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout: id => timers.delete(id),
        fetch: async (url, options) => {
            calls.push({url, options});
            let data = handler ? await handler(url, options) : undefined;
            if (data === undefined && url === '/api/editor/capabilities') data = {ffmpeg: true, ffprobe: true,
                capcut_compat: capcut, encoders: {libx264: true}, optional: {edge_tts: {available: true}, audio_separator: {available: false}}};
            if (data === undefined && url.startsWith('/api/editor/projects/')) {
                const job = decodeURIComponent(url.split('/')[4]);
                data = {snapshot: snapshot(job), options: {...plain(window.VietSubEditor.helpers.defaults),
                    crop_box: snapshot(job).manifest.crop_box, ...saved}, active_task: null};
            }
            if (data === undefined && url === '/api/editor/jobs') data = task();
            return {ok: true, status: 200, json: async () => data ?? {}};
        },
    });
    vm.runInContext(editorSource, context, {filename: 'static/editor.js'});
    const editor = window.VietSubEditor;
    const set = (id, value) => { const control = elements[id]; if (control.type === 'checkbox') control.checked = value; else control.value = String(value); return control; };
    return {editor, elements, calls, storage, set,
        async init() { await window.emit('load'); await settle(); },
        async receive(value = snapshot()) { editor.projectReceived(value); await settle(); },
        async change(id, value, type = 'input') { await set(id, value).emit(type); },
        async click(id = 'editor-tts-generate-btn') { await elements[id].emit('click'); await settle(); },
        async poll(delay = 700) {
            const entry = [...timers].find(([, timer]) => timer.delay === delay);
            assert.ok(entry, `No poll scheduled at ${delay}ms`);
            timers.delete(entry[0]); await entry[1].fn(); await settle();
        },
    };
}
const submission = h => h.calls.find(call => call.url === '/api/editor/jobs' && call.options.method === 'POST');
const ttsOptions = options => Object.fromEntries(Object.keys(savedTts).map(key => [key, options[key]]));

for (const provider of ['capcut_internal', 'edge_tts']) test(`generate TTS sends unsaved ${provider} voice, speed, pitch and fallback with no save request`, async () => {
    const h = harness(); await h.init(); await h.receive();
    await h.change('editor-tts-provider', provider, 'change');
    await h.change('editor-tts-voice', provider === 'edge_tts' ? 'vi-VN-NamMinhNeural' : 'new-capcut', 'change');
    await h.change('editor-tts-speed', '1.35'); await h.change('editor-tts-pitch', '-2');
    await h.change('editor-tts-fallback', false, 'change');
    await h.click();
    const request = submission(h), body = JSON.parse(request.options.body);
    assert.equal(body.job_id, 'job-a'); assert.equal(body.action, 'tts');
    assert.deepEqual(ttsOptions(body.options), {tts_provider: provider,
        tts_voice: provider === 'edge_tts' ? 'vi-VN-NamMinhNeural' : 'new-capcut', tts_speed: 1.35, tts_pitch: -2, tts_fallback: false});
    assert.equal(body.options.tts_enabled, false, 'standalone TTS does not require render TTS to be enabled');
    assert.equal(request.options.headers.get('X-VietSub-Token'), 'csrf-tts-test');
    assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 1, 'launch never relies on saving the project first');
    assert.equal(h.editor.state().dirty, true);
});

test('generate TTS synchronizes visible controls even when input/change has not fired', async () => {
    const h = harness({saved: {...savedTts, tts_provider: 'edge_tts', tts_voice: 'vi-VN-HoaiMyNeural'}});
    await h.init(); await h.receive();
    h.set('editor-tts-voice', 'vi-VN-NamMinhNeural'); h.set('editor-tts-speed', '1.6');
    h.set('editor-tts-pitch', '3'); h.set('editor-tts-fallback', false);
    assert.equal(h.editor.state().options.tts_fallback, true, 'model still has saved settings before click');
    await h.click();
    const sent = JSON.parse(submission(h).options.body).options;
    assert.deepEqual(ttsOptions(sent), {tts_provider: 'edge_tts', tts_voice: 'vi-VN-NamMinhNeural', tts_speed: 1.6, tts_pitch: 3, tts_fallback: false});
    assert.deepEqual(ttsOptions(plain(h.editor.state().options)), ttsOptions(sent));
    const draft = JSON.parse(h.storage.get(h.editor.helpers.draftStorageKey)).projects[0];
    assert.deepEqual(ttsOptions(draft.options), ttsOptions(sent));
    assert.equal(h.editor.state().dirty, true);
});

test('provider and voice restored together are captured before repopulating the voice selector', async () => {
    const h = harness(); await h.init(); await h.receive();
    h.set('editor-tts-provider', 'edge_tts'); h.set('editor-tts-voice', 'vi-VN-NamMinhNeural');
    h.set('editor-tts-speed', '1.2'); h.set('editor-tts-fallback', false);
    await h.click();
    const sent = JSON.parse(submission(h).options.body).options;
    assert.equal(sent.tts_provider, 'edge_tts'); assert.equal(sent.tts_voice, 'vi-VN-NamMinhNeural');
    assert.equal(h.elements['editor-tts-voice'].value, 'vi-VN-NamMinhNeural');
    assert.equal(sent.tts_speed, 1.2); assert.equal(sent.tts_fallback, false);
});

test('generate TTS revalidates uncommitted numeric edits and never submits old valid speed', async () => {
    for (const raw of ['', '.4', '2.5']) {
        const h = harness(); await h.init(); await h.receive();
        assert.equal(h.elements['editor-tts-generate-btn'].disabled, false);
        h.set('editor-tts-speed', raw); await h.click();
        assert.equal(submission(h), undefined, `invalid speed ${JSON.stringify(raw)} must not submit`);
        assert.equal(h.elements['editor-tts-speed'].getAttribute('aria-invalid'), 'true');
        assert.equal(h.editor.state().numericDraft.tts_speed, raw);
        assert.equal(h.editor.state().dirty, true);
    }
});

test('uncommitted fallback checkbox also governs runtime gating', async () => {
    const h = harness({capcut: false}); await h.init(); await h.receive();
    assert.equal(h.elements['editor-tts-generate-btn'].disabled, false, 'Edge fallback initially permits CapCut request');
    h.set('editor-tts-fallback', false); await h.click();
    assert.equal(submission(h), undefined);
    assert.equal(h.elements['editor-tts-generate-btn'].disabled, true);
    assert.equal(h.editor.state().options.tts_fallback, false);
});

test('TTS preview and subtitle generation share the same visible form values', async () => {
    const h = harness({handler: url => url === '/api/editor/tts/preview' ? task({id: 'preview-a', job_id: '', action: 'tts_preview', status: 'success'}) : undefined});
    await h.init(); await h.receive();
    h.set('editor-tts-voice', 'new-capcut'); h.set('editor-tts-speed', '1.25');
    h.set('editor-tts-pitch', '-1'); h.set('editor-tts-fallback', false);
    h.set('editor-tts-preview-text', 'Xin chào'); await h.click('editor-tts-preview-btn'); await h.click();
    const preview = JSON.parse(h.calls.find(call => call.url === '/api/editor/tts/preview').options.body);
    const options = JSON.parse(submission(h).options.body).options;
    assert.deepEqual(preview, {text: 'Xin chào', voice: options.tts_voice, provider: options.tts_provider,
        speed: options.tts_speed, pitch: options.tts_pitch, allow_fallback: options.tts_fallback});
    assert.equal(preview.allow_fallback, false); assert.equal(preview.speed, 1.25);
});

test('restored per-project TTS draft survives a fresh server snapshot before launch', async () => {
    const storage = new Map(), original = harness({storage}); await original.init(); await original.receive();
    await original.change('editor-tts-voice', 'draft-voice', 'change');
    await original.change('editor-tts-speed', '1.45'); await original.change('editor-tts-fallback', false, 'change');
    const h = harness({storage}); await h.init(); await h.receive();
    await h.receive(snapshot('job-b')); await h.receive();
    await h.elements['editor-refresh-project'].emit('click'); await settle(); await h.click();
    const sent = JSON.parse(submission(h).options.body);
    assert.equal(sent.job_id, 'job-a'); assert.equal(sent.options.tts_voice, 'draft-voice');
    assert.equal(sent.options.tts_speed, 1.45); assert.equal(sent.options.tts_fallback, false);
    assert.equal(h.editor.state().dirty, true);
});

test('polling renders backend progress/message without overwriting a newer TTS draft or finishing on current=total', async () => {
    let polls = 0;
    const message = 'CapCut: đang tạo câu 2/2 <chờ phản hồi>';
    const h = harness({handler: url => url === '/api/editor/jobs/tts-a' ? (++polls === 1 ? task({status: 'running', phase: 'tts_request',
        provider: 'capcut_internal', current: 2, completed: 1, total: 2, percent: 50, message}) : polls === 2 ?
        task({status: 'running', phase: 'tts_audio', percent: 65, message}) : task({status: 'success', phase: 'done', percent: 100,
        message, output_url: '/api/editor/jobs/tts-a/output', output_path: 'voice.wav'})) : undefined});
    await h.init(); await h.receive(); await h.change('editor-tts-fallback', false, 'change'); await h.click();
    await h.change('editor-tts-speed', '1.8'); await h.change('editor-tts-voice', 'next-voice', 'change');
    const sent = JSON.parse(submission(h).options.body).options;
    await h.poll();
    assert.equal(h.elements['editor-task-percent'].textContent, '50%');
    assert.equal(h.elements['editor-task-progress'].getAttribute('aria-valuenow'), '50');
    assert.equal(h.elements['editor-task-stage'].textContent, `Đang xử lý · ${message}`);
    assert.equal(h.elements['editor-task-progress'].getAttribute('aria-valuetext'), `50% · ${message}`);
    assert.equal(h.elements['editor-task-stage'].children.length, 0, 'message is text, never interpreted as markup');
    assert.equal(h.elements['editor-task-panel'].getAttribute('aria-busy'), 'true');
    assert.equal(h.editor.state().options.tts_speed, 1.8); assert.equal(h.editor.state().options.tts_voice, 'next-voice');
    assert.equal(sent.tts_speed, .8); assert.equal(sent.tts_voice, 'saved-capcut'); assert.equal(sent.tts_fallback, false);
    await h.poll(1000);
    assert.equal(h.elements['editor-task-stage'].textContent, 'Đang xử lý · tts_audio', 'audio processing hides stale synthesis message');
    assert.equal(h.elements['editor-task-progress'].getAttribute('aria-valuetext'), '65% · tts_audio');
    await h.poll(1000);
    assert.equal(h.elements['editor-task-stage'].textContent, 'Hoàn tất · done', 'terminal status hides stale in-flight message');
    assert.equal(h.elements['editor-task-percent'].textContent, '100%');
    assert.equal(h.elements['editor-output-audio'].src, '/api/editor/jobs/tts-a/output');
    assert.equal(h.editor.state().dirty, true); assert.equal(h.editor.state().options.tts_speed, 1.8);
});
