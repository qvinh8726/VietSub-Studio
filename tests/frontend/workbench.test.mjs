import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/workbench.js', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('../../static/app.js', import.meta.url), 'utf8');
const template = readFileSync(new URL('../../templates/index.html', import.meta.url), 'utf8');
const STORAGE_KEY = 'vietsub.studio.workbench.v1';
const layout = (overrides = {}) => ({version: 1, sidebar_ratio: .25, console_ratio: .24, console_collapsed: false, ...overrides});
const plain = value => JSON.parse(JSON.stringify(value));
const approx = (actual, expected, message = '') => assert.ok(Math.abs(actual - expected) < 1e-8, `${message}: ${actual} != ${expected}`);
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function eventTarget() {
    const listeners = new Map();
    return {
        listeners,
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
    const window = Object.assign(eventTarget(), {
        innerWidth: 1280, innerHeight: 720, devicePixelRatio: 1.5,
        matchMedia: () => media, confirm: () => true,
        VietSubVFX: {init() {}, setEnabled(value) { vfxEnabled = value; motionCalls.push(value); }, smoke() {}, pipeline() {}},
    });
    const context = vm.createContext({document, window, console, Headers, AbortController, FormData,
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
        assert.equal(helpers.layoutPixels(layout({sidebar_ratio: .01, console_ratio: .01}), width, height).sidebar, 320);
        const max = helpers.layoutPixels(layout({sidebar_ratio: .99, console_ratio: .99}), width, height);
        assert.equal(max.sidebar, 380); approx(max.console, height * .4);
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
        assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '350px');
        assert.equal(h.elements['console-toggle-btn'].getAttribute('aria-expanded'), 'true');
        assert.equal(h.calls.length, 0, 'boot should not overwrite server preferences with defaults');
    }
});

test('server layout restores across origins while a local user drag wins over a delayed GET', async () => {
    const h = harness(); h.init();
    h.wb.applySettings({sakuga_vfx_enabled: false, workbench_layout: layout({sidebar_ratio: .28, console_collapsed: true})});
    approx(parseFloat(h.elements['workbench-shell'].style['--sidebar-width']), 358.4);
    assert.equal(h.elements['workbench-shell'].style['--console-height'], '36px');
    assert.equal(h.vfxEnabled, false);
    assert.deepEqual(JSON.parse(h.storage), layout({sidebar_ratio: .28, console_collapsed: true}));
    h.elements['sidebar-resizer'].emit('keydown', {key: 'End'}); await settle();
    h.wb.applySettings({workbench_layout: layout({sidebar_ratio: .1})});
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '380px', 'late GET must not undo drag');
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
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '320px');
    assert.equal(el.hasPointerCapture(1), false);
    el.emit('keydown', {key: 'End'}); await settle();
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '380px');
    el.emit('dblclick'); await settle();
    assert.equal(h.elements['workbench-shell'].style['--sidebar-width'], '350px');
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
    approx(JSON.parse(h.calls[1][1].body).workbench_layout.sidebar_ratio, 380 / 1280);
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
