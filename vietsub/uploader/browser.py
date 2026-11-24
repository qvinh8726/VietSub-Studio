"""Thread-scoped uploader browsers; never connects to the app's shared CDP port.

Importing this module imports neither Playwright/Patchright nor platform adapters.
Call these synchronous methods *inside* a worker thread: all Playwright objects
are created, used and closed in that call, never retained on BrowserRuntime.
The dedicated persistent user-data directory is authoritative; private optional
storage snapshots are backup artifacts, not injected into another profile.
"""

from contextlib import contextmanager
import importlib
import math
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .profiles import (
    ProfileBusy, ProfileNotFound, ProfileStoreError, _assert_plain_path,
    _mkdir, _open_private,
)


MANUAL_LOGIN_SECONDS = 600
LOGIN_POLL_SECONDS = 0.2


def get_sync_playwright():
    """Prefer Patchright if installed; do not hide a broken installed package."""
    try:
        module = importlib.import_module("patchright.sync_api")
    except ModuleNotFoundError as error:
        if error.name not in ("patchright", "patchright.sync_api"):
            raise
        module = importlib.import_module("playwright.sync_api")
    return module.sync_playwright


def _adapters():
    return importlib.import_module(".adapters", __package__)


def _signalled(event):
    if event is None:
        return False
    return bool(event.is_set()) if hasattr(event, "is_set") else bool(event())


