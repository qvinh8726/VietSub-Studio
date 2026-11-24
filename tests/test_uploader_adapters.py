"""Offline contract tests. No app import, browser dependency, account or network.

The fake Page models locator visibility, upload progress, wizard transitions,
explicit privacy readback and fresh server completion independently of the
adapter's selector dictionaries. This is not a claim of live platform verification.
Run: python -m unittest discover -s tests -p test_uploader_adapters.py -v
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

_MODULE_PATH = Path(__file__).resolve().parents[1] / 'vietsub' / 'uploader' / 'adapters.py'
_SPEC = importlib.util.spec_from_file_location('isolated_uploader_adapters', _MODULE_PATH)
adapters = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapters)


class Group:
    def __init__(self, items):
        self.items = items

    def values(self):
        return self.items() if callable(self.items) else self.items

    def count(self):
        return len(self.values())

    def nth(self, index):
        return self.values()[index]


class Node:
    def __init__(self, page, name, *, text='', attrs=None, visible=True, enabled=True,
                 checked=False, click=None):
        self.page, self.name = page, name
        self.text, self.value = text, text
        self.attrs = attrs or {}
        self.visible, self.enabled, self.checked = visible, enabled, checked
        self.on_click = click
        self.children = {}
        self.fail_fill = False
        self.on_press = None

    @staticmethod
    def evaluate_value(value):
        return value() if callable(value) else value

    def locator(self, selector):
        self.page.queries.append(selector)
        return Group(self.children.get(selector, []))

    def is_visible(self):
        return bool(self.evaluate_value(self.visible))

    def is_enabled(self, **kwargs):
        return bool(self.evaluate_value(self.enabled))

    def is_checked(self, **kwargs):
        return bool(self.evaluate_value(self.checked))

    def get_attribute(self, name, **kwargs):
        return self.evaluate_value(self.attrs.get(name))

    def inner_text(self, **kwargs):
        return self.evaluate_value(self.text)

    def input_value(self, **kwargs):
        return self.value

    def fill(self, value, **kwargs):
        self.page.events.append(('fill', self.name, value))
        if not self.fail_fill:
            self.text = self.value = value

    def press(self, key, **kwargs):
        self.page.events.append(('press', self.name, key))
        if self.on_press:
            self.on_press(key)

    def set_input_files(self, path, **kwargs):
        self.page.events.append(('file', self.name, path))
        self.page.file_selected = True

    def click(self, **kwargs):
        self.page.events.append(('click', self.name))
        if self.on_click:
            self.on_click()
        else:
            self.checked = True


class Response:
    url = 'https://business.facebook.com/api/graphql/'

    class Request:
        post_data = 'fb_api_req_friendly_name=BusinessComposerStoryCreationMutation'

    request = Request()

    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {
            'data': {'xfamily_content_create': {'items': [{}], 'errors': {}}}}

    def json(self):
        return self.payload


class Page:
    """The minimal synchronous browser contract, with observable state transitions."""

    def __init__(self, platform, mode='public', *, pending=False, no_confirmation=False,
                 iframe=False, fallback=False, chooser=False):
        self.platform, self.mode = platform, mode
        self.pending, self.no_confirmation = pending, no_confirmation
        self.events, self.queries, self.waits, self.listeners = [], [], [], {}
        self.mapping, self.nodes = {}, {}
        self.url = 'about:blank'
        self.redirect = None
        self.closed = self.file_selected = self.submitted = self.confirmed = False
        self.stage = 0
        self.submit_count = 0
        self.throw_final = False
        self.on_wait = None
        self.on_submit = None
        self.response = Response()
        self.privacy_refuses = False
        self.iframe = iframe
        self.frame = Node(self, 'frame')
        self.chooser = chooser
        self._build(fallback)

    def add(self, selector, name, *, parent=None, **kwargs):
        node = Node(self, name, **kwargs)
        (self.mapping if parent is None else parent.children)[selector] = [node]
        self.nodes[name] = node
        return node

    def locator(self, selector):
        self.queries.append(selector)
        return Group(self.mapping.get(selector, []))

    def frame_locator(self, selector):
        if selector != 'iframe[data-tt="Upload_index_iframe"]':
            raise AssertionError('Unknown frame')
        return self.frame

    def is_closed(self):
        return self.closed

    def goto(self, url, **kwargs):
        self.events.append(('goto', url))
        self.url = self.redirect or url
        if self.platform == 'facebook' and not self.redirect:
            self.url += '?asset_id=12345'

    def wait_for_timeout(self, value):
        if self.closed:
            raise AssertionError('Wait called after browser closed')
        self.waits.append(value)
        if self.on_wait:
            self.on_wait(self)

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)

    def expect_file_chooser(self, **kwargs):
        page = self

        class ChooserContext:
            class Chosen:
                element = page.nodes['file']
            value = Chosen()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        return ChooserContext()

    def submit(self):
        self.submitted = True
        self.submit_count += 1
        self.events.append(('final', self.mode))
        if self.on_submit:
            self.on_submit(self)
        for callback in list(self.listeners.get('response', [])):
            callback(self.response)
        if self.throw_final:
            raise TimeoutError('click may already have reached the server')
        if not self.no_confirmation:
            self.confirmed = True
            self.success_url()

    def success_url(self):
        if self.platform == 'douyin' and self.mode != 'draft':
            self.url = 'https://creator.douyin.com/creator-micro/content/manage?enter_from=publish'
        elif self.platform == 'bilibili' and self.mode != 'draft':
            self.url = 'https://member.bilibili.com/platform/upload/success'
        elif self.platform == 'xiaohongshu':
            self.url = 'https://creator.xiaohongshu.com/publish/success?note_id=abcdef123456'

    def _build(self, fallback):
        p = self.platform
        root = self.frame if self.iframe else None
        if self.iframe:
            self.add('iframe[data-tt="Upload_index_iframe"]', 'iframe')
        file_selectors = {
            'tiktok': 'input[type="file"][accept*="video"]',
            'youtube': 'ytcp-uploads-dialog input[type="file"]',
            'facebook': '[data-surface$="lib:bizweb_create_post:media"] input[type="file"][accept*="video"]',
            'douyin': 'input[type="file"][class*="upload-btn-input"]',
            'bilibili': 'input[type="file"][accept*="video"]',
            'xiaohongshu': 'div[class^="upload-content"] input[type="file"].upload-input',
        }
        if fallback:
            file_selectors.update(tiktok='input[type="file"]', youtube='input[type="file"]',
                                  facebook='[data-surface$="lib:bizweb_create_post:media"] input[type="file"]',
                                  douyin='input[type="file"][accept*="video"]', bilibili='.upload-wrp input[type="file"]',
                                  xiaohongshu='input[type="file"][accept*="video"]')
        self.add(file_selectors[p], 'file', parent=root, visible=False)
        title_selectors = {
            'youtube': '#title-textarea #textbox',
            'facebook': '[data-surface$="bizweb_create_reel:left_panel"] input[type="text"]:not([role]):visible',
            'douyin': 'input[placeholder*="填写作品标题"]',
            'bilibili': '.video-title input.input-val',
            'xiaohongshu': 'input[placeholder*="填写标题"]',
        }
        if fallback:
            title_selectors.update(youtube='ytcp-social-suggestions-textbox#title-textarea [contenteditable="true"]',
                                   douyin='input[placeholder*="作品标题"]', bilibili='input[placeholder="请输入稿件标题"]',
                                   xiaohongshu='input[placeholder*="输入标题"]')
        body_selectors = {
            'tiktok': 'div.public-DraftEditor-content[contenteditable="true"]',
            'youtube': '#description-textarea #textbox',
            'facebook': '[data-surface$="lib:bizweb_create_reel"] [contenteditable="true"]:visible',
            'douyin': 'div.zone-container[contenteditable="true"]',
            'bilibili': '.video-desc .ql-editor[contenteditable="true"]',
            'xiaohongshu': '.tiptap[contenteditable="true"]',
        }
        if fallback:
            body_selectors.update(tiktok='div[contenteditable="true"][role="textbox"]',
                                  youtube='ytcp-social-suggestions-textbox#description-textarea [contenteditable="true"]',
                                  douyin='div[contenteditable="true"][data-placeholder*="描述"]',
                                  bilibili='textarea[placeholder*="简介"]', xiaohongshu='.ql-editor[contenteditable="true"]')
        if p != 'tiktok':
            self.add(title_selectors[p], 'title', parent=root, visible=lambda: self.file_selected,
                     attrs={'contenteditable': 'true' if p == 'youtube' else None})
        self.add(body_selectors[p], 'body', parent=root, visible=lambda: self.file_selected,
                 attrs={'contenteditable': 'false' if p == 'bilibili' and fallback else 'true'})
        self.add('[role="progressbar"]', 'progress', parent=root, visible=lambda: self.file_selected,
                 attrs={'aria-valuenow': lambda: '43' if self.pending else '100', 'aria-valuemax': '100'})
        if p == 'youtube':
            self._youtube()
        elif p == 'facebook':
            self._facebook()
        else:
            self._simple(root, fallback)

    def _simple(self, root, fallback):
        p = self.platform
        readiness = {'tiktok': ':text-is("Uploaded")',
                     'douyin': '[class^="long-card"] :text-is("重新上传")',
                     'bilibili': '.upload-status:text-is("上传完成")',
                     'xiaohongshu': '.preview-new:text-matches("上传成功|重新上传|100%")'}
        self.add(readiness[p], 'ready', parent=root, visible=lambda: self.file_selected and not self.pending)
        if p in ('tiktok', 'xiaohongshu'):
            trigger_sel = '[data-e2e="post-visibility"]' if p == 'tiktok' else '[data-testid="visibility-select"]'
            trigger = self.add(trigger_sel, 'privacy', parent=root,
                               text='Everyone' if p == 'tiktok' else '公开可见')
            for mode, text in (('public', 'Everyone' if p == 'tiktok' else '公开可见'),
                               ('private', 'Only you' if p == 'tiktok' else '仅自己可见')):
                def select(text=text):
                    if not self.privacy_refuses:
                        trigger.text = text
                self.add(f'[role="option"]:text-is("{text}")', 'option-' + mode,
                         parent=root, click=select)
        elif p == 'douyin':
            public = self.add('label.semi-radio:text-is("公开")', 'option-public', parent=root,
                              checked=True, attrs={'aria-checked': lambda: str(self.mode_selected == 'public').lower()})
            self.mode_selected = 'public'
            private = self.add('label.semi-radio:text-is("仅自己可见")', 'option-private', parent=root,
                               attrs={'aria-checked': lambda: str(self.mode_selected == 'private').lower()})
            def select(mode):
                if not self.privacy_refuses:
                    self.mode_selected = mode
            public.on_click = lambda: select('public')
            private.on_click = lambda: select('private')
        elif p == 'bilibili':
            tags = self.add('.tag-container', 'tags', text='')
            tag_input = self.add('.tag-container input', 'tag-input')
            tag_input.on_press = lambda key: setattr(tags, 'text', tags.text + ' ' + tag_input.value)
            self.add('.select-item-cont .select-item.selected', 'category', text='生活 / 日常')
            self.add('.copyright [role="radio"][aria-checked="true"]', 'copyright',
                     attrs={'aria-checked': 'true'})
        actions = {
            'tiktok': ('button[data-e2e="save_draft_button"]' if self.mode == 'draft'
                       else 'button[data-e2e="post_video_button"]'),
            'douyin': 'button:text-is("暂存草稿")' if self.mode == 'draft' else 'button:text-is("发布")',
            'bilibili': 'button:text-is("存草稿")' if self.mode == 'draft' else '.submit-add',
            'xiaohongshu': 'button.publishBtn',
        }
        self.add(actions[p], 'submit', parent=root, click=self.submit, enabled=lambda: not self.pending)
        signals = {
            'tiktok': ('[role="status"]:text-matches("^(Draft saved|Saved to drafts|Your video has been saved as a draft)[.!]?$", "i")'
                       if self.mode == 'draft' else
                       '[role="status"]:text-matches("^(Your video has been (uploaded|posted)|Video (uploaded|posted)|Post uploaded)[.!]?$", "i")'),
            'douyin': ':text-is("草稿保存成功")' if self.mode == 'draft' else '[role="status"]:text-is("发布成功")',
            'bilibili': ':text-is("草稿保存成功")' if self.mode == 'draft' else '.upload-success',
            'xiaohongshu': ':text-is("发布成功")',
        }
        completion = self.add(signals[p], 'confirmation', parent=root, visible=lambda: self.confirmed,
                              text='投稿成功' if p == 'bilibili' and self.mode != 'draft' else 'confirmed')
        self.add('a[href]', 'result-link', parent=completion, attrs={'href': {
            'tiktok': 'https://www.tiktok.com/@tester/video/12345?tracking=secret',
            'douyin': 'https://www.douyin.com/video/12345',
            'bilibili': 'https://www.bilibili.com/video/BV123abc/',
            'xiaohongshu': 'https://www.xiaohongshu.com/explore/abcdef123456',
        }[p]})

    def _youtube(self):
        self.add('ytcp-uploads-dialog', 'wizard', attrs={'current-step': lambda: str(self.stage)})
        mfk = self.add('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_MFK"]', 'made-for-kids',
                       attrs={'aria-checked': 'false'})
        not_mfk = self.add('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]', 'not-made-for-kids',
                           attrs={'aria-checked': 'true'})
        self.mapping['tp-yt-paper-radio-button[name^="VIDEO_MADE_FOR_KIDS_"]'] = [mfk, not_mfk]
        def advance():
            self.stage += 1
        self.add('#next-button', 'next', click=advance)
        self.youtube_visibility = None
        for mode in ('public', 'private'):
            def choose(mode=mode):
                if not self.privacy_refuses:
                    self.youtube_visibility = mode
            self.add(f'tp-yt-paper-radio-button[name="{mode.upper()}"]', 'option-' + mode,
                     visible=lambda: self.stage == 3, click=choose,
                     attrs={'aria-checked': lambda mode=mode: str(self.youtube_visibility == mode).lower()})
        self.add('#done-button', 'submit', click=self.submit, enabled=lambda: not self.pending,
                 visible=lambda: self.stage == 3)
        self.add('ytcp-video-upload-progress:text-matches("Upload complete|Checks complete|Finished processing|上传完成|检查完毕", "i")',
                 'ready', visible=lambda: not self.pending)
        completion = self.add('ytcp-video-share-dialog', 'confirmation', visible=lambda: self.confirmed)
        self.add(':text-is("Video published")', 'success-heading', parent=completion)
        link = self.add('a[href]', 'result-link', parent=completion,
                        attrs={'href': 'https://youtu.be/abcdefghijk?tracking=secret'})

    def _facebook(self):
        self.add('a[href*="/latest/home"]', 'session-anchor')
        self.add('[data-surface$="bizweb_create_post:left_panel"] [role="combobox"][aria-haspopup="listbox"]:has(img[alt="Facebook"])',
                 'page-identity', text='Creator Page')
        if self.chooser:
            for selector in list(self.mapping):
                if 'input[type="file"]' in selector:
                    self.mapping.pop(selector)
            media = self.add('[data-surface$="lib:bizweb_create_post:media"]', 'media')
            self.add('[role="button"]', 'choose-local-file', parent=media)
        reel = self.add('[data-surface$="lib:bizweb_create_reel"]', 'reel')
        self.add(':text-is("100%")', 'ready', visible=lambda: not self.pending)
        steps = [Node(self, f'step-{i}', attrs={'aria-current': lambda i=i: 'step' if i == self.stage else None})
                 for i in range(3)]
        reel.children['[role="button"][aria-labelledby]:not([aria-haspopup])'] = steps
        back = Node(self, 'back')
        def advance_or_submit():
            if self.stage < 2:
                self.stage += 1
            else:
                self.submit()
        main = Node(self, 'next-or-submit', click=advance_or_submit, enabled=lambda: not self.pending)
        self.nodes['submit'] = main
        self.mapping['[data-surface$="bizweb_create_reel:footer"] [role="button"]:not([aria-label])'] = [back, main]
        self.add('[data-surface$="bizweb_create_reel:left_panel"] input[role="switch"][aria-label="Share to story"]',
                 'story', checked=False)
        self.meta_mode = 0
        modes = []
        for i in range(3):
            def choose(i=i):
                if not self.privacy_refuses:
                    self.meta_mode = i
            node = Node(self, f'publish-mode-{i}', attrs={'aria-pressed': lambda i=i: str(i == self.meta_mode).lower()}, click=choose)
            modes.append(node)
            self.nodes[node.name] = node
        self.mapping['[data-surface$="bizweb_create_reel:left_panel"] [role="button"][aria-pressed]:not([aria-haspopup])'] = modes
        selector = ('[role="dialog"]:text-matches("Reel saved as (a )?draft|Your reel was saved as (a )?draft|已保存为草稿", "i")'
                    if self.mode == 'draft' else
                    '[role="dialog"]:text-matches("Your reel (has been|was) published|Reel published|Reel 已发布|Reels 已发布|你的 Reels 已发布", "i")')
        completion = self.add(selector, 'confirmation', visible=lambda: self.confirmed)
        self.add('a[href]', 'result-link', parent=completion,
                 attrs={'href': 'https://www.facebook.com/reel/12345?tracking=secret'})


class AdapterTests(unittest.TestCase):
    metadata = {'title': 'Clip title', 'description': 'Description <b>not HTML</b> "quotes"',
                'hashtags': ['one', '#hai']}
    platforms = ('tiktok', 'youtube', 'facebook', 'douyin', 'bilibili', 'xiaohongshu')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.video = Path(self.directory.name) / 'trusted clip.mp4'
        self.video.write_bytes(b'offline mock video; never uploaded')

    def adapter(self, platform):
        value = adapters.adapter_for(platform)
        value.form_timeout_ms = value.processing_timeout_ms = value.confirmation_timeout_ms = 500
        value.probe_timeout_ms = 500
        return value

    def upload(self, platform, mode='public', **page_options):
        page = Page(platform, mode, **page_options)
        adapter = self.adapter(platform)
        result = adapter.upload(page, self.video, self.metadata, visibility=mode,
                                before_publish=lambda: page.events.append(('checkpoint',)),
                                progress=lambda p, m: page.events.append(('progress', p)))
        return page, result

    def test_api_and_capability_matrix(self):
        self.assertEqual(set(adapters.PLATFORMS), set(self.platforms))
        expected = {'tiktok': ['public', 'private', 'draft'], 'youtube': ['public', 'private'],
                    'facebook': ['public', 'draft'], 'douyin': ['public', 'private', 'draft'],
                    'bilibili': ['public', 'draft'], 'xiaohongshu': ['public', 'private']}
        for key, value in adapters.PLATFORMS.items():
            self.assertEqual(set(value), {'label', 'login_url', 'upload_url', 'modes'})
            self.assertEqual(value['modes'], expected[key])
            self.assertEqual(self.adapter(key).login_url, value['login_url'])
        self.assertTrue(issubclass(adapters.VerificationRequired, RuntimeError))
        self.assertTrue(issubclass(adapters.UploadCancelled, RuntimeError))
        self.assertIsNot(self.adapter('tiktok'), self.adapter('tiktok'))
        for key in ('TIKTOK', 'unknown', '../youtube', None, [], {}):
            with self.subTest(key=key), self.assertRaises(adapters.UnsupportedMode):
                adapters.adapter_for(key)

    def test_stdlib_only_import_even_when_optional_runtimes_unavailable(self):
        code = """
