import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/workbench.js', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('../../static/app.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../../templates/index.html', import.meta.url), 'utf8');
const STORAGE_KEY = 'vietsub.studio.workbench.v1';
test('existing progress tick reconciles reduced-motion without adding an animation loop', () => {
    const app = harness(); app.init();
    app.wb.applySettings({sakuga_vfx_enabled: true});
    const before = app.motionCalls.length;
    app.media.matches = true; // Simulate a delayed matchMedia change event.
    app.wb.progressChanged({status: 'idle'});
    assert.equal(app.motionCalls.length, before + 1);
    assert.match(app.elements['motion-help'].textContent, /giảm chuyển động/);
});

const layout = (overrides = {}) => ({version: 1, sidebar_ratio: .25, console_ratio: .24, console_collapsed: false, ...overrides});
const plain = value => JSON.parse(JSON.stringify(value));
const approx = (actual, expected, message = '') => assert.ok(Math.abs(actual - expected) < 1e-8, `${message}: ${actual} != ${expected}`);
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function eventTarget() {
    const listeners = new Map();
    return {
        listeners,
        dispatchEvent(event) { return !this.emit(event.type, event).event.defaultPrevented; },
        addEventListener(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); },
        emit(name, detail = {}) {
            const event = {target: this, currentTarget: this, button: 0, pointerId: 1,
                defaultPrevented: false, stopped: false,
                preventDefault() { this.defaultPrevented = true; },
                stopImmediatePropagation() { this.stopped = true; }, ...detail};
            const results = [];
            for (const fn of listeners.get(name) || []) { results.push(fn(event)); if (event.stopped) break; }
            return {event, done: Promise.all(results)};
        },
    };
}

function harness({stored = null, storageThrows = false, request, fetch, withApp = false} = {}) {
    const elements = {}, calls = [], notices = [], motionCalls = [], frames = new Map(), timers = new Map();
    const rootClasses = new Set();
    let frameId = 0, timerId = 0, storage = stored, endedDrags = 0, vfxEnabled = false;
    const media = Object.assign(eventTarget(), {matches: false});
    const document = Object.assign(eventTarget(), {
        hidden: false, activeElement: null,
        documentElement: {dataset: {}, classList: {add: key => rootClasses.add(key), remove: key => rootClasses.delete(key)}},
        getElementById(id) { assert.ok(elements[id], `markup is missing #${id}`); return elements[id]; },
        querySelector(selector) { return {content: selector.includes('api-token') ? 'test-token' : '1.7.0'}; },
        createElement: tag => makeElement('', tag),
    });
    function makeElement(id, tag = 'div') {
        const classes = new Set(), attrs = {}, captures = new Set();
        return Object.assign(eventTarget(), {
            id, tagName: tag.toUpperCase(), style: {
                setProperty(name, value) { this[name] = value; }, getPropertyValue(name) { return this[name] || ''; },
            }, dataset: {}, attributes: attrs, children: [], options: [],
            hidden: false, disabled: false, checked: false, value: '', textContent: '', innerHTML: '', className: '',
            currentTime: 0, duration: NaN, videoWidth: 0, videoHeight: 0, readyState: 0, paused: true,
            isConnected: true, clientWidth: 900, clientHeight: 400, offsetHeight: 30,
            classList: {add: key => classes.add(key), remove: key => classes.delete(key), contains: key => classes.has(key),
                toggle(key, force) { const on = force ?? !classes.has(key); if (on) classes.add(key); else classes.delete(key); }},
            setAttribute(name, value) { attrs[name] = String(value); if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())] = String(value); },
            getAttribute(name) { return attrs[name] ?? null; },
            removeAttribute(name) { delete attrs[name]; if (name.startsWith('data-')) delete this.dataset[name.slice(5).replace(/-([a-z])/g, (_, x) => x.toUpperCase())]; },
            focus() { document.activeElement = this; },
            contains(el) { return this === el || this.children.some(child => child.contains(el)); },
            querySelectorAll() { return this.children; },
            querySelector() { return this.children[0] || null; },
            appendChild(child) { this.children.push(child); return child; }, append(...children) { this.children.push(...children); },
            replaceChildren(...children) { this.children = children; },
            get firstChild() { return this.children[0]; },
            getBoundingClientRect() { return {left: 0, top: 0, width: parseFloat(this.style.width) || this.clientWidth, height: parseFloat(this.style.height) || this.clientHeight}; },
            getClientRects() { return this.hidden ? [] : [this.getBoundingClientRect()]; },
            setPointerCapture(id) { captures.add(id); }, hasPointerCapture(id) { return captures.has(id); }, releasePointerCapture(id) { captures.delete(id); },
            pause() { this.paused = true; }, async play() { this.paused = false; }, load() {}, scrollIntoView() {},
            matches(selector) { return (tag === 'select' && selector.includes('select')) || (this.type === 'checkbox' && selector.includes('checkbox')); },
        });
    }
    // Use only IDs from the real template. Unlike the old VM fixture, unknown IDs fail loudly.
    for (const match of template.matchAll(/<([a-z][\w:-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
        const [, tag, attrs, id] = match, el = elements[id] = makeElement(id, tag);
        for (const attr of attrs.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)) el.attributes[attr[1]] = attr[2] || '';
        el.hidden = /\bhidden(?:\s|$)/.test(attrs); el.disabled = /\bdisabled(?:\s|$)/.test(attrs);
        el.type = /\btype="([^"]*)"/.exec(attrs)?.[1] || ''; el.value = /\bvalue="([^"]*)"/.exec(attrs)?.[1] || '';
    }
    elements['workbench-shell'].clientWidth = 1280; elements['workbench-shell'].clientHeight = 720;
    elements['console-body'].children.push(elements['console-log-panel'], elements['console-result-panel']);
    elements['settings-drawer'].children.push(elements['settings-close-btn'], elements['setting-sakuga-vfx']);
    for (const id of ['url-input', 'preview-player', 'crop-box', 'queue-list']) {
        elements.workbench.children.push(elements[id]); elements[id].parentElement = elements.workbench;
    }
    for (const name of ['editor', 'uploader']) {
        const panel = elements[`workspace-${name}`];
        for (const id of [`${name}-video`, `${name}-back-source`, `${name}-refresh-project`]) {
            panel.children.push(elements[id]); elements[id].parentElement = panel;
        }
    }
    const window = Object.assign(eventTarget(), {
        innerWidth: 1280, innerHeight: 720, devicePixelRatio: 1.5,
        matchMedia: () => media, confirm: () => true, location: {href: 'http://127.0.0.1:8123/'},
        VietSubVFX: {init() {}, setEnabled(value) { vfxEnabled = value; motionCalls.push(value); }, smoke() {}, pipeline() {}},
    });
    const context = vm.createContext({document, window, console, Headers, AbortController, FormData, URL,
        CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
        localStorage: {
            getItem(key) { assert.equal(key, STORAGE_KEY); if (storageThrows) throw Error('Storage blocked'); return storage; },
            setItem(key, value) { assert.equal(key, STORAGE_KEY); if (storageThrows) throw Error('Storage blocked'); storage = value; },
        },
        requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; }, cancelAnimationFrame: id => frames.delete(id),
        setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
        setInterval(fn) { timers.set(++timerId, fn); return timerId; }, clearInterval: id => timers.delete(id),
        fetch: fetch || (async () => ({ok: true, status: 200, json: async () => ({})})),
    });
    vm.runInContext(source, context, {filename: 'static/workbench.js'});
    if (withApp) vm.runInContext(appSource, context, {filename: 'static/app.js'});
    const wb = window.VietSubWorkbench;
    const adapter = {
        request: async (...args) => { calls.push(args); return request ? request(...args) : {ok: true}; },
        notify: (...args) => notices.push(args), endCropDrag() { endedDrags++; }, updateSteps() {},
    };
    function flushFrames() { const pending = [...frames.values()]; frames.clear(); for (const fn of pending) fn(16); }
    return {wb, elements, window, document, media, calls, notices, motionCalls, frames,
        init: () => wb.init(adapter), adapter, flushFrames,
        run: code => vm.runInContext(code, context),
        get storage() { return storage; }, get endedDrags() { return endedDrags; }, get vfxEnabled() { return vfxEnabled; }};
}

