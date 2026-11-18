// Compatibility contract captured from the pre-redesign template, independent of Git at test time.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';

const baseline = JSON.parse(readFileSync(new URL('./dom-contract.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../../templates/index.html', import.meta.url), 'utf8');
const voidTags = new Set('area base br col embed hr img input link meta param source track wbr'.split(' '));

// Tokenize this static template, including quoted attributes containing >. No DOM/npm dependency.
function parseTemplate(source) {
    const nodes = [], stack = [];
    const tags = source.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<\/?[a-z][\w:-]*(?:[^<>"']|"[^"]*"|'[^']*')*>/gi);
    for (const [token] of tags) {
        const tag = /^<\/?([\w:-]+)/.exec(token)[1].toLowerCase();
        if (token.startsWith('</')) {
            const at = stack.findLastIndex(node => node.tag === tag);
            if (at >= 0) stack.splice(at);
            continue;
        }
        const attrs = {};
        const body = token.slice(tag.length + 1, token.endsWith('/>') ? -2 : -1);
        for (const match of body.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) {
            attrs[match[1].toLowerCase()] = match[2] ?? match[3] ?? match[4] ?? '';
        }
        const node = {tag, attrs, ancestors: [...stack]};
        nodes.push(node);
        if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(node);
    }
    return nodes;
}
const nodes = parseTemplate(html);
const byId = id => nodes.filter(node => node.attrs.id === id);

test('all 115 legacy IDs survive exactly once, and every new ID is unique', () => {
    assert.equal(Object.keys(baseline.ids).length, 115, 'baseline must not be regenerated from the redesigned template');
    for (const [id, expected] of Object.entries(baseline.ids)) {
        const matches = byId(id);
        assert.equal(matches.length, 1, `legacy ID ${id}`);
        assert.equal(matches[0].tag, expected.tag, `element kind of ${id}`);
    }
    const ids = nodes.filter(node => node.attrs.id).map(node => node.attrs.id);
    assert.equal(new Set(ids).size, ids.length, 'duplicate IDs break byId and accessibility relationships');
});

test('legacy form fields preserve types, limits, file acceptance, defaults and select values', () => {
    for (const [id, expected] of Object.entries(baseline.ids)) {
        const actual = byId(id)[0];
        assert.ok(actual, id);
        for (const [name, value] of Object.entries(expected.attributes || {})) {
            assert.equal(actual.attrs[name], value, `${id}[${name}]`);
        }
        if (expected.options) {
            const options = nodes.filter(node => node.tag === 'option' &&
                node.ancestors.findLast(parent => parent.tag === 'select')?.attrs.id === id)
                .map(node => Object.fromEntries(Object.entries(node.attrs).filter(([key]) => ['value', 'selected', 'disabled'].includes(key))));
            assert.deepEqual(options, expected.options, `${id} options submitted to the backend`);
        }
    }
    assert.ok(byId('prepare-btn')[0].ancestors.some(node => node.attrs.id === 'preview-form'));
    assert.equal(byId('prepare-btn')[0].attrs.type, 'submit');
    for (const id of ['url-input', 'project-name', 'ocr-lang', 'local-video-input']) {
        assert.ok(byId(id)[0].ancestors.some(node => node.attrs.id === 'preview-form'), `${id} stays in the existing source form`);
    }
});

test('legacy data handles and token/version metadata keep their exact values', () => {
    for (const expected of baseline.data_attributes) {
        const matches = nodes.filter(node => node.tag === expected.tag &&
            (node.attrs.id || node.ancestors.findLast(parent => parent.attrs.id)?.attrs.id) === expected.owner &&
            Object.entries(expected.attributes).every(([key, value]) => node.attrs[key] === value));
        assert.equal(matches.length, 1, JSON.stringify(expected));
        assert.equal(nodes.filter(node => node.ancestors.includes(matches[0])).length, 0,
            'crop handles must remain the direct event target; a nested SVG changes event.target.dataset.handle');
    }
    for (const [name, value] of Object.entries(baseline.meta)) {
        const matches = nodes.filter(node => node.tag === 'meta' && node.attrs.name === name);
        assert.equal(matches.length, 1, name);
        assert.equal(matches[0].attrs.content, value, name);
    }
});

test('workbench loads local deferred scripts in dependency order without inline event handlers', () => {
    const scripts = nodes.filter(node => node.tag === 'script');
    assert.deepEqual(scripts.map(node => node.attrs.src), [
        '/static/sakuga-vfx.js', '/static/workbench.js', '/static/app.js',
    ]);
    for (const script of scripts) assert.ok(Object.hasOwn(script.attrs, 'defer'), script.attrs.src);
    for (const [, body] of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) assert.equal(body.trim(), '');
    for (const node of nodes) {
        assert.equal(Object.keys(node.attrs).some(name => /^on[a-z]+$/.test(name)), false,
            `${node.tag}#${node.attrs.id || ''}: inline handlers violate the app CSP`);
    }
    assert.ok(nodes.some(node => node.tag === 'link' && node.attrs.href === '/static/fonts.css'));
});

test('new tabs, splitters, drawer and numeric crop alternatives are connected to real nodes', () => {
    for (const node of nodes.filter(node => node.attrs.role === 'tab')) {
        assert.equal(byId(node.attrs['aria-controls']).length, 1, node.attrs.id);
        assert.equal(byId(node.attrs['aria-controls'])[0].attrs['aria-labelledby'], node.attrs.id);
    }
    for (const id of ['sidebar-resizer', 'console-resizer']) {
        const {attrs} = byId(id)[0];
        assert.equal(attrs.role, 'separator');
        assert.equal(attrs.tabindex, '0');
        assert.ok(attrs['aria-label']);
    }
    const drawer = byId('settings-drawer')[0];
    assert.equal(drawer.attrs.role, 'dialog');
    assert.equal(drawer.attrs['aria-modal'], 'true');
    assert.equal(byId(drawer.attrs['aria-labelledby']).length, 1);
    for (const id of ['crop-x', 'crop-y', 'crop-width', 'crop-height']) {
        assert.equal(byId(id)[0].attrs.type, 'number');
        assert.ok(byId(id)[0].ancestors.some(node => node.tag === 'label'), id);
    }
});
