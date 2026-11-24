"""Synchronous, fail-closed browser upload adapters (no browser/runtime imports).

The caller supplies a synchronous Playwright/Patchright Page and owns its persistent
context, account selection, profile lease, screenshots, redaction and scheduling.
No live account or platform upload was used to verify this module. Selectors are
version-sensitive: an unrecognised form is a verification task, never success.

Protocol::

    adapter = adapter_for('tiktok')
    adapter.login_url
    adapter.probe(page)  # {'logged_in': True | False | None, 'reason': str}
    adapter.upload(page, video_path, {'title': ..., 'description': ...,
                   'hashtags': [...]}, visibility='draft', cancel=None,
                   progress=None, before_publish=None, upload_mode='auto',
                   confirm_submitted=None, on_awaiting_submit=None,
                   on_step=None, assisted_timeout_ms=None)

``cancel`` is a zero-argument predicate or threading.Event; ``progress`` receives
(percent, message). ``before_publish`` is a zero-argument durable parent checkpoint;
its exception aborts submission. It is invoked before the ONE final publish/save-draft
click (or before confirming a manual assisted submit), never before file selection or
ordinary wizard steps. Read-only safety checks are repeated after the checkpoint, since
it may block while the browser form changes. Callers MUST NOT automatically retry
VerificationRequired after that checkpoint.

Success/draft results require a fresh, distinctive platform confirmation (or explicit
user confirmation in ``assisted`` mode). Challenges, authentication, paywalls, unknown
privacy and unconfirmed submissions raise VerificationRequired. We never solve
challenges or change legal declarations.

Capabilities: TikTok public/private/draft; YouTube public/private (not its implicit
close-to-autosave draft); Facebook *Page/Meta Business Suite* public/draft, NOT
personal profiles or private; Douyin public/private/draft; Bilibili public/draft;
Xiaohongshu public/private (no reliably explicit web draft commit). The common draft
default intentionally raises UnsupportedMode on YouTube and Xiaohongshu. YouTube
requires a previously selected audience/channel default. Bilibili public submission
requires an already chosen category/copyright declaration. Facebook requires one
remembered numeric Page asset_id, Facebook-only destination and story sharing off.
The adapter never guesses those identity/compliance choices.

Research provenance (selector/flow reference, NOT a dependency or vendored runtime):
https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59
See UPSTREAM_SOURCES below for exact fetched files. Current upstream Bilibili uses
biliup CLI, not a browser uploader; our Bilibili DOM implementation is independent
and mock-verified only. Unlike upstream, no final-click retry loops, arbitrary base
URL overrides, forced clicks, verification automation, browser launch or scheduling
are retained here.

Upstream license: https://github.com/dreammis/social-auto-upload/blob/
3847fff01f5434a8a32829833163cd6723cabc59/LICENSE
MIT License -- Copyright (c) 2023 dreammis
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from time import monotonic
from urllib.parse import parse_qs, urljoin, urlsplit

__all__ = [
    'PLATFORMS', 'PLATFORM_TITLE_LIMITS', 'PLATFORM_TAG_LIMITS',
    'UPLOAD_STEPS', 'UPLOAD_MODES', 'ASSISTED_SUBMIT_TIMEOUT_MS',
    'adapter_for', 'validate_platform_metadata',
    'UploadError', 'VerificationRequired',
    'UploadCancelled', 'UnsupportedMode',
]

UPSTREAM_REVISION = '3847fff01f5434a8a32829833163cd6723cabc59'
_UPSTREAM = f'https://github.com/dreammis/social-auto-upload/blob/{UPSTREAM_REVISION}/'
UPSTREAM_SOURCES = {
    'tiktok': (_UPSTREAM + 'uploader/tk_uploader/main_chrome.py',),
    'youtube': (_UPSTREAM + 'uploader/youtube_uploader/main.py',),
    'facebook': (
        _UPSTREAM + 'uploader/meta_uploader/facebook_page_uploader/main.py',
        _UPSTREAM + 'uploader/meta_uploader/facebook_uploader/main.py',
    ),
    'douyin': (_UPSTREAM + 'uploader/douyin_uploader/main.py',),
    'bilibili': (
        _UPSTREAM + 'sau_commands/bilibili.py',
        _UPSTREAM + 'uploader/bilibili_uploader/runtime.py',
    ),
    'xiaohongshu': (_UPSTREAM + 'uploader/xiaohongshu_uploader/main.py',),
}
UPSTREAM_LICENSE = {'spdx': 'MIT', 'url': _UPSTREAM + 'LICENSE',
                    'copyright': 'Copyright (c) 2023 dreammis'}

UPLOAD_STEPS = (
    'open_studio',
    'check_session',
    'upload_file',
    'fill_metadata',
    'set_visibility',
    'submit',
    'confirm',
)
UPLOAD_MODES = ('auto', 'assisted')
ASSISTED_SUBMIT_TIMEOUT_MS = 600_000


class UploadError(RuntimeError):
    """Invalid request or a failed/bounded-out pre-submission upload."""


class VerificationRequired(UploadError):
    """Human action or reconciliation is required; do not blindly retry."""


class UploadCancelled(UploadError):
    """The parent cancelled this operation."""


class UnsupportedMode(UploadError):
    """The requested platform/mode has no implemented safe submission path."""


# Private immutable-by-convention records drive navigation, not mutable UI metadata
# and never external selector overrides.
_CONFIG = {
    'tiktok': ('TikTok', 'https://www.tiktok.com/login?lang=en',
               'https://www.tiktok.com/tiktokstudio/upload?lang=en',
               ('public', 'private', 'draft'), ('www.tiktok.com',)),
    'youtube': ('YouTube', 'https://studio.youtube.com/',
                'https://www.youtube.com/upload', ('public', 'private'),
                ('www.youtube.com', 'studio.youtube.com')),
    'facebook': ('Facebook Page (Meta Business Suite)', 'https://www.facebook.com/login/',
                 'https://business.facebook.com/latest/composer/', ('public', 'draft'),
                 ('business.facebook.com',)),
    'douyin': ('Douyin', 'https://creator.douyin.com/',
               'https://creator.douyin.com/creator-micro/content/upload',
               ('public', 'private', 'draft'), ('creator.douyin.com',)),
    'bilibili': ('Bilibili', 'https://passport.bilibili.com/login',
                 'https://member.bilibili.com/platform/upload/video/frame',
                 ('public', 'draft'), ('member.bilibili.com',)),
    'xiaohongshu': ('Xiaohongshu', 'https://creator.xiaohongshu.com/login',
                    'https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video',
                    ('public', 'private'), ('creator.xiaohongshu.com',)),
}
PLATFORMS = {key: {'label': value[0], 'login_url': value[1],
                   'upload_url': value[2], 'modes': list(value[3])}
             for key, value in _CONFIG.items()}
PLATFORM_TITLE_LIMITS = {
    'tiktok': 2200,
    'youtube': 100,
    'facebook': 255,
    'douyin': 30,
    'bilibili': 80,
    'xiaohongshu': 20,
}
PLATFORM_TAG_LIMITS = {
    'bilibili': 10,
    'xiaohongshu': 10,
}

_SELECTORS_PATH = Path(__file__).resolve().parent / 'selectors.json'
_DEFAULT_SELECTORS = json.loads(_SELECTORS_PATH.read_text(encoding='utf-8'))


def validate_platform_metadata(platform, metadata, visibility='draft'):
    """Validate and normalize metadata against platform limits."""
    if not isinstance(metadata, Mapping):
        raise UploadError('metadata must be a mapping.')
    title, description = metadata.get('title', ''), metadata.get('description', '')
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str):
        raise UploadError('A nonempty text title and a text description are required.')
    tags = metadata.get('hashtags', [])
    if isinstance(tags, str):
        tags = re.split(r'[\s,]+', tags.strip()) if tags.strip() else []
    if not isinstance(tags, (list, tuple)) or any(not isinstance(tag, str) for tag in tags):
        raise UploadError('hashtags must be text or a list of text values.')
    tags = [tag.strip().lstrip('#') for tag in tags if tag.strip().lstrip('#')]
    if any('\x00' in value for value in [title, description] + tags):
        raise UploadError('Metadata cannot contain NUL characters.')
    limit = PLATFORM_TITLE_LIMITS.get(platform, 2200)
    if len(title.strip()) > limit or len(title) > limit:
        raise UploadError(f'{platform}: tiêu đề vượt quá giới hạn {limit} ký tự (Title exceeds the implemented {limit}-character platform limit).')
    tag_limit = PLATFORM_TAG_LIMITS.get(platform)
    if tag_limit is not None and len(tags) > tag_limit:
        raise UploadError(f'{platform}: chỉ hỗ trợ tối đa {tag_limit} hashtag.')
    if platform == 'bilibili' and visibility == 'public' and not tags:
        raise UploadError('Bilibili yêu cầu ít nhất 1 hashtag khi đăng công khai.')
    body = '\n\n'.join(value for value in (description, ' '.join('#' + tag for tag in tags)) if value)
    caption = '\n\n'.join(value for value in (title, body) if value)
    return title, body, caption, tags


def _find(root, selectors, *, attached=False):
    """Select a single visible (or attached file) target, never a blind .first."""
    for selector in selectors:
        group = root.locator(selector)
        count = group.count()
        if count > 30:
            raise VerificationRequired('Ambiguous browser form; manual verification required.')
        matches = [group.nth(index) for index in range(count)
                   if attached or group.nth(index).is_visible()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise VerificationRequired('Ambiguous browser control; no submission was attempted.')
    return None


def _checked(control):
    for attr in ('aria-checked', 'aria-selected', 'aria-pressed'):
        value = control.get_attribute(attr, timeout=1_000)
        if value is not None:
            return value == 'true'
    native = _find(control, ('input[type="radio"]', 'input[type="checkbox"]'), attached=True)
    if native is not None:
        return native.is_checked(timeout=1_000)
    try:
        return control.is_checked(timeout=1_000)
    except Exception:
        # Custom Polymer radio elements expose a checked attribute but are not
        # native inputs. Native inputs above must use their live checked property.
        return control.get_attribute('checked', timeout=1_000) is not None


def _enabled(control):
    return (control.is_enabled(timeout=1_000)
            and control.get_attribute('aria-disabled', timeout=1_000) != 'true'
            and control.get_attribute('aria-busy', timeout=1_000) != 'true'
            and 'disabled' not in (control.get_attribute('class', timeout=1_000) or '').lower())


def _is_signalled(flag):
    if flag is None:
        return False
    if callable(flag):
        return bool(flag())
    if hasattr(flag, 'is_set'):
        return bool(flag.is_set())
    raise UploadError('signal flag must be a predicate or an Event.')


class _Run:
    """Per-call state: adapters do not retain a Page, callbacks or submission state."""

    def __init__(self, adapter, page, cancel=None, progress=None, before_publish=None, *,
                 upload_mode='auto', confirm_submitted=None, on_awaiting_submit=None,
                 on_step=None, assisted_timeout_ms=None):
        self.adapter, self.page = adapter, page
        self.cancel, self.progress, self.before_publish = cancel, progress, before_publish
        self.upload_mode = upload_mode
        self.confirm_submitted = confirm_submitted
        self.on_awaiting_submit = on_awaiting_submit
        self.on_step = on_step
        self.assisted_timeout_ms = assisted_timeout_ms
        self.root = page
        self.submitted = False
        self.awaiting_user_submit = False
        self.invariants = []

    def cancelled(self):
        if self.cancel is None:
            return False
        if callable(self.cancel):
            return bool(self.cancel())
        if hasattr(self.cancel, 'is_set'):
            return bool(self.cancel.is_set())
        raise UploadError('cancel must be a predicate or an Event.')

    def user_confirmed(self):
        return _is_signalled(self.confirm_submitted)

    def active(self):
        if self.cancelled():
            raise UploadCancelled('Upload cancelled; reconcile the platform if already submitted.'
                                  if self.submitted else 'Upload cancelled before submission.')
        if self.page.is_closed():
            err = VerificationRequired('Browser closed; submission state requires reconciliation.'
                                       if (self.submitted or self.awaiting_user_submit) else 'Browser page is closed.')
            if self.submitted or self.awaiting_user_submit:
                err.uncertain = True
            raise err

    def on_allowed_host(self):
        location = urlsplit(str(self.page.url))
        return (location.scheme == 'https' and location.hostname in self.adapter.hosts
                and not location.username and not location.password and location.port in (None, 443))

    def guard(self):
        self.active()
        if self.adapter.platform == 'tiktok' and self.submitted and self.root is not self.page:
            iframe_selectors = self.adapter._platform_sel['iframe']
            if self.page.locator(iframe_selectors[0]).count() == 0:
                self.root = self.page  # server-confirmation view replaced the upload iframe
        reason, state = self.adapter._barrier(self.page, self.root)
        if reason:
            raise VerificationRequired(reason)
        if not self.on_allowed_host():
            raise VerificationRequired('Unexpected platform redirect; no further browser actions allowed.')
        error_selectors = self.adapter._common['error']
        for root in (self.page, self.root) if self.root is not self.page else (self.page,):
            if _find(root, error_selectors) is not None:
                if self.submitted:
                    raise VerificationRequired('Platform reported a submission error; reconcile before retrying.')
                raise UploadError('Platform reported an upload/processing error.')

    def emit(self, percent, message, *, step=None):
        self.active()
        if self.on_step is not None and step is not None:
            self.on_step(step)
        if self.progress is not None:
            self.progress(percent, message)

    def wait(self, predicate, message, timeout_ms=None, *, processing=False):
        timeout_ms = self.adapter.form_timeout_ms if timeout_ms is None else timeout_ms
        deadline = monotonic() + timeout_ms / 1000
        # A finite poll budget also bounds test doubles/broken browser clocks.
        for _ in range(max(1, (timeout_ms + 249) // 250)):
            self.guard()
            result = predicate()
            if result:
                return result
            remaining = (deadline - monotonic()) * 1000
            if remaining <= 0:
                break
            self.active()
            self.page.wait_for_timeout(min(250, max(1, int(remaining))))
        self.guard()
        if self.submitted:
            raise VerificationRequired('Submission outcome unconfirmed; do not automatically retry. ' + message)
        if processing:
            raise UploadError('Upload/processing timed out before submission. ' + message)
        raise VerificationRequired('Unsupported or incomplete platform form. ' + message)

    def require(self, selectors, message, *, attached=False, root=None, timeout_ms=None):
        return self.wait(lambda: _find(self.root if root is None else root, selectors,
                                      attached=attached), message, timeout_ms)

    def click(self, control):
        self.guard()
        control.click(timeout=self.adapter.action_timeout_ms)

    def fill(self, selectors, text, message, *, editable=True, root=None):
        control = self.require(selectors, message, root=root)
        self.guard()
        control.fill(text, timeout=self.adapter.action_timeout_ms)
        value = control.inner_text(timeout=self.adapter.action_timeout_ms) if editable else control.input_value(timeout=self.adapter.action_timeout_ms)
        if value.replace('\r\n', '\n') != text.replace('\r\n', '\n'):
            raise VerificationRequired('Platform did not retain the requested metadata; not submitted.')
        return control

    def choose(self, selectors, message):
        control = self.require(selectors, message)
        if not _checked(control):
            self.click(control)
        self.wait(lambda: _checked(control), 'Requested option was not selected.')
        self.invariants.append(lambda: _checked(control))
        return control

    def _emit_confirmed(self, result):
        # Do not let nonessential post-confirmation reporting turn a confirmed publish
        # into a retryable failure in the parent executor.
        if self.on_step is not None:
            try:
                self.on_step('confirm')
            except Exception:
                pass
        if self.progress is not None:
            try:
                self.progress(100, result['message'])
            except Exception:
                pass

    def _finish_assisted(self, visibility, response_state):
        timeout_ms = (self.assisted_timeout_ms if self.assisted_timeout_ms is not None
                      else self.adapter.assisted_submit_timeout_ms)
        self.awaiting_user_submit = True
        self.emit(88, 'Form filled; waiting for manual submit in browser.', step='submit')
        if self.on_awaiting_submit is not None:
            self.on_awaiting_submit(timeout_ms / 1000.0)
        deadline = monotonic() + timeout_ms / 1000.0
        for _ in range(max(1, (timeout_ms + 249) // 250)):
            if self.cancelled():
                err = UploadCancelled('Upload cancelled while waiting for manual submit; check the platform before retrying.')
                err.uncertain = True
                raise err
            if self.page.is_closed():
                err = VerificationRequired('Browser closed while awaiting manual submit; reconcile on the platform.')
                err.uncertain = True
                raise err
            if self.user_confirmed():
                if self.before_publish is not None:
                    self.before_publish()
                self.submitted = True
                result_url = ''
                if self.on_allowed_host():
                    try:
                        if self.adapter.platform == 'tiktok' and self.root is not self.page:
                            iframe_selectors = self.adapter._platform_sel['iframe']
                            if self.page.locator(iframe_selectors[0]).count() == 0:
                                self.root = self.page
                        confirmation = self.adapter._confirmation(self, visibility)
                        if confirmation is not None:
                            result_url = self.adapter._result_url(confirmation)
                    except Exception:
                        result_url = ''
                result = {
                    'status': 'draft' if visibility == 'draft' else 'success',
                    'url': result_url,
                    'message': ('Draft save confirmed manually.' if visibility == 'draft'
                                else f'{visibility.capitalize()} publication confirmed manually.'),
                }
                self._emit_confirmed(result)
                return result
            confirmation = None
            if self.on_allowed_host():
                try:
                    if self.adapter.platform == 'tiktok' and self.root is not self.page:
                        iframe_selectors = self.adapter._platform_sel['iframe']
                        if self.page.locator(iframe_selectors[0]).count() == 0:
                            self.root = self.page
                    confirmation = self.adapter._confirmed(self, visibility, response_state)
                except VerificationRequired:
                    raise
                except Exception:
                    confirmation = None
            if confirmation:
                if self.before_publish is not None:
                    self.before_publish()
                self.submitted = True
                result = {
                    'status': 'draft' if visibility == 'draft' else 'success',
                    'url': self.adapter._result_url(confirmation),
                    'message': ('Draft save confirmed.' if visibility == 'draft'
                                else f'{visibility.capitalize()} publication confirmed.'),
                }
                self._emit_confirmed(result)
                return result
            remaining = (deadline - monotonic()) * 1000
            if remaining <= 0:
                break
            try:
                self.page.wait_for_timeout(min(250, max(1, int(remaining))))
            except Exception:
                if self.page.is_closed():
                    err = VerificationRequired('Browser closed while awaiting manual submit; reconcile on the platform.')
                    err.uncertain = True
                    raise err from None
                raise
        err = VerificationRequired('Assisted upload timed out awaiting manual submit; check the platform before retrying.')
        err.uncertain = True
        raise err

    def finish(self, control, visibility, *, response_state=None):
        processing_selectors = self.adapter._common['processing']
        schedule_selectors = self.adapter._common['schedule_active']
        self.wait(lambda: _enabled(control) and self.adapter._progress_complete(self.root)
                  and _find(self.root, processing_selectors) is None,
                  'Final action is not ready or media is still processing.',
                  self.adapter.processing_timeout_ms, processing=True)
        if self.adapter._confirmation(self, visibility, baseline=True) is not None:
            raise VerificationRequired('A pre-existing success signal makes this submission ambiguous; not submitted.')
        if self.upload_mode == 'assisted':
            self.guard()
            if _find(self.root, schedule_selectors) is not None:
                raise VerificationRequired('Disable platform scheduling manually; the parent owns local scheduling.')
            if not all(check() for check in self.invariants):
                raise VerificationRequired('Requested privacy/publication option changed before submission; not submitted.')
            return self._finish_assisted(visibility, response_state)
        self.emit(90, 'Ready for final submission checkpoint.', step='submit')
        self.guard()
        if _find(self.root, schedule_selectors) is not None:
            raise VerificationRequired('Disable platform scheduling manually; the parent owns local scheduling.')
        if not all(check() for check in self.invariants):
            raise VerificationRequired('Requested privacy/publication option changed before submission; not submitted.')
        # The durable checkpoint may block while the live browser form changes.
        # It is called once, can veto by raising or cancelling, and is followed only
        # by read-only checks: never restore options, wait for readiness or retry.
        if self.before_publish is not None:
            self.before_publish()
        self.active()  # honour a cancellation requested by the checkpoint itself
        self.guard()
        if not all(check() for check in self.invariants):
            raise VerificationRequired('Requested privacy/publication option changed before submission; not submitted.')
        if _find(self.root, schedule_selectors) is not None:
            raise VerificationRequired('Disable platform scheduling manually; the parent owns local scheduling.')
        if self.adapter._confirmation(self, visibility, baseline=True) is not None:
            raise VerificationRequired('A pre-existing success signal makes this submission ambiguous; not submitted.')
        if (not self.adapter._progress_complete(self.root)
                or _find(self.root, processing_selectors) is not None or not _enabled(control)):
            raise VerificationRequired('Final action readiness changed after the checkpoint; not submitted.')
        self.submitted = True
        try:
            control.click(timeout=self.adapter.action_timeout_ms)
        except Exception:
            raise VerificationRequired('Final click outcome unknown; do not automatically retry.') from None
        if self.on_step is not None:
            try:
                self.on_step('confirm')
            except Exception:
                pass
        confirmation = self.wait(
            lambda: self.adapter._confirmed(self, visibility, response_state),
            'No distinctive completion signal was observed.', self.adapter.confirmation_timeout_ms)
        result = {'status': 'draft' if visibility == 'draft' else 'success',
                  'url': self.adapter._result_url(confirmation),
                  'message': ('Draft save confirmed.' if visibility == 'draft'
                              else f'{visibility.capitalize()} publication confirmed.')}
        self._emit_confirmed(result)
        return result


class BrowserUploadAdapter:
    """Adapter over a caller-owned synchronous browser Page."""

    form_timeout_ms = 30_000
    processing_timeout_ms = 300_000
    confirmation_timeout_ms = 90_000
    action_timeout_ms = 5_000
    probe_timeout_ms = 8_000
    assisted_submit_timeout_ms = ASSISTED_SUBMIT_TIMEOUT_MS

    def __init__(self, platform, selectors=None):
        self.platform = platform
        self.label, self.login_url, self.upload_url, self.modes, self.hosts = _CONFIG[platform]
        source = (selectors if isinstance(selectors, Mapping)
                  and isinstance(selectors.get('common'), Mapping)
                  and isinstance(selectors.get('platforms'), Mapping)
                  and platform in selectors['platforms']
                  else _DEFAULT_SELECTORS)
        self._common = source['common']
        self._platform_sel = source['platforms'][platform]

    def _barrier(self, page, root=None):
        location = urlsplit(str(page.url))
        host, path = location.hostname or '', location.path.lower()
        if ('captcha' in path or 'checkpoint' in path or 'two_step_verification' in path
                or '/challenge' in path or '/authentication/' in path):
            return 'Platform security verification requires manual action.', None
        if (host == 'accounts.google.com' or host == 'passport.bilibili.com'
                or '/login' in path or '/signin' in path):
            return 'Platform login is required; use the parent manual-login flow.', False
        challenge_selectors = self._common['challenge']
        auth_selectors = self._common['auth']
        for scope in (page, root) if root is not None and root is not page else (page,):
            if _find(scope, challenge_selectors) is not None:
                return 'Captcha, verification or paywall requires manual action.', None
            if _find(scope, auth_selectors) is not None:
                return 'Platform login is required; use the parent manual-login flow.', False
        return '', None

    def _navigate(self, run):
        run.active()
        try:
            run.page.goto(self.upload_url, wait_until='domcontentloaded', timeout=30_000)
        except Exception:
            run.active()
            raise UploadError('Platform navigation failed; no submission was attempted.') from None
        run.guard()
        if self.platform == 'tiktok':
            iframe_selectors = self._platform_sel['iframe']
            frame = _find(run.page, iframe_selectors)
            if frame is not None:
                run.root = run.page.frame_locator(iframe_selectors[0])
                run.guard()

    def probe(self, page):
        """Navigate to the known uploader and require positive signed-in evidence."""
        run = _Run(self, page)
        try:
            run.active()
            page.goto(self.upload_url, wait_until='domcontentloaded', timeout=30_000)
            for _ in range(max(1, (self.probe_timeout_ms + 249) // 250)):
                run.active()
                reason, state = self._barrier(page)
                if reason:
                    return {'logged_in': state, 'reason': reason}
                run.guard()
                root = page
                if self.platform == 'tiktok':
                    iframe_selectors = self._platform_sel['iframe']
                    if _find(page, iframe_selectors) is not None:
                        root = page.frame_locator(iframe_selectors[0])
                        reason, state = self._barrier(page, root)
                        if reason:
                            return {'logged_in': state, 'reason': reason}
                positive = (self._platform_sel['session_anchor'] if self.platform == 'facebook'
                            else self._platform_sel['file'])
                if _find(root, positive, attached=self.platform != 'facebook') is not None:
                    return {'logged_in': True, 'reason': 'Authenticated uploader control observed.'}
                page.wait_for_timeout(250)
        except Exception:
            return {'logged_in': None, 'reason': 'Session could not be verified from the known uploader.'}
        return {'logged_in': None, 'reason': 'No positive signed-in evidence; manual verification required.'}

    def _metadata(self, metadata, visibility='draft'):
        return validate_platform_metadata(self.platform, metadata, visibility=visibility)

    def upload(self, page, video_path, metadata, *, visibility='draft', cancel=None,
               progress=None, before_publish=None, upload_mode='auto',
               confirm_submitted=None, on_awaiting_submit=None, on_step=None,
               assisted_timeout_ms=None):
        """Upload and commit once, returning only after a distinctive confirmation."""
        if visibility not in self.modes:
            raise UnsupportedMode(f'{self.label} does not implement the requested visibility mode.')
        if upload_mode not in UPLOAD_MODES:
            raise UploadError('upload_mode must be auto or assisted.')
        if before_publish is not None and not callable(before_publish):
            raise UploadError('before_publish must be callable.')
        if progress is not None and not callable(progress):
            raise UploadError('progress must be callable.')
        if on_awaiting_submit is not None and not callable(on_awaiting_submit):
            raise UploadError('on_awaiting_submit must be callable.')
        if on_step is not None and not callable(on_step):
            raise UploadError('on_step must be callable.')
        if confirm_submitted is not None and not (callable(confirm_submitted) or hasattr(confirm_submitted, 'is_set')):
            raise UploadError('confirm_submitted must be a predicate or an Event.')
        title, body, caption, tags = self._metadata(metadata, visibility=visibility)
        try:
            if str(video_path).startswith(('\\\\', '//')):
                raise ValueError('not a local path')
            path = Path(video_path).resolve(strict=True)
            if (not path.is_file() or path.stat().st_size == 0 or path.suffix.lower() not in
                    {'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v', '.ts', '.m2ts', '.wmv', '.flv'}):
                raise ValueError('not a video file')
        except (OSError, ValueError, TypeError):
            raise UploadError('A trusted existing nonempty local video file is required.') from None
        run = _Run(self, page, cancel, progress, before_publish,
                   upload_mode=upload_mode, confirm_submitted=confirm_submitted,
                   on_awaiting_submit=on_awaiting_submit, on_step=on_step,
                   assisted_timeout_ms=assisted_timeout_ms)
        try:
            self._navigate(run)
            run.emit(5, 'Known platform uploader opened.', step='open_studio')
            if self.platform == 'facebook':
                self._facebook_identity(run)
            run.emit(12, 'Platform session verified.', step='check_session')
            if self.platform == 'facebook':
                self._facebook_file(run, path)
            else:
                file_input = run.require(self._platform_sel['file'], 'Video file input not recognized.', attached=True)
                run.guard()
                file_input.set_input_files(str(path), timeout=self.action_timeout_ms)
            run.emit(25, 'Local video selected; waiting for platform form.', step='upload_file')
            if self.platform == 'facebook':
                return self._facebook(run, title, body, visibility)
            if self.platform == 'youtube':
                return self._youtube(run, title, body, visibility)
            if self.platform == 'tiktok':
                run.fill(self._platform_sel['body'], caption, 'Caption editor not recognized.')
            else:
                run.fill(self._platform_sel['title'], title, 'Title editor not recognized.', editable=False)
                if self.platform == 'bilibili':
                    self._bilibili_body(run, body)
                else:
                    run.fill(self._platform_sel['body'], body, 'Description editor not recognized.')
            run.emit(50, 'Metadata filled.', step='fill_metadata')
            if self.platform == 'bilibili':
                self._bilibili_options(run, tags, visibility)
            elif visibility != 'draft':
                self._privacy(run, visibility)
            run.emit(65, 'Metadata and requested visibility read back.', step='set_visibility')
            final = run.require(self._platform_sel['actions'][visibility], 'Final action not recognized.')
            self._media_ready(run, final)
            return run.finish(final, visibility)
        except UploadError:
            raise
        except Exception:
            if run.submitted or run.awaiting_user_submit:
                err = VerificationRequired('Submission outcome unknown; do not automatically retry.')
                err.uncertain = True
                raise err from None
            raise VerificationRequired('Browser form could not be completed safely; manual verification required.') from None

    def _media_ready(self, run, final):
        signals = self._platform_sel['media_ready']
        # A preview alone is not enough; all visible progressbars must be complete.
        def ready():
            return (_find(run.root, signals) is not None and self._progress_complete(run.root)
                    and _enabled(final))
        run.wait(ready, 'Media has no completed-upload evidence.', self.processing_timeout_ms, processing=True)

    @staticmethod
    def _progress_complete(root):
        bars = root.locator('[role="progressbar"]')
        for index in range(bars.count()):
            bar = bars.nth(index)
            if not bar.is_visible():
                continue
            try:
                value = float(bar.get_attribute('aria-valuenow', timeout=1_000))
                maximum = float(bar.get_attribute('aria-valuemax', timeout=1_000) or '100')
            except (TypeError, ValueError):
                return False
            if maximum <= 0 or value < maximum:
                return False
        return True

    def _privacy(self, run, visibility):
        if self.platform == 'tiktok':
            accepted = set(self._platform_sel['privacy_labels'][visibility])
            triggers = self._platform_sel['privacy_triggers']
            trigger = run.require(triggers, 'TikTok privacy control not recognized.')
            run.click(trigger)
            options = self._platform_sel['privacy_options'][visibility]
            option = run.require(options, 'Requested TikTok privacy option unavailable.')
            run.click(option)
            run.wait(lambda: trigger.inner_text(timeout=self.action_timeout_ms).strip() in accepted,
                     'TikTok privacy was not read back; public fallback is forbidden.')
            run.invariants.append(lambda: trigger.inner_text(timeout=self.action_timeout_ms).strip() in accepted)
        elif self.platform == 'douyin':
            selectors = self._platform_sel['privacy_options'][visibility]
            run.choose(selectors, 'Explicit requested Douyin visibility not available.')
        elif self.platform == 'xiaohongshu':
            trigger = run.require(self._platform_sel['privacy_triggers'],
                                  'Xiaohongshu visibility control not recognized.')
            run.click(trigger)
            names = self._platform_sel['privacy_options'][visibility]
            option = run.require(names, 'Requested Xiaohongshu visibility unavailable.')
            run.click(option)
            accepted = set(self._platform_sel['privacy_labels'][visibility])
            run.wait(lambda: trigger.inner_text(timeout=self.action_timeout_ms).strip() in accepted,
                     'Xiaohongshu privacy was not read back; public fallback is forbidden.')
            run.invariants.append(lambda: trigger.inner_text(timeout=self.action_timeout_ms).strip() in accepted)

    def _youtube(self, run, title, body, visibility):
        run.fill(self._platform_sel['title'], title, 'YouTube title editor not recognized.')
        run.fill(self._platform_sel['body'], body, 'YouTube description editor not recognized.')
        run.emit(50, 'YouTube metadata filled.', step='fill_metadata')
        run.require(self._platform_sel['audience_controls'],
                    'YouTube audience controls not recognized.')
        audience_controls = run.root.locator(self._platform_sel['audience_group'])
        if not any(_checked(audience_controls.nth(i)) for i in range(audience_controls.count())):
            raise VerificationRequired('Select the correct YouTube audience manually or set a channel default; not guessed.')
        visibility_selectors = (f'tp-yt-paper-radio-button[name="{visibility.upper()}"]',)
        for _ in range(4):
            if _find(run.root, visibility_selectors) is not None:
                break
            next_button = run.require(self._platform_sel['wizard_next'], 'YouTube Next step not recognized.')
            run.wait(lambda: _enabled(next_button), 'YouTube Next is blocked.',
                     self.processing_timeout_ms, processing=True)
            # Observe the real wizard state rather than blindly clicking Next
            # several times or sleeping for guessed animation durations.
            dialog = run.require(self._platform_sel['wizard_dialog'], 'YouTube upload wizard not recognized.')

            def wizard_state():
                step = dialog.get_attribute('current-step', timeout=self.action_timeout_ms)
                if step is not None:
                    return ('step', step)
                heading = _find(run.root, self._platform_sel['wizard_step_headings'])
                if heading is not None:
                    return ('title', heading.inner_text(timeout=self.action_timeout_ms).strip())
                badge = _find(run.root, tuple(
                    f'#step-badge-{index}[{attribute}="{value}"]'
                    for index in range(4)
                    for attribute, value in (('aria-current', 'step'), ('aria-selected', 'true'))))
                if badge is not None:
                    return ('badge', badge.get_attribute('id', timeout=self.action_timeout_ms))
                return None

            old_step = run.wait(wizard_state, 'YouTube current wizard step not recognized.')
            run.click(next_button)
            run.wait(lambda: _find(run.root, visibility_selectors) is not None
                     or (wizard_state() is not None and wizard_state() != old_step),
                     'YouTube wizard did not advance after Next.')
        run.choose(visibility_selectors, 'Explicit YouTube visibility step unavailable.')
        final = run.require(self._platform_sel['actions'][visibility], 'YouTube Save/Publish action not recognized.')
        progress = self._platform_sel['upload_ready']
        run.wait(lambda: _find(run.root, progress) is not None
                 and self._progress_complete(run.root) and _enabled(final),
                 'YouTube upload/checks have not completed.', self.processing_timeout_ms, processing=True)
        run.emit(75, 'YouTube visibility explicitly selected; upload completed.', step='set_visibility')
        return run.finish(final, visibility)

    def _bilibili_body(self, run, body):
        body_selectors = self._platform_sel['body']
        control = run.require(body_selectors, 'Bilibili description editor not recognized.')
        editable = control.get_attribute('contenteditable', timeout=self.action_timeout_ms) == 'true'
        run.fill(body_selectors, body, 'Bilibili description editor not recognized.', editable=editable)

    def _bilibili_options(self, run, tags, visibility):
        if tags:
            tag_input = run.require(self._platform_sel['tag_input'], 'Bilibili tags control not recognized.')
            for tag in tags:
                run.guard()
                tag_input.fill(tag, timeout=self.action_timeout_ms)
                tag_input.press('Enter', timeout=self.action_timeout_ms)
            tokens = run.require(self._platform_sel['tag_container'], 'Bilibili tag readback unavailable.')
            if not all(tag in tokens.inner_text(timeout=self.action_timeout_ms) for tag in tags):
                raise VerificationRequired('Bilibili tags were not retained; not submitted.')
        if visibility == 'public':
            category = run.require(self._platform_sel['category_selected'],
                                   'Choose a Bilibili category manually; the adapter will not guess.')
            if not category.inner_text(timeout=self.action_timeout_ms).strip():
                raise VerificationRequired('Bilibili category has no readable selection.')
            run.require(self._platform_sel['copyright_checked'],
                        'Choose the correct Bilibili copyright declaration manually.')
            if not tags:
                raise UploadError('Bilibili public submission requires at least one metadata hashtag.')

    def _facebook_identity(self, run):
        combo = run.require(self._platform_sel['identity_combo'],
                            'Meta Facebook Page destination not recognized.')
        if not combo.inner_text(timeout=self.action_timeout_ms).replace('\u200b', '').strip():
            raise VerificationRequired('Meta Page identity is empty; not uploaded.')
        assets = parse_qs(urlsplit(str(run.page.url)).query).get('asset_id', [])
        if len(assets) != 1 or not assets[0].isascii() or not assets[0].isdigit():
            raise VerificationRequired('Meta requires one remembered numeric Page asset_id; not uploaded.')
        if _find(run.root, self._platform_sel['disallowed_destinations']) is not None:
            raise VerificationRequired('Disable other Meta publishing destinations manually; Facebook only.')

    def _facebook_file(self, run, path):
        file_input = _find(run.root, self._platform_sel['file'], attached=True)
        if file_input is None:
            # Meta lazily mounts its hidden input only when this scoped local-media
            # action opens a chooser. Use the chooser's real file element, not a
            # general Add/Create button and not OS keyboard automation.
            media = run.require(self._platform_sel['media_surface'],
                                'Meta media surface not recognized.')
            if media.locator('img').count() != 0:
                raise VerificationRequired('Meta composer already contains media; not overwritten.')
            buttons = media.locator('[role="button"]')
            if buttons.count() not in (1, 2):
                raise VerificationRequired('Meta local media action is ambiguous; not uploaded.')
            run.guard()
            with run.page.expect_file_chooser(timeout=self.action_timeout_ms) as chooser:
                buttons.nth(0).click(timeout=self.action_timeout_ms)
            file_input = chooser.value.element
        run.guard()
        file_input.set_input_files(str(path), timeout=self.action_timeout_ms)

    def _facebook(self, run, title, body, visibility):
        reel = run.require(self._platform_sel['reel_wizard'], 'Meta Reel wizard not recognized.')
        run.wait(lambda: _find(run.root, self._platform_sel['upload_ready']) is not None
                 and self._progress_complete(run.root), 'Meta media upload has not completed.',
                 self.processing_timeout_ms, processing=True)
        run.fill(self._platform_sel['title'], title, 'Meta Reel title input not recognized.', editable=False)
        run.fill(self._platform_sel['body'], body, 'Meta Reel caption editor not recognized.')
        run.emit(50, 'Meta Reel metadata filled.', step='fill_metadata')
        steps = reel.locator(self._platform_sel['wizard_steps'])
        if steps.count() != 3:
            raise VerificationRequired('Meta Reel wizard step structure changed; not submitted.')
        footer_selector = self._platform_sel['footer_actions']
        for index in (0, 1):
            run.wait(lambda: steps.nth(index).get_attribute('aria-current', timeout=self.action_timeout_ms) == 'step',
                     'Meta Reel current step not recognized.')
            actions = run.root.locator(footer_selector)
            if actions.count() == 2:
                action = actions.nth(1)
            else:
                action = _find(run.root, self._platform_sel['footer_next_fallback'])
                if action is None:
                    raise VerificationRequired('Meta Reel footer structure changed; not submitted.')
            run.wait(lambda: _enabled(action), 'Meta Reel Next step is not ready.',
                     self.processing_timeout_ms, processing=True)
            run.click(action)
            run.wait(lambda: steps.nth(index + 1).get_attribute('aria-current', timeout=self.action_timeout_ms) == 'step',
                     'Meta Reel Next step did not advance.')
        story = run.require(self._platform_sel['story_switch'],
                            'Meta story-sharing option not recognized.')
        if _checked(story):
            raise VerificationRequired('Disable Meta story sharing manually before a Facebook-only upload.')
        modes = run.root.locator(self._platform_sel['publish_modes'])
        if modes.count() == 3:
            selected = modes.nth(2 if visibility == 'draft' else 0)
        else:
            selected = _find(run.root, self._platform_sel['publish_modes_fallback'][visibility])
            if selected is None:
                raise VerificationRequired('Meta publish-now/schedule/draft modes not recognized; not submitted.')
        if not _checked(selected):
            run.click(selected)
        run.wait(lambda: _checked(selected), 'Requested Meta publication mode was not selected.')
        run.invariants.append(lambda: _checked(selected) and not _checked(story))
        final_actions = run.root.locator(footer_selector)
        if final_actions.count() == 2:
            final = final_actions.nth(1)
        else:
            final = _find(run.root, self._platform_sel['footer_submit_fallback'][visibility])
            if final is None:
                raise VerificationRequired('Meta final footer structure changed; not submitted.')
        state = {'accepted': False, 'error': False}
        allowed_mutations = set(self._platform_sel['graphql_mutations'])

        def observed(response):
            # Observe only the browser's own final-creation response, not an asset
            # upload/ad response. No additional API calls or credential extraction.
            if not (run.submitted or run.awaiting_user_submit):
                return
            try:
                data = response.request.post_data or ''
                friendly_names = parse_qs(data).get('fb_api_req_friendly_name', [])
                if (urlsplit(response.url).hostname != 'business.facebook.com'
                        or '/api/graphql' not in urlsplit(response.url).path
                        or len(friendly_names) != 1 or friendly_names[0] not in allowed_mutations):
                    return
                payload = response.json()
                creation = payload.get('data', {}).get('xfamily_content_create', {})
                items = creation.get('items')
                if isinstance(items, dict):
                    items = [items]
                if (payload.get('errors') or any((creation.get('errors') or {}).values())
                        or not isinstance(items, list) or len(items) != 1):
                    state['error'] = True
                else:
                    state['accepted'] = True
            except Exception:
                state['error'] = True

        run.page.on('response', observed)
        try:
            run.emit(75, 'Meta Page destination and explicit publication mode verified.', step='set_visibility')
            return run.finish(final, visibility, response_state=state)
        finally:
            run.page.remove_listener('response', observed)

    def _confirmation(self, run, visibility, *, baseline=False):
        selectors = (self._platform_sel['draft_success'] if visibility == 'draft'
                     else self._platform_sel['success'])
        signal = _find(run.root, selectors)
        if signal is None and run.root is not run.page:
            signal = _find(run.page, selectors)
        if signal is None:
            return None
        if baseline:
            return signal
        path = urlsplit(str(run.page.url)).path
        if self.platform == 'youtube':
            # share dialogs/links can exist earlier in the wizard. This dialog must
            # be fresh AND explicitly state that the server completed publication.
            heading = _find(signal, self._platform_sel['confirm_headings'])
            if heading is None:
                normalized = ' '.join(signal.inner_text(timeout=self.action_timeout_ms).split()).casefold()
                if not any(phrase in normalized for phrase in self._platform_sel['confirm_phrases']):
                    return None
            if not self._result_url(signal):
                return None
        elif self.platform == 'douyin' and visibility != 'draft':
            if not path.startswith('/creator-micro/content/manage'):
                return None
        elif self.platform == 'bilibili' and visibility != 'draft':
            if not path.startswith('/platform/upload/success'):
                return None
            text = ' '.join(signal.inner_text(timeout=self.action_timeout_ms).split())
            if not any(token in text for token in self._platform_sel['success_tokens']):
                return None
        elif self.platform == 'xiaohongshu' and path != '/publish/success':
            return None
        return signal

    def _confirmed(self, run, visibility, response_state):
        # A completed POST or optimistic toast during server processing is not a
        # completed publication. Keep the caller's browser alive and reconcile on
        # a bounded timeout instead of returning a fake success.
        if not self._progress_complete(run.root) or _find(run.root, self._common['processing']) is not None:
            return None
        if response_state is not None:
            if response_state['error']:
                raise VerificationRequired('Meta creation response was erroneous/ambiguous; do not retry automatically.')
            if not response_state['accepted']:
                return None
        return self._confirmation(run, visibility)

    def _result_url(self, confirmation):
        allowed = {
            'tiktok': (('www.tiktok.com',), r'^/@[^/]+/video/\d+$'),
            'youtube': (('youtu.be', 'www.youtube.com', 'youtube.com'), r'^/(?:[A-Za-z0-9_-]{11}|watch/?|shorts/[A-Za-z0-9_-]{11}/?)$'),
            'facebook': (('www.facebook.com', 'business.facebook.com'), r'^/(?:reel/\d+|[^/]+/posts/[^/]+|watch/?)$'),
            'douyin': (('www.douyin.com',), r'^/video/\d+$'),
            'bilibili': (('www.bilibili.com',), r'^/video/BV[A-Za-z0-9]+/?$'),
            'xiaohongshu': (('www.xiaohongshu.com',), r'^/explore/[a-fA-F0-9]+$'),
        }[self.platform]
        for selector in ('a[href]',):
            links = confirmation.locator(selector)
            for index in range(min(links.count(), 20)):
                href = links.nth(index).get_attribute('href', timeout=self.action_timeout_ms) or ''
                location = urlsplit(urljoin(self.upload_url, href))
                if (location.scheme == 'https' and location.hostname in allowed[0]
                        and not location.username and not location.password
                        and location.port in (None, 443) and re.fullmatch(allowed[1], location.path)):
                    if self.platform == 'youtube':
                        if location.hostname == 'youtu.be':
                            short_match = re.fullmatch(r'^/([A-Za-z0-9_-]{11})/?$', location.path)
                            if short_match:
                                return 'https://youtu.be/' + short_match.group(1)
                            continue
                        if location.path in ('/watch', '/watch/'):
                            ids = parse_qs(location.query).get('v', [])
                            if len(ids) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{11}', ids[0]):
                                continue
                            return f'https://{location.hostname}/watch?v={ids[0]}'
                        shorts_match = re.fullmatch(r'^/shorts/([A-Za-z0-9_-]{11})/?$', location.path)
                        if shorts_match:
                            return f'https://{location.hostname}/shorts/{shorts_match.group(1)}'
                        continue
                    if self.platform == 'facebook' and location.path in ('/watch', '/watch/'):
                        ids = parse_qs(location.query).get('v', [])
                        if len(ids) != 1 or not re.fullmatch(r'\d{4,30}', ids[0]):
                            continue
                        return f'https://{location.hostname}/watch/?v={ids[0]}'
                    # Strip query/fragment tracking or signed access tokens from logs.
                    return f'https://{location.hostname}{location.path}'
        return ''


def adapter_for(platform, selectors=None):
    """Return a fresh adapter, rejecting aliases and unknown platform identifiers."""
    if not isinstance(platform, str) or platform not in _CONFIG:
        raise UnsupportedMode('Unknown upload platform.')
    return BrowserUploadAdapter(platform, selectors=selectors)