const helpers = harness().wb.helpers;

test('layout validation rejects corrupt versions, booleans-as-ratios, infinity and unexpected fields', () => {
    assert.equal(helpers.validLayout(layout()), true);
    const invalid = [null, [], {}, layout({version: 2}), layout({version: true}), layout({sidebar_ratio: true}),
        layout({sidebar_ratio: '0.3'}), layout({sidebar_ratio: 0}), layout({sidebar_ratio: 1}),
        layout({console_ratio: NaN}), layout({console_ratio: Infinity}), layout({console_collapsed: 'false'}), {...layout(), extra: 1}];
    for (const value of invalid) assert.equal(helpers.validLayout(value), false, String(value));
});

test('restored pane sizes clamp to desktop minimum/maximum and preserve a visible collapsed console', () => {
    for (const [width, height] of [[1280, 676], [1440, 856], [1920, 1036]]) {
        assert.equal(helpers.layoutPixels(layout({sidebar_ratio: .01, console_ratio: .01}), width, height).sidebar, width * .16);
        const max = helpers.layoutPixels(layout({sidebar_ratio: .99, console_ratio: .99}), width, height);
        assert.equal(max.sidebar, width * .185); approx(max.console, height * .4);
        assert.equal(helpers.layoutPixels(layout({console_collapsed: true}), width, height).console, 36);
    }
});

test('contain-fit respects image aspect ratio for landscape, portrait and square sources at all QA sizes', () => {
    for (const [width, height] of [[850, 380], [1010, 520], [1490, 700]]) {
        for (const [sourceWidth, sourceHeight] of [[1920, 1080], [1080, 1920], [1080, 1080]]) {
            const fit = helpers.fitRect(width, height, sourceWidth, sourceHeight);
            assert.ok(fit.width <= width + 1e-8 && fit.height <= height + 1e-8);
            approx(fit.width / fit.height, sourceWidth / sourceHeight);
            assert.ok(Math.abs(fit.width - width) < 1e-8 || Math.abs(fit.height - height) < 1e-8);
        }
    }
    for (const value of [0, -1, NaN, Infinity, undefined, '900']) {
        assert.deepEqual(plain(helpers.fitRect(value, 400, 1920, 1080)), {width: 0, height: 0});
    }
});

test('frame stepping uses source FPS, clamps both ends and disables unknown media rates', () => {
    approx(helpers.frameTime(1, 1, 25, 10), 1.04);
    approx(helpers.frameTime(1, -1, 25, 10), .96);
    assert.equal(helpers.frameTime(0, -1, 25, 10), 0);
    approx(helpers.frameTime(10, 1, 25, 10), 9.96);
    assert.equal(helpers.frameTime(0, 1, 25, .02), 0);
    const fps = 30000 / 1001;
    approx(helpers.frameTime(0, 1, fps, 10), 1001 / 30000);
    for (const rate of [0, null, undefined, NaN, Infinity, '30']) assert.equal(helpers.frameTime(1, 1, rate, 10), null);
    for (const duration of [0, NaN, Infinity]) assert.equal(helpers.frameTime(1, 1, 25, duration), null);
});

