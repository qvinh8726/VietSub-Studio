/* VietSub Studio Sakuga effects adapter.
 * Springs and burst/slash/puff geometry adapted from Sakuga VFX by Justin Finkelman,
 * https://github.com/finktheartist/sakuga-vfx, commit c3dabb173928a3ad2372183103caf2e6f50953d0.
 * MIT license: /static/sakuga-LICENSE.txt. One lazy canvas; no timers or observers.
 */
(() => {
    'use strict';
    const TAU = Math.PI * 2, MAX_EFFECTS = 48, MAX_MOTIONS = 24, MAX_ANIMATIONS = 16;
    const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
    const out = t => 1 - (1 - t) ** 3;
    const rnd = (a, b) => a + Math.random() * (b - a);
    const controls = 'button, [role="button"], [role="tab"], [data-pitch-preset], [data-vfx], summary';
    const excluded = '[data-vfx="off"], [data-editor-handle], .crop-handle, [role="separator"], #settings-backdrop';
    const palette = {fill: '#fff6dc', shade: '#f5c542', line: '#07110e', accent: '#7ee0b8', coral: '#ff6b3d'};
    class Spring {
        constructor(x = 1, k = 420, d = 23) { this.x = x; this.to = x; this.v = 0; this.k = k; this.d = d; }
        step(dt) {
            if (!(dt > 0)) return this.x;
            const count = Math.ceil(dt / .008), h = dt / count;
            for (let n = 0; n < count; n++) {
                this.v += (-this.k * (this.x - this.to) - this.d * this.v) * h;
                this.x += this.v * h;
            }
            return this.x;
        }
        get settled() { return Math.abs(this.x - this.to) < .001 && Math.abs(this.v) < .01; }
    }
    let enabled = false, initialized = false, media = null, canvas = null, ctx = null, canvasFailed = false;
    let raf = 0, last = 0, dpr = 1, held = null, lifecycle = 0;
    const effects = [], motions = new Map(), animations = new Map(), jobs = new Map(), completions = new Map();
    const listeners = [], tabs = new Map(), signals = new Set();
    let springs = new WeakMap(), revealed = new WeakMap(), smoked = new WeakMap();
    const active = () => initialized && enabled && !media?.matches && !document.hidden;
    const $ = id => typeof id === 'string' ? document.getElementById(id) : id;
    function visible(el) {
        if (!el || el.isConnected === false || el.hidden || el.inert) return false;
        return !el.closest?.('[hidden], [inert]');
    }
    function usable(el) {
        return visible(el) && !el.disabled && el.getAttribute?.('aria-disabled') !== 'true' &&
            !el.matches?.(':disabled') && !el.closest?.(excluded);
    }
    function point(target) {
        const el = $(target);
        if (!el) return null;
        if (!el.getBoundingClientRect) {
            if (!Number.isFinite(el.x) || !Number.isFinite(el.y)) return null;
            return {x: el.x, y: el.y, w: Number.isFinite(el.w) ? Math.max(0, el.w) : 0, h: Number.isFinite(el.h) ? Math.max(0, el.h) : 0};
        }
        if (!visible(el)) return null;
        const r = el.getBoundingClientRect();
        if (![r.left, r.top, r.width, r.height].every(Number.isFinite) || r.width <= 0 || r.height <= 0) return null;
        if (r.left + r.width < 0 || r.top + r.height < 0 || r.left > window.innerWidth || r.top > window.innerHeight) return null;
        return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height};
    }
    function resize() {
        if (!canvas) return;
        const w = Math.max(1, window.innerWidth), h = Math.max(1, window.innerHeight);
        // Cap both density and total backing pixels on large desktop/WebView monitors.
        dpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(8294400 / (w * h)));
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    }
    function ensureCanvas() {
        if (canvas) return Boolean(ctx);
        if (canvasFailed) return false;
        const node = document.createElement('canvas');
        try { ctx = node.getContext('2d'); } catch (_) { ctx = null; }
        if (!ctx) { canvasFailed = true; return false; }
        canvas = node; canvas.className = 'sakuga-fx';
        canvas.setAttribute('aria-hidden', 'true');
        Object.assign(canvas.style, {position: 'fixed', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', zIndex: '110'});
        document.body.appendChild(canvas); resize();
        return true;
    }
    function start() {
        if (!raf && active()) { last = performance.now(); raf = requestAnimationFrame(frame); }
    }
    function add(life, draw) {
        if (!active() || effects.length >= MAX_EFFECTS || !ensureCanvas()) return;
        effects.push({life, t: 0, draw}); canvas.hidden = false; start();
    }
    function restore(el, spring) {
        if (el.style.transform === spring.applied) el.style.transform = spring.original;
        spring.x = spring.to = 1; spring.v = 0;
        springs.delete(el); motions.delete(el);
    }
    function cancelAnimation(el) {
        const animation = animations.get(el);
        if (!animation) return;
        animations.delete(el); animation.onfinish = animation.oncancel = null;
        animation.cancel();
    }
    function stop() {
        if (raf) cancelAnimationFrame(raf);
        raf = 0; effects.length = 0;
        for (const [el, spring] of motions) restore(el, spring);
        if (held) {
            const spring = springs.get(held);
            if (spring) restore(held, spring);
            held = null;
        }
        for (const el of animations.keys()) cancelAnimation(el);
        for (const el of signals) delete el.dataset.vfxStep;
        signals.clear();
        if (ctx) { ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr); canvas.hidden = true; }
    }
    function frame(now) {
        raf = 0;
        if (!active()) { stop(); return; }
        const elapsed = Math.max(0, (now - last) / 1000), dt = Math.min(elapsed, .034); last = now;
        for (const [el, spring] of motions) {
            if (el.isConnected === false) { restore(el, spring); if (held === el) held = null; continue; }
            // Never fight another owner updating the same inline transform.
            if ((el.style.transform || '') !== spring.applied && (el.style.transform || '') !== spring.original) {
                motions.delete(el); springs.delete(el); if (held === el) held = null; continue;
            }
            spring.age += elapsed;
            const y = spring.step(dt);
            el.style.transform = `${spring.original && spring.original !== 'none' ? spring.original + ' ' : ''}scale(${1 + (1 - y) * .36}, ${y})`;
            // CSSOM rounds transform numbers; compare against its serialized value next frame.
            spring.applied = el.style.transform;
            if (spring.settled || spring.age >= .8) {
                spring.x = spring.to; spring.v = 0;
                if (spring.to === 1) restore(el, spring);
                else motions.delete(el); // A held press stays still, with no polling.
            }
        }
        if (ctx) {
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
            ctx.lineJoin = ctx.lineCap = 'round';
            for (let i = effects.length - 1; i >= 0; i--) {
                const fx = effects[i]; fx.t += elapsed;
                if (fx.t >= fx.life) { effects.splice(i, 1); continue; }
                ctx.save(); fx.draw(fx.t / fx.life, fx.t); ctx.restore();
            }
        }
        if (motions.size || effects.length) raf = requestAnimationFrame(frame);
        else if (ctx) { ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr); canvas.hidden = true; }
    }
    function springTo(el, target, kick = false) {
        if (!active() || !usable(el)) return;
        let spring = springs.get(el);
        if (!spring) {
            if (motions.size >= MAX_MOTIONS) return;
            spring = new Spring(); spring.original = el.style.transform || ''; spring.applied = spring.original;
            springs.set(el, spring);
        }
        spring.to = target; spring.age = 0;
        if (kick) { spring.x = .95; spring.v = 1.2; }
        motions.set(el, spring); start();
    }
    function star(x, y, radius, spikes, rotation, inner) {
        ctx.beginPath();
        for (let i = 0; i < spikes * 2; i++) {
            const angle = rotation + i * Math.PI / spikes, r = radius * (i % 2 ? inner : 1);
            const px = x + Math.cos(angle) * r, py = y + Math.sin(angle) * r;
            if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();
    }
    function burst(x, y, r = 20) {
        if (![x, y, r].every(Number.isFinite)) return;
        const rotation = rnd(0, TAU); r = clamp(r, 4, 32);
        add(.32, k => {
            const radius = r * out(clamp(k / .4));
            ctx.globalAlpha = .85 * (1 - k);
            star(x, y, radius * 1.25, 9, rotation + .12, .42);
            ctx.fillStyle = palette.shade; ctx.fill();
            star(x, y, radius, 9, rotation, .25 + k * .6);
            ctx.fillStyle = palette.fill; ctx.fill();
            ctx.strokeStyle = palette.line; ctx.lineWidth = 1; ctx.stroke();
        });
    }
    function slash(x, y, length = 60) {
        if (![x, y, length].every(Number.isFinite)) return;
        length = clamp(length, 12, 100);
        add(.36, k => {
            const head = out(clamp(k / .42)), tail = clamp((k - .22) / .78) ** 3;
            ctx.translate(x, y); ctx.rotate(-.22); ctx.globalAlpha = .8 * (1 - k);
            ctx.beginPath();
            ctx.moveTo(-length / 2 + length * tail, 3);
            ctx.quadraticCurveTo(0, -12, -length / 2 + length * head, -1);
            ctx.quadraticCurveTo(0, -5, -length / 2 + length * tail, 3);
            ctx.fillStyle = palette.shade; ctx.fill();
            ctx.strokeStyle = palette.fill; ctx.lineWidth = 1; ctx.stroke();
        });
    }
    function sparks(target, count = 7) {
        if (!active()) return;
        const c = point(target); if (!c) return;
        count = Number.isFinite(count) ? clamp(Math.floor(count), 1, 9) : 7;
        for (let i = 0; i < count; i++) {
            const a = rnd(-Math.PI + .2, -.2), velocity = rnd(32, 72), size = rnd(1.3, 2.4);
            add(.6, (k, t) => {
                ctx.globalAlpha = .85 * (1 - k);
                ctx.fillStyle = [palette.shade, palette.accent, palette.coral][i % 3];
                ctx.translate(c.x + Math.cos(a) * velocity * t, c.y + Math.sin(a) * velocity * t + 42 * t * t);
                ctx.rotate(a + t * 4); ctx.fillRect(-size, -size / 2, size * 2, size);
            });
        }
    }
    function smoke(target) {
        if (!active()) return;
        const c = point(target); if (!c) return;
        if (target?.getBoundingClientRect) {
            const now = performance.now();
            if (now - (smoked.get(target) ?? -Infinity) < 60) return;
            smoked.set(target, now);
        }
        const lobes = Array.from({length: 5}, (_, i) => ({a: i / 5 * TAU, r: rnd(3, 6), d: rnd(4, 9)}));
        add(.48, (k, t) => {
            ctx.globalAlpha = .4 * (1 - k); ctx.beginPath();
            for (const l of lobes) {
                const r = l.r * Math.sin(Math.PI * k) ** .5;
                const x = c.x + Math.cos(l.a + t) * l.d * (1 + k), y = c.y + Math.sin(l.a + t) * l.d - 15 * k;
                ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU);
            }
            ctx.fillStyle = palette.accent; ctx.fill();
            ctx.strokeStyle = palette.shade; ctx.lineWidth = .8; ctx.stroke();
        });
    }
    function dissolve(target) {
        if (!active()) return;
        const c = point(target); if (!c) return;
        const fragments = Array.from({length: 12}, () => ({x: rnd(-Math.min(c.w / 2, 70), Math.min(c.w / 2, 70)), y: rnd(-8, 8), v: rnd(12, 28), r: rnd(1.5, 3.5)}));
        add(.52, k => {
            ctx.globalAlpha = .65 * (1 - k);
            for (let i = 0; i < fragments.length; i++) {
                const f = fragments[i], r = f.r * (1 - k * .65);
                ctx.fillStyle = i % 3 ? palette.accent : palette.coral;
                ctx.fillRect(c.x + f.x * (1 + k * .3), c.y + f.y - f.v * out(k), r, r);
            }
        });
    }
    function impact(el, quiet = false) {
        if (!active() || !usable(el)) return;
        const c = point(el); if (!c) return;
        springTo(el, 1, true);
        // Emit at the control's edge, keeping labels and focus indicators legible.
        const edge = {x: c.x + c.w / 2 - 5, y: c.y - c.h / 2 + 5};
        if (!quiet) burst(edge.x, edge.y, 16);
        else sparks(edge, 3);
        slash(c.x, c.y + c.h / 2 - 2, Math.min(c.w * .65, quiet ? 38 : 72));
    }
    function animate(el, keyframes, options) {
        if (!active() || !visible(el) || typeof el.animate !== 'function') return;
        cancelAnimation(el);
        if (animations.size >= MAX_ANIMATIONS) return;
        let animation;
        try { animation = el.animate(keyframes, {fill: 'none', easing: 'cubic-bezier(.2,.8,.2,1)', ...options}); }
        catch (_) { return; } // Older WebViews still get immediate, fully usable content.
        if (!animation?.cancel) return;
        animations.set(el, animation);
        const done = () => { if (animations.get(el) === animation) cancelAnimation(el); };
        animation.onfinish = animation.oncancel = done;
    }
    function cancelWithin(panel) {
        if (!panel) return;
        for (const el of animations.keys()) if (el === panel || panel.contains?.(el)) cancelAnimation(el);
    }
    function reveal(panel, previous, workspace = false) {
        panel = $(panel); previous = $(previous);
        if (panel === previous) return;
        cancelWithin(previous);
        if (!active() || !visible(panel)) return;
        const now = performance.now();
        if (now - (revealed.get(panel) ?? -Infinity) < 60) return; // Semantic + delegated event, same transition.
        revealed.set(panel, now);
        // Animate content only; never clone, move, hide, focus or disable controls.
        animate(panel, [{opacity: .55, transform: `translateY(${workspace ? 8 : 5}px)`}, {opacity: 1, transform: 'translateY(0)'}], {duration: workspace ? 240 : 180});
        if (workspace) {
            const children = [...(panel.querySelectorAll?.('[data-motion-stagger], .source-card, .queue-card, .staging-header, .editor-preview-column, .editor-options-form, .uploader-asset-column, .uploader-compose') || [])].filter(visible).slice(0, 5);
            children.forEach((el, i) => animate(el, [{opacity: .65, transform: 'translateY(4px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 180, delay: 20 + i * 24}));
        }
    }
    function tabChanged(el) {
        if (!el || el.getAttribute?.('role') !== 'tab' || el.getAttribute('aria-selected') !== 'true') return;
        const panel = $(el.getAttribute('aria-controls')), group = el.closest?.('[role="tablist"]') || el.parentElement || 'tabs';
        const previous = tabs.get(group);
        tabs.set(group, panel);
        reveal(panel, previous, el.id?.startsWith('workspace-'));
    }
    function remember(map, key, value) {
        map.set(key, value);
        if (map.size > 128) map.delete(map.keys().next().value);
    }
    function flashStep(el, state) {
        if (!active() || !visible(el) || !el.dataset) return;
        el.dataset.vfxStep = state; signals.add(el);
    }
    function pipeline({jobId, step, status, resultReady, translatedSrt} = {}) {
        if (!jobId) {
            for (const el of signals) { delete el.dataset.vfxStep; }
            signals.clear(); return;
        }
        const prior = jobs.get(jobId);
        const next = {step, status, celebrated: prior?.celebrated || false, watched: prior?.watched || status === 'running'};
        remember(jobs, jobId, next);
        if (prior?.step !== step && status === 'running') {
            const el = $(`step-${step}`), c = point(el);
            flashStep(el, 'active');
            if (prior?.status === 'running') flashStep($(`step-${prior.step}`), 'complete');
            if (c) slash(c.x, c.y + c.h / 2 - 2, Math.min(c.w, 75));
        }
        if (status !== 'running') {
            for (const el of signals) if (el.dataset.vfxStep === 'active') { delete el.dataset.vfxStep; signals.delete(el); }
        }
        if (next.watched && !next.celebrated && status === 'success' && resultReady && translatedSrt) {
            next.celebrated = true;
            const c = point($('step-done'));
            if (!c || !active()) return;
            burst(c.x + c.w / 2 - 6, c.y, 24); sparks(c); slash(c.x, c.y, Math.min(c.w, 75));
        }
    }
    // Business owners emit this only after confirmed success, with a stable operation id.
    // document.dispatchEvent(new CustomEvent('vietsub:complete', {detail: {kind, id, status: 'success', target}}));
    function complete({kind, id, status, target} = {}) {
        const defaults = {render: 'editor-output', tts: 'editor-output', audio: 'editor-output', upload: 'uploader-status', save: 'editor-save-btn', queue: 'queue-count'};
        if (!Object.hasOwn(defaults, kind) || !id || status !== 'success') return;
        const key = `${kind}:${id}`;
        if (completions.has(key)) return;
        remember(completions, key, true);
        if (!active()) return;
        const c = point(target || defaults[kind]); if (!c) return;
        burst(c.x + Math.min(c.w / 2, 48), c.y, kind === 'save' ? 14 : 23);
        sparks(c, kind === 'save' ? 4 : 7); slash(c.x, c.y + 4, 64);
    }
    function refresh() {
        const value = active() ? 'on' : 'off';
        if (document.documentElement.dataset.motion !== value) document.documentElement.dataset.motion = value;
        if (!active()) stop();
    }
    function setEnabled(value) { enabled = value === true; refresh(); }
    function release() {
        if (!held) return;
        const el = held; held = null;
        if (usable(el) && active()) springTo(el, 1);
        else if (springs.has(el)) restore(el, springs.get(el));
    }
    function listen(target, type, callback, options) {
        if (!target?.addEventListener) return;
        target.addEventListener(type, callback, options);
        listeners.push(() => target.removeEventListener?.(type, callback, options));
    }
    function init() {
        if (initialized) return;
        initialized = true;
        const generation = ++lifecycle;
        media = window.matchMedia?.('(prefers-reduced-motion: reduce)') || null;
        if (media?.addEventListener) listen(media, 'change', refresh);
        else if (media?.addListener) { media.addListener(refresh); listeners.push(() => media.removeListener?.(refresh)); }
        listen(document, 'visibilitychange', refresh);
        listen(window, 'resize', resize);
        listen(window, 'blur', stop);
        listen(window, 'pagehide', stop);
        listen(document, 'pointerdown', e => {
            // Finish entrance motion before crop/slider owners measure coordinates.
            for (const panel of animations.keys()) if (panel === e.target || panel.contains?.(e.target)) cancelAnimation(panel);
            if (!active() || e.button !== 0 || e.isPrimary === false) return;
            const el = e.target.closest?.(controls);
            if (!usable(el)) return;
            release(); held = el; springTo(el, .96);
        }, true);
        listen(document, 'pointerup', release);
        listen(document, 'pointercancel', release);
        listen(document, 'click', e => {
            const el = e.target.closest?.(controls);
            if (!el) return;
            if (!e.defaultPrevented && usable(el)) {
                const primary = el.matches?.('.btn-primary, [data-vfx="impact"], #prepare-btn, #editor-draft-btn, #uploader-queue-submit');
                impact(el, !primary);
            }
            // Read the selected panel after its normal target handler has run. A microtask
            // adds no timer/rAF and keeps keyboard/submit feedback ahead of disabled state.
            if (el.getAttribute?.('role') === 'tab') Promise.resolve().then(() => { if (initialized && generation === lifecycle) tabChanged(el); });
        }, true);
        listen(document, 'keydown', e => {
            if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
            const group = e.target.closest?.('[role="tablist"]');
            tabChanged(group?.querySelector?.('[role="tab"][aria-selected="true"]'));
        });
        listen(document, 'change', e => {
            if (!active() || !usable(e.target) || !e.target.matches?.('select, input[type="checkbox"], input[type="radio"]')) return;
            smoke(e.target);
            if (e.target.matches?.('input[type="checkbox"], input[type="radio"]')) { springTo(e.target, 1, true); sparks(e.target, 4); }
        });
        listen(document, 'toggle', e => {
            if (e.target.tagName !== 'DETAILS' || !e.target.open || !visible(e.target)) return;
            const children = [...(e.target.children || [])].filter(el => el.tagName !== 'SUMMARY').slice(0, 4);
            children.forEach((el, i) => animate(el, [{opacity: .5, transform: 'translateY(4px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 180, delay: i * 20}));
        }, true);
        listen(document, 'vietsub:inspector-change', e => reveal(e.detail?.panel, e.detail?.previous));
        listen(document, 'vietsub:workspace-change', e => {
            const panel = name => name === 'vietsub' ? 'workbench' : `workspace-${name}`;
            reveal(e.detail?.panel || panel(e.detail?.workspace || e.detail?.name), panel(e.detail?.previous), true);
        });
        listen(document, 'animationend', e => {
            if (!['sakuga-step-beam', 'sakuga-step-settle'].includes(e.animationName) || !signals.has(e.target)) return;
            delete e.target.dataset.vfxStep; signals.delete(e.target);
        });
        listen(document, 'vietsub:complete', e => complete(e.detail));
        listen(document, 'vietsub:dissolve', e => dissolve(e.detail?.point || e.detail?.target));
        for (const el of document.querySelectorAll?.('[role="tab"][aria-selected="true"]') || []) {
            tabs.set(el.closest?.('[role="tablist"]') || el.parentElement || 'tabs', $(el.getAttribute('aria-controls')));
        }
        const css = getComputedStyle(document.documentElement);
        for (const [key, variable] of Object.entries({fill: '--sk-card', shade: '--sk-pri', line: '--sk-ink', accent: '--sk-acc', coral: '--sk-fill'})) {
            palette[key] = css.getPropertyValue(variable).trim() || palette[key];
        }
        refresh();
    }
    function destroy() {
        enabled = false; refresh();
        for (const unlisten of listeners.splice(0)) unlisten();
        canvas?.remove?.(); canvas = ctx = media = null; canvasFailed = false; initialized = false;
        jobs.clear(); completions.clear(); tabs.clear();
        springs = new WeakMap(); revealed = new WeakMap(); smoked = new WeakMap();
    }
    window.VietSubVFX = {init, destroy, setEnabled, impact, smoke, dissolve, sparks, burst, slash, reveal, complete, pipeline, point, stop, Spring,
        get active() { return active(); }, get pending() { return effects.length + motions.size + animations.size; }};
})();
