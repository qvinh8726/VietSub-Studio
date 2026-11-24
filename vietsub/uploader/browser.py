"""Thread-scoped uploader browsers; never connects to the app's shared CDP port.

Importing this module imports neither Playwright/Patchright nor platform adapters.
Call these synchronous methods *inside* a worker thread: all Playwright objects
are created, used and closed in that call, never retained on BrowserRuntime.
The dedicated persistent user-data directory is authoritative; private optional
storage snapshots are backup artifacts, not injected into another profile.
"""

from contextlib import contextmanager
import importlib
import inspect
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
MAX_TRACE_FILES_PER_PROFILE = 10
MAX_TRACE_BYTES_PER_PROFILE = 100 * 1024 * 1024
_ALLOWED_STEPS = frozenset((
    "open_studio",
    "check_session",
    "upload_file",
    "fill_metadata",
    "set_visibility",
    "submit",
    "confirm",
))


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


def _redaction():
    return importlib.import_module("vietsub.redaction")


def _signalled(event):
    if event is None:
        return False
    return bool(event.is_set()) if hasattr(event, "is_set") else bool(event())


class BrowserRuntime:
    """Independent per-account headed login and headless/headed upload workers.

    Login waits for explicit ``finish`` (a threading.Event or predicate), checking
    cancellation and page closure every 200 ms, with an absolute ten-minute cap.
    Without finish it may only end by cancellation, closure or timeout; it never
    silently treats a login page as a completed login.

    Upload progress callbacks receive ``{'phase': str, 'percent': number}`` (plus
    optional whitelisted ``'step'``). Failures raise sanitized exceptions;
    VerificationRequired keeps its adapter class for the durable queue and carries
    optional local ``screenshot_path``, ``trace_path``, ``failed_step``, and
    ``uncertain`` attributes.
    """

    def __init__(self, store, executable_resolver, log=None, *, selectors_provider=None):
        if not callable(executable_resolver):
            raise TypeError("A browser executable resolver is required.")
        self.store = store
        self.executable_resolver = executable_resolver
        self.log = log
        self.selectors_provider = selectors_provider

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

    def _make_adapter(self, module, platform, selectors=None):
        resolved = selectors
        if resolved is None and callable(self.selectors_provider):
            try:
                resolved = self.selectors_provider()
            except Exception:
                resolved = None
        if resolved is not None:
            try:
                return module.adapter_for(platform, selectors=resolved)
            except TypeError:
                pass
        return module.adapter_for(platform)

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

    def _start_tracing(self, identifier, context):
        tracing = getattr(context, "tracing", None)
        if tracing is None or not callable(getattr(tracing, "start", None)):
            return False
        try:
            tracing.start(screenshots=True, snapshots=True, sources=False)
            self._log(identifier, "Debug tracing enabled for this upload session.")
            return True
        except Exception:
            self._log(identifier, "Debug tracing could not be started.")
            return False

    def _prune_traces(self, traces_dir):
        try:
            directory = _assert_plain_path(traces_dir, kind="directory")
            entries = []
            for item in directory.iterdir():
                if not item.name.startswith("trace-") or not item.name.endswith(".zip"):
                    continue
                try:
                    plain = _assert_plain_path(item, kind="file")
                    info = os.lstat(plain)
                except Exception:
                    continue
                entries.append((info.st_mtime_ns, info.st_size, plain))
            entries.sort(key=lambda entry: entry[0])
            total_bytes = sum(entry[1] for entry in entries)
            while entries and (len(entries) > MAX_TRACE_FILES_PER_PROFILE or total_bytes > MAX_TRACE_BYTES_PER_PROFILE):
                _, size, victim = entries.pop(0)
                try:
                    victim.unlink(missing_ok=True)
                    total_bytes -= size
                except OSError:
                    pass
        except Exception:
            pass

    def _stop_tracing(self, identifier, context, *, save, job_id=""):
        tracing = getattr(context, "tracing", None)
        if tracing is None or not callable(getattr(tracing, "stop", None)):
            return ""
        if not save:
            try:
                tracing.stop()
            except Exception:
                pass
            return ""
        reserved = None
        try:
            directory = Path(self.store.trace_dir(identifier))
            _assert_plain_path(directory, kind="directory")
            safe_job = job_id if isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{32}", job_id) else uuid.uuid4().hex
            path = directory / f"trace-{safe_job}-{uuid.uuid4().hex[:8]}.zip"
            descriptor = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(descriptor)
            reserved = path
            _assert_plain_path(path, kind="file")
            tracing.stop(path=str(path))
            _assert_plain_path(path, kind="file")
            if path.stat().st_size == 0:
                path.unlink(missing_ok=True)
                return ""
            self._prune_traces(directory)
            self._log(identifier, "Private Playwright debug trace saved locally (may contain session data).")
            return str(path)
        except Exception:
            if reserved is not None:
                try:
                    if reserved.exists() and reserved.stat().st_size == 0:
                        reserved.unlink(missing_ok=True)
                except OSError:
                    pass
            self._log(identifier, "Private Playwright debug trace could not be saved.")
            return ""

    def login(self, profile_id, *, cancel=None, finish=None):
        """Open only this account's headed browser and await explicit completion."""
        with self.store.lease(profile_id):
            account = self.store.get(profile_id)
            if _signalled(cancel):
                return self._session(profile_id, account["session_status"], outcome="cancelled", detail="login_cancelled")
            adapter = self._make_adapter(_adapters(), account["platform"])
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
            adapter = self._make_adapter(_adapters(), self.store.get(profile_id)["platform"])
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
    def _progress(callback, step_holder=None):
        if callback is None:
            return None
        def report(percent, _message=""):
            number = float(percent) if type(percent) in (int, float) and math.isfinite(percent) else 0
            # Adapter/page text is deliberately dropped, even if an adapter later
            # adds a raw URL, browser error or account-specific message here.
            payload = {
                "phase": "confirming" if number >= 90 else "uploading",
                "percent": max(0, min(100, number)),
            }
            if step_holder and step_holder[0] in _ALLOWED_STEPS:
                payload["step"] = step_holder[0]
            callback(payload)
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
        try:
            return _redaction().redact_error(text)
        except Exception:
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
               progress=None, before_publish=None, artifact_dir=None,
               upload_mode="auto", debug_mode=False, confirm_submitted=None,
               on_awaiting_submit=None, selectors=None, job_id="",
               assisted_timeout_ms=None):
        """Use one persistent context and call the platform adapter once.

        ``upload_mode="assisted"`` or ``debug_mode=True`` launches a headed
        browser. When ``debug_mode=True``, Playwright tracing is recorded and
        saved locally only when the upload fails or needs verification.
        """
        if upload_mode not in ("auto", "assisted"):
            raise ValueError("upload_mode must be 'auto' or 'assisted'.")
        debug_mode = bool(debug_mode)
        headless = upload_mode == "auto" and not debug_mode
        with self.store.lease(profile_id):
            module = _adapters()
            adapter = self._make_adapter(module, self.store.get(profile_id)["platform"], selectors=selectors)
            if artifact_dir is not None:
                _assert_plain_path(artifact_dir, kind="directory")
            if _signalled(cancel):
                raise module.UploadCancelled("Uploader operation cancelled.")
            current_step = [None]
            def record_step(step):
                if step in _ALLOWED_STEPS:
                    current_step[0] = step
            try:
                with self._context(profile_id, headless=headless) as (context, page):
                    tracing_active = self._start_tracing(profile_id, context) if debug_mode else False
                    try:
                        if _signalled(cancel):
                            raise module.UploadCancelled("Uploader operation cancelled.")
                        adapter_kwargs = {
                            "visibility": visibility,
                            "cancel": cancel,
                            "progress": self._progress(progress, current_step),
                            "before_publish": before_publish,
                        }
                        try:
                            sig = inspect.signature(adapter.upload)
                            accepts_var = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                            param_names = set(sig.parameters)
                        except (TypeError, ValueError):
                            accepts_var, param_names = True, set()
                        if accepts_var or "on_step" in param_names:
                            adapter_kwargs["on_step"] = record_step
                        if upload_mode != "auto" and (accepts_var or "upload_mode" in param_names):
                            adapter_kwargs["upload_mode"] = upload_mode
                        if confirm_submitted is not None and (accepts_var or "confirm_submitted" in param_names):
                            adapter_kwargs["confirm_submitted"] = confirm_submitted
                        if on_awaiting_submit is not None and (accepts_var or "on_awaiting_submit" in param_names):
                            adapter_kwargs["on_awaiting_submit"] = on_awaiting_submit
                        if assisted_timeout_ms is not None and (accepts_var or "assisted_timeout_ms" in param_names):
                            adapter_kwargs["assisted_timeout_ms"] = assisted_timeout_ms

                        result = adapter.upload(page, video_path, metadata, **adapter_kwargs)
                        result = self._result(adapter, result)
                        if tracing_active:
                            self._stop_tracing(profile_id, context, save=False)
                            tracing_active = False
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
                        uncertain = bool(getattr(error, "uncertain", False))
                        shot = self._screenshot(profile_id, page, artifact_dir, verification=verification)
                        trace_path = ""
                        if tracing_active:
                            trace_path = self._stop_tracing(profile_id, context, save=True, job_id=job_id)
                            tracing_active = False
                        if verification:
                            try:
                                self.store.update_session(profile_id, "needs_verification")
                            except Exception:
                                self._log(profile_id, "Verification required; session metadata could not be updated.")
                            if uncertain:
                                message = "Trình duyệt đã đóng trong khi chờ đăng thủ công; kiểm tra nền tảng trước khi thử lại."
                            elif upload_mode == "assisted" and "timed out" in str(error).lower():
                                message = "Hết thời gian chờ bấm Đăng thủ công; kiểm tra nền tảng trước khi thử lại."
                            else:
                                message = "Manual platform verification required; no automatic bypass or retry."
                            safe = module.VerificationRequired(message)
                            if uncertain:
                                safe.uncertain = True
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
                        safe.trace_path = trace_path
                        safe.failed_step = current_step[0] or ""
                        raise safe from None
            except (ProfileStoreError, ProfileNotFound, module.UploadError):
                raise
            except Exception:
                self._log(profile_id, "Uploader browser launch/driver failed.")
                raise module.UploadError("Uploader browser is unavailable or could not be opened.") from None