test('timecodes remain deterministic through minute/hour boundaries and invalid metadata', () => {
    for (const [input, output] of [[0, '00:00.000'], [1.234, '00:01.234'], [59.9996, '01:00.000'],
        [3600, '01:00:00.000'], [3661.125, '01:01:01.125'], [-1, '00:00.000'], [Infinity, '00:00.000']]) {
        assert.equal(helpers.formatTime(input), output);
    }
});

test('invalid or unavailable localStorage boots the default workbench without writes', () => {
    for (const options of [{stored: '{broken'}, {stored: JSON.stringify(layout({sidebar_ratio: 5}))}, {storageThrows: true}]) {
        const h = harness(options); assert.doesNotThrow(h.init); h.flushFrames();
        approx(parseFloat(h.elements['workbench-shell'].style['--sidebar-width']), 1280 * .18);
        assert.equal(h.elements['console-toggle-btn'].getAttribute('aria-expanded'), 'true');
        assert.equal(h.calls.length, 0, 'boot should not overwrite server preferences with defaults');
    }
});

test('server layout restores across origins while a local user drag wins over a delayed GET', async () => {
    const h = harness(); h.init();
    h.wb.applySettings({sakuga_vfx_enabled: false, workbench_layout: layout({sidebar_ratio: .28, console_collapsed: true})});
    approx(parseFloat(h.elements['workbench-shell'].style['--sidebar-width']), 236.8);
    assert.equal(h.elements['workbench-shell'].style['--console-height'], '36px');
    assert.equal(h.vfxEnabled, false);
    assert.deepEqual(JSON.parse(h.storage), layout({sidebar_ratio: .28, console_collapsed: true}));
    h.elements['sidebar-resizer'].emit('keydown', {key: 'End'}); await settle();
    h.wb.applySettings({workbench_layout: layout({sidebar_ratio: .1})});
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '236.8px', 'late GET must not undo drag');
    assert.deepEqual(Object.keys(JSON.parse(h.calls[0][1].body)), ['workbench_layout']);
});

test('valid local-only preferences are mirrored to the server once configuration loads', async () => {
    const saved = layout({sidebar_ratio: .29, console_collapsed: true});
    const h = harness({stored: JSON.stringify(saved)}); h.init();
    assert.equal(h.elements['workbench-shell'].style['--console-height'], '36px');
    h.wb.applySettings({workbench_layout: null}); await settle();
    assert.equal(h.calls.length, 1);
    assert.deepEqual(JSON.parse(h.calls[0][1].body), {workbench_layout: saved});
});

test('pane pointer cancellation, keyboard limits and double-click reset persist valid bounded layouts', async () => {
    const h = harness(); h.init(); const el = h.elements['sidebar-resizer'];
    el.emit('pointerdown', {clientX: 350, clientY: 50});
    el.emit('pointermove', {clientX: -100, clientY: 50});
    el.emit('pointercancel'); await settle();
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '204.8px');
    assert.equal(el.hasPointerCapture(1), false);
    el.emit('keydown', {key: 'End'}); await settle();
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '236.8px');
    el.emit('dblclick'); await settle();
    approx(parseFloat(h.elements['workbench-shell'].style['--sidebar-width']), 1280 * .18);
    assert.equal(h.endedDrags > 0, true);
    for (const [, options] of h.calls) assert.equal(helpers.validLayout(JSON.parse(options.body).workbench_layout), true);
});

test('preference saves serialize so a slow first write cannot arrive after a later drag', async () => {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    let count = 0;
    const h = harness({request: () => ++count === 1 ? pending : Promise.resolve({ok: true})}); h.init();
    h.elements['sidebar-resizer'].emit('keydown', {key: 'Home'}); await settle();
    h.elements['sidebar-resizer'].emit('keydown', {key: 'End'}); await settle();
    assert.equal(h.calls.length, 1);
    release({ok: true}); await settle();
    assert.equal(h.calls.length, 2);
    approx(JSON.parse(h.calls[1][1].body).workbench_layout.sidebar_ratio, 236.8 / 1280);
});

test('failed layout persistence retains browser preference and reports the failure', async () => {
    const h = harness({request: async () => { throw Error('disk full'); }}); h.init();
    h.elements['console-toggle-btn'].emit('click'); await settle();
    assert.equal(JSON.parse(h.storage).console_collapsed, true);
    assert.equal(h.elements['workbench-shell'].style['--console-height'], '36px');
    assert.match(h.notices[0][0], /disk full/); assert.equal(h.notices[0][1], 'error');
});

test('VFX setting saves a strict boolean only and rolls back a rejected update', async () => {
    const h = harness({request: async () => { throw Error('cannot save'); }}); h.init();
    h.wb.applySettings({sakuga_vfx_enabled: false});
    const checkbox = h.elements['setting-sakuga-vfx']; checkbox.checked = true;
    const change = checkbox.emit('change');
    assert.equal(checkbox.disabled, true); assert.equal(h.vfxEnabled, true);
    await change.done;
    assert.deepEqual(JSON.parse(h.calls[0][1].body), {sakuga_vfx_enabled: true});
    assert.equal(checkbox.checked, false); assert.equal(checkbox.disabled, false); assert.equal(h.vfxEnabled, false);
    assert.match(h.notices[0][0], /cannot save/);
});