import builtins, importlib.util
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'playwright', 'patchright', 'flask', 'app', 'vietsub'}:
        raise AssertionError('unexpected optional/app import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
spec = importlib.util.spec_from_file_location('standalone_adapter', %r)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert len(module.PLATFORMS) == 6
""" % str(_MODULE_PATH)
        completed = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_all_six_public_fill_file_options_submit_and_confirm(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page, result = self.upload(platform)
                self.assertEqual(result['status'], 'success')
                self.assertEqual(set(result), {'status', 'url', 'message'})
                self.assertNotIn('secret', result['url'])
                self.assertTrue(result['url'].startswith('https://'))
                self.assertEqual(page.events[0], ('goto', adapters.PLATFORMS[platform]['upload_url']))
                self.assertEqual([event for event in page.events if event[0] == 'file'],
                                 [('file', 'file', str(self.video.resolve()))])
                if platform != 'tiktok':
                    self.assertEqual(page.nodes['title'].value, self.metadata['title'])
                text = page.nodes['body'].value
                self.assertIn(self.metadata['description'], text)
                self.assertIn('#one #hai', text)
                if platform == 'tiktok':
                    self.assertIn(self.metadata['title'], text)
                self.assertEqual(page.submit_count, 1)
                final = page.events.index(('final', 'public'))
                self.assertEqual(page.events[final - 2], ('checkpoint',))
                self.assertEqual(len([event for event in page.events if event[0] == 'checkpoint']), 1)
                self.assertEqual(page.events[-1], ('progress', 100))
                if platform == 'youtube':
                    self.assertEqual(page.stage, 3)
                    self.assertEqual(page.youtube_visibility, 'public')
                    self.assertEqual([e for e in page.events if e == ('click', 'next')], [('click', 'next')] * 3)
                if platform == 'facebook':
                    self.assertEqual(page.stage, 2)
                    self.assertEqual(page.meta_mode, 0)
                    self.assertEqual(page.listeners['response'], [])

    def test_all_advertised_modes_are_implemented(self):
        for platform in self.platforms:
            for mode in adapters.PLATFORMS[platform]['modes']:
                with self.subTest(platform=platform, mode=mode):
                    page, result = self.upload(platform, mode)
                    self.assertEqual(result['status'], 'draft' if mode == 'draft' else 'success')
                    self.assertEqual(page.submit_count, 1)
                    if platform in ('tiktok', 'xiaohongshu') and mode == 'private':
                        self.assertIn(page.nodes['privacy'].text, ('Only you', '仅自己可见'))
                    if platform == 'youtube':
                        self.assertEqual(page.youtube_visibility, mode)
                    if platform == 'douyin' and mode != 'draft':
                        self.assertEqual(page.mode_selected, mode)
                    if platform == 'facebook':
                        self.assertEqual(page.meta_mode, 2 if mode == 'draft' else 0)

    def test_default_is_draft_or_explicitly_unsupported_never_public(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform, 'draft')
                if 'draft' in adapters.PLATFORMS[platform]['modes']:
                    result = self.adapter(platform).upload(page, self.video, self.metadata)
                    self.assertEqual(result['status'], 'draft')
                else:
                    with self.assertRaises(adapters.UnsupportedMode):
                        self.adapter(platform).upload(page, self.video, self.metadata)
                    self.assertEqual(page.events, [])

    def test_unsupported_modes_rejected_before_navigation(self):
        for platform in self.platforms:
            for mode in ('unlisted', 'friends', 'schedule', 'PUBLIC', None, 'private'):
                if mode in adapters.PLATFORMS[platform]['modes']:
                    continue
                with self.subTest(platform=platform, mode=mode):
                    page = Page(platform)
                    with self.assertRaises(adapters.UnsupportedMode):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility=mode)
                    self.assertEqual(page.events, [])

    def test_fallback_selectors_for_six_platforms(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page, result = self.upload(platform, fallback=True)
                self.assertEqual(result['status'], 'success')

    def test_tiktok_iframe_supplied_page_remains_owned_by_parent(self):
        page, result = self.upload('tiktok', 'private', iframe=True)
        self.assertEqual(result['status'], 'success')
        self.assertFalse(page.closed)
        self.assertEqual(page.nodes['privacy'].text, 'Only you')

    def test_meta_lazy_local_file_chooser_uses_set_input_files(self):
        page, result = self.upload('facebook', 'draft', chooser=True)
        self.assertEqual(result['status'], 'draft')
        self.assertEqual(len([e for e in page.events if e[0] == 'file']), 1)

    def test_all_six_verification_and_login_barriers_never_submit(self):
        for platform in self.platforms:
            for barrier in ('captcha', 'auth', 'paywall'):
                with self.subTest(platform=platform, barrier=barrier):
                    page = Page(platform)
                    selector = {'captcha': 'iframe[src*="captcha"]', 'auth': 'input[type="password"]',
                                'paywall': ':text-is("Payment required")'}[barrier]
                    page.add(selector, 'barrier')
                    with self.assertRaises(adapters.VerificationRequired):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility='public')
                    self.assertFalse(page.file_selected)
                    self.assertFalse(page.submitted)

    def test_challenge_during_processing_stops_without_click(self):
        page = Page('douyin', pending=True)
        page.on_wait = lambda p: p.add('input[placeholder*="验证码"]', 'challenge')
        with self.assertRaises(adapters.VerificationRequired):
            self.adapter('douyin').upload(page, self.video, self.metadata, visibility='public')
        self.assertFalse(page.submitted)
        self.assertTrue(page.waits)

    def test_pending_upload_or_processing_never_success_or_checkpoint(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform, pending=True)
                checkpoints = []
                with self.assertRaises(adapters.UploadError):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public',
                                                  before_publish=lambda: checkpoints.append(True))
                self.assertEqual(checkpoints, [])
                self.assertFalse(page.submitted)
                self.assertTrue(page.waits)
                self.assertTrue(all(0 < wait <= 250 for wait in page.waits))

    def test_all_six_final_click_without_confirmation_is_not_success_or_retried(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform, no_confirmation=True)
                checkpoints = []
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public',
                                                  before_publish=lambda: checkpoints.append('durable'))
                self.assertEqual(checkpoints, ['durable'])
                self.assertEqual(page.submit_count, 1)
                self.assertTrue(page.waits)

    def test_processing_status_after_final_click_never_returns_fake_success(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                def submitted(p):
                    p.add('[role="status"]:text-matches("^(Processing(?! (complete|completed|finished))|Uploading(?! (complete|completed))|正在处理|处理中|正在上传)([.。… 0-9%]|$)", "i")',
                          'still-processing')
                page.on_submit = submitted
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public')
                self.assertEqual(page.submit_count, 1)
                self.assertTrue(page.confirmed)  # optimistic UI signal is insufficient
                self.assertTrue(page.waits)

    def test_platform_schedule_toggle_is_never_reused_or_created(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                page.add('[role="switch"][aria-label="Schedule"][aria-checked="true"]', 'remote-schedule')
                with self.assertRaisesRegex(adapters.VerificationRequired, 'scheduling'):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public')
                self.assertFalse(page.submitted)

    def test_meta_mode_drift_before_checkpoint_is_not_public_fallback(self):
        page = Page('facebook', 'draft')
        def progress(percent, message):
            if percent == 90:
                page.meta_mode = 0
        with self.assertRaises(adapters.VerificationRequired):
            self.adapter('facebook').upload(page, self.video, self.metadata, visibility='draft', progress=progress)
        self.assertFalse(page.submitted)

    def test_final_click_timeout_is_never_blindly_retried(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                page.throw_final = True
                with self.assertRaisesRegex(adapters.VerificationRequired, 'do not automatically retry'):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public')
                self.assertEqual(page.submit_count, 1)

    def test_preexisting_success_signal_rejected_before_checkpoint(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                page.confirmed = True
                checkpoints = []
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public',
                                                  before_publish=lambda: checkpoints.append(True))
                self.assertEqual(checkpoints, [])
                self.assertFalse(page.submitted)

    def test_cancel_before_navigation_and_while_processing_for_all_six(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                with self.assertRaises(adapters.UploadCancelled):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public', cancel=lambda: True)
                self.assertEqual(page.events, [])
                event = threading.Event()
                page = Page(platform, pending=True)
                page.on_wait = lambda p: event.set()
                with self.assertRaises(adapters.UploadCancelled):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public', cancel=event)
                self.assertFalse(page.submitted)
                self.assertEqual(len(page.waits), 1)

    def test_cancel_at_checkpoint_vetoes_irreversible_click(self):
        for platform in self.platforms:
            for veto in ('event', 'exception'):
                with self.subTest(platform=platform, veto=veto):
                    page = Page(platform)
                    cancelled = threading.Event()
                    def checkpoint():
                        page.events.append(('checkpoint',))
                        if veto == 'exception':
                            raise adapters.UploadCancelled('Checkpoint vetoed submission.')
                        cancelled.set()
                    with self.assertRaises(adapters.UploadCancelled):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility='public',
                                                      cancel=cancelled, before_publish=checkpoint)
                    self.assertFalse(page.submitted)
                    self.assertEqual(page.events.count(('checkpoint',)), 1)
                    self.assertEqual(page.events[-1], ('checkpoint',))

    def test_checkpoint_failure_vetoes_irreversible_click(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                def fail():
                    raise OSError('durable queue write failed')
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public', before_publish=fail)
                self.assertFalse(page.submitted)

    def test_cancel_after_submit_requires_reconciliation_and_never_retries(self):
        page = Page('tiktok', no_confirmation=True)
        cancelled = threading.Event()
        page.on_wait = lambda p: cancelled.set()
        with self.assertRaisesRegex(adapters.UploadCancelled, 'reconcile'):
            self.adapter('tiktok').upload(page, self.video, self.metadata, visibility='public', cancel=cancelled)
        self.assertEqual(page.submit_count, 1)

    def test_missing_private_control_or_refused_selection_never_falls_back(self):
        for platform in ('tiktok', 'youtube', 'douyin', 'xiaohongshu'):
            for missing in (True, False):
                with self.subTest(platform=platform, missing=missing):
                    page = Page(platform, 'private')
                    if missing:
                        page.nodes['option-private'].visible = False
                    else:
                        page.privacy_refuses = True
                    with self.assertRaises(adapters.VerificationRequired):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility='private')
                    self.assertFalse(page.submitted)

    def test_privacy_changed_during_wait_rejected_at_final_checkpoint(self):
        page = Page('tiktok', 'private')
        def progress(percent, message):
            if percent == 90:
                page.nodes['privacy'].text = 'Everyone'
        with self.assertRaises(adapters.VerificationRequired):
            self.adapter('tiktok').upload(page, self.video, self.metadata, visibility='private', progress=progress)
        self.assertFalse(page.submitted)

    def test_private_visibility_changed_during_checkpoint_never_clicks(self):
        cases = [('tiktok', False, None), ('tiktok', True, None),
                 ('youtube', False, None), ('douyin', False, None),
                 ('douyin', False, 'radio'), ('douyin', False, 'checkbox'),
                 ('xiaohongshu', False, None)]
        for platform, iframe, native_kind in cases:
            with self.subTest(platform=platform, iframe=iframe, native_kind=native_kind):
                page = Page(platform, 'private', iframe=iframe)
                if native_kind:
                    label = page.nodes['option-private']
                    label.attrs.clear()
                    label.children[f'input[type="{native_kind}"]'] = [
                        Node(page, 'native-private', checked=lambda: page.mode_selected == 'private')]
                def checkpoint():
                    page.events.append(('checkpoint',))
                    if platform in ('tiktok', 'xiaohongshu'):
                        self.assertIn(page.nodes['privacy'].text, ('Only you', '仅自己可见'))
                        page.nodes['privacy'].text = 'Everyone' if platform == 'tiktok' else '公开可见'
                    elif platform == 'youtube':
                        self.assertEqual(page.youtube_visibility, 'private')
                        page.youtube_visibility = 'public'
                    else:
                        self.assertEqual(page.mode_selected, 'private')
                        page.mode_selected = 'public'
                with self.assertRaisesRegex(adapters.VerificationRequired, 'option changed'):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='private',
                                                  before_publish=checkpoint)
                self.assertEqual(page.submit_count, 0)
                self.assertEqual(page.events.count(('checkpoint',)), 1)
                self.assertEqual(page.events[-1], ('checkpoint',))

    def test_meta_mode_and_story_changed_during_checkpoint_never_click(self):
        for mode in ('public', 'draft'):
            for changed in ('mode', 'story'):
                with self.subTest(mode=mode, changed=changed):
                    page = Page('facebook', mode)
                    def checkpoint():
                        page.events.append(('checkpoint',))
                        if changed == 'mode':
                            page.meta_mode = 0 if mode == 'draft' else 2
                        else:
                            page.nodes['story'].checked = True
                    with self.assertRaisesRegex(adapters.VerificationRequired, 'option changed'):
                        self.adapter('facebook').upload(page, self.video, self.metadata, visibility=mode,
                                                        before_publish=checkpoint)
                    self.assertEqual(page.submit_count, 0)
                    self.assertEqual(page.events.count(('checkpoint',)), 1)
                    self.assertEqual(page.events[-1], ('checkpoint',))
                    self.assertEqual(page.listeners['response'], [])

    def test_barrier_or_schedule_changed_during_checkpoint_never_clicks(self):
        cases = [(platform, False) for platform in self.platforms] + [('tiktok', True)]
        for platform, iframe in cases:
            for changed in ('redirect', 'challenge', 'login', 'schedule', 'closed'):
                with self.subTest(platform=platform, iframe=iframe, changed=changed):
                    page = Page(platform, iframe=iframe)
                    def checkpoint():
                        page.events.append(('checkpoint',))
                        root = page.frame if iframe else None
                        if changed == 'redirect':
                            page.url = 'https://unexpected.example/upload'
                        elif changed == 'challenge':
                            page.add('iframe[src*="captcha"]', 'checkpoint-challenge', parent=root)
                        elif changed == 'login':
                            page.add('input[type="password"]', 'checkpoint-login', parent=root)
                        elif changed == 'schedule':
                            page.add('[role="switch"][aria-label="Schedule"][aria-checked="true"]',
                                     'checkpoint-schedule', parent=root)
                        else:
                            page.closed = True
                    with self.assertRaises(adapters.VerificationRequired):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility='public',
                                                      before_publish=checkpoint)
                    self.assertEqual(page.submit_count, 0)
                    self.assertEqual(page.events.count(('checkpoint',)), 1)
                    self.assertEqual(page.events[-1], ('checkpoint',))

    def test_success_signal_appearing_during_checkpoint_is_not_fresh_confirmation(self):
        for platform in self.platforms:
            for mode in adapters.PLATFORMS[platform]['modes']:
                with self.subTest(platform=platform, mode=mode):
                    page = Page(platform, mode)
                    def checkpoint():
                        page.events.append(('checkpoint',))
                        page.confirmed = True
                    with self.assertRaisesRegex(adapters.VerificationRequired, 'pre-existing success'):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility=mode,
                                                      before_publish=checkpoint)
                    self.assertEqual(page.submit_count, 0)
                    self.assertEqual(page.events.count(('checkpoint',)), 1)
                    self.assertEqual(page.events[-1], ('checkpoint',))

    def test_readiness_changed_during_checkpoint_never_clicks_or_waits(self):
        for platform in self.platforms:
            for changed in ('disabled', 'aria-disabled', 'aria-busy', 'disabled-class',
                            'progress', 'processing'):
                with self.subTest(platform=platform, changed=changed):
                    page = Page(platform)
                    waits_at_checkpoint = []
                    def checkpoint():
                        page.events.append(('checkpoint',))
                        waits_at_checkpoint.append(len(page.waits))
                        if changed == 'disabled':
                            page.nodes['submit'].enabled = False
                        elif changed in ('aria-disabled', 'aria-busy'):
                            page.nodes['submit'].attrs[changed] = 'true'
                        elif changed == 'disabled-class':
                            page.nodes['submit'].attrs['class'] = 'publish-button disabled'
                        elif changed == 'progress':
                            page.nodes['progress'].attrs['aria-valuenow'] = '43'
                        else:
                            page.add('[role="status"]:text-matches("^(Processing(?! (complete|completed|finished))|Uploading(?! (complete|completed))|正在处理|处理中|正在上传)([.。… 0-9%]|$)", "i")',
                                     'checkpoint-processing')
                    with self.assertRaisesRegex(adapters.VerificationRequired, 'readiness changed'):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility='public',
                                                      before_publish=checkpoint)
                    self.assertEqual(page.submit_count, 0)
                    self.assertEqual(page.events.count(('checkpoint',)), 1)
                    self.assertEqual(page.events[-1], ('checkpoint',))
                    self.assertEqual(waits_at_checkpoint, [len(page.waits)])

    def test_unknown_or_ambiguous_selectors_and_metadata_readback_fail_closed(self):
        for platform in self.platforms:
            for failure in ('missing', 'ambiguous', 'readback'):
                with self.subTest(platform=platform, failure=failure):
                    page = Page(platform)
                    node = page.nodes['body']
                    if failure == 'missing':
                        node.visible = False
                    elif failure == 'readback':
                        node.fail_fill = True
                    else:
                        selector = next(k for k, v in page.mapping.items() if v == [node])
                        page.mapping[selector] = [node, Node(page, 'duplicate')]
                    with self.assertRaises(adapters.VerificationRequired):
                        self.adapter(platform).upload(page, self.video, self.metadata, visibility='public')
                    self.assertFalse(page.submitted)

    def test_unsafe_paths_and_metadata_are_rejected_without_browser_action(self):
        bad_paths = [self.video.parent, self.video.parent / 'missing.mp4', 'https://evil.test/video.mp4',
                     r'\\server\share\video.mp4']
        empty = self.video.parent / 'empty.mp4'
        empty.touch()
        bad_paths.append(empty)
        for path in bad_paths:
            with self.subTest(path=path):
                page = Page('tiktok')
                with self.assertRaises(adapters.UploadError):
                    self.adapter('tiktok').upload(page, path, self.metadata)
                self.assertEqual(page.events, [])
        for metadata in (None, {}, {'title': 4}, {'title': 'a', 'hashtags': [1]},
                         {'title': 'a', 'description': '\x00'}):
            with self.subTest(metadata=metadata):
                with self.assertRaises(adapters.UploadError):
                    self.adapter('tiktok').upload(Page('tiktok'), self.video, metadata)

    def test_title_limits_not_silently_truncated(self):
        for platform, length in (('youtube', 101), ('douyin', 31), ('bilibili', 81), ('xiaohongshu', 21)):
            with self.subTest(platform=platform), self.assertRaises(adapters.UploadError):
                self.adapter(platform).upload(Page(platform), self.video, {'title': 'a' * length}, visibility='public')

    def test_untrusted_metadata_is_text_not_selector_or_html(self):
        payload = "'] >> xpath=//* <img src=x onerror=boom()>"
        metadata = {'title': 'safe title', 'description': payload, 'hashtags': [payload]}
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                result = self.adapter(platform).upload(page, self.video, metadata, visibility='public')
                self.assertEqual(result['status'], 'success')
                self.assertIn(payload, page.nodes['body'].value)
                self.assertFalse(any(payload in selector for selector in page.queries))

    def test_probe_has_positive_negative_and_unknown_states_without_upload(self):
        for platform in self.platforms:
            with self.subTest(platform=platform):
                page = Page(platform)
                self.assertIs(self.adapter(platform).probe(page)['logged_in'], True)
                self.assertFalse(page.file_selected)
                page = Page(platform)
                page.add('input[type="password"]', 'auth')
                self.assertIs(self.adapter(platform).probe(page)['logged_in'], False)
                page = Page(platform)
                page.add('iframe[src*="captcha"]', 'challenge')
                self.assertIsNone(self.adapter(platform).probe(page)['logged_in'])
                page = Page(platform)
                page.mapping.clear()
                self.assertIsNone(self.adapter(platform).probe(page)['logged_in'])

    def test_redirects_to_login_or_other_origin_never_receive_files(self):
        for redirect in ('https://accounts.google.com/signin', 'https://evil.test/upload',
                         'https://www.tiktok.com.evil.test/upload', 'http://www.tiktok.com/upload',
                         'https://www.tiktok.com:444/upload', 'https://user:secret@www.tiktok.com/upload'):
            with self.subTest(redirect=redirect):
                page = Page('tiktok')
                page.redirect = redirect
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter('tiktok').upload(page, self.video, self.metadata)
                self.assertFalse(page.file_selected)

    def test_closed_page_and_mid_upload_close_do_not_wait_or_submit(self):
        page = Page('tiktok')
        page.closed = True
        with self.assertRaises(adapters.VerificationRequired):
            self.adapter('tiktok').upload(page, self.video, self.metadata)
        self.assertEqual(page.events, [])
        page = Page('tiktok', pending=True)
        page.on_wait = lambda p: setattr(p, 'closed', True)
        with self.assertRaises(adapters.VerificationRequired):
            self.adapter('tiktok').upload(page, self.video, self.metadata, visibility='public')
        self.assertEqual(len(page.waits), 1)
        self.assertFalse(page.submitted)

    def test_youtube_wizard_heading_fallback_and_stalled_next_are_guarded(self):
        page = Page('youtube', 'private')
        page.nodes['wizard'].attrs.clear()
        page.add('ytcp-uploads-dialog #step-title', 'step-title', text=lambda: f'Wizard step {page.stage}')
        result = self.adapter('youtube').upload(page, self.video, self.metadata, visibility='private')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(page.stage, 3)
        page = Page('youtube')
        page.nodes['next'].on_click = lambda: None
        with self.assertRaises(adapters.VerificationRequired):
            self.adapter('youtube').upload(page, self.video, self.metadata, visibility='public')
        self.assertEqual([event for event in page.events if event == ('click', 'next')], [('click', 'next')])
        self.assertFalse(page.submitted)

    def test_douyin_label_reads_the_live_native_radio_state(self):
        page = Page('douyin', 'private')
        label = page.nodes['option-private']
        label.attrs.clear()
        native = Node(page, 'native-private', checked=lambda: page.mode_selected == 'private')
        label.children['input[type="radio"]'] = [native]
        result = self.adapter('douyin').upload(page, self.video, self.metadata, visibility='private')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(page.mode_selected, 'private')

    def test_youtube_never_guesses_audience_or_skips_wizard_steps(self):
        page = Page('youtube')
        page.nodes['not-made-for-kids'].attrs['aria-checked'] = 'false'
        with self.assertRaisesRegex(adapters.VerificationRequired, 'audience'):
            self.adapter('youtube').upload(page, self.video, self.metadata, visibility='private')
        self.assertFalse(page.submitted)
        self.assertEqual(page.stage, 0)

    def test_meta_guard_identity_modes_story_and_graphql_confirmation(self):
        for failure in ('identity', 'story', 'modes', 'response', 'unknown-items', 'no-response'):
            with self.subTest(failure=failure):
                page = Page('facebook', 'draft')
                if failure == 'identity':
                    page.redirect = 'https://business.facebook.com/latest/composer/'
                elif failure == 'story':
                    page.nodes['story'].checked = True
                elif failure == 'modes':
                    selector = next(k for k in page.mapping if '[aria-pressed]' in k)
                    page.mapping[selector] = page.mapping[selector][:2]
                elif failure == 'response':
                    page.response = Response({'errors': [{'message': 'secret error not exposed'}]})
                elif failure == 'unknown-items':
                    page.response = Response({'data': {'xfamily_content_create': {'items': []}}})
                else:
                    page.response.request = type('OtherRequest', (), {'post_data': 'fb_api_req_friendly_name=MediaUploadMutation'})()
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter('facebook').upload(page, self.video, self.metadata, visibility='draft')
                self.assertEqual(page.submit_count, 1 if failure in ('response', 'unknown-items', 'no-response') else 0)
                if 'response' in page.listeners:
                    self.assertEqual(page.listeners['response'], [])

    def test_bilibili_category_and_copyright_required_public_but_not_guessed(self):
        for missing in ('category', 'copyright'):
            page = Page('bilibili')
            page.nodes[missing].visible = False
            with self.assertRaises(adapters.VerificationRequired):
                self.adapter('bilibili').upload(page, self.video, self.metadata, visibility='public')
            self.assertFalse(page.submitted)

    def test_final_confirmation_requires_known_success_page_for_cn_platforms(self):
        for platform in ('douyin', 'bilibili', 'xiaohongshu'):
            with self.subTest(platform=platform):
                page = Page(platform)
                page.success_url = lambda: None
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter(platform).upload(page, self.video, self.metadata, visibility='public')
                self.assertEqual(page.submit_count, 1)

    def test_return_url_does_not_accept_offplatform_or_credential_links(self):
        for href in ('https://evil.test/watch?v=abcdefghijk', 'javascript:alert(1)',
                     'https://u:secret@youtu.be/abcdefghijk', 'https://youtu.be:444/abcdefghijk'):
            with self.subTest(href=href):
                page = Page('youtube')
                page.nodes['result-link'].attrs['href'] = href
                with self.assertRaises(adapters.VerificationRequired):
                    self.adapter('youtube').upload(page, self.video, self.metadata, visibility='private')
                self.assertEqual(page.submit_count, 1)

    def test_confirmation_reporting_failure_does_not_turn_publish_into_retry(self):
        def progress(percent, message):
            if percent == 100:
                raise OSError('nonessential progress reporting failed')
        page = Page('tiktok')
        result = self.adapter('tiktok').upload(page, self.video, self.metadata, visibility='public', progress=progress)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(page.submit_count, 1)

    def test_recorded_exact_upstream_revision_sources_and_license(self):
        self.assertEqual(len(adapters.UPSTREAM_REVISION), 40)
        self.assertEqual(set(adapters.UPSTREAM_SOURCES), set(self.platforms))
        for sources in adapters.UPSTREAM_SOURCES.values():
            for source in sources:
                self.assertIn('/blob/' + adapters.UPSTREAM_REVISION + '/', source)
        self.assertEqual(adapters.UPSTREAM_LICENSE['spdx'], 'MIT')
        self.assertIn('2023 dreammis', adapters.UPSTREAM_LICENSE['copyright'])
        self.assertTrue(any('runtime.py' in url for url in adapters.UPSTREAM_SOURCES['bilibili']))

    def test_no_runtime_launch_network_client_sleep_or_screenshot_implementation(self):
        tree = ast.parse(_MODULE_PATH.read_text(encoding='utf-8'))
        imports = {node.names[0].name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import)}
        imports.update(node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        self.assertLessEqual(imports, {'__future__', 'collections', 'json', 'pathlib', 're', 'time', 'urllib'})
        forbidden = {'sleep', 'screenshot', 'launch', 'new_context', 'new_page', 'close', 'evaluate', 'request', 'get'}
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse((forbidden - {'get'}) & calls)


if __name__ == '__main__':
    unittest.main()
