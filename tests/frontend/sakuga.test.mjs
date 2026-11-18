import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/sakuga-vfx.js', import.meta.url), 'utf8');
function eventTarget() {
    const listeners = new Map();
    return {
        listeners,
        addEventListener(type, callback) { const list = listeners.get(type) || []; list.push(callback); listeners.set(type, list); },
        emit(type, values = {}) { for (const callback of listeners.get(type) || []) callback({target: this, button: 0, ...values}); },
    };
}
function harness({reduced = false, hidden = false, canvasAvailable = true} = {}) {
    const frames = new Map(), canvases = [], drawCalls = [];
    let clock = 0, frameId = 0;
    function element(id) {
        return {id, style: {}, disabled: false, isConnected: true,
            closest: selector => selector.includes(`#${id}`) ? elements[id] : null,
            getBoundingClientRect: () => ({left: 40, top: 30, width: 120, height: 34})};
    }
    const elements = Object.fromEntries(['prepare-btn', 'add-queue-btn', 'start-queue-btn', 'source-link-tab',
        'step-resolve', 'step-download', 'step-ocr', 'step-translate', 'step-done'].map(id => [id, element(id)]));
    const ctx = Object.fromEntries(['clearRect', 'setTransform', 'save', 'restore', 'beginPath', 'moveTo', 'lineTo',
        'closePath', 'fill', 'stroke', 'translate', 'rotate', 'quadraticCurveTo', 'arc', 'fillRect'].map(name => [name, (...args) => {
            assert.ok(args.every(arg => typeof arg !== 'number' || Number.isFinite(arg)), `${name}: invalid canvas coordinates`);
            drawCalls.push({name, args});
        }]));
    const media = Object.assign(eventTarget(), {matches: reduced});
    const document = Object.assign(eventTarget(), {
        hidden, documentElement: {dataset: {}},
        body: {appendChild(canvas) { canvases.push(canvas); }},
        getElementById: id => elements[id] || null,
        createElement(tag) {
            assert.equal(tag, 'canvas');
            return {style: {}, attrs: {}, setAttribute(name, value) { this.attrs[name] = value; }, getContext: () => canvasAvailable ? ctx : null};
        },
    });
    const window = Object.assign(eventTarget(), {innerWidth: 1280, innerHeight: 720, devicePixelRatio: 3, matchMedia: () => media});
    const context = vm.createContext({document, window, console,
        getComputedStyle: () => ({getPropertyValue: () => ''}), performance: {now: () => clock},
        requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame: id => frames.delete(id),
    });
    vm.runInContext(source, context, {filename: 'static/sakuga-vfx.js'});
    const api = window.VietSubVFX;
    function advance(ms = 16) {
        clock += ms;
        const pending = [...frames.values()]; frames.clear();
        for (const callback of pending) callback(clock);
    }
    function drain() {
        for (let i = 0; frames.size && i < 500; i++) advance();
        assert.equal(frames.size, 0, 'animation must stop requesting frames once settled');
    }
    return {api, elements, frames, canvases, drawCalls, media, document, window, advance, drain,
        start() { api.init(); api.setEnabled(true); }};
}

test('VFX boot is inert and repeated init does not register duplicate event listeners', () => {
    const h = harness();
    assert.equal(h.frames.size, 0); assert.equal(h.canvases.length, 0);
    h.api.init(); h.api.init();
    for (const event of ['pointerdown', 'pointerup', 'pointercancel', 'click', 'visibilitychange']) {
        assert.equal(h.document.listeners.get(event).length, 1, event);
    }
    h.api.impact(h.elements['prepare-btn']); h.api.smoke({x: 10, y: 10});
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0); assert.equal(h.canvases.length, 0);
});

test('enabled but idle effects never create a canvas or schedule a frame', () => {
    const h = harness(); h.start();
    assert.equal(h.api.active, true); assert.equal(h.document.documentElement.dataset.motion, 'on');
    assert.equal(h.frames.size, 0); assert.equal(h.canvases.length, 0);
});