test('source tabs preserve input values and suppress hidden-link submission from the local panel', () => {
    const h = harness(); h.init();
    h.elements['url-input'].value = 'https://example.com/video';
    h.elements['source-link-tab'].emit('keydown', {key: 'ArrowRight'});
    assert.equal(h.document.activeElement, h.elements['source-local-tab']);
    assert.equal(h.elements['source-link-panel'].hidden, true);
    assert.equal(h.elements['source-local-panel'].hidden, false);
    const submission = h.elements['preview-form'].emit('submit').event;
    assert.equal(submission.defaultPrevented, true); assert.equal(submission.stopped, true);
    h.elements['source-local-tab'].emit('keydown', {key: 'Home'});
    assert.equal(h.elements['url-input'].value, 'https://example.com/video');
    assert.equal(h.elements['preview-form'].emit('submit').event.defaultPrevented, false);
});

test('drawer owns keyboard focus while open and restores the trigger on Escape', () => {
    const h = harness(); h.init(); const opener = h.elements['settings-open-btn']; opener.focus();
    opener.emit('click');
    assert.equal(h.elements['workbench-shell'].inert, true);
    assert.equal(h.document.activeElement, h.elements['settings-close-btn']);
    h.elements['setting-sakuga-vfx'].focus(); h.document.emit('keydown', {key: 'Tab'});
    assert.equal(h.document.activeElement, h.elements['settings-close-btn']);
    h.document.emit('keydown', {key: 'Escape'});
    assert.equal(h.elements['settings-drawer'].hidden, true);
    assert.equal(h.elements['workbench-shell'].inert, false);
    assert.equal(h.document.activeElement, opener);
});

test('video controls use source FPS and stage fitting uses CSS pixels, not device pixels', () => {
    const h = harness(); h.init(); const player = h.elements['preview-player'];
    Object.assign(player, {videoWidth: 1080, videoHeight: 1920, readyState: 1, duration: 10, currentTime: 1, paused: false});
    player.dataset.previewId = 'portrait';
    h.wb.previewChanged({id: 'portrait', source_type: 'local', video_resolution: {width: 1080, height: 1920}, video_metadata: {frame_rate: 25}});
    h.flushFrames();
    assert.equal(h.elements['video-stage'].style.width, '225.000px');
    assert.equal(h.elements['video-stage'].style.height, '400.000px');
    h.elements['frame-forward-btn'].emit('click');
    approx(player.currentTime, 1.04); assert.equal(player.paused, true);
    h.elements['frame-back-btn'].emit('click'); approx(player.currentTime, 1);
    h.wb.previewChanged({id: 'portrait', source_type: 'local', video_metadata: null});
    assert.equal(h.elements['frame-forward-btn'].disabled, true);
    assert.equal(h.elements['play-pause-btn'].disabled, false);
    player.emit('error');
    assert.equal(h.elements['play-pause-btn'].disabled, true);
    assert.equal(h.elements['video-seek'].disabled, true);
});

test('missing decoder metadata still fits the known source resolution without enabling transport', () => {
    const h = harness(); h.init(); h.elements['preview-player'].dataset.previewId = 'unsupported-codec';
    h.wb.previewChanged({id: 'unsupported-codec', video_resolution: {width: 1080, height: 1920}, video_metadata: {duration: 12, frame_rate: 30}});
    h.flushFrames();
    assert.equal(h.elements['video-stage'].style.width, '225.000px');
    assert.equal(h.elements['play-pause-btn'].disabled, true);
    assert.equal(h.elements['frame-back-btn'].disabled, true);
    assert.match(h.elements['video-timecode'].textContent, /00:12\.000/);
});

test('download metrics use real connection counts, distinguish media bitrate and reveal urgent notices', () => {
    const h = harness(); h.init();
    h.wb.applySettings({workbench_layout: layout({console_collapsed: true})});
    h.wb.previewChanged({id: 'p', video_metadata: {bit_rate: 2500000}});
    h.wb.downloadChanged({active: true, connections: 6, speed: 5 * 1048576}, 'preview');
    assert.equal(h.elements['metric-connections'].textContent, '6');
    assert.equal(h.elements['metric-speed'].textContent, '5,0');
    assert.equal(h.elements['metric-bitrate'].textContent, '2,5 Mb/s');
    h.wb.downloadChanged({active: true, connections: 1, speed: null, notice: 'Captcha'}, 'preview');
    assert.equal(h.elements['metric-connections'].textContent, '1');
    assert.equal(h.elements['metric-speed'].textContent, '—');
    assert.equal(h.elements['console-toggle-btn'].getAttribute('aria-expanded'), 'true');
    h.wb.downloadChanged(null, 'preview'); assert.equal(h.elements['metric-connections'].textContent, '—');
});

test('result polling does not repeatedly steal a manually selected console tab', () => {
    const h = harness(); h.init(); const result = {translated_srt: 'project.vi.srt'};
    h.wb.resultShown(result, false);
    assert.equal(h.elements['console-result-panel'].hidden, false);
    h.elements['console-log-tab'].emit('click');
    h.wb.resultShown(result, false);
    assert.equal(h.elements['console-log-panel'].hidden, false);
    h.wb.resultShown(result, true);
    assert.equal(h.elements['console-result-panel'].hidden, false);
    h.wb.resultHidden(); assert.equal(h.elements['console-log-panel'].hidden, false);
});

