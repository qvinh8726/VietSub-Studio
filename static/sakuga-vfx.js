/* VietSub Studio Sakuga effects adapter.
 * Springs and burst/slash/puff geometry adapted from Sakuga VFX by Justin Finkelman,
 * https://github.com/finktheartist/sakuga-vfx, commit c3dabb173928a3ad2372183103caf2e6f50953d0.
 * MIT license: /static/sakuga-LICENSE.txt. Single canvas, no dependencies.
 */
(() => {
    'use strict';
    const TAU = Math.PI * 2;
    const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
    const out = t => 1 - (1 - t) ** 3;
    const rnd = (a, b) => a + Math.random() * (b - a);
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
    let enabled = false, initialized = false, media = null, canvas = null, ctx = null;
    let raf = 0, last = 0, dpr = 1, held = null;
    const effects = [], motions = new Map(), springs = new WeakMap(), jobs = new Map();
    const palette = {fill: '#fff6dc', shade: '#f5c542', line: '#07110e', accent: '#7ee0b8'};
    const active = () => enabled && !media?.matches && !document.hidden;
    function point(el) {
        if (!el?.getBoundingClientRect) return el;
        const r = el.getBoundingClientRect();
        return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height};
    }
    function resize() {
        if (!canvas) return;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(window.innerWidth * dpr);
        canvas.height = Math.round(window.innerHeight * dpr);
    }
    function ensureCanvas() {
        if (canvas) return Boolean(ctx);
        canvas = document.createElement('canvas');
        canvas.className = 'sakuga-fx';
        canvas.setAttribute('aria-hidden', 'true');
        Object.assign(canvas.style, {position: 'fixed', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', zIndex: '70'});
        ctx = canvas.getContext('2d');
        if (!ctx) { canvas = null; return false; }
        document.body.appendChild(canvas);
        resize();
        return true;
    }
    function start() {
        if (!raf && active()) { last = performance.now(); raf = requestAnimationFrame(frame); }
    }
    function add(life, draw) {
        if (!active() || !ensureCanvas() || effects.length >= 48) return;
        effects.push({life, t: 0, draw});
        start();
    }
    function stop() {
        if (raf) cancelAnimationFrame(raf);
        raf = 0; effects.length = 0;
        for (const [el, spring] of motions) { el.style.transform = ''; spring.x = spring.to = 1; spring.v = 0; }
        motions.clear();
        if (held) { held.style.transform = ''; springs.delete(held); held = null; }
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    function frame(now) {
        raf = 0;
        if (!active()) { stop(); return; }
        const dt = clamp((now - last) / 1000, 0, .034); last = now;
        for (const [el, spring] of motions) {
            if (!el.isConnected) { motions.delete(el); continue; }
            const y = spring.step(dt);
            el.style.transform = `scale(${1 + (1 - y) * .36}, ${y})`;
            if (spring.settled) {
                spring.x = spring.to; spring.v = 0;
                if (spring.to === 1) el.style.transform = '';
                motions.delete(el);
            }
        }
        if (ctx) {
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
            ctx.lineJoin = ctx.lineCap = 'round';
            for (let i = effects.length - 1; i >= 0; i--) {
                const fx = effects[i]; fx.t += dt;
                if (fx.t >= fx.life) { effects.splice(i, 1); continue; }
                ctx.save(); fx.draw(fx.t / fx.life, fx.t); ctx.restore();
            }
        }
        if (motions.size || effects.length) raf = requestAnimationFrame(frame);
        else if (ctx) ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    }
    function springTo(el, target, kick = false) {
        if (!active() || !el || el.disabled) return;
        let spring = springs.get(el);
        if (!spring) { spring = new Spring(); springs.set(el, spring); }
        spring.to = target;
        if (kick) { spring.x = .94; spring.v = 1.2; }
        motions.set(el, spring); start();
    }
    function star(x, y, radius, spikes, rotation, inner) {
        ctx.beginPath();
        for (let i = 0; i < spikes * 2; i++) {
            const angle = rotation + i * Math.PI / spikes;
            const r = radius * (i % 2 ? inner : 1);
            const px = x + Math.cos(angle) * r, py = y + Math.sin(angle) * r;
            if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();
    }
    function burst(x, y, r = 20) {
        const rotation = rnd(0, TAU);
        add(.32, k => {
            const radius = r * out(clamp(k / .4));
            ctx.globalAlpha = 1 - k;
            star(x, y, radius * 1.25, 9, rotation + .12, .42);
            ctx.fillStyle = palette.shade; ctx.fill();
            star(x, y, radius, 9, rotation, .25 + k * .6);
            ctx.fillStyle = palette.fill; ctx.fill();
            ctx.strokeStyle = palette.line; ctx.lineWidth = 1; ctx.stroke();
        });
    }
    function slash(x, y, length = 60) {
        add(.36, k => {
            const head = out(clamp(k / .42)), tail = clamp((k - .22) / .78) ** 3;
            ctx.translate(x, y); ctx.rotate(-.38); ctx.globalAlpha = 1 - k;
            ctx.beginPath();
            ctx.moveTo(-length / 2 + length * tail, 3);
            ctx.quadraticCurveTo(0, -16, -length / 2 + length * head, -1);
            ctx.quadraticCurveTo(0, -6, -length / 2 + length * tail, 3);
            ctx.fillStyle = palette.shade; ctx.fill();
            ctx.strokeStyle = palette.fill; ctx.lineWidth = 1; ctx.stroke();
        });
    }
    function smoke(target) {
        if (!active()) return;
        const c = point(target); if (!c) return;
        const lobes = Array.from({length: 5}, (_, i) => ({a: i / 5 * TAU, r: rnd(3, 6), d: rnd(4, 9)}));
        add(.48, (k, t) => {
            ctx.globalAlpha = .48 * (1 - k);
            ctx.beginPath();
            for (const l of lobes) {
                const r = l.r * Math.sin(Math.PI * k) ** .5;
                const x = c.x + Math.cos(l.a + t) * l.d * (1 + k), y = c.y + Math.sin(l.a + t) * l.d - 15 * k;
                ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU);
            }
            ctx.fillStyle = palette.accent; ctx.fill();
            ctx.strokeStyle = palette.shade; ctx.lineWidth = .8; ctx.stroke();
        });
    }
    function impact(el) {
        if (!active() || !el || el.disabled) return;
        const c = point(el); springTo(el, 1, true);
        // Offset effects to keep the button's label readable.
        burst(c.x + c.w / 2 - 8, c.y, 17);
        slash(c.x, c.y + c.h / 2 - 3, Math.min(c.w * .65, 72));
    }
    function pipeline({jobId, step, status, resultReady, translatedSrt}) {
        if (!jobId) return;
        const prior = jobs.get(jobId);
        const next = {step, status, celebrated: prior?.celebrated || false, watched: prior?.watched || status === 'running'};
        jobs.set(jobId, next);
        if (jobs.size > 128) jobs.delete(jobs.keys().next().value);
        if (prior?.step !== step && status === 'running') {
            const el = document.getElementById(`step-${step}`), c = point(el);
            if (c) slash(c.x, c.y, Math.min(c.w, 75));
        }
        if (next.watched && !next.celebrated && status === 'success' && resultReady && translatedSrt) {
            next.celebrated = true;
            const c = point(document.getElementById('step-done'));
            if (!c || !active()) return;
            burst(c.x, c.y, 24);
            for (let i = 0; i < 7; i++) {
                const a = rnd(.1, Math.PI - .1), velocity = rnd(35, 80);
                add(.65, (k, t) => {
                    ctx.globalAlpha = 1 - k; ctx.fillStyle = i % 2 ? palette.accent : palette.shade;
                    const x = c.x + Math.cos(a) * velocity * t, y = c.y + Math.sin(a) * velocity * t;
                    ctx.translate(x, y); ctx.rotate(t * 5 + a); ctx.fillRect(-2, -2, 4, 4);
                });
            }
        }
    }
    function refresh() {
        document.documentElement.dataset.motion = active() ? 'on' : 'off';
        if (!active()) stop();
    }
    function setEnabled(value) { enabled = value === true; refresh(); }
    function release() {
        if (held) { springTo(held, 1); held = null; }
    }
    function init() {
        if (initialized) return;
        initialized = true;
        media = window.matchMedia('(prefers-reduced-motion: reduce)');
        media.addEventListener?.('change', refresh);
        document.addEventListener('visibilitychange', refresh);
        window.addEventListener('resize', resize);
        window.addEventListener('blur', () => { release(); stop(); });
        document.addEventListener('pointerdown', e => {
            const el = e.target.closest?.('#prepare-btn, #add-queue-btn, #start-queue-btn, [data-source-tab]');
            if (!el || el.disabled || e.button !== 0 || !active()) return;
            held = el; springTo(el, .94);
        });
        document.addEventListener('pointerup', release);
        document.addEventListener('pointercancel', release);
        document.addEventListener('click', e => {
            const el = e.target.closest?.('#prepare-btn, #add-queue-btn, #start-queue-btn');
            if (el) impact(el);
        }, true);
        const css = getComputedStyle(document.documentElement);
        for (const [key, variable] of Object.entries({fill: '--sk-card', shade: '--sk-pri', line: '--sk-ink', accent: '--sk-acc'})) {
            palette[key] = css.getPropertyValue(variable).trim() || palette[key];
        }
        refresh();
    }
    window.VietSubVFX = {init, setEnabled, impact, smoke, pipeline, point, stop, Spring,
        get active() { return active(); }, get pending() { return effects.length + motions.size; }};
})();
