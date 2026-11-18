// Unit tests for static/app.js. Run with: node --test tests/frontend
// app.js is a classic browser script, so it is loaded into a vm context with a
// small DOM stand-in; only the behaviour under test touches the DOM.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const APP_JS = readFileSync(new URL('../../static/app.js', import.meta.url), 'utf8');

function makeElement(id = '') {
    return {
        id, hidden: false, textContent: '', className: '', disabled: false, value: '', innerHTML: '',
        style: {}, dataset: {}, children: [], options: [], attributes: {}, onclick: null,
        setAttribute(name, value) { this.attributes[name] = String(value); },
        getAttribute(name) { return this.attributes[name]; },
        removeAttribute(name) { delete this.attributes[name]; },
        appendChild(child) { this.children.push(child); return child; },
        append(...nodes) { this.children.push(...nodes); },
        replaceChildren(...nodes) { this.children = [...nodes]; },
        addEventListener() {},
        querySelector() { return makeElement(); },
        classList: { toggle() {}, add() {}, remove() {} },
        pause() {}, load() {}, focus() {}, scrollIntoView() {}, remove() {},
        get firstChild() { return this.children[0]; },
    };
}

function loadApp({ fetch, confirm = () => true } = {}) {
    const elements = {};
    const document = {
        getElementById(id) { return (elements[id] ??= makeElement(id)); },
        querySelector(selector) { return { content: selector.includes('api-token') ? 'token' : '1.5.4' }; },
        createElement(tag) { return makeElement(tag); },
    };
    const window = { addEventListener() {}, matchMedia: () => ({ matches: true }), confirm, scrollTo() {} };
    const context = vm.createContext({
        document, window, fetch, console, Headers, AbortController, FormData,
        setTimeout, clearTimeout, setInterval, clearInterval, JSON, Date, Math,
    });
    vm.runInContext(APP_JS, context, { filename: 'static/app.js' });
    return { context, elements, run: code => vm.runInContext(code, context) };
}

test('a request that never settles fails after its timeout and shows the disconnected banner', async () => {
    const neverSettles = (_url, options) => new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => {
            const error = new Error('aborted');
            error.name = 'AbortError';
            reject(error);
        });
    });
    const app = loadApp({ fetch: neverSettles });
    const message = await app.run("fetchJson('/api/queue', {timeoutMs: 30}).then(() => 'ok', error => error.message)");
    assert.match(message, /không phản hồi/);
    assert.equal(app.elements['connection-banner'].hidden, false);
});

test('a successful response hides the disconnected banner again', async () => {
    const ok = async () => ({ ok: true, status: 200, json: async () => ({ jobs: [] }) });
    const app = loadApp({ fetch: ok });
    app.run("setConnection(false)");
    await app.run("fetchJson('/api/queue')");
    assert.equal(app.elements['connection-banner'].hidden, true);
});

test('HTTP errors keep their status so callers can react to 409', async () => {
    const conflict = async () => ({ ok: false, status: 409, json: async () => ({ error: 'Hãy chờ job xong' }) });
    const app = loadApp({ fetch: conflict });
    const status = await app.run("fetchJson('/api/update/install', {method: 'POST'}).then(() => 0, error => error.status)");
    assert.equal(status, 409);
});

test('the cancel button is enabled again when the next job starts', () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
    app.run(`
        healthReadiness = {local: true, douyin: true};
        queueJobs = [{id: 'job-b', status: 'running', step: 'ocr', ocr_lang: 'ch', result: {}}];
        cancellingJobId = 'job-a';
        renderQueue();
    `);
    assert.equal(app.elements['cancel-btn'].disabled, false);
    assert.equal(app.elements['cancel-btn'].textContent, 'Huỷ job');
    assert.equal(app.run('cancellingJobId'), '');

    app.run("cancellingJobId = 'job-b'; byId('cancel-btn').disabled = true; renderQueue();");
    assert.equal(app.elements['cancel-btn'].disabled, true);
});

test('a second click is ignored while the same action is running', async () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
    const calls = await app.run(`
        (async () => {
            let calls = 0;
            let release;
            const slow = () => new Promise(resolve => { calls += 1; release = resolve; });
            const first = runOnce('start', slow);
            const second = runOnce('start', slow);
            release();
            await Promise.all([first, second]);
            return calls;
        })()
    `);
    assert.equal(calls, 1);
});

test('the progress caption names the configured translation provider', () => {
    const app = loadApp({ fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }) });
    app.run("translationProviderName = 'DeepSeek API'; updateSteps('translate', 'running');");
    assert.equal(app.elements['process-caption'].textContent, 'DeepSeek API đang dịch');
});