test('real app preview and crop handlers remain usable with the workbench adapter loaded', () => {
    const h = harness({withApp: true}); h.init();
    h.run("applyPreview({id:'p1', source_type:'local', source_url:'local://p1', video_url:'/api/previews/p1/video', video_title:'测试', video_resolution:{width:1080,height:1920}})");
    h.flushFrames();
    assert.equal(h.elements['source-local-panel'].hidden, false);
    assert.equal(h.elements['crop-box'].hidden, false);
    assert.equal(h.elements['preview-player'].dataset.previewId, 'p1');
    h.run('setCrop({crop_x:.1,crop_y:.2,crop_width:.5,crop_height:.4})');
    h.run("startCropDrag({button:0,clientX:0,clientY:0,target:{dataset:{}},currentTarget:byId('crop-box'),pointerId:1,preventDefault(){}})");
    h.run('moveCrop({clientX:22.5,clientY:40}); endCropDrag()');
    const crop = plain(h.run('crop'));
    approx(crop.crop_x, .2); approx(crop.crop_y, .3);
    approx(crop.crop_width, .5); approx(crop.crop_height, .4);
    assert.equal(h.elements['crop-x'].value, '20.0');
    h.run('invalidatePreview()');
    assert.equal(h.elements['crop-box'].hidden, true);
    assert.equal(h.elements['add-queue-btn'].disabled, true);
});

test('real download and result hooks preserve old fields while updating the new dock', () => {
    const h = harness({withApp: true}); h.init();
    h.run("renderDownloadProgress({active:true,source:'douyin',phase:'download',connections:6,percent:50,downloaded:1048576,total:2097152,speed:1048576},'job')");
    assert.equal(h.elements['job-download-stats'].textContent, '50% · 1,0/2,0 MB · 1,0 MB/s');
    assert.equal(h.elements['job-download-fill'].style.width, '50%');
    assert.equal(h.elements['metric-connections'].textContent, '6');
    h.run("showResult({project_dir:'project',video:'project.mp4',raw_srt:'project.raw.srt',translated_srt:'project.vi.srt'},true)");
    assert.equal(h.elements['res-vi'].textContent, 'project.vi.srt');
    assert.equal(h.elements['console-result-panel'].hidden, false);
    h.run('hideResult()'); assert.equal(h.elements['console-log-panel'].hidden, false);
});

test('vertical activity tabs support arrows, wrap, Home/End and only expose the active mounted panel', () => {
    const h = harness(); h.init();
    const panels = [h.elements.workbench, h.elements['workspace-editor'], h.elements['workspace-uploader']];
    const tabs = ['vietsub', 'editor', 'uploader'].map(name => h.elements[`workspace-${name}-tab`]);
    assert.equal(h.elements['workspace-tabs'].getAttribute('aria-orientation'), 'vertical');
    for (const [from, key, to] of [[0, 'ArrowDown', 1], [1, 'End', 2], [2, 'ArrowDown', 0], [0, 'ArrowUp', 2], [2, 'Home', 0]]) {
        const event = tabs[from].emit('keydown', {key}).event;
        assert.equal(event.defaultPrevented, true);
        assert.equal(h.document.activeElement, tabs[to]);
        for (let index = 0; index < panels.length; index++) {
            assert.equal(panels[index].hidden, index !== to);
            assert.equal(panels[index].inert, index !== to);
            assert.equal(tabs[index].getAttribute('aria-selected'), String(index === to));
            assert.equal(tabs[index].tabIndex, index === to ? 0 : -1);
        }
    }
    assert.equal(h.wb.selectWorkspace('not-a-workspace'), false);
    assert.equal(h.elements.workbench, panels[0], 'switching must not clone or remount VietSub');
});

test('workspace round trips retain source, preview, normalized crop, transport and shared console state', () => {
    const h = harness({withApp: true}); h.init();
    h.run("applyPreview({id:'keep',source_type:'local',source_url:'local://keep',video_url:'/api/previews/keep/video',video_title:'Keep',video_resolution:{width:1080,height:1920}})");
    const player = h.elements['preview-player'];
    Object.assign(player, {currentTime: 3.25, duration: 20, readyState: 1, paused: true});
    player.emit('loadedmetadata');
    h.run('setCrop({crop_x:.1,crop_y:.6,crop_width:.8,crop_height:.2})');
    h.run("showResult({translated_srt:'keep.vi.srt',project_dir:'keep'},true)");
    h.flushFrames();
    const src = player.src, stageWidth = h.elements['video-stage'].style.width, crop = plain(h.run('crop'));
    h.elements['url-input'].focus();
    h.wb.selectWorkspace('editor');
    assert.equal(h.document.activeElement, h.elements['workspace-editor-tab'], 'never leave focus in an inert workspace');
    h.elements['preview-viewport'].clientWidth = 0; h.elements['preview-viewport'].clientHeight = 0;
    h.flushFrames();
    assert.equal(h.elements['video-stage'].style.width, stageWidth, 'hidden preview must not be resized to zero');
    h.wb.selectWorkspace('uploader'); h.wb.selectWorkspace('vietsub');
    assert.equal(h.elements['source-local-panel'].hidden, false);
    assert.equal(player.src, src); assert.equal(player.currentTime, 3.25);
    assert.equal(h.elements['video-seek'].value, '3.25');
    assert.deepEqual(plain(h.run('crop')), crop);
    assert.equal(h.elements['console-result-panel'].hidden, false);
    assert.equal(h.run('currentPreview.id'), 'keep');
});

