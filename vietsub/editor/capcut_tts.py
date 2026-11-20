"""CapCut common-task HTTP adapter with optional requests connection pooling.

Protocol evidence (independent implementation, reviewed 2026-11-19):
https://github.com/K07VN/capcut-tts-api/tree/7ab87fbd12cbafa60bea4a48903eddf42a939b07
Exact source files at that revision: ``capcut_tts_api/client.py``, ``config.py``,
``signer.py``, ``Voice.json``, and ``pyproject.toml`` (declares MIT; the tree has
no separate LICENSE file). ``UPSTREAM_SOURCES`` records permanent raw URLs.

IMPORTANT: upstream signer.py explicitly says the production payload-signing
algorithm is NOT established, while client.py still automatically calls its
legacy RSA candidate. We deliberately do NOT copy/guess that candidate or ship
session credentials. Supply ``payload_signer(ssml, extra_info, device) -> str``
from an independently verified, authorized integration. Without it availability
is false and synthesis fails clearly (the service may use optional Edge fallback).
The observed MD5 HTTP header formula is implemented separately and is not a
claim that the payload signature or live service has been verified.

There is no observed voice-list or preview API: voices are an offline, curated
subset of upstream Voice.json; preview is normal synthesis. Catalog presence
never means account entitlement, service availability, or multilingual support
beyond the record's language. Caller-supplied auth headers remain in memory and
are never logged, persisted, or forwarded to artifact downloads.
"""
from __future__ import annotations

from copy import copy
from functools import lru_cache
import hashlib
import http.client
import ssl
import ipaddress
import json
import math
import re
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, LifoQueue
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from xml.sax.saxutils import escape

UPSTREAM_REVISION = "7ab87fbd12cbafa60bea4a48903eddf42a939b07"
_UPSTREAM_ROOT = f"https://raw.githubusercontent.com/K07VN/capcut-tts-api/{UPSTREAM_REVISION}/"
UPSTREAM_SOURCES = {name: _UPSTREAM_ROOT + name for name in (
    "capcut_tts_api/client.py", "capcut_tts_api/config.py", "capcut_tts_api/signer.py",
    "capcut_tts_api/Voice.json", "pyproject.toml")}
UPSTREAM_LICENSE = "MIT (declared by pyproject.toml; no separate LICENSE in the inspected tree)"
BASE_URL = "https://editor-api-sg.capcutapi.com"
PROVIDER = "capcut_internal"


class TTSError(RuntimeError):
    """Safe, user-facing speech error; never contains request text or credentials."""


class ProviderUnavailable(TTSError):
    """Provider missing configuration/runtime or not presently usable."""


class TTSCancelled(TTSError):
    """The caller cancelled work; never eligible for fallback or retry."""


class TTSTimeout(TTSError):
    """A bounded request/task/worker exceeded its deadline."""


class CapCutProtocolError(TTSError):
    """The service returned malformed, failed, or unsupported task data."""


class VerificationRequired(ProviderUnavailable):
    """Authentication/challenge must be handled by the user, not bypassed."""


class _TransientHTTPError(ProviderUnavailable):
    """A safe query/download may retry this failure, within the shared budget."""


CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 15.0
TASK_TIMEOUT = 20.0
POLL_DELAYS = (.15, .25, .4, .8)
_INHERIT = object()
_SUCCESS_STATES = {"success", "succeed", "succeeded", "completed", "done"}
_FAILURE_STATES = {"failed", "failure", "error", "cancelled", "canceled"}
_PENDING_STATES = {"pending", "queueing", "queued", "running", "processing", "created", "waiting"}
_HTTP_CONTEXT = threading.local()
_DIAGNOSTIC_CONTEXT = threading.local()


def _safe_code(value, *, identifier=False, sensitive=()) -> str:
    # Keep short protocol identifiers useful (including future/unknown states),
    # but redact known credentials, text fragments, URLs and arbitrary messages.
    if isinstance(value, (str, int)) and str(value):
        folded = str(value).casefold()
        if any(isinstance(secret, str) and (folded == secret.casefold()
               or isinstance(value, str) and len(value) >= 4 and not value.isdecimal()
               and folded in secret.casefold()) for secret in sensitive):
            return "[đã ẩn]"
    if value is None:
        return "không có"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int) and abs(value) < 10 ** 12:
        return str(value)
    if isinstance(value, str):
        if value == "":
            return '\"\"'
        if len(value) <= 12 and value.lstrip("-").isascii() and value.lstrip("-").isdigit():
            return value
        if value.lower() in _SUCCESS_STATES | _FAILURE_STATES | _PENDING_STATES | {"unknown", "ok", "timeout"}:
            return value
        if identifier and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", value):
            return value
    return "[đã ẩn]"


def _diagnostics(http_status=None, data=None) -> dict:
    body = data if isinstance(data, dict) else {}
    nested = body.get("data")
    tasks = nested.get("tasks") if isinstance(nested, dict) else None
    task = tasks[0] if isinstance(tasks, list) and len(tasks) == 1 and isinstance(tasks[0], dict) else {}
    sensitive = [*getattr(_DIAGNOSTIC_CONTEXT, "secrets", ()),
                 *(task.get(key) for key in ("id", "token", "bind_id"))]
    return {"http_status": http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None,
            "ret": _safe_code(body.get("ret", task.get("ret")), sensitive=sensitive),
            "code": _safe_code(task.get("code", body.get("code")), identifier=True, sensitive=sensitive),
            "status": _safe_code(task.get("status", body.get("status")), identifier=True, sensitive=sensitive)}


