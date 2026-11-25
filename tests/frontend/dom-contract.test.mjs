// Compatibility contract captured from the pre-redesign template, independent of Git at test time.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';

const baseline = JSON.parse(readFileSync(new URL('./dom-contract.json', import.meta.url), 'utf8'));
const html = readFileSync(new URL('../../templates/index.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../static/style.css', import.meta.url), 'utf8');
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
        '/static/sakuga-vfx.js', '/static/workbench.js', '/static/editor.js', '/static/uploader.js', '/static/app.js',
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

test('activity rail owns exactly three accessible workspace tabs with mounted inactive staging', () => {
    const tabs = nodes.filter(node => node.attrs.role === 'tab' && node.ancestors.some(parent => parent.attrs.id === 'workspace-tabs'));
    assert.deepEqual(tabs.map(node => node.attrs.id), ['workspace-vietsub-tab', 'workspace-editor-tab', 'workspace-uploader-tab']);
    assert.equal(byId('workspace-tabs')[0].attrs['aria-orientation'], 'vertical');
    for (const [index, tab] of tabs.entries()) {
        const panel = byId(tab.attrs['aria-controls'])[0];
        assert.equal(panel.attrs.role, 'tabpanel');
        assert.equal(tab.attrs['aria-selected'], String(index === 0));
        assert.equal(tab.attrs.tabindex, index === 0 ? '0' : '-1');
        assert.equal(Object.hasOwn(panel.attrs, 'hidden'), index !== 0);
        assert.equal(Object.hasOwn(panel.attrs, 'inert'), index !== 0);
    }
    for (const id of ['sidebar', 'preview-form', 'queue-list', 'preview-player', 'video-stage', 'crop-box', 'transport-bar']) {
        assert.ok(byId(id)[0].ancestors.some(node => node.attrs.id === 'workbench'), id);
    }
    for (const id of ['console-dock', 'settings-drawer']) {
        assert.equal(byId(id)[0].ancestors.some(node => ['workbench', 'workspace-editor', 'workspace-uploader'].includes(node.attrs.id)), false, `${id} stays shared`);
    }
});

