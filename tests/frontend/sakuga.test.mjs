import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/sakuga-vfx.js', import.meta.url), 'utf8');
const motionCSS = readFileSync(new URL('../../static/motion.css', import.meta.url), 'utf8');
function eventTarget() {
    const listeners = new Map();
    return {
        listeners,
        addEventListener(type, callback) { const list = listeners.get(type) || []; list.push(callback); listeners.set(type, list); },
        removeEventListener(type, callback) { listeners.set(type, (listeners.get(type) || []).filter(fn => fn !== callback)); },
        emit(type, values = {}) { for (const callback of [...(listeners.get(type) || [])]) callback({target: this, button: 0, defaultPrevented: false, ...values}); },
    };
}
function harness({reduced = false, hidden = false, canvasAvailable = true, canvasThrows = false, waapi = false, legacyMedia = false} = {}) {
    const frames = new Map(), canvases = [], drawCalls = [], animations = [], liveAnimations = new Set(), elements = {};
    let clock = 0, frameId = 0;
    function element(id, {tag = 'button', attrs = {}, parent = null, rect = {left: 40, top: 30, width: 120, height: 34}} = {}) {
        const el = Object.assign(eventTarget(), {
            id, tagName: tag.toUpperCase(), attrs: {...attrs}, dataset: {}, style: {transform: ''}, children: [], parentElement: parent,
            disabled: false, hidden: false, inert: false, isConnected: true,
            getAttribute(name) { return this.attrs[name] ?? null; },
            setAttribute(name, value) { this.attrs[name] = String(value); },
            matches(selector) {
                return selector.split(',').some(part => {
                    let rule = part.trim();
                    if (!rule) return false;
                    if (rule === ':disabled') return this.disabled || Boolean(this.closest('fieldset[disabled]'));
                    const tag = rule.match(/^[a-z]+/i)?.[0];
                    if (tag && tag.toUpperCase() !== this.tagName) return false;
                    if (tag) rule = rule.slice(tag.length);
                    for (const match of rule.matchAll(/#([\w-]+)/g)) if (match[1] !== this.id) return false;
                    for (const match of rule.matchAll(/\.([\w-]+)/g)) if (!(this.attrs.class || '').split(' ').includes(match[1])) return false;
                    for (const match of rule.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
                        const value = ['hidden', 'inert', 'disabled'].includes(match[1]) ? (this[match[1]] ? '' : null) : this.getAttribute(match[1]);
                        if (value === null || (match[2] !== undefined && value !== match[2])) return false;
                    }
                    return true;
                });
            },
            closest(selector) { for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node; return null; },
            contains(other) { for (let node = other; node; node = node.parentElement) if (node === this) return true; return false; },
            querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); },
            querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
            getBoundingClientRect: () => rect,
            focus() { document.activeElement = this; },
        });
        if (waapi) el.animate = (keyframes, options) => {
            const animation = {el, keyframes, options, until: clock + options.duration + (options.delay || 0), onfinish: null, oncancel: null,
                cancel() { liveAnimations.delete(this); this.cancelled = true; this.oncancel?.(); },
                finish() { liveAnimations.delete(this); this.finished = true; this.onfinish?.(); }};
            animations.push(animation); liveAnimations.add(animation); return animation;
        };
        if (parent) parent.children.push(el);
        elements[id] = el; return el;
    }
    for (const id of ['prepare-btn', 'add-queue-btn', 'start-queue-btn', 'source-link-tab',
        'step-resolve', 'step-download', 'step-ocr', 'step-translate', 'step-done']) element(id);
    const ctx = Object.fromEntries(['clearRect', 'setTransform', 'save', 'restore', 'beginPath', 'moveTo', 'lineTo',
        'closePath', 'fill', 'stroke', 'translate', 'rotate', 'quadraticCurveTo', 'arc', 'fillRect'].map(name => [name, (...args) => {
            assert.ok(args.every(arg => typeof arg !== 'number' || Number.isFinite(arg)), `${name}: invalid canvas coordinates`);
            drawCalls.push({name, args});
        }]));
    const media = Object.assign(eventTarget(), {matches: reduced});
    if (legacyMedia) {
        media.addListener = callback => media.listeners.set('change', [...(media.listeners.get('change') || []), callback]);
        media.removeListener = callback => media.removeEventListener('change', callback);
        media.addEventListener = undefined;
    }
    const document = Object.assign(eventTarget(), {
        hidden, activeElement: null, documentElement: {dataset: {}},
        body: {appendChild(canvas) { canvases.push(canvas); }},
        getElementById: id => elements[id] || null,
        querySelectorAll: selector => Object.values(elements).filter(el => el.matches(selector)),
        createElement(tag) {
            assert.equal(tag, 'canvas');
            return {style: {}, attrs: {}, setAttribute(name, value) { this.attrs[name] = value; },
                getContext() { if (canvasThrows) throw new Error('canvas unsupported'); return canvasAvailable ? ctx : null; },
                remove() { this.removed = true; }};
        },
    });
    const window = Object.assign(eventTarget(), {innerWidth: 1280, innerHeight: 720, devicePixelRatio: 3, matchMedia: () => media});
    const context = vm.createContext({document, window, console,
        getComputedStyle: () => ({getPropertyValue: () => ''}), performance: {now: () => clock},
        requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
        cancelAnimationFrame: id => frames.delete(id),
        setTimeout() { assert.fail('VFX must not install timers'); },
        setInterval() { assert.fail('VFX must not install polling'); },
        MutationObserver: class { constructor() { assert.fail('VFX must not observe the full application'); } },
    });
    vm.runInContext(source, context, {filename: 'static/sakuga-vfx.js'});
    const api = window.VietSubVFX;
    function advance(ms = 16) {
        clock += ms;
        const pending = [...frames.values()]; frames.clear();
        for (const callback of pending) callback(clock);
        for (const animation of [...liveAnimations]) if (animation.until <= clock) animation.finish();
    }
    function drain() {
        for (let i = 0; (frames.size || liveAnimations.size) && i < 500; i++) advance();
        assert.equal(frames.size, 0, 'animation must stop requesting frames once settled');
        assert.equal(liveAnimations.size, 0, 'finite WAAPI animations must finish');
    }
    return {api, element, elements, frames, canvases, drawCalls, media, document, window, animations, liveAnimations, advance, drain,
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
    assert.equal(h.api.pending, 9, 'one burst, one slash and seven small celebratory particles');
    for (let i = 0; i < 20; i++) h.api.pipeline(ready);
    assert.equal(h.api.pending, 9);
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

test('delegated feedback covers dynamic buttons, nested icons, preset chips and summaries', () => {
    const h = harness(); h.start();
    for (const [id, opts] of [
        ['editor-render-btn', {attrs: {class: 'btn-primary'}}],
        ['pitch-chip', {attrs: {'data-pitch-preset': '2'}}],
        ['account-action', {attrs: {class: 'btn-secondary'}}],
        ['runtime-summary', {tag: 'summary'}],
    ]) {
        const button = h.element(id, opts), icon = h.element(`${id}-icon`, {tag: 'svg', parent: button});
        h.document.emit('click', {target: icon});
        assert.ok(h.api.pending > 0, id); h.drain();
        assert.equal(button.style.transform, '');
    }
    assert.equal(h.canvases.length, 1);
    const before = h.drawCalls.length;
    h.advance(10000);
    assert.equal(h.frames.size, 0); assert.equal(h.drawCalls.length, before, 'nothing executes while idle');
});

test('input focus, disabled fieldsets, inert panels and crop handles never get button VFX', () => {
    const h = harness(); h.start();
    const fieldset = h.element('disabled-fields', {tag: 'fieldset'}); fieldset.disabled = true;
    const panel = h.element('inactive-panel', {tag: 'section'}); panel.inert = true;
    const targets = [
        h.element('text', {tag: 'input', attrs: {type: 'text'}}),
        h.element('range', {tag: 'input', attrs: {type: 'range'}}),
        h.element('fieldset-button', {parent: fieldset}),
        h.element('inert-button', {parent: panel}),
        h.element('crop-handle', {attrs: {'data-editor-handle': 'nw'}}),
        h.element('opt-out', {attrs: {'data-vfx': 'off'}}),
        h.element('aria-disabled-button', {attrs: {'aria-disabled': 'true'}}),
    ];
    for (const target of targets) {
        h.document.emit('pointerdown', {target}); h.document.emit('click', {target});
    }
    h.document.emit('pointerdown', {target: h.elements['prepare-btn'], button: 2});
    h.document.emit('pointerdown', {target: h.elements['prepare-btn'], isPrimary: false});
    assert.equal(h.api.pending, 0); assert.equal(h.canvases.length, 0); assert.equal(h.frames.size, 0);
});

test('toggles pop with four sparks and one deduplicated puff; text and sliders stay quiet', () => {
    const h = harness(); h.start();
    const checkbox = h.element('pitch-toggle', {tag: 'input', attrs: {type: 'checkbox'}});
    h.api.smoke(checkbox); // Existing settings adapter may also emit this effect.
    h.document.emit('change', {target: checkbox});
    assert.equal(h.api.pending, 6); // one puff, four sparks, one spring
    h.document.emit('change', {target: h.element('voice-select', {tag: 'select'})});
    h.document.emit('change', {target: h.element('text', {tag: 'input', attrs: {type: 'text'}})});
    h.document.emit('change', {target: h.element('slider', {tag: 'input', attrs: {type: 'range'}})});
    assert.equal(h.api.pending, 7); h.drain();
    assert.equal(checkbox.style.transform, '');
});

test('teardown cancels all work, removes every listener and supports a clean reinitialization', () => {
    const h = harness({waapi: true}); h.start();
    h.api.impact(h.elements['prepare-btn']); h.api.reveal(h.element('panel', {tag: 'section'})); h.advance();
    assert.ok(h.api.pending > 0);
    h.api.destroy();
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0); assert.equal(h.liveAnimations.size, 0);
    assert.equal(h.canvases[0].removed, true);
    for (const target of [h.document, h.window, h.media]) for (const list of target.listeners.values()) assert.equal(list.length, 0);
    assert.equal(h.document.documentElement.dataset.motion, 'off');
    h.start(); h.api.init();
    assert.equal(h.document.listeners.get('click').length, 1);
    assert.equal(h.frames.size, 0); h.api.impact(h.elements['prepare-btn']); h.drain();
    assert.equal(h.canvases.filter(canvas => !canvas.removed).length, 1);
});

test('spring effects restore preexisting inline transforms and yield to another owner', () => {
    const h = harness(); h.start(); const button = h.elements['prepare-btn'];
    button.style.transform = 'rotate(2deg)';
    h.api.impact(button); h.advance();
    assert.match(button.style.transform, /^rotate\(2deg\) scale\(/);
    h.drain(); assert.equal(button.style.transform, 'rotate(2deg)');
    h.api.impact(button); h.advance(); button.style.transform = 'translateX(10px)'; h.drain();
    assert.equal(button.style.transform, 'translateX(10px)', 'VFX must not overwrite a newer transform');
});

test('a disabled held control restores immediately on release and a blurred held press resets', () => {
    const h = harness(); h.start(); const button = h.elements['prepare-btn'];
    h.document.emit('pointerdown', {target: button}); h.drain(); button.disabled = true;
    h.document.emit('pointerup');
    assert.equal(button.style.transform, ''); assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
    button.disabled = false; h.document.emit('pointerdown', {target: button}); h.drain();
    h.window.emit('blur'); assert.equal(button.style.transform, ''); assert.equal(h.frames.size, 0);
});

test('both per-control springs and canvas particles have hard bounds during event storms', () => {
    const h = harness(); h.start();
    for (let i = 0; i < 200; i++) h.api.impact(h.element(`dynamic-${i}`));
    assert.ok(h.api.pending <= 48 + 24); assert.equal(h.frames.size, 1); assert.equal(h.canvases.length, 1);
    h.drain(); assert.equal(h.api.pending, 0);
});

test('backing pixels remain bounded on an 8K desktop and the canvas is hidden after its last effect', () => {
    const h = harness(); h.start(); h.window.innerWidth = 7680; h.window.innerHeight = 4320;
    h.api.smoke({x: 100, y: 100});
    assert.ok(h.canvases[0].width * h.canvases[0].height <= 8300000);
    assert.equal(h.canvases[0].hidden, false); h.drain(); assert.equal(h.canvases[0].hidden, true);
    h.api.smoke({x: 50, y: 50}); assert.equal(h.canvases[0].hidden, false); h.drain();
});

test('missing or throwing WAAPI and canvas support safely degrade to static content', () => {
    const h = harness({canvasThrows: true}); h.start(); const panel = h.element('panel', {tag: 'section'});
    assert.doesNotThrow(() => { h.api.smoke(panel); h.api.reveal(panel); });
    h.advance(100); panel.animate = () => { throw new Error('unsupported animation'); };
    assert.doesNotThrow(() => h.api.reveal(panel));
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0); assert.equal(panel.hidden, false);
});

test('legacy WebView media-query listeners stop motion and detach on destroy', () => {
    const h = harness({legacyMedia: true}); h.start(); h.api.impact(h.elements['prepare-btn']);
    h.media.matches = true; h.media.emit('change');
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
    h.api.destroy(); assert.equal(h.media.listeners.get('change').length, 0);
});

test('panel entrance uses only finite WAAPI and preserves focus, hidden state and inline styles', () => {
    const h = harness({waapi: true}); h.start(); const panel = h.element('panel', {tag: 'section'});
    panel.style.transform = 'translateX(0)'; h.document.activeElement = h.elements['prepare-btn'];
    h.api.reveal(panel);
    assert.equal(h.liveAnimations.size, 1); assert.equal(h.frames.size, 0); assert.equal(h.canvases.length, 0);
    assert.equal(h.animations[0].options.fill, 'none'); assert.equal(h.animations[0].options.duration, 180);
    h.drain(); assert.equal(h.api.pending, 0); assert.equal(panel.style.transform, 'translateX(0)');
    assert.equal(panel.hidden, false); assert.equal(panel.inert, false); assert.equal(h.document.activeElement, h.elements['prepare-btn']);
});

test('workspace entrances stagger a bounded set and cancel the entire outgoing group', () => {
    const h = harness({waapi: true}); h.start(); const first = h.element('workspace-editor', {tag: 'section'});
    for (let i = 0; i < 12; i++) h.element(`card-${i}`, {tag: 'section', parent: first, attrs: {'data-motion-stagger': ''}});
    h.document.emit('vietsub:workspace-change', {detail: {workspace: 'editor', previous: 'vietsub'}});
    assert.equal(h.liveAnimations.size, 6);
    assert.ok(h.animations.every(a => a.options.duration + (a.options.delay || 0) <= 300));
    const next = h.element('workspace-uploader', {tag: 'section'});
    h.document.emit('vietsub:workspace-change', {detail: {workspace: 'uploader', previous: 'editor'}});
    assert.equal(h.liveAnimations.size, 1); assert.equal(h.animations.at(-1).el, next);
    h.drain(); assert.equal(h.api.pending, 0);
});

test('semantic inspector changes replace interrupted motion without replaying duplicate events', () => {
    const h = harness({waapi: true}); h.start();
    const first = h.element('editor-inspector-frame-panel', {tag: 'section'}), next = h.element('editor-inspector-audio-panel', {tag: 'section'});
    const detail = {panel: first.id, previous: next.id};
    h.document.emit('vietsub:inspector-change', {detail});
    h.document.emit('vietsub:inspector-change', {detail});
    assert.equal(h.liveAnimations.size, 1); assert.equal(h.animations.length, 1);
    h.document.emit('vietsub:inspector-change', {detail: {panel: next.id, previous: first.id}});
    assert.equal(h.liveAnimations.size, 1); assert.equal(h.animations[0].cancelled, true);
    h.drain(); assert.equal(h.api.pending, 0);
});

test('delegated mouse and arrow-key tab changes animate selected content after selection', async () => {
    const h = harness({waapi: true});
    const group = h.element('workspace-tabs', {tag: 'nav', attrs: {role: 'tablist'}});
    const first = h.element('workbench', {tag: 'section'}), next = h.element('workspace-editor', {tag: 'section'});
    const a = h.element('workspace-vietsub-tab', {parent: group, attrs: {role: 'tab', 'aria-selected': 'true', 'aria-controls': first.id}});
    const b = h.element('workspace-editor-tab', {parent: group, attrs: {role: 'tab', 'aria-selected': 'false', 'aria-controls': next.id}});
    next.hidden = true; h.start();
    h.document.emit('click', {target: b, defaultPrevented: true}); // Only inspect the panel transition.
    a.attrs['aria-selected'] = 'false'; b.attrs['aria-selected'] = 'true'; first.hidden = true; next.hidden = false;
    await Promise.resolve();
    assert.equal(h.animations.length, 1); assert.equal(h.animations[0].el, next); h.drain();
    a.attrs['aria-selected'] = 'true'; b.attrs['aria-selected'] = 'false'; first.hidden = false; next.hidden = true;
    h.document.emit('keydown', {target: b, key: 'ArrowUp'});
    assert.equal(h.animations.length, 2); assert.equal(h.animations[1].el, first); h.drain();
    h.document.emit('keydown', {target: a, key: 'Home'});
    assert.equal(h.liveAnimations.size, 0, 'selecting the same tab does not replay its entrance');
});

test('pointerdown finishes entrance transforms before crop owners measure their surface', () => {
    const h = harness({waapi: true}); h.start();
    const panel = h.element('workspace-editor', {tag: 'section'}), crop = h.element('crop', {parent: panel, attrs: {'data-editor-handle': 'nw'}});
    h.api.reveal(panel); assert.equal(h.liveAnimations.size, 1);
    h.document.emit('pointerdown', {target: crop});
    assert.equal(h.liveAnimations.size, 0); assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
});

test('WAAPI is globally bounded and each preference/lifecycle stop cancels it immediately', () => {
    for (const reason of ['preference', 'reduced', 'hidden', 'blur', 'pagehide']) {
        const h = harness({waapi: true}); h.start();
        for (let i = 0; i < 100; i++) h.api.reveal(h.element(`panel-${i}`, {tag: 'section'}));
        assert.equal(h.liveAnimations.size, 16); assert.equal(h.frames.size, 0);
        if (reason === 'preference') h.api.setEnabled(false);
        if (reason === 'reduced') { h.media.matches = true; h.media.emit('change'); }
        if (reason === 'hidden') { h.document.hidden = true; h.document.emit('visibilitychange'); }
        if (reason === 'blur' || reason === 'pagehide') h.window.emit(reason);
        assert.equal(h.liveAnimations.size, 0, reason); assert.equal(h.api.pending, 0, reason);
    }
});

test('semantic completion requires confirmed success and deduplicates by operation id', () => {
    const h = harness(); h.start(); h.element('editor-output', {tag: 'section'});
    for (const status of ['running', 'queued', 'cancelled', 'error', undefined]) h.document.emit('vietsub:complete', {detail: {kind: 'render', id: 'task', status}});
    assert.equal(h.api.pending, 0);
    const detail = {kind: 'render', id: 'task', status: 'success'};
    h.document.emit('vietsub:complete', {detail}); assert.equal(h.api.pending, 9);
    h.document.emit('vietsub:complete', {detail}); assert.equal(h.api.pending, 9); h.drain();
    h.document.emit('vietsub:complete', {detail}); assert.equal(h.api.pending, 0);
    h.api.setEnabled(false); h.api.complete({...detail, id: 'disabled'}); h.api.setEnabled(true);
    h.api.complete({...detail, id: 'disabled'}); assert.equal(h.api.pending, 0);
    h.api.complete({...detail, id: ''}); h.api.complete({...detail, kind: 'unknown'}); assert.equal(h.api.pending, 0);
});

test('dissolve works from a captured point after removal without retaining the removed node', () => {
    const h = harness(); h.start(); const row = h.element('queue-row', {tag: 'section'}), saved = h.api.point(row);
    row.isConnected = false;
    h.document.emit('vietsub:dissolve', {detail: {point: saved}});
    assert.equal(h.api.pending, 1); h.advance();
    assert.ok(h.drawCalls.some(call => call.name === 'fillRect')); h.drain();
    assert.equal(h.api.pending, 0);
    h.api.dissolve({x: NaN, y: 1}); h.api.smoke({x: 1, y: Infinity});
    h.api.sparks(null); h.api.slash(NaN, 1); h.api.burst(1, Infinity);
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
});

test('pipeline CSS signals clear after animation and never replay after disabling', () => {
    const h = harness(); h.start(); const running = {jobId: 'job', step: 'download', status: 'running'};
    h.api.pipeline(running); assert.equal(h.elements['step-download'].dataset.vfxStep, 'active');
    h.document.emit('animationend', {target: h.elements['step-download'], animationName: 'sakuga-step-beam'});
    assert.equal(h.elements['step-download'].dataset.vfxStep, undefined);
    h.api.pipeline(running); assert.equal(h.elements['step-download'].dataset.vfxStep, undefined);
    h.api.pipeline({...running, step: 'ocr'});
    assert.equal(h.elements['step-download'].dataset.vfxStep, 'complete');
    assert.equal(h.elements['step-ocr'].dataset.vfxStep, 'active');
    h.api.setEnabled(false); h.api.setEnabled(true); h.api.pipeline({...running, step: 'ocr'});
    assert.equal(h.elements['step-ocr'].dataset.vfxStep, undefined);
    assert.equal(h.elements['step-download'].dataset.vfxStep, undefined);
    assert.equal(h.frames.size, 0);
});

test('a long compositor stall expires effects on wall time instead of extending animation work', () => {
    const h = harness(); h.start(); h.api.impact(h.elements['prepare-btn']); h.advance(5000);
    assert.equal(h.api.pending, 0); assert.equal(h.frames.size, 0);
    assert.equal(h.elements['prepare-btn'].style.transform, '');
});

test('enabling before init never bypasses the OS reduced-motion preference', () => {
    const h = harness({reduced: true});
    h.api.setEnabled(true); h.api.impact(h.elements['prepare-btn']);
    assert.equal(h.api.active, false); assert.equal(h.frames.size, 0); assert.equal(h.canvases.length, 0);
    h.api.init(); assert.equal(h.api.active, false); assert.equal(h.api.pending, 0);
});

test('an explicit inline none transform remains valid during press and is restored', () => {
    const h = harness(); h.start(); const button = h.elements['prepare-btn']; button.style.transform = 'none';
    h.api.impact(button); h.advance(); assert.match(button.style.transform, /^scale\(/);
    h.drain(); assert.equal(button.style.transform, 'none');
});

test('CSSOM number serialization cannot strand a partially squashed control', () => {
    const h = harness(); h.start(); const button = h.elements['prepare-btn']; let serialized = '';
    Object.defineProperty(button.style, 'transform', {
        get() { return serialized; },
        set(value) { serialized = value.replace(/-?\d+\.\d+/g, number => String(Number(Number(number).toFixed(6)))); },
    });
    h.api.impact(button); h.advance(); assert.match(serialized, /scale\(/);
    h.drain(); assert.equal(serialized, ''); assert.equal(h.api.pending, 0);
});

test('a deferred tab event from a destroyed instance cannot animate after reinitialization', async () => {
    const h = harness({waapi: true});
    const group = h.element('tabs', {tag: 'nav', attrs: {role: 'tablist'}});
    const first = h.element('first-panel', {tag: 'section'}), next = h.element('next-panel', {tag: 'section'});
    const a = h.element('first-tab', {parent: group, attrs: {role: 'tab', 'aria-selected': 'true', 'aria-controls': first.id}});
    const b = h.element('next-tab', {parent: group, attrs: {role: 'tab', 'aria-selected': 'false', 'aria-controls': next.id}});
    h.start(); h.document.emit('click', {target: b, defaultPrevented: true});
    h.api.destroy(); h.start(); a.attrs['aria-selected'] = 'false'; b.attrs['aria-selected'] = 'true';
    await Promise.resolve();
    assert.equal(h.api.pending, 0); assert.equal(h.animations.length, 0); assert.equal(h.frames.size, 0);
});

test('continuous beams require a running task and all motion stops before preferences load', () => {
    const loopingRules = [...motionCSS.matchAll(/([^{}]+)\{([^{}]*animation\s*:[^;{}]*\binfinite\b[^{}]*)\}/g)];
    assert.equal(loopingRules.length, 2);
    for (const [, selectors] of loopingRules) for (const selector of selectors.replace(/\/\*[\s\S]*?\*\//g, '').split(',')) {
        assert.match(selector, /data-motion="on"/);
        assert.match(selector, /data-status="running"|aria-busy="true"/);
    }
    assert.match(motionCSS, /\.step\.active::before\s*\{\s*animation:\s*none/);
    assert.match(motionCSS, /:root:not\(\[data-motion="on"\]\)/);
    assert.match(motionCSS, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    assert.doesNotMatch(source, /\b(?:setInterval|setTimeout|MutationObserver|ResizeObserver)\s*\(/);
});


test('clearing the active job also clears its CSS beam without scheduling a new frame', () => {
    const h = harness(); h.start();
    h.api.pipeline({jobId: 'active', step: 'download', status: 'running'}); h.drain();
    assert.equal(h.elements['step-download'].dataset.vfxStep, 'active');
    h.api.pipeline({jobId: '', step: 'resolve', status: 'running'});
    assert.equal(h.elements['step-download'].dataset.vfxStep, undefined);
    assert.equal(h.frames.size, 0);
});
