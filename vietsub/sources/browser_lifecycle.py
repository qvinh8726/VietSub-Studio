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
        self._timer = None
        self._generation = 0

    def _cancel_timer(self):
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm(self):
        self._cancel_timer()
        if self.instance is None or self.active:
            return
        generation = self._generation
        remaining = max(0.01, self.idle_seconds - (self.clock() - self.last_activity))
        self._timer = self.timer_factory(remaining, lambda: self._expire(generation))
        self._timer.daemon = True
        self._timer.start()

    def _expire(self, generation):
        with self.lock:
            if generation != self._generation or self.active or self.instance is None:
                return
            if self.clock() - self.last_activity < self.idle_seconds:
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

    def remember(self, instance):
        with self.lock:
            self.instance = instance
            self.last_activity = self.clock()
            self._arm()

    def forget(self):
        with self.lock:
            self._cancel_timer()
            self.instance = None

    def shutdown(self):
        """Close only when no lease is held; caller already verifies ownership."""
        with self.lock:
            if self.active:
                return False
            self._cancel_timer()
            if self.instance is None:
                return True
            if self.close(self.instance):
                self.forget()
                return True
            self._arm()
            return False
