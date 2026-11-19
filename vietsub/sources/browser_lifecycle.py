"""Serial browser leases and a cancellable idle deadline, shared by CLI and UI.

The caller supplies an ownership-checked close callback. No process discovery or
process termination lives here; a lease covers the entire browser operation,
including manual challenge waits, and excludes shutdown and mode transitions.
"""

from contextlib import contextmanager
from functools import wraps
from threading import RLock, Timer
import time

IDLE_SECONDS = 60
MANUAL_LOGIN_SECONDS = 10 * 60


class HeadedBrowserRequired(RuntimeError):
    """Unwind an operation before reopening its persistent profile visibly."""

    def __init__(self, url):
        super().__init__("The browser needs a visible window for manual verification.")
        self.url = url


class BrowserLifecycle:
    def __init__(self, close, *, idle_seconds=IDLE_SECONDS, clock=time.monotonic, timer_factory=Timer):
        self.lock = RLock()
        self.close = close
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.timer_factory = timer_factory
        self.active = 0
        self.instance = None
        self.last_activity = clock()
        self.manual_login_until = None
        self._timer = None
        self._generation = 0

    def _cancel_timer(self):
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _deadline(self):
        return max(self.last_activity + self.idle_seconds, self.manual_login_until or 0.0)

    def _arm(self):
        self._cancel_timer()
        if self.instance is None or self.active:
            return
        generation = self._generation
        remaining = max(0.01, self._deadline() - self.clock())
        self._timer = self.timer_factory(remaining, lambda: self._expire(generation))
        self._timer.daemon = True
        self._timer.start()

    def _expire(self, generation):
        with self.lock:
            if generation != self._generation or self.active or self.instance is None:
                return
            if self.clock() < self._deadline():
                self._arm()
                return
            try:
                closed = self.close(self.instance)
            except Exception:
                closed = False
            if closed:
                self.forget()
            else:
                # A temporarily busy endpoint must not spin or kill an unrelated PID.
                self.last_activity = self.clock()
                self._arm()

    @contextmanager
    def lease(self, *, activity=True):
        with self.lock:
            self._cancel_timer()
            self.active += 1
            try:
                yield
            finally:
                self.active -= 1
                if activity:
                    self.last_activity = self.clock()
                self._arm()

    def leased(self, *, activity=True):
        def decorate(function):
            @wraps(function)
            def run(*args, **kwargs):
                with self.lease(activity=activity):
                    return function(*args, **kwargs)
            return run
        return decorate

    def grant_manual_login(self, seconds=MANUAL_LOGIN_SECONDS):
        """Keep an explicitly opened login window alive for a bounded grace period.

        This is not a lease: automated challenge waits keep their full operation
        lease instead. Status reads never grant or refresh this deadline.
        """
        with self.lock:
            self.manual_login_until = self.clock() + max(0.0, min(float(seconds), MANUAL_LOGIN_SECONDS))
            self._arm()

    def clear_manual_login(self):
        """Automation has resumed; restore the ordinary idle deadline."""
        with self.lock:
            if self.manual_login_until is not None:
                self.manual_login_until = None
                self._arm()

    def remember(self, instance):
        with self.lock:
            previous = self.instance or {}
            if previous.get("session") != instance.get("session"):
                self.manual_login_until = None
            self.instance = instance
            self.last_activity = self.clock()
            self._arm()

    def forget(self):
        with self.lock:
            self._cancel_timer()
            self.instance = None
            self.manual_login_until = None

    def shutdown(self):
        """Refuse immediately if another thread owns a lease; never wait for it."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.active:
                return False
            self._cancel_timer()
            if self.instance is None:
                return True
            try:
                closed = self.close(self.instance)
            except Exception:
                closed = False
            if closed:
                self.forget()
                return True
            self._arm()
            return False
        finally:
            self.lock.release()