test('focusSource and the preview skip link reveal VietSub before focusing an existing control', () => {
    const h = harness(); h.init(); h.wb.selectWorkspace('uploader');
    h.wb.focusSource();
    assert.equal(h.elements.workbench.hidden, false);
    assert.equal(h.elements['source-link-panel'].hidden, false);
    assert.equal(h.document.activeElement, h.elements['url-input']);
    h.wb.selectWorkspace('editor');
    assert.equal(h.elements['preview-skip-link'].emit('click').event.defaultPrevented, true);
    assert.equal(h.elements.workbench.hidden, false);
    assert.equal(h.document.activeElement, h.elements['preview-viewport']);
});

test('drawer restoration never targets a control in a now-hidden workspace', () => {
    const h = harness(); h.init();
    h.elements['url-input'].focus(); h.elements['settings-open-btn'].emit('click');
    h.wb.selectWorkspace('editor');
    assert.equal(h.document.activeElement, h.elements['settings-close-btn'], 'workspace updates do not focus behind the modal');
    h.document.emit('keydown', {key: 'Escape'});
    assert.equal(h.document.activeElement, h.elements['workspace-editor-tab']);
});

const projectSnapshot = (workspace = 'editor', overrides = {}) => ({
    workspace, job_id: 'job-1', manifest_path: 'clip.project.json', video_url: '/api/projects/job-1/video?cap=server-token',
    ready: true, availability: {video: true, translated_srt: true},
    manifest: {schema_version: 1, project_id: 'project-1', project_name: '<img src=x onerror=alert(1)>',
        source: {url: 'local://clip', video_id: 'clip', original_video: 'original.mp4'},
        assets: {video: 'clip.mp4', translated_srt: 'clip.vi.srt'}, crop_box: {x: .1, y: .6, width: .8, height: .2},
        source_resolution: {width: 1080, height: 1920}, metadata: {title: 'Title', description: 'Line 1\nLine 2', hashtags: ['#one', '#two']},
        stages: {vietsub: {status: 'success'}, editor: {status: 'not_started'}, uploader: {status: 'not_started'}}}, ...overrides,
});

test('staging projects show real manifest fields as text with normalized source crop and server scoped video', () => {
    const h = harness(); h.init();
    const snapshot = projectSnapshot();
    h.wb.projectReceived('editor', snapshot);
    assert.equal(h.elements['editor-empty'].hidden, true);
    assert.equal(h.elements['editor-project'].hidden, false);
    assert.equal(h.elements['editor-project-name'].textContent, snapshot.manifest.project_name);
    assert.equal(h.elements['editor-project-name'].innerHTML, '', 'manifest text is never interpreted as markup');
    assert.equal(h.elements['editor-project-id'].textContent, 'job-1 / project-1');
    assert.equal(h.elements['editor-source'].textContent, 'local://clip · clip · original.mp4');
    assert.equal(h.elements['editor-srt'].textContent, 'clip.vi.srt');
    assert.equal(h.elements['editor-crop'].textContent, 'x 0.1000 · y 0.6000 · rộng 0.8000 · cao 0.2000');
    assert.equal(h.elements['editor-resolution'].textContent, '1080 × 1920');
    assert.equal(h.elements['editor-manifest'].textContent, 'clip.project.json');
    assert.equal(h.elements['editor-title'].textContent, 'Title');
    assert.equal(h.elements['editor-description'].textContent, 'Line 1\nLine 2');
    assert.equal(h.elements['editor-hashtags'].textContent, '#one #two');
    assert.match(h.elements['editor-stages'].textContent, /Auto Edit: Chưa bắt đầu/);
    assert.equal(h.elements['editor-video'].src, snapshot.video_url);
    assert.equal(h.elements['editor-video'].hidden, false);
    assert.match(h.elements['editor-project-status'].textContent, /chưa render \/ upload/);
    h.elements['editor-video'].currentTime = 4;
    h.wb.projectReceived('editor', snapshot);
    assert.equal(h.elements['editor-video'].currentTime, 4, 'unchanged snapshot does not reset preview time');
    h.elements['editor-video'].emit('error');
    assert.match(h.elements['editor-video-status'].textContent, /Không phát được/);
    assert.equal(h.elements['editor-project'].hidden, false);
});

test('staging only embeds same-origin server URLs for the exact job and degrades safely after cleanup', () => {
    const h = harness(); h.init();
    for (const url of ['file:///C:/clip.mp4', 'https://evil.test/api/projects/job-1/video', '//evil.test/api/projects/job-1/video',
        'javascript:alert(1)', '/api/projects/other/video', '/api/open-folder?path=clip.mp4']) {
        assert.equal(h.wb.helpers.projectVideoUrl(url, 'job-1'), '');
        h.wb.projectReceived('uploader', projectSnapshot('uploader', {video_url: url}));
        assert.equal(h.elements['uploader-video'].hidden, true);
    }
    assert.equal(h.wb.helpers.projectVideoUrl('http://127.0.0.1:8123/api/projects/job-1/video?t=1', 'job-1'), '/api/projects/job-1/video?t=1');
    h.wb.projectReceived('uploader', projectSnapshot('uploader', {ready: false, availability: {video: false, translated_srt: false}}));
    assert.match(h.elements['uploader-video-path'].textContent, /không còn khả dụng/);
    assert.match(h.elements['uploader-srt'].textContent, /không còn khả dụng/);
    assert.equal(h.elements['uploader-project-status'].dataset.state, 'error');
    assert.equal(h.elements['uploader-video'].hidden, true);
    assert.match(h.wb.helpers.cropText({x: 2, y: 0, width: .5, height: .2}), /không hợp lệ/);
});