class _HTTPState:
    """A watchdog owns the caller deadline; the daemon only collects bytes.

    Socket shutdown wakes real reads without blocking on HTTPResponse's buffered
    reader lock. A transport stuck in DNS/open cannot be forcibly killed by
    Python; late responses are closed by the daemon and never publish files or
    callbacks. This also keeps fake open(Request, timeout=...) transports usable.
    """
    def __init__(self, deadline, read_timeout):
        self.deadline, self.read_timeout = deadline, read_timeout
        self.stopped, self.done = threading.Event(), threading.Event()
        self.read_deadline = None
        self.socket = None
        self._socket_lock = threading.Lock()
        self.status = None
        self.raw = b""
        self.error = None

    def reading(self, sock=None):
        with self._socket_lock:
            if sock is not None:
                self.socket = sock
                sock.settimeout(max(.001, min(self.read_timeout, self.deadline - time.monotonic())))
            if self.read_deadline is None:
                self.read_deadline = min(self.deadline, time.monotonic() + self.read_timeout)
        if self.stopped.is_set():
            self.abort()
            raise TimeoutError()

    def detach(self):
        # A completed pooled connection can immediately belong to another cue.
        # Detach before returning its Session, so a late watchdog cannot kill it.
        with self._socket_lock:
            self.socket = None

    def abort(self):
        self.stopped.set()
        with self._socket_lock:
            if self.socket is not None:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def _connected(sock):
    state = getattr(_HTTP_CONTEXT, "state", None)
    if state is not None:
        state.reading(sock)


class _TimedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _connected(self.sock)


class _TimedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_TimedHTTPSConnection, req, context=self._context)


# Facts transcribed from Voice.json, excluding unverified digital-human assets.
# These are offline reference records, NOT a remotely enumerated entitlement list.
_CATALOG = (
    ("BV421_vivn_streaming", "Nhỏ Ngọt Ngào", "vi-VN", "7252594014782755330"),
    ("vi_female_huong", "Giọng Nữ Phổ Thông", "vi-VN", "7264854897953083905"),
    ("BV074_streaming_dsp", "Giọng Bé", "vi-VN", "7550087831092251920"),
    ("BV074_streaming", "Cô Gái Hoạt Ngôn", "vi-VN", "7102355709945188865"),
    ("vi-VN-HoaiMyNeural", "Hoai My", "vi-VN", "7371666434650280464"),
    ("vi-VN-NamMinhNeural", "Nam Minh", "vi-VN", "7371666524727153168"),
    ("BV075_streaming_vibrato_dsp", "Việt Méo", "vi-VN", "7569450639810465040"),
    ("BV562_streaming", "Mai", "vi-VN", "7483736254694035984"),
    ("multi_female_yangguangnv_uranus_bigtts", "Ban Mai", "vi-VN", "7637456432522218773"),
    ("multi_female_richgirl_uranus_bigtts", "Review Phim new", "vi-VN", "7637460351541447956"),
    ("multi_female_quanweinv_uranus_bigtts", "Bản Tin 1", "vi-VN", "7637458743197732117"),
    ("multi_female_stokie_uranus_bigtts", "Review Phim 4", "vi-VN", "7637456729696996628"),
    ("multi_female_sisi_uranus_bigtts", "Bản Tin nữ", "vi-VN", "7637455857285860629"),
    ("multi_female_daqi_uranus_bigtts", "Review Phim 3", "vi-VN", "7637451983389019409"),
    ("multi_female_xyf04auto_uranus_bigtts", "Review Phim 2", "vi-VN", "7637458743197732117"),
    ("multi_female_kiwi_uranus_bigtts", "Sunny Idol", "vi-VN", "7637457995882089749"),
    ("BV075_streaming_demon_dsp", "Kenny Đại Đế", "vi-VN", "7569442422665661712"),
    ("BV075_streaming_robot_dsp", "Robot VN", "vi-VN", "7538698409633516816"),
    ("multi_male_felipe_uranus_bigtts", "Giọng Nam Trầm", "vi-VN", "7637456729696996628"),
    ("multi_female_peiqi_uranus_bigtts", "Giọng Gái Mới Lớn", "vi-VN", "7637458789033151751"),
    ("multi_female_xinwenjieshuo_uranus_bigtts", "Nam bản tin", "vi-VN", "7637455039719640327"),
    ("multi_female_tianmeijieshuo_uranus_bigtts", "Quên Tên Tự Test", "vi-VN", "7637460417295469832"),
    ("BV075_streaming", "Thanh Niên Tự Tin", "vi-VN", "7102355803792740865"),
    ("en_us_006", "EN US 2", "en-US", "7114563482518819329"),
    ("en_female_janeamber_mars_bigtts", "Janeamber", "en-US", "7538652405005700369"),
    ("en-US-JennyMultilingualNeural", "Jenny", "en-US", "7569439521352338689"),
    ("BV452_streaming", "Vũ hán", "zh-CN", "7543766515837848833"),
    ("ICL_ja_female_zhiyu", "Lovely Idol", "ja-JP", "7579078759446285584"),
    ("th", "ThaiLand 3", "th-TH", "7371666742055014913"),
    ("id_female_icha_uranus_bigtts", "Icathian", "id-ID", "7587328219989249296"),
    ("DiT_fr_male_wit", "Wit", "fr-FR", "7573964692587023617"),
    ("DiT_es_male_bilunan", "Señor entusiasta", "es-ES", "7597943534309690641"),
    ("DiT_de_male_koubo", "Koubo", "de-DE", "7584344912276114704"),
    ("DiT_pt_male_wenrou", "Wenrou", "pt-BR", "7576131428711255297"),
)