test('Auto Edit and Publish Hub retain expanded phase1 facts and add real explicit actions', () => {
    for (const name of ['editor', 'uploader']) {
        for (const field of ['project-name', 'project-id', 'source', 'video-path', 'srt', 'crop', 'resolution', 'manifest', 'stages', 'title', 'description', 'hashtags']) {
            assert.equal(byId(`${name}-${field}`).length, 1, `${name}-${field}`);
        }
        assert.equal(byId(`${name}-video`)[0].attrs.preload, 'metadata');
    }
    const uploaderButtons = nodes.filter(node => node.tag === 'button' && node.ancestors.some(parent => parent.attrs.id === 'workspace-uploader'));
    assert.deepEqual(uploaderButtons.map(node => node.attrs.id).sort(), ['uploader-media-upload', 'uploader-use-project',
        'uploader-save-metadata', 'uploader-generate-metadata', 'uploader-queue-submit', 'uploader-profiles-refresh', 'uploader-profile-create', 'uploader-queue-refresh',
        'uploader-queue-clear-finished',
        'uploader-inspector-accounts-tab', 'uploader-inspector-publish-tab',
        'uploader-back-source', 'uploader-refresh-project'].sort());
    assert.ok(Object.hasOwn(byId('uploader-project-facts')[0].attrs, 'open'), 'old QA can see mounted project facts');
    for (const field of ['title', 'description', 'hashtags']) {
        const control = byId(`uploader-${field}-input`)[0];
        assert.ok(control.ancestors.some(parent => parent.attrs.id === 'uploader-publish-form'));
        assert.ok(nodes.some(label => label.tag === 'label' && label.attrs.for === control.attrs.id));
    }
    for (const id of ['uploader-media-upload', 'uploader-save-metadata', 'uploader-generate-metadata', 'uploader-queue-submit', 'uploader-profile-create']) {
        assert.ok(Object.hasOwn(byId(id)[0].attrs, 'disabled'), `${id} waits for actual readiness`);
    }
    assert.equal(byId('uploader-media-file')[0].attrs.accept, 'video/mp4,.mp4');
    assert.equal(byId('uploader-confirm-public')[0].attrs.type, 'checkbox');
    assert.equal(byId('uploader-source-ack')[0].attrs.type, 'checkbox');
    assert.equal(byId('uploader-scheduled-at')[0].attrs.type, 'datetime-local');
    assert.ok(Object.hasOwn(byId('uploader-scheduled-at')[0].attrs, 'disabled'));
    const platforms = nodes.filter(node => node.tag === 'option' && node.ancestors.some(parent => parent.attrs.id === 'uploader-profile-platform')).map(node => node.attrs.value);
    assert.deepEqual(platforms, ['tiktok', 'youtube', 'facebook', 'douyin', 'bilibili', 'xiaohongshu']);
    assert.equal(byId('uploader-publish-jobs')[0].attrs['aria-live'], 'polite');
    assert.match(html, /Asia\/Saigon \(UTC\+07:00\)/);
    assert.match(html, /app và máy tính phải đang chạy/);
    assert.match(html, /thử lại có nguy cơ đăng trùng/);
    for (const id of ['editor-save-btn', 'editor-render-btn', 'editor-extract-btn', 'editor-tts-generate-btn', 'editor-tts-preview-btn',
        'editor-load-voices-btn', 'editor-cancel-btn', 'editor-send-publish-btn']) {
        assert.equal(byId(id).length, 1, id);
        assert.ok(Object.hasOwn(byId(id)[0].attrs, 'disabled'), `${id} must wait for actual runtime/project readiness`);
    }
    const optionNames = nodes.filter(node => node.attrs['data-editor-option']).map(node => node.attrs['data-editor-option']);
    assert.deepEqual([...optionNames].sort(), ['mask_mode', 'blur_strength', 'padding_px', 'solid_color', 'solid_opacity',
        'subtitle_enabled', 'subtitle_preset', 'font', 'font_size', 'text_color', 'outline', 'shadow', 'back_color', 'subtitle_position',
        'subtitle_animation', 'subtitle_highlight_color', 'subtitle_max_words_per_chunk',
        'banner_text', 'banner_position', 'watermark_enabled', 'watermark_path', 'watermark_opacity', 'watermark_corner', 'reframe',
        'output_height', 'encoder', 'audio_mode', 'audio_format', 'bgm_volume', 'tts_enabled', 'tts_provider', 'tts_voice', 'tts_speed',
        'tts_pitch', 'tts_fallback', 'speed', 'change_audio_pitch', 'pitch_semitones', 'ducking_enabled'].sort());
    for (const node of nodes.filter(node => node.attrs['data-editor-option'])) {
        assert.ok(node.ancestors.some(parent => parent.attrs.id === 'editor-options-form'), node.attrs.id);
        assert.ok(nodes.some(label => label.tag === 'label' && (label.attrs.for === node.attrs.id || node.ancestors.includes(label))), node.attrs.id);
    }
    for (const field of ['x', 'y', 'width', 'height']) {
        const node = byId(`editor-crop-${field}`)[0];
        assert.equal(node.attrs.type, 'number');
        assert.equal(node.attrs['aria-invalid'], 'false');
        for (const id of node.attrs['aria-describedby'].split(' ')) assert.equal(byId(id).length, 1, id);
    }
    assert.equal(byId('editor-crop-box')[0].attrs.tabindex, '0');
    assert.equal(byId('editor-task-progress')[0].attrs.role, 'progressbar');
    assert.equal(byId('editor-status')[0].attrs['aria-live'], 'polite');
    assert.equal(byId('editor-watermark-file')[0].attrs.accept, 'image/png,.png');
    assert.notEqual(byId('editor-output-video')[0], byId('editor-video')[0], 'render output must not replace source media');
    assert.doesNotMatch(html, /Upload · Phase 3|WORKSPACE 0\d/, 'internal phase labels stay out of the UI');
    assert.match(html, /Chuyển sang Auto Edit →/); assert.match(html, /Đẩy sang Đăng đa nền tảng →/);
    assert.match(html, /ngăn bàn giao sang Auto Edit \/ Đăng đa nền tảng/);
    assert.match(html, /Chạy headless, không có cửa sổ/);
});

test('48px activity rail is a grid track inside the fixed-height shell with shared console track', () => {
    assert.match(css, /--activity-rail-width:\s*48px/);
    const rule = selector => css.split(`${selector} {`)[1]?.split('}')[0] || '';
    assert.match(rule('.shell'), /grid-template-columns:\s*var\(--activity-rail-width\) minmax\(0, 1fr\)/);
    assert.match(rule('.shell'), /height:\s*100vh/);
    assert.match(rule('.activity-rail'), /grid-column:\s*1/);
    assert.doesNotMatch(rule('.activity-rail'), /position:\s*(fixed|absolute)/);
    for (const selector of ['.workbench', '.staging-workspace', '.console-dock']) assert.match(rule(selector), /grid-column:\s*2/, selector);
    for (const color of ['#111214', '#16181c', '#1d2026', '#262a32', '#4c8bf5']) assert.ok(css.includes(color));
});