test('the canvas is shared, pointer-transparent, decorative and caps its backing resolution', () => {
    const h = harness(); h.start();
    h.api.impact(h.elements['prepare-btn']); h.api.smoke(h.elements['source-link-tab']);
    assert.equal(h.canvases.length, 1);
    const canvas = h.canvases[0];
    assert.equal(canvas.style.pointerEvents, 'none'); assert.equal(canvas.attrs['aria-hidden'], 'true');
    assert.equal(canvas.width, 2560); assert.equal(canvas.height, 1440);
    assert.equal(h.frames.size, 1, 'all effects use one scheduler');
    h.window.innerWidth = 1920; h.window.innerHeight = 1080; h.window.emit('resize');
    assert.equal(canvas.width, 3840); assert.equal(canvas.height, 2160);
    h.drain();
    assert.equal(h.api.pending, 0); assert.equal(h.elements['prepare-btn'].style.transform, '');
    assert.ok(h.drawCalls.some(call => call.name === 'quadraticCurveTo'), 'slash rendered through the real drawing code');
});

test('disabled controls do not squash or emit an impact', () => {
    const h = harness(); h.start(); const button = h.elements['prepare-btn']; button.disabled = true;
    h.document.emit('pointerdown', {target: button}); h.document.emit('click', {target: button});
    h.api.impact(button);
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0); assert.equal(h.canvases.length, 0);
});

test('pointer press settles while held, then pointercancel restores the transform without a permanent loop', () => {
    const h = harness(); h.start(); const button = h.elements['prepare-btn'];
    h.document.emit('pointerdown', {target: button}); h.drain();
    assert.match(button.style.transform, /scale\(/);
    h.document.emit('pointercancel', {target: button}); h.drain();
    assert.equal(button.style.transform, ''); assert.equal(h.api.pending, 0);
});

test('turning effects off clears pending paint and resets an in-flight spring immediately', () => {
    const h = harness(); h.start(); const button = h.elements['add-queue-btn'];
    h.api.impact(button); h.advance(); assert.notEqual(button.style.transform, '');
    h.api.setEnabled(false);
    assert.equal(h.api.active, false); assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
    assert.equal(button.style.transform, '');
    assert.equal(h.document.documentElement.dataset.motion, 'off');
    h.api.setEnabled(true);
    assert.equal(h.frames.size, 0, 'reenabling must not replay old effects');
});

test('reduced-motion suppresses canvas and spring work even when user preference is enabled', () => {
    const h = harness({reduced: true}); h.start();
    h.api.impact(h.elements['prepare-btn']); h.api.smoke({x: 30, y: 30});
    h.api.pipeline({jobId: 'r', step: 'download', status: 'running'});
    assert.equal(h.api.active, false); assert.equal(h.document.documentElement.dataset.motion, 'off');
    assert.equal(h.frames.size, 0); assert.equal(h.api.pending, 0); assert.equal(h.canvases.length, 0);
});

test('an OS reduced-motion change stops an already running effect and does not replay it', () => {
    const h = harness(); h.start(); h.api.impact(h.elements['prepare-btn']); h.advance();
    h.media.matches = true; h.media.emit('change');
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0); assert.equal(h.api.active, false);
    assert.equal(h.elements['prepare-btn'].style.transform, '');
    h.media.matches = false; h.media.emit('change');
    assert.equal(h.api.active, true); assert.equal(h.frames.size, 0);
});

test('hidden windows stop animations and remain idle on return', () => {
    const h = harness(); h.start(); h.api.smoke({x: 100, y: 80});
    h.document.hidden = true; h.document.emit('visibilitychange');
    assert.equal(h.api.active, false); assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
    h.document.hidden = false; h.document.emit('visibilitychange');
    assert.equal(h.api.active, true); assert.equal(h.frames.size, 0);
});

test('effect bursts are bounded under repeated clicks and stop once the particles expire', () => {
    const h = harness(); h.start();
    for (let i = 0; i < 1000; i++) h.api.smoke({x: 100, y: 100});
    assert.equal(h.api.pending, 48);
    assert.equal(h.canvases.length, 1); assert.equal(h.frames.size, 1);
    h.drain(); assert.equal(h.api.pending, 0);
});