def check_cancel(cancel=None) -> None:
    if cancel is None:
        return
    requested = cancel.is_set() if hasattr(cancel, "is_set") else cancel()
    if requested:
        raise TTSCancelled("Speech/audio work cancelled")


def bounded_wait(seconds: float, cancel=None, *, deadline: float | None = None) -> None:
    end = time.monotonic() + max(0.0, seconds)
    if deadline is not None:
        end = min(end, deadline)
    while True:
        check_cancel(cancel)
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        if hasattr(cancel, "wait"):
            cancel.wait(min(remaining, 0.05))
        else:
            time.sleep(min(remaining, 0.05))


def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _xml(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})


def validate_text(text: str, *, max_text: int = 2000) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > max_text:
        raise ValueError(f"Speech text must contain 1–{max_text} characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise ValueError("Speech text contains unsupported control characters")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise ValueError("Speech text contains invalid Unicode")
    return text.strip()


def catalog_voices(language="vi") -> list[dict]:
    language = str(language or "").replace("_", "-").lower()
    return [dict(record) for record in _catalog_voices(language)]


@lru_cache(maxsize=64)
def _catalog_voices(language):
    return tuple({"id": identifier, "name": name, "language": lang, "provider": PROVIDER,
             "resource_id": resource, "catalog_only": True, "service_verified": False}
            for identifier, name, lang, resource in _CATALOG
            if not language or language == "*" or lang.lower() == language
            or ("-" not in language and lang.lower().split("-", 1)[0] == language))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward caller credentials, task tokens, or text on redirects.
        return None


def _https_url(url: str, *, artifact: bool = False) -> str:
    if not isinstance(url, str) or len(url) > 16384:
        raise CapCutProtocolError("Invalid service URL")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise CapCutProtocolError("Invalid service URL") from exc
    if (parts.scheme != "https" or not hostname or parts.username is not None
            or parts.password is not None or parts.fragment or port not in (None, 443)):
        raise CapCutProtocolError("Only credential-free HTTPS service URLs on port 443 are supported")
    if artifact:
        # Native artifact domains vary; never invent a resolver or accept local URLs.
        lowered = hostname.lower().rstrip(".")
        if (lowered in {"localhost", "localhost.localdomain"} or "." not in lowered
                or lowered.endswith((".localhost", ".local", ".internal", ".lan"))
                or all(char in "0123456789.xabcdef" for char in lowered)):
            raise CapCutProtocolError("Local/numeric artifact URL rejected")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise CapCutProtocolError("Non-public artifact URL rejected")
    return url


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    """Resolve once, reject private answers and pin the actual TLS socket to an IP.

    Hostname/SNI and certificate verification keep using the original CDN host;
    no second DNS lookup can rebind the approved address to a local service.
    """
    def connect(self):
        if self._tunnel_host:
            raise CapCutProtocolError("Artifact proxy tunnels are not supported")
        answers = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(item[4][0]).is_global for item in answers):
            raise CapCutProtocolError("Artifact DNS resolved to a non-public address")
        last_error = None
        for family, socktype, proto, _, address in answers:
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(self.timeout)
                sock.connect(address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                _connected(self.sock)
                return
            except OSError as error:
                last_error = error
                sock.close()
        raise last_error or OSError("No public artifact address connected")


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


@lru_cache(maxsize=1)
def _pooled_adapter_type():
    # Import lazily: the offline catalog and legacy fake transports still work
    # in installations without requests. No sockets are opened by this factory.
    from requests.adapters import HTTPAdapter
    from urllib3.connection import HTTPSConnection
    from urllib3.connectionpool import HTTPSConnectionPool

    class TimedConnection(HTTPSConnection):
        def connect(self):
            super().connect()
            _connected(self.sock)

        def request(self, *args, **kwargs):
            if self.sock is not None:
                _connected(self.sock)  # Reused TLS sockets need the new cue's watchdog.
            return super().request(*args, **kwargs)

    class TimedPool(HTTPSConnectionPool):
        ConnectionCls = TimedConnection

    class TimedAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            super().init_poolmanager(*args, **kwargs)
            # Never mutate urllib3's global pool registry.
            self.poolmanager.pool_classes_by_scheme = {"https": TimedPool}

    return TimedAdapter


class _PooledResponse:
    """An exclusive Session lease, returned only after body/close has finished."""
    def __init__(self, response, session, transport, state):
        self.response, self.session, self.transport, self.state = response, session, transport, state
        self.status = response.status_code
        self.closed = False

    def read1(self, size):
        try:
            raw = self.response.raw
            return getattr(raw, "read1", raw.read)(size, decode_content=False)
        except self.transport.network_errors as error:
            self.transport.raise_network_error(error)

    read = read1

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.state is not None:
            self.state.detach()
        try:
            self.response.close()
        finally:
            self.transport.release(self.session)


class _RequestsTransport:
    """Three exclusive Sessions with private keep-alive pools, shared by forks.

    requests.Session has mutable cookies and is not thread safe. Each HTTP
    exchange leases one Session through body close; each Session owns its own
    HTTPAdapter so a returned urllib3 connection cannot escape that lease.
    Preparation ignores session cookies/auth and the environment (netrc/proxies).
    Downloads deliberately use the separate, DNS-pinned artifact transport.
    """
    def __init__(self, pool_size=3):
        self.sessions = []
        import requests
        from urllib3.exceptions import HTTPError as PoolError, TimeoutError as PoolTimeout
        self.requests = requests
        self.network_errors = (requests.RequestException, PoolError)
        self.timeout_errors = (requests.Timeout, PoolTimeout)
        self.available = LifoQueue(pool_size)
        adapter_type = _pooled_adapter_type()
        for _ in range(pool_size):
            session = requests.Session()
            session.trust_env = False
            session.adapters.clear()
            # The task owns the sole retry budget, including 429 and timeouts.
            session.mount("https://", adapter_type(pool_connections=1, pool_maxsize=1,
                                                   pool_block=True, max_retries=0))
            self.sessions.append(session)
            self.available.put(session)

    def raise_network_error(self, error):
        if isinstance(error, self.timeout_errors):
            raise TimeoutError("HTTP request timed out") from None
        raise URLError("HTTP connection failed") from None

    def release(self, session):
        session.cookies.clear()
        self.available.put(session)

    def open(self, request, timeout):
        _https_url(request.full_url)
        if urlsplit(request.full_url).hostname != urlsplit(BASE_URL).hostname:
            raise CapCutProtocolError("API transport only accepts the CapCut API host")
        state = getattr(_HTTP_CONTEXT, "state", None)
        deadline = state.deadline if state is not None else time.monotonic() + timeout
        while True:
            if (state is not None and state.stopped.is_set()) or time.monotonic() >= deadline:
                raise TimeoutError("HTTP pool wait timed out")
            try:
                session = self.available.get(timeout=min(.05, max(.001, deadline - time.monotonic())))
                break
            except Empty:
                pass
        try:
            if state is not None and state.stopped.is_set():
                raise TimeoutError("HTTP request stopped")
            # Request.prepare (not Session.prepare_request) excludes cookies and
            # persisted auth. Redirects never forward secrets or resubmit a POST.
            prepared = self.requests.Request(request.get_method(), request.full_url,
                                             headers=dict(request.header_items()), data=request.data).prepare()
            remaining = max(.001, deadline - time.monotonic())
            read_timeout = state.read_timeout if state is not None else READ_TIMEOUT
            response = session.send(prepared, stream=True, allow_redirects=False,
                                    timeout=(min(timeout, remaining), min(read_timeout, remaining)),
                                    verify=True, proxies={})
            return _PooledResponse(response, session, self, state)
        except Exception as error:
            if state is not None:
                state.detach()
            self.release(session)
            if isinstance(error, self.network_errors):
                self.raise_network_error(error)
            raise

    def close(self):
        for session in self.sessions:
            session.close()

    def __del__(self):
        self.close()


class CapCutClient:
    """Bounded, mockable client for the observed CapCut common-task API.

    ``transport`` can be an object exposing ``open(Request, timeout=...)``; tests
    use a fake transport. No request is made during import, construction, voices,
    or availability. Retries apply only to safe polling/download requests, never
    task creation (to avoid duplicate billable tasks after ambiguous failures).
    Socket connect/read limits are 8/15 seconds, with a 20-second wall-clock
    budget shared by creation, polling, the single retry, and download. Cancellation
    is checked at most every 50ms while waiting for blocking HTTP operations.
    """
    provider = PROVIDER

    def __init__(self, *, payload_signer=None, headers=None, device=None, transport=None,
                 timeout: float = 8.0, task_timeout: float = 20.0, retries: int = 1,
                 poll_interval: float | None = None, max_polls: int = 120, cancel=None,
                 progress=None, max_audio_bytes: int = 32 * 1024 * 1024,
                 read_timeout: float = 15.0):
        for value, name, upper in ((timeout, "timeout", 60), (task_timeout, "task_timeout", 600),
                                   (read_timeout, "read_timeout", 60),
                                   (POLL_DELAYS[0] if poll_interval is None else poll_interval, "poll_interval", 30)):
            if isinstance(value, bool) or not math.isfinite(float(value)) or not 0 < float(value) <= upper:
                raise ValueError(f"{name} must be positive and at most {upper}")
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 3:
            raise ValueError("retries must lie between 0 and 3")
        if isinstance(max_polls, bool) or not isinstance(max_polls, int) or not 1 <= max_polls <= 600:
            raise ValueError("max_polls must lie between 1 and 600")
        if not isinstance(max_audio_bytes, int) or not 1 <= max_audio_bytes <= 128 * 1024 * 1024:
            raise ValueError("max_audio_bytes exceeds safe bounds")
        if payload_signer is not None and not callable(payload_signer):
            raise TypeError("payload_signer must be a callable")
        identifier = str(10**19 + secrets.randbelow(9 * 10**19))
        self.device = {"aid": "359289", "app_name": "CapCut", "appvr": "8.7.0",
                       "version_name": "8.7.0", "version_code": "8.7.0", "channel": "capcutpc_google",
                       "device_platform": "mac", "device_type": "MacBookPro17,4", "device_brand": "MacBookPro17,4",
                       "os_version": "15.7.4", "device_id": identifier, "iid": identifier,
                       "tdid": identifier, "region": "VN", "loc": "VN", "lan": "vi-VN", "pf": "3"}
        for key, value in (device or {}).items():
            if key not in self.device or not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError("Invalid explicit CapCut device configuration")
            self.device[key] = value
        self.headers = {}
        for key, value in (headers or {}).items():
            if (not isinstance(key, str) or not isinstance(value, str) or not key
                    or any(char in key + value for char in "\r\n")
                    or key.lower() in {"host", "content-length"}):
                raise ValueError("Invalid explicit CapCut HTTP headers")
            self.headers[key] = value
        self.payload_signer = payload_signer
        self._custom_transport = transport is not None
        # Scripted legacy fakes describe one serial transcript. The scheduler
        # must not interleave whole cues unless a custom transport opts in.
        self.concurrent_safe = transport is None or getattr(transport, "thread_safe", False) is True
        if transport is None:
            try:
                self.transport = _RequestsTransport()
            except ImportError:
                self.transport = build_opener(_NoRedirect(), _TimedHTTPSHandler())
            self.artifact_transport = build_opener(ProxyHandler({}), _NoRedirect(), _PublicHTTPSHandler())
        else:
            self.transport = self.artifact_transport = transport
        # Injected legacy transports need not be thread safe: forks serialize
        # each exchange through body close. Native Session leases run in parallel.
        self._transport_lock = None
        self._fork_transport_lock = threading.Lock()
        self._artifact_lock = None
        self._signer_lock = threading.Lock()
        # Legacy callers may still supply 90s / 2 retries; clamp those settings
        # rather than silently restoring the old multi-minute failure path.
        self.timeout = min(float(timeout), CONNECT_TIMEOUT)
        self.read_timeout = min(float(read_timeout), READ_TIMEOUT)
        self.task_timeout = min(float(task_timeout), TASK_TIMEOUT)
        self.retries, self.max_polls = min(retries, 1), max_polls
        self.poll_interval = None if poll_interval is None else float(poll_interval)
        self.cancel, self.progress, self.max_audio_bytes = cancel, progress, max_audio_bytes
        # Bound both per-client and aggregate abandoned daemons. Fresh forks must
        # not bypass the bound when a broken resolver ignores cancellation.
        self._http_slots = threading.BoundedSemaphore(2)
        self._http_family_slots = threading.BoundedSemaphore(6)

    def fork(self, *, cancel=_INHERIT, progress=_INHERIT, task_timeout=_INHERIT):
        """A cue-local adapter; keep the parent unchanged and share bounded pools.

        Omitted arguments inherit; explicit None clears cancel/progress. Headers
        and device are copied. Use forks for all concurrent calls: signer calls
        and injected transports are serialized among forks because caller-provided
        implementations need not support concurrency. Create forks before use.
        """
        child = copy(self)
        if task_timeout is not _INHERIT:
            if (isinstance(task_timeout, bool) or not math.isfinite(float(task_timeout))
                    or not 0 < float(task_timeout) <= 600):
                raise ValueError("task_timeout must be positive and at most 600")
            child.task_timeout = min(float(task_timeout), TASK_TIMEOUT)
        if cancel is not _INHERIT:
            child.cancel = cancel
        if progress is not _INHERIT:
            child.progress = progress
        child.headers, child.device = dict(self.headers), dict(self.device)
        child._http_slots = threading.BoundedSemaphore(2)
        if self._custom_transport:
            if not self.concurrent_safe:
                child._transport_lock = child._artifact_lock = self._fork_transport_lock
        else:
            # Stateless urllib handlers are inexpensive; keep IP pinning on every
            # fork without sharing opener state across artifact downloads.
            child.artifact_transport = build_opener(ProxyHandler({}), _NoRedirect(), _PublicHTTPSHandler())
            if not isinstance(self.transport, _RequestsTransport):
                child.transport = build_opener(_NoRedirect(), _TimedHTTPSHandler())
        return child

    clone = fork

    def voices(self, language="vi") -> list[dict]:
        return catalog_voices(language)

    def availability(self) -> dict:
        configured = self.payload_signer is not None
        return {"provider": PROVIDER, "available": configured, "configured": configured,
                "service_verified": False, "network_checked": False,
                "reason": ("Configured; live CapCut availability/voice entitlement is unverified."
                           if configured else "Verified CapCut payload signer is not configured; upstream production signing remains unproven."),
                "guidance": "Supply an authorized, independently verified payload_signer or install optional edge-tts in runtime_python. No browser credentials are read."}

    def _voice(self, voice) -> dict:
        if isinstance(voice, dict):
            identifier, resource = voice.get("id"), voice.get("resource_id")
            if (isinstance(identifier, str) and 0 < len(identifier) <= 200
                    and isinstance(resource, str) and resource.isdecimal() and len(resource) <= 32):
                return {"id": identifier, "resource_id": resource}
            raise ValueError("Explicit CapCut voice requires id and numeric resource_id")
        for item in catalog_voices(None):
            if voice == item["id"]:
                return item
        raise ValueError("Unknown CapCut voice; supply a catalog id or explicit id/resource_id")

    def _request(self, path: str, body: dict, *, new: bool) -> Request:
        device = self.device
        babi = {"feature_entrance": "editor", "feature_entrance_detail": "editor-feature-text_to_speech",
                "feature_key": "text_to_speech", "scenario": "video_editor"}
        query = {key: device[key] for key in ("app_name", "device_type", "os_version", "channel", "version_name",
                                              "device_brand", "device_id", "iid", "version_code", "device_platform", "aid")}
        if new:
            query.update(region=device["region"], babi_param=_compact(babi))
        url = BASE_URL + path + "?" + urlencode(query)
        raw = _compact(body).encode("utf-8")
        timestamp = str(int(time.time()))
        trace = uuid.uuid4().hex
        headers = {"content-type": "application/json", "appvr": device["appvr"], "ch": device["channel"],
                   "device-time": timestamp, "lan": device["lan"], "loc": device["loc"], "pf": device["pf"],
                   "sign-ver": "1", "tdid": device["tdid"], "x-ss-stub": hashlib.md5(raw).hexdigest(),
                   "x-ss-dp": device["aid"], "x-khronos": timestamp, "x-tt-trace-id": f"00-{trace}-{trace[:16]}-01",
                   "user-agent": "Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03",
                   "accept-encoding": "identity", "store-country-code": device["loc"].lower(),
                   "store-country-code-src": "did", "is-dispatch-us-ttp": "0", "is-app-region-us-ttp": "0",
                   "app-sdk-version": device["appvr"], "appid": device["aid"]}
        # Exact observed upstream header formula; not the separate payload sign.
        material = f"9e2c|{path[-7:]}|3|{device['appvr']}|{timestamp}|{device['tdid']}|11ac"
        headers["sign"] = hashlib.md5(material.encode("utf-8")).hexdigest()
        headers.update(self.headers)
        return Request(url, data=raw, headers=headers, method="POST")

    def build_tts_request(self, text: str, voice: str | dict) -> Request:
        text = validate_text(text)
        resolved = self._voice(voice)
        if self.payload_signer is None:
            raise ProviderUnavailable(self.availability()["reason"])
        ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">\n'
                f'    <voice name="{_xml(resolved["id"])}" mock_tone_info="" platform="sami" '
                f'resource_id="{_xml(resolved["resource_id"])}" emotion="" emotion_scale="0" style="" role="" '
                'moyin_emotion="" is_clone_tone="false" need_subtitle_timestamp="false">\n'
                f'        <prosody rate="1.0">{_xml(text)}</prosody>\n    </voice>\n</speak>')
        extra = _compact({"benefit_info": {}})
        try:
            while not self._signer_lock.acquire(timeout=.05):
                check_cancel(self.cancel)
            try:
                check_cancel(self.cancel)
                signature = self.payload_signer(ssml, extra, dict(self.device))
            finally:
                self._signer_lock.release()
        except TTSCancelled:
            raise
        except Exception:
            raise ProviderUnavailable("Configured CapCut payload signer failed") from None
        if not isinstance(signature, str) or not signature or len(signature) > 16384:
            raise ProviderUnavailable("Configured CapCut payload signer returned no valid signature")
        if isinstance(getattr(_DIAGNOSTIC_CONTEXT, "secrets", None), list):
            _DIAGNOSTIC_CONTEXT.secrets.append(signature)
        payload = {"audio_format": "mp3", "babi_param": _compact({"feature_entrance": "editor",
                   "feature_entrance_detail": "editor-feature-text_to_speech", "feature_key": "text_to_speech",
                   "scenario": "video_editor"}), "credit_disable": False, "extra_info": extra,
                   "need_merge_voice": False, "need_subtitle_timestamp": False, "scene": "text_to_speech",
                   "ssml": ssml, "sign": signature}
        return self._request("/lv/v1/common_task/new", {
            "bind_id": str(uuid.uuid4()), "can_queue": True, "enter_from": "text_to_speech",
            "tasks": [{"context": str(uuid.uuid4()), "payload": _compact(payload),
                       "req_key": "sami_text_to_speech", "task_version": "v3"}]}, new=True)

    def build_query_request(self, task_id: str, token: str, bind_id: str = "") -> Request:
        if not all(isinstance(value, str) and len(value) <= 8192 for value in (task_id, token, bind_id)) or not task_id or not token:
            raise CapCutProtocolError("Task response lacks a valid id/token")
        return self._request("/lv/v1/common_task/query", {"tasks": [{"bind_id": bind_id, "id": task_id,
                            "req_key": "sami_text_to_speech", "task_version": "v3", "token": token}]}, new=False)

    def _notify(self, stage: str, description: str, details=None, **extra) -> str:
        details = details or _diagnostics()
        http_status = details["http_status"] if details["http_status"] is not None else "chưa có phản hồi"
        message = (f"CapCut: {description}. HTTP {http_status}; ret={details['ret']}; "
                   f"code={details['code']}; status={details['status']}")
        if self.progress:
            self.progress({"stage": stage, "provider": PROVIDER, "message": message,
                           **details, **extra})
        return message

    def _failure(self, kind, reason, details=None, *, stage="tts_http"):
        error = kind(self._notify(stage, reason, details))
        error.details = details or _diagnostics()
        error.reason = reason
        return error

    def _retry(self, error, budget, deadline, *, stage):
        check_cancel(self.cancel)
        if not budget[0] or time.monotonic() >= deadline:
            return False
        budget[0] -= 1
        reason = getattr(error, "reason", "Yêu cầu chưa thành công")
        self._notify(stage, f"{reason}; thử lại 1/1 trong thời hạn của câu",
                     getattr(error, "details", None), retry=1, reason=reason)
        bounded_wait(.25, self.cancel, deadline=deadline)
        return time.monotonic() < deadline

    def _exchange(self, request, *, deadline, limit, artifact):
        """One HTTP attempt, with wall-clock bounds even for a blocking fake/DNS."""
        check_cancel(self.cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._failure(TTSTimeout, "Đã hết thời gian tối đa cho câu; dừng yêu cầu")
        if not self._http_slots.acquire(blocking=False):
            raise self._failure(ProviderUnavailable, "Kết nối cũ chưa đóng; dừng để tránh tích lũy yêu cầu treo")
        if not self._http_family_slots.acquire(blocking=False):
            self._http_slots.release()
            raise self._failure(ProviderUnavailable, "Nhóm kết nối chưa đóng; dừng để tránh tích lũy yêu cầu treo")
        state = _HTTPState(deadline, self.read_timeout)
        connect_timeout = min(self.timeout, remaining)
        connect_deadline = min(deadline, time.monotonic() + connect_timeout)
        transport = self.artifact_transport if artifact else self.transport
        transport_lock = self._artifact_lock if artifact else self._transport_lock

        def run():
            response, locked = None, False
            _HTTP_CONTEXT.state = state
            try:
                if transport_lock is not None:
                    while not state.stopped.is_set():
                        if time.monotonic() >= deadline:
                            raise TimeoutError()
                        if transport_lock.acquire(timeout=.05):
                            locked = True
                            break
                if state.stopped.is_set():
                    return
                try:
                    response = transport.open(request, timeout=connect_timeout)
                except HTTPError as error:
                    # Error bodies may contain raw ret/code/status; inspect only
                    # bounded bytes and never expose errmsg, URL, headers or text.
                    response = error
                state.status = getattr(response, "status", None) or getattr(response, "code", 200)
                state.reading()
                chunks, size = [], 0
                body_limit = min(limit, 65536) if state.status != 200 else limit
                while not state.stopped.is_set():
                    if time.monotonic() >= state.read_deadline:
                        raise TimeoutError()
                    reader = getattr(response, "read1", response.read)
                    chunk = reader(min(65536, body_limit + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > body_limit:
                        if state.status != 200:
                            break  # Preserve HTTP classification, discard a large error body.
                        raise CapCutProtocolError("Phản hồi CapCut vượt giới hạn dung lượng")
                    chunks.append(chunk)
                if not state.stopped.is_set():
                    state.raw = b"".join(chunks)
            except Exception as error:
                state.error = error
            finally:
                try:
                    if response is not None:
                        response.close()
                except Exception:
                    pass
                finally:
                    state.detach()
                    if locked:
                        transport_lock.release()
                    del _HTTP_CONTEXT.state
                    self._http_slots.release()
                    self._http_family_slots.release()
                    state.done.set()

        worker = threading.Thread(target=run, name="capcut-http", daemon=True)
        try:
            worker.start()
        except Exception:
            self._http_slots.release()
            self._http_family_slots.release()
            raise
        try:
            while True:
                check_cancel(self.cancel)
                end = state.read_deadline if state.read_deadline is not None else connect_deadline
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise self._failure(TTSTimeout, "Yêu cầu HTTP hết thời gian; dừng chờ CapCut",
                                        _diagnostics(state.status))
                if state.done.wait(min(.05, remaining)):
                    check_cancel(self.cancel)
                    end = state.read_deadline if state.read_deadline is not None else connect_deadline
                    if time.monotonic() >= end:
                        raise self._failure(TTSTimeout, "Yêu cầu HTTP hết thời gian; dừng chờ CapCut",
                                            _diagnostics(state.status))
                    break
            if state.error is not None:
                error = state.error
                if isinstance(error, TTSError):
                    raise self._failure(type(error), "Phản hồi HTTP không hợp lệ; yêu cầu thất bại",
                                        _diagnostics(state.status)) from None
                if isinstance(error, (URLError, OSError, TimeoutError, http.client.HTTPException)):
                    timed_out = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
                    raise self._failure(TTSTimeout if timed_out else _TransientHTTPError,
                                        "Kết nối HTTP hết thời gian" if timed_out else "Lỗi kết nối HTTP",
                                        _diagnostics(state.status)) from None
                raise self._failure(CapCutProtocolError, "Transport HTTP trả lỗi không hợp lệ; tạo giọng nói thất bại",
                                    _diagnostics(state.status)) from None
            return state.raw, state.status
        except TTSCancelled:
            self._notify("tts_http", "Đã hủy; dừng chờ yêu cầu HTTP", _diagnostics(state.status))
            raise
        finally:
            # Do not join an uncooperative transport or call response.close() on
            # this thread: either could block cancellation behind a read lock.
            if not state.done.is_set():
                state.abort()

    def _bytes(self, request: Request, *, deadline: float, limit: int, safe_retry: bool,
               artifact=False, retry_budget=None) -> bytes:
        budget = retry_budget if retry_budget is not None else [self.retries]
        while True:
            try:
                raw, status = self._exchange(request, deadline=deadline, limit=limit, artifact=artifact)
                try:
                    data = json.loads(raw) if not artifact or status != 200 else None
                except (ValueError, UnicodeError, RecursionError):
                    data = None
                details = _diagnostics(status, data)
                if status in (401, 403):
                    raise self._failure(VerificationRequired, "Cần đăng nhập/xác minh thủ công; không thể tạo giọng nói", details)
                if status != 200:
                    transient = status == 429 or isinstance(status, int) and 500 <= status <= 599
                    raise self._failure(_TransientHTTPError if transient else ProviderUnavailable,
                                        "Máy chủ từ chối yêu cầu; tạo giọng nói thất bại", details)
                self._notify("tts_http", "Đã nhận phản hồi" if not artifact else "Đã tải âm thanh", details)
                return raw
            except (TTSTimeout, _TransientHTTPError) as error:
                if safe_retry and self._retry(error, budget, deadline, stage="tts_http"):
                    continue
                raise

    def _json(self, request: Request, *, deadline: float, safe_retry: bool) -> dict:
        raw = self._bytes(request, deadline=deadline, limit=2 * 1024 * 1024, safe_retry=safe_retry)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise self._failure(CapCutProtocolError, "Phản hồi không phải JSON hợp lệ; có thể cần đăng nhập/xác minh",
                                _diagnostics(200)) from None
        if not isinstance(data, dict):
            raise self._failure(CapCutProtocolError, "Phản hồi JSON sai cấu trúc", _diagnostics(200))
        if data.get("ret") not in (None, 0, "0", ""):
            raise self._failure(ProviderUnavailable, "CapCut trả mã ret lỗi; tài khoản, giọng hoặc dịch vụ không khả dụng",
                                _diagnostics(200, data))
        return data

    def _task(self, data: dict) -> dict:
        body = data.get("data")
        tasks = body.get("tasks") if isinstance(body, dict) else None
        if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
            raise self._failure(CapCutProtocolError, "Phản hồi không có đúng một tác vụ giọng nói", _diagnostics(200, data))
        return tasks[0]

    def _poll(self, task_id, token, bind_id, *, deadline, budget):
        details = _diagnostics()
        delays = POLL_DELAYS if self.poll_interval is None else (self.poll_interval,)
        pending_count = 0
        for poll in range(self.max_polls):
            check_cancel(self.cancel)
            if time.monotonic() >= deadline:
                raise self._failure(TTSTimeout, "Hết thời gian chờ kết quả cho câu; tạo giọng nói thất bại",
                                    details, stage="tts_poll")
            self._notify("tts_poll", "Đang chờ kết quả tạo giọng nói", details, poll=poll + 1)
            # Refresh signature/trace headers on every attempt, including retries.
            query = self.build_query_request(task_id, token, bind_id)
            try:
                reply = self._json(query, deadline=deadline, safe_retry=False)
            except (TTSTimeout, _TransientHTTPError) as error:
                if self._retry(error, budget, deadline, stage="tts_poll"):
                    continue
                raise
            details = _diagnostics(200, reply)
            completed = self._task(reply)
            raw_state = completed.get("status")
            state = raw_state.lower() if isinstance(raw_state, str) else None
            if state in _SUCCESS_STATES:
                self._notify("tts_poll", "Tạo giọng nói thành công; chuẩn bị tải âm thanh", details, poll=poll + 1)
                return completed
            if state in _FAILURE_STATES:
                raise self._failure(ProviderUnavailable, "Tác vụ tạo giọng nói thất bại trên máy chủ", details, stage="tts_poll")
            if state not in _PENDING_STATES:
                error = self._failure(CapCutProtocolError, "Trạng thái tác vụ không xác định", details, stage="tts_poll")
                if self._retry(error, budget, deadline, stage="tts_poll"):
                    continue
                raise error
            self._notify("tts_poll", "Máy chủ vẫn đang xử lý câu", details, poll=poll + 1)
            bounded_wait(delays[min(pending_count, len(delays) - 1)], self.cancel, deadline=deadline)
            pending_count += 1
        raise self._failure(TTSTimeout, "Đã hết số lần kiểm tra; tạo giọng nói thất bại", details, stage="tts_poll")

    def synthesize(self, text: str, voice: str | dict, output_path) -> dict:
        """Keep diagnostic redactions scoped to this call, including on failure."""
        previous = getattr(_DIAGNOSTIC_CONTEXT, "secrets", ())
        _DIAGNOSTIC_CONTEXT.secrets = [text, *self.headers.values(),
                                      *(part for value in self.headers.values()
                                        for part in re.findall(r"[A-Za-z0-9_-]+", value))]
        try:
            return self._synthesize(text, voice, output_path)
        finally:
            _DIAGNOSTIC_CONTEXT.secrets = previous

    def _synthesize(self, text: str, voice: str | dict, output_path) -> dict:
        """Write returned artifact bytes, atomically; speed/pitch handled by service."""
        check_cancel(self.cancel)
        deadline = time.monotonic() + self.task_timeout
        request = self.build_tts_request(text, voice)
        _DIAGNOSTIC_CONTEXT.secrets.extend(value for key, value in request.header_items()
                                          if key.lower() in {"sign", "x-ss-stub", "x-tt-trace-id", "tdid"})
        created = self._json(request, deadline=deadline, safe_retry=False)
        task = self._task(created)
        task_id, token = task.get("id"), task.get("token")
        bind_id = task.get("bind_id") or created["data"].get("bind_id") or ""
        _DIAGNOSTIC_CONTEXT.secrets.extend((task_id, token, bind_id))
        budget = [self.retries]
        try:
            completed = self._poll(task_id, token, bind_id, deadline=deadline, budget=budget)
        except TTSCancelled:
            self._notify("tts_poll", "Đã hủy tạo giọng nói; dừng kiểm tra kết quả")
            raise
        raw_payload = completed.get("payload")
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except (ValueError, RecursionError):
            raise CapCutProtocolError("CapCut completed-task payload is invalid") from None
        artifacts = payload.get("audio_subtitles") if isinstance(payload, dict) else None
        if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
            raise CapCutProtocolError("CapCut returned no single speech artifact")
        artifact = artifacts[0]
        url = next((artifact[key] for key in ("speech_url", "audio_url", "url")
                    if isinstance(artifact.get(key), str) and artifact[key]), None)
        if not url:
            raise ProviderUnavailable("CapCut returned no observed download URL; VID-only responses are unsupported")
        _https_url(url, artifact=True)
        # No cookies, authorization, or POST headers are sent to artifact hosts.
        audio = self._bytes(Request(url, headers={"Accept-Encoding": "identity"}), deadline=deadline,
                            limit=self.max_audio_bytes, safe_retry=True, artifact=True, retry_budget=budget)
        if not (audio.startswith((b"ID3", b"fLaC"))
                or (len(audio) >= 2 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0)
                or (audio[:4] == b"RIFF" and audio[8:12] == b"WAVE")):
            raise CapCutProtocolError("CapCut returned no recognized MP3/WAV/FLAC audio (possibly login/error data)")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        import os
        import tempfile
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".capcut-", suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(audio)
                stream.flush()
                os.fsync(stream.fileno())
            check_cancel(self.cancel)
            os.replace(temporary, output)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return {"path": str(output), "provider": PROVIDER, "voice": self._voice(voice)["id"],
                "warnings": []}

    def preview(self, text: str, voice: str | dict, output_path) -> dict:
        return self.synthesize(text, voice, output_path)