test('successful real app handoff changes workspace without replacing the VietSub preview or crop', async () => {
    const calls = [];
    const h = harness({withApp: true, fetch: async (url, options) => {
        calls.push([url, JSON.parse(options.body)]);
        return {ok: true, status: 200, json: async () => projectSnapshot()};
    }}); h.init();
    h.run("applyPreview({id:'keep',source_type:'local',source_url:'local://keep',video_url:'/api/previews/keep/video',video_resolution:{width:1920,height:1080}}); setCrop({crop_x:.2,crop_y:.3,crop_width:.5,crop_height:.4})");
    const crop = plain(h.run('crop')), src = h.elements['preview-player'].src;
    await h.run("handoffProject('job-1','editor')");
    assert.deepEqual(calls, [['/api/projects/job-1/handoff', {workspace: 'editor'}]]);
    assert.equal(h.elements.workbench.hidden, true);
    assert.equal(h.elements['workspace-editor'].hidden, false);
    assert.equal(h.document.activeElement, h.elements['workspace-editor-tab']);
    assert.equal(h.elements['editor-srt'].textContent, 'clip.vi.srt');
    assert.deepEqual(plain(h.run('crop')), crop);
    assert.equal(h.elements['preview-player'].src, src);
    h.wb.selectWorkspace('vietsub');
    assert.equal(h.elements['source-local-panel'].hidden, false);
});

test('late handoff responses cannot undo a newer rail selection or steal settings modal focus', async () => {
    const pending = [];
    const h = harness({withApp: true, fetch: () => new Promise(resolve => pending.push(resolve))}); h.init();
    const first = h.run("handoffProject('job-1','editor')");
    h.wb.selectWorkspace('uploader', true);
    pending.shift()({ok: true, status: 200, json: async () => projectSnapshot()}); await first;
    assert.equal(h.elements['workspace-uploader'].hidden, false);
    assert.equal(h.document.activeElement, h.elements['workspace-uploader-tab']);
    h.elements['settings-open-btn'].focus(); h.elements['settings-open-btn'].emit('click');
    const second = h.run("handoffProject('job-1','editor')");
    pending.shift()({ok: true, status: 200, json: async () => projectSnapshot()}); await second;
    assert.equal(h.elements['workspace-uploader'].hidden, false);
    assert.equal(h.document.activeElement, h.elements['settings-close-btn']);
});


test('inspector tabs support arrows, wrap and Home/End without changing workspace or draft values', () => {
    const h = harness(); h.init(); h.wb.selectWorkspace('editor');
    h.elements['editor-speed'].value = '1.25';
    const names = ['frame', 'audio', 'subtitle', 'export'];
    for (const [from, key, to] of [[0, 'ArrowRight', 1], [1, 'End', 3], [3, 'ArrowRight', 0], [0, 'ArrowLeft', 3], [3, 'Home', 0]]) {
        const event = h.elements[`editor-inspector-${names[from]}-tab`].emit('keydown', {key}).event;
        assert.equal(event.defaultPrevented, true);
        assert.equal(h.document.activeElement, h.elements[`editor-inspector-${names[to]}-tab`]);
        for (const [index, name] of names.entries()) {
            const tab = h.elements[`editor-inspector-${name}-tab`], panel = h.elements[`editor-inspector-${name}-panel`];
            assert.equal(tab.tabIndex, index === to ? 0 : -1);
            assert.equal(tab.getAttribute('aria-selected'), String(index === to));
            assert.equal(panel.hidden, index !== to); assert.equal(panel.inert, index !== to);
        }
    }
    assert.equal(h.elements['editor-speed'].value, '1.25');
    assert.equal(h.wb.workspaceState().name, 'editor');
    assert.equal(h.calls.length, 0, 'inspector navigation never saves or calls an API');
    assert.equal(h.wb.selectInspector('editor', 'missing'), false);
    assert.equal(h.wb.selectInspector('missing', 'frame'), false);
});

test('inspector state survives workspace switches and emits one semantic event per actual change', () => {
    const h = harness(); h.init(); const events = [];
    h.document.addEventListener('vietsub:inspector-change', event => events.push(plain(event.detail)));
    h.wb.selectWorkspace('editor'); h.wb.selectInspector('editor', 'audio', true);
    h.wb.selectWorkspace('uploader');
    h.elements['uploader-inspector-accounts-tab'].emit('keydown', {key: 'ArrowRight'});
    assert.equal(h.elements['uploader-inspector-publish-panel'].hidden, false);
    assert.equal(h.document.activeElement, h.elements['uploader-inspector-publish-tab']);
    h.wb.selectWorkspace('editor');
    assert.equal(h.elements['editor-inspector-audio-panel'].hidden, false);
    h.wb.selectInspector('editor', 'audio');
    assert.deepEqual(events, [
        {panel: 'editor-inspector-audio-panel', previous: 'editor-inspector-frame-panel'},
        {panel: 'uploader-inspector-publish-panel', previous: 'uploader-inspector-accounts-panel'},
    ]);
});

