"""Dependency-free CapCut common-task HTTP adapter; no browser/session scraping.

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

from functools import lru_cache
import hashlib
import http.client
import ssl
import ipaddress
import json
import math
import secrets
import socket
import time
import uuid
from pathlib import Path
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
            cancel.wait(min(remaining, 0.1))
        else:
            time.sleep(min(remaining, 0.1))


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
                return
            except OSError as error:
                last_error = error
                sock.close()
        raise last_error or OSError("No public artifact address connected")


class _PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class CapCutClient:
    """Bounded, mockable urllib client for the observed CapCut common-task API.

    ``transport`` can be an object exposing ``open(Request, timeout=...)``; tests
    use a fake transport. No request is made during import, construction, voices,
    or availability. Retries apply only to safe polling/download requests, never
    task creation (to avoid duplicate billable tasks after ambiguous failures).
    Socket cancellation latency is bounded by ``timeout`` (default 8 seconds).
    """
    provider = PROVIDER

    def __init__(self, *, payload_signer=None, headers=None, device=None, transport=None,
                 timeout: float = 8.0, task_timeout: float = 90.0, retries: int = 2,
                 poll_interval: float = 1.0, max_polls: int = 120, cancel=None,
                 progress=None, max_audio_bytes: int = 32 * 1024 * 1024):
        for value, name, upper in ((timeout, "timeout", 60), (task_timeout, "task_timeout", 600),
                                   (poll_interval, "poll_interval", 30)):
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
        self.transport = transport or build_opener(_NoRedirect())
        self.artifact_transport = transport or build_opener(ProxyHandler({}), _NoRedirect(), _PublicHTTPSHandler())
        self.timeout, self.task_timeout = float(timeout), float(task_timeout)
        self.retries, self.poll_interval, self.max_polls = retries, float(poll_interval), max_polls
        self.cancel, self.progress, self.max_audio_bytes = cancel, progress, max_audio_bytes

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
            signature = self.payload_signer(ssml, extra, dict(self.device))
        except TTSCancelled:
            raise
        except Exception:
            raise ProviderUnavailable("Configured CapCut payload signer failed") from None
        if not isinstance(signature, str) or not signature or len(signature) > 16384:
            raise ProviderUnavailable("Configured CapCut payload signer returned no valid signature")
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

    def _bytes(self, request: Request, *, deadline: float, limit: int, safe_retry: bool, artifact=False) -> bytes:
        attempts = 1 + (self.retries if safe_retry else 0)
        for attempt in range(attempts):
            check_cancel(self.cancel)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TTSTimeout("CapCut task deadline exceeded")
            response = None
            try:
                transport = self.artifact_transport if artifact else self.transport
                response = transport.open(request, timeout=min(self.timeout, remaining))
                status = getattr(response, "status", 200)
                if status in (401, 403):
                    raise VerificationRequired("CapCut requires login/verification; complete it manually. No challenge bypass is attempted.")
                if status != 200:
                    if status in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                        bounded_wait(min(2 ** attempt * 0.25, 2), self.cancel, deadline=deadline)
                        continue
                    raise ProviderUnavailable(f"CapCut returned HTTP {status}; service unavailable")
                chunks, size = [], 0
                while True:
                    check_cancel(self.cancel)
                    if time.monotonic() >= deadline:
                        raise TTSTimeout("CapCut response exceeded the task deadline")
                    # HTTPResponse.read(n) may wait for n bytes under a slow-drip
                    # server; read1 performs a single buffered/socket read so the
                    # wall-clock/cancel checks above cannot be starved indefinitely.
                    reader = getattr(response, "read1", response.read)
                    chunk = reader(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise CapCutProtocolError("CapCut response exceeds configured size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if status in (401, 403):
                    raise VerificationRequired("CapCut requires login/verification; complete it manually. No challenge bypass is attempted.") from None
                if status not in (429, 500, 502, 503, 504) or attempt + 1 >= attempts:
                    raise ProviderUnavailable(f"CapCut returned HTTP {status}; service unavailable") from None
            except (URLError, OSError, socket.timeout, TimeoutError):
                if attempt + 1 >= attempts:
                    raise ProviderUnavailable("CapCut network request failed or timed out") from None
            finally:
                if response is not None:
                    response.close()
            bounded_wait(min(2 ** attempt * 0.25, 2), self.cancel, deadline=deadline)
        raise ProviderUnavailable("CapCut retries exhausted")

    def _json(self, request: Request, *, deadline: float, safe_retry: bool) -> dict:
        raw = self._bytes(request, deadline=deadline, limit=2 * 1024 * 1024, safe_retry=safe_retry)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise CapCutProtocolError("CapCut returned invalid JSON (possibly a login/challenge page)") from None
        if not isinstance(data, dict):
            raise CapCutProtocolError("CapCut response must be an object")
        if data.get("ret") not in (None, 0, "0", ""):
            # Do not echo errmsg/response: it may contain user text or credentials.
            raise ProviderUnavailable("CapCut rejected the speech task (nonzero ret); account, voice, or service may be unavailable")
        return data

    @staticmethod
    def _task(data: dict) -> dict:
        body = data.get("data")
        tasks = body.get("tasks") if isinstance(body, dict) else None
        if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
            raise CapCutProtocolError("CapCut response must contain exactly one speech task")
        return tasks[0]

    def synthesize(self, text: str, voice: str | dict, output_path) -> dict:
        """Write returned artifact bytes, atomically; speed/pitch handled by service."""
        check_cancel(self.cancel)
        request = self.build_tts_request(text, voice)
        deadline = time.monotonic() + self.task_timeout
        created = self._json(request, deadline=deadline, safe_retry=False)
        task = self._task(created)
        task_id, token = task.get("id"), task.get("token")
        bind_id = task.get("bind_id") or created["data"].get("bind_id") or ""
        completed = None
        for poll in range(self.max_polls):
            check_cancel(self.cancel)
            if time.monotonic() >= deadline:
                raise TTSTimeout("CapCut task timed out")
            if self.progress:
                self.progress({"stage": "tts_poll", "provider": PROVIDER, "poll": poll + 1})
            # Timestamp/sign/trace headers are refreshed for every poll, matching
            # upstream query_tts_task rather than replaying a stale signed request.
            query = self.build_query_request(task_id, token, bind_id)
            completed = self._task(self._json(query, deadline=deadline, safe_retry=True))
            state = completed.get("status")
            if not isinstance(state, str):
                raise CapCutProtocolError("CapCut task is missing a string status")
            state = state.lower()
            if state in {"success", "succeed", "succeeded", "completed", "done"}:
                break
            if state in {"failed", "failure", "error", "cancelled", "canceled"}:
                raise ProviderUnavailable("CapCut speech task failed")
            if state not in {"pending", "queueing", "queued", "running", "processing", "created", "waiting"}:
                raise CapCutProtocolError("CapCut returned an unsupported task state")
            bounded_wait(self.poll_interval, self.cancel, deadline=deadline)
        else:
            raise TTSTimeout("CapCut task exceeded the bounded poll count")
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
                            limit=self.max_audio_bytes, safe_retry=True, artifact=True)
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