class BrowserRuntime:
    """Independent per-account headed login and headless probe/upload workers.

    Login waits for explicit ``finish`` (a threading.Event or predicate), checking
    cancellation and page closure every 200 ms, with an absolute ten-minute cap.
    Without finish it may only end by cancellation, closure or timeout; it never
    silently treats a login page as a completed login.

    Upload progress callbacks receive ``{'phase': str, 'percent': number}``.
    Failures raise sanitized exceptions; VerificationRequired keeps its adapter
    class for the durable queue and carries an optional local ``screenshot_path``.
    """

    def __init__(self, store, executable_resolver, log=None):
        if not callable(executable_resolver):
            raise TypeError("A browser executable resolver is required.")
        self.store = store
        self.executable_resolver = executable_resolver
        self.log = log

    def _log(self, identifier, message):
        # Every caller supplies developer-owned text; never page/adapter/exception text.
        if self.log is not None:
            text = "[Publish Hub] " + identifier + " · " + message
            try:
                self.log(text, "system")
            except TypeError:
                try:
                    self.log(text)
                except Exception:
                    pass
            except Exception:
                pass

    @contextmanager
    def _context(self, identifier, *, headless):
        executable = self.executable_resolver()
        if not isinstance(executable, (str, os.PathLike)) or not os.fspath(executable):
            raise RuntimeError("Uploader browser is unavailable; select a browser executable first.")
        # The app resolver is authoritative; no everyday Chrome profile is copied,
        # no user-data-dir override and no shared/global browser object is accepted.
        directory = self.store.profile_dir(identifier)
        arguments = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
        if headless:
            arguments.append("--headless=new")
        with get_sync_playwright()() as playwright:
            context = None
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=directory,
                    executable_path=os.fspath(executable),
                    headless=headless,
                    args=arguments,
                )
                context.set_default_timeout(5_000)
                context.set_default_navigation_timeout(30_000)
                # Persistent launch already supplies its initial tab; never add a
                # second blank tab when it exists, including after profile restart.
                page = context.pages[0] if context.pages else context.new_page()
                yield context, page
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        self._log(identifier, "Owned uploader context cleanup failed; browser driver will stop.")

    def _snapshot(self, identifier, context):
        # Called only on positive login/upload evidence, never by probe().
        self.store.save_storage_state(identifier, context.storage_state())

    def _try_snapshot(self, identifier, context):
        # A post-submission backup failure must never turn confirmed publication
        # into an automatic duplicate upload. Persistent Chromium state is primary.
        try:
            self._snapshot(identifier, context)
        except Exception:
            self._log(identifier, "Private session snapshot could not be saved.")

    def _session(self, identifier, status, *, screenshot="", outcome=None, detail=""):
        record = self.store.update_session(identifier, status, detail)
        result = {"id": identifier, "platform": record["platform"],
                  "status": outcome or status, "session_status": status,
                  "session_detail": record["session_detail"]}
        if screenshot:
            result["screenshot_path"] = screenshot
        return result

    @staticmethod
    def _probe_status(value):
        # Positive evidence is mandatory: arbitrary strings/truthy dicts are not
        # treated as logged-in. Unknown/challenge is a human-verification state.
        if isinstance(value, dict):
            if value.get("logged_in") is True:
                return "alive"
            if value.get("logged_in") is False:
                return "expired"
        return "needs_verification"

    def _screenshot(self, identifier, page, artifact_dir, *, verification=False):
        if page is None:
            return ""
        try:
            directory = (_mkdir(artifact_dir) if artifact_dir is not None
                         else Path(self.store.artifact_dir(identifier)))
            _assert_plain_path(directory, kind="directory")
            path = directory / (("verification-" if verification else "error-") + uuid.uuid4().hex + ".png")
            # Reserve a private local file; browser-provided names/paths are never used.
            descriptor = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(descriptor)
            _assert_plain_path(path, kind="file")
            page.screenshot(path=str(path), type="png", full_page=False, timeout=5_000)
            _assert_plain_path(path, kind="file")
            self._log(identifier, "Private diagnostic screenshot saved locally (may contain page content).")
            return str(path)
        except Exception:
            self._log(identifier, "Private diagnostic screenshot could not be saved.")
            return ""

    def login(self, profile_id, *, cancel=None, finish=None):
        """Open only this account's headed browser and await explicit completion."""
        with self.store.lease(profile_id):
            account = self.store.get(profile_id)
            if _signalled(cancel):
                return self._session(profile_id, account["session_status"], outcome="cancelled", detail="login_cancelled")
            adapter = _adapters().adapter_for(account["platform"])
            page = None
            # No finish/cancel Events or browser objects are retained on the runtime.
            try:
                with self._context(profile_id, headless=False) as (context, page):
                    try:
                        deadline = time.monotonic() + MANUAL_LOGIN_SECONDS
                        page.goto(adapter.login_url, wait_until="domcontentloaded", timeout=30_000)
                        self._log(profile_id, "Manual headed login opened; use Finish login when ready (maximum ten minutes).")
                        while True:
                            if _signalled(cancel):
                                return self._session(profile_id, account["session_status"], outcome="cancelled", detail="login_cancelled")
                            if time.monotonic() >= deadline:
                                shot = self._screenshot(profile_id, page, None, verification=True)
                                return self._session(profile_id, "unknown", screenshot=shot, outcome="timeout", detail="login_timeout")
                            if page.is_closed():
                                return self._session(profile_id, "unknown", outcome="cancelled", detail="login_cancelled")
                            if _signalled(finish):
                                break
                            # Pump the synchronous browser event loop in short slices;
                            # no blind sleep and no asynchronous cross-thread API use.
                            page.wait_for_timeout(int(LOGIN_POLL_SECONDS * 1000))
                        if _signalled(cancel):
                            return self._session(profile_id, account["session_status"], outcome="cancelled", detail="login_cancelled")
                        status = self._probe_status(adapter.probe(page))
                        if _signalled(cancel):
                            return self._session(profile_id, account["session_status"], outcome="cancelled", detail="login_cancelled")
                        shot = "" if status == "alive" else self._screenshot(profile_id, page, None, verification=True)
                        if status == "alive":
                            self._snapshot(profile_id, context)
                        self._log(profile_id, "Manual login checked: " + status + ".")
                        return self._session(profile_id, status, screenshot=shot)
                    except Exception:
                        shot = self._screenshot(profile_id, page, None)
                        if _signalled(cancel):
                            return self._session(profile_id, account["session_status"], outcome="cancelled", detail="login_cancelled")
                        self._log(profile_id, "Manual login failed; no session credentials were logged.")
                        return self._session(profile_id, "error", screenshot=shot)
            except (ProfileStoreError, ProfileNotFound):
                raise
            except Exception:
                self._log(profile_id, "Uploader browser could not be opened for manual login.")
                return self._session(profile_id, "error", detail="browser_unavailable")

    def probe(self, profile_id):
        """Headless session check; no snapshot/config/metadata content is exported."""
        with self.store.lease(profile_id):
            adapter = _adapters().adapter_for(self.store.get(profile_id)["platform"])
            try:
                with self._context(profile_id, headless=True) as (_, page):
                    try:
                        status = self._probe_status(adapter.probe(page))
                        shot = "" if status == "alive" else self._screenshot(profile_id, page, None, verification=True)
                        self._log(profile_id, "Headless session check: " + status + ".")
                        return self._session(profile_id, status, screenshot=shot)
                    except Exception:
                        shot = self._screenshot(profile_id, page, None)
                        self._log(profile_id, "Headless session check failed.")
                        return self._session(profile_id, "error", screenshot=shot)
            except (ProfileStoreError, ProfileNotFound):
                raise
            except Exception:
                return self._session(profile_id, "error", detail="browser_unavailable")

    @staticmethod
    def _progress(callback):
        if callback is None:
            return None
        def report(percent, _message=""):
            number = float(percent) if type(percent) in (int, float) and math.isfinite(percent) else 0
            # Adapter/page text is deliberately dropped, even if an adapter later
            # adds a raw URL, browser error or account-specific message here.
            callback({"phase": "confirming" if number >= 90 else "uploading",
                      "percent": max(0, min(100, number))})
        return report

    @staticmethod
    def _safe_business_error(error, upload_error_class):
        fallback = "Uploader browser operation failed; see private local diagnostic screenshot if available."
        if type(error) is not upload_error_class:
            return fallback
        text = " ".join(str(error or "").split()).strip()
        if not text or len(text) > 240:
            return fallback
        lowered = text.lower()
        sensitive = ("http://", "https://", "://", "cookie", "token", "secret", "bearer",
                     "authorization", "sessionid", "password", "passwd", "auth=")
        if any(marker in lowered for marker in sensitive):
            return fallback
        return text

    @staticmethod
    def _result(adapter, value):
        if not isinstance(value, dict) or value.get("status") not in ("success", "draft"):
            raise RuntimeError("Uploader did not confirm publication or draft save.")
        url = ""
        candidate = value.get("url", "")
        if isinstance(candidate, str):
            try:
                parsed = urlsplit(candidate)
                platform = getattr(adapter, "platform", "")
                public_hosts = {
                    "tiktok": ("www.tiktok.com",), "youtube": ("www.youtube.com", "youtube.com", "youtu.be"),
                    "facebook": ("www.facebook.com", "business.facebook.com"),
                    "douyin": ("www.douyin.com",), "bilibili": ("www.bilibili.com",),
                    "xiaohongshu": ("www.xiaohongshu.com",),
                }
                hosts = public_hosts.get(platform, getattr(adapter, "hosts", ()))
                if parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username and not parsed.password and parsed.port in (None, 443):
                    # Query/fragment may contain ephemeral tokens or account secrets.
                    query = ""
                    if platform == "youtube" and parsed.path == "/watch":
                        ids = parse_qs(parsed.query).get("v", [])
                        if len(ids) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{11}", ids[0]):
                            query = "v=" + ids[0]
                    elif platform == "facebook" and parsed.path in ("/watch", "/watch/"):
                        ids = parse_qs(parsed.query).get("v", [])
                        if len(ids) == 1 and re.fullmatch(r"\d{4,30}", ids[0]):
                            query = "v=" + ids[0]
                    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
            except ValueError:
                pass
        status = value["status"]
        return {"status": status, "url": url,
                "message": "Draft save confirmed." if status == "draft" else "Publication confirmed."}

    def upload(self, profile_id, video_path, metadata, *, visibility, cancel=None,
               progress=None, before_publish=None, artifact_dir=None):
        """Use one headless persistent context and call the platform adapter once.

        ``artifact_dir`` is the exact trusted task-owned directory supplied by the
        queue. Symlink/junction components are refused before any file is written.
        Error screenshots remain local and their path is attached to the sanitized
        exception so the caller can provide its own authenticated local preview.
        """
        with self.store.lease(profile_id):
            module = _adapters()
            adapter = module.adapter_for(self.store.get(profile_id)["platform"])
            if artifact_dir is not None:
                _assert_plain_path(artifact_dir, kind="directory")
            if _signalled(cancel):
                raise module.UploadCancelled("Uploader operation cancelled.")
            try:
                with self._context(profile_id, headless=True) as (context, page):
                    try:
                        if _signalled(cancel):
                            raise module.UploadCancelled("Uploader operation cancelled.")
                        result = adapter.upload(
                            page, video_path, metadata, visibility=visibility, cancel=cancel,
                            progress=self._progress(progress), before_publish=before_publish,
                        )
                        result = self._result(adapter, result)
                        # Once confirmed, cancellation or nonessential backup/logging
                        # failures must not change success into a duplicate retry.
                        self._try_snapshot(profile_id, context)
                        try:
                            self.store.update_session(profile_id, "alive", "upload_completed")
                        except Exception:
                            self._log(profile_id, "Confirmed upload; session metadata could not be updated.")
                        self._log(profile_id, "Upload completion confirmed by platform adapter.")
                        return result
                    except Exception as error:
                        verification = isinstance(error, module.VerificationRequired)
                        cancelled = isinstance(error, module.UploadCancelled) or _signalled(cancel)
                        shot = self._screenshot(profile_id, page, artifact_dir, verification=verification)
                        if verification:
                            try:
                                self.store.update_session(profile_id, "needs_verification")
                            except Exception:
                                self._log(profile_id, "Verification required; session metadata could not be updated.")
                            safe = module.VerificationRequired("Manual platform verification required; no automatic bypass or retry.")
                            self._log(profile_id, "Upload needs manual verification; other accounts may continue.")
                        elif cancelled:
                            safe = module.UploadCancelled("Uploader operation cancelled.")
                            self._log(profile_id, "Uploader operation cancelled.")
                        elif isinstance(error, module.UnsupportedMode):
                            safe = module.UnsupportedMode("Requested publication mode is not supported by this platform adapter.")
                        else:
                            safe = module.UploadError(self._safe_business_error(error, module.UploadError))
                            self._log(profile_id, "Uploader operation failed; no page or credential details were logged.")
                        safe.screenshot_path = shot
                        raise safe from None
            except (ProfileStoreError, ProfileNotFound, module.UploadError):
                raise
            except Exception:
                self._log(profile_id, "Uploader browser launch/driver failed.")
                raise module.UploadError("Uploader browser is unavailable or could not be opened.") from None