test('inspector programmatic changes recover hidden focus and never steal focus behind settings', () => {
    const h = harness(); h.init(); h.wb.selectWorkspace('editor');
    const control = h.elements['editor-mask-mode'];
    h.elements['editor-inspector-frame-panel'].children.push(control);
    control.focus(); h.wb.selectInspector('editor', 'audio');
    assert.equal(h.document.activeElement, h.elements['editor-inspector-audio-tab']);
    h.elements['settings-open-btn'].focus(); h.elements['settings-open-btn'].emit('click');
    h.wb.selectInspector('editor', 'export', true);
    assert.equal(h.document.activeElement, h.elements['settings-close-btn']);
});

test('audio UI exposes one persisted numeric field per option and bounded presentation sliders', () => {
    const h = harness();
    for (const [id, option, min, max, value] of [
        ['editor-speed', 'speed', '0.5', '2', '1'],
        ['editor-pitch-semitones', 'pitch_semitones', '-12', '12', '0'],
    ]) {
        const input = h.elements[id];
        assert.equal(input.type, 'number'); assert.equal(input.getAttribute('data-editor-option'), option);
        assert.equal(input.getAttribute('min'), min); assert.equal(input.getAttribute('max'), max); assert.equal(input.value, value);
        assert.equal([...template.matchAll(new RegExp(`data-editor-option="${option}"`, 'g'))].length, 1);
    }
    for (const id of ['editor-speed-range', 'editor-pitch-range']) {
        assert.equal(h.elements[id].type, 'range');
        assert.equal(h.elements[id].getAttribute('data-editor-option'), null);
        assert.ok(h.elements[id].getAttribute('aria-label'));
    }
    assert.equal(h.elements['editor-change-audio-pitch'].getAttribute('checked'), null);
    assert.equal(h.elements['editor-change-audio-pitch'].getAttribute('data-editor-option'), 'change_audio_pitch');
    assert.deepEqual([...template.matchAll(/data-pitch-preset="(-?\d+)"/g)].map(match => Number(match[1])), [-2, 0, 2, 4]);
    assert.equal(h.elements['editor-audio-preview'].disabled, true);
    assert.equal(h.elements['editor-speed-duration'].getAttribute('aria-live'), 'polite');
    assert.equal(h.elements['editor-tts-provider'].getAttribute('aria-describedby'), 'editor-capcut-voice-help');
    assert.match(template, /href="\/static\/motion\.css"/);
});

test('v2.0.3 M-09 & L-02 frontend fixes preserve empty SVG, clear stage dimensions, hideResult, and encode IDs', () => {
    const h = harness({withApp: true});
    h.init();

    assert.match(appSource, /setAttribute\('accept',\s*'video\/mp4,video\/x-matroska,\.mp4,\.mkv'\)/);

    h.elements['video-stage'].style.width = '320px';
    h.elements['video-stage'].style.height = '180px';
    h.run('invalidatePreview()');
    assert.equal(h.elements['video-stage'].style.width, '');
    assert.equal(h.elements['video-stage'].style.height, '');

    h.run("queueJobs = []; renderQueue()");
    assert.match(h.elements['queue-list'].innerHTML, /class="queue-empty-icon"/);
    assert.match(h.elements['queue-list'].innerHTML, /<svg/);

    h.run("showResult({project_dir:'p',video:'p.mp4',raw_srt:'r.srt',translated_srt:'v.srt',publish_metadata:'p.json'},false)");
    assert.equal(h.elements['res-publish'].textContent, 'p.json');
    assert.equal(h.elements['res-publish-row'].hidden, false);
    h.run('hideResult()');
    assert.equal(h.elements['res-publish'].textContent, '');
    assert.equal(h.elements['res-publish-row'].hidden, true);
    assert.match(appSource, /\/api\/queue\/\$\{encodeURIComponent\(id\)\}\/retry/);
});

test('Phase 4 settings tabs navigate via click/keyboard and console toggle preserves SVG label span', () => {
    const h = harness();
    const toggle = h.elements['console-toggle-btn'];
    const svg = {tagName: 'SVG', className: 'icon'};
    const label = {tagName: 'SPAN', className: 'btn-label', textContent: 'Thu gọn'};
    toggle.children = [svg, label];
    toggle.querySelector = sel => sel === '.btn-label' ? label : sel === 'svg' ? svg : null;
    h.init();

    assert.equal(label.textContent, 'Thu gọn');
    assert.equal(toggle.children.includes(svg), true);
    toggle.emit('click');
    assert.equal(label.textContent, 'Mở rộng');
    assert.equal(toggle.children.includes(svg), true);

    const names = ['general', 'translation', 'presets'];
    assert.equal(h.elements['settings-general-panel'].hidden, false);
    assert.equal(h.elements['settings-translation-panel'].hidden, true);
    assert.equal(h.elements['settings-presets-panel'].hidden, true);
    h.elements['settings-open-btn'].emit('click');
    for (const [from, key, to] of [[0, 'ArrowRight', 1], [1, 'End', 2], [2, 'ArrowRight', 0], [0, 'ArrowLeft', 2], [2, 'Home', 0]]) {
        const event = h.elements[`settings-${names[from]}-tab`].emit('keydown', {key}).event;
        assert.equal(event.defaultPrevented, true);
        assert.equal(h.document.activeElement, h.elements[`settings-${names[to]}-tab`]);
        for (const [index, name] of names.entries()) {
            assert.equal(h.elements[`settings-${name}-tab`].getAttribute('aria-selected'), String(index === to));
            assert.equal(h.elements[`settings-${name}-panel`].hidden, index !== to);
            assert.equal(h.elements[`settings-${name}-panel`].inert, index !== to);
        }
    }
    assert.equal(h.wb.selectSettingsTab('invalid'), false);
});