test('repeated pipeline polls animate a transition only once', () => {
    const h = harness(); h.start();
    const first = {jobId: 'job-a', step: 'download', status: 'running'};
    for (let i = 0; i < 100; i++) h.api.pipeline(first);
    assert.equal(h.api.pending, 1);
    h.api.pipeline({...first, step: 'ocr'});
    assert.equal(h.api.pending, 2);
    h.drain();
    h.api.pipeline({...first, step: 'ocr'});
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
});

test('loading an already completed historical job never triggers celebration', () => {
    const h = harness(); h.start();
    const past = {jobId: 'history', step: 'done', status: 'success', resultReady: true, translatedSrt: 'history.vi.srt'};
    for (let i = 0; i < 20; i++) h.api.pipeline(past);
    assert.equal(h.api.pending, 0); assert.equal(h.canvases.length, 0); assert.equal(h.frames.size, 0);
});

test('a watched job celebrates exactly once only after its Vietnamese result is ready', () => {
    const h = harness(); h.start();
    h.api.pipeline({jobId: 'live', step: 'translate', status: 'running'}); h.drain();
    h.api.pipeline({jobId: 'live', step: 'done', status: 'success', resultReady: false, translatedSrt: 'live.vi.srt'});
    assert.equal(h.api.pending, 0);
    h.api.pipeline({jobId: 'live', step: 'done', status: 'success', resultReady: true, translatedSrt: ''});
    assert.equal(h.api.pending, 0);
    const ready = {jobId: 'live', step: 'done', status: 'success', resultReady: true, translatedSrt: 'live.vi.srt'};
    h.api.pipeline(ready);
    assert.equal(h.api.pending, 8, 'one burst plus seven small celebratory particles');
    for (let i = 0; i < 20; i++) h.api.pipeline(ready);
    assert.equal(h.api.pending, 8);
    h.drain(); h.api.pipeline(ready);
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
});

test('error and cancellation never celebrate, and disabled completions do not replay later', () => {
    const h = harness(); h.start();
    for (const status of ['error', 'cancelled']) {
        h.api.pipeline({jobId: status, step: 'ocr', status: 'running'}); h.drain();
        h.api.pipeline({jobId: status, step: 'done', status, resultReady: true, translatedSrt: 'bad.vi.srt'});
        assert.equal(h.api.pending, 0);
    }
    h.api.pipeline({jobId: 'disabled', step: 'translate', status: 'running'}); h.drain();
    h.api.setEnabled(false);
    const ready = {jobId: 'disabled', step: 'done', status: 'success', resultReady: true, translatedSrt: 'disabled.vi.srt'};
    h.api.pipeline(ready); h.api.setEnabled(true); h.api.pipeline(ready);
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
});

test('missing canvas support and removed controls cannot strand the scheduler', () => {
    const h = harness({canvasAvailable: false}); h.start();
    assert.doesNotThrow(() => h.api.smoke({x: 30, y: 30}));
    assert.equal(h.api.pending, 0); assert.equal(h.canvases.length, 0); assert.equal(h.frames.size, 0);
    const button = h.elements['prepare-btn']; h.api.impact(button); button.isConnected = false; h.drain();
    assert.equal(h.api.pending, 0);
});

test('the spring converges for variable frame intervals and ignores nonpositive time', () => {
    const h = harness(), spring = new h.api.Spring(1);
    spring.to = .94;
    assert.equal(spring.step(0), 1); assert.equal(spring.step(-1), 1);
    for (let i = 0; i < 120; i++) spring.step(i % 2 ? .016 : .034);
    assert.equal(spring.settled, true); assert.ok(Math.abs(spring.x - .94) < .001);
    spring.to = 1;
    for (let i = 0; i < 120; i++) spring.step(.016);
    assert.equal(spring.settled, true); assert.ok(Math.abs(spring.x - 1) < .001);
});
