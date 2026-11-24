"""Centralized runtime secret and sensitive path redaction for logs and API errors."""

import os
import re
import threading
import urllib.parse

_LOCK = threading.RLock()
_MAX_SECRETS = 512
_PINNED_SECRETS = []
_SECRETS = []
_PATH_ROOTS = {}

_KEY_PATTERNS = (
    re.compile(r"\bsk-(?:proj-|or-v1-|live-|test-)?[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_\-.]{16,}\b"),
    re.compile(r"(?<![A-Za-z0-9_])1//[A-Za-z0-9_\-.]{16,}\b"),
)
_AUTH_HEADER_PATTERN = re.compile(
    r"(?i)(\bAuthorization\s*:\s*Bearer\s+)[^\s,;\"'<>]+"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9\-._~+/]{8,}=*")
_TOKEN_HEADER_PATTERN = re.compile(
    r"(?i)(\bX-VietSub-Token\s*[:=]\s*)[^\s,;\"'<>]+"
)
_COOKIE_HEADER_PATTERN = re.compile(
    r"(?im)(\b(?:Set-)?Cookie\s*:\s*)(?:"
    r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+=[^\s;\r\n\"']*"
    r"(?:\s*;\s*(?:[!#$%&'*+\-.^_`|~0-9A-Za-z]+=[^\s;\r\n\"']*|HttpOnly|Secure|Partitioned))*;?"
    r"|[^\s;\r\n\"']+)"
)
_SENSITIVE_PARAM_PATTERN = re.compile(
    r"(?i)(\b(?:api_key|access_token|refresh_token|client_secret|code_verifier|upload_id|"
    r"passToken|sessionid_ss|sessionid|sid_tt|SESSDATA|bili_jct|DedeUserID|web_session|c_user|xs)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s&;,\"'\}]+)"
)
_URL_SIGNATURE_PATTERN = re.compile(r"(?i)([?&]signature=)[^&\s\"'<>]+")
_ENCRYPTED_BLOB_PATTERN = re.compile(r"\b(?:dpapi|plain):[A-Za-z0-9+/=]{16,}")
_MEI_WIN_PATTERN = re.compile(
    r"(?i)(?:[A-Za-z]:)?(?:[\\/][^\\/\r\n]*)*?[\\/]Temp[\\/]_MEI[A-Za-z0-9_.-]+"
)
_MEI_POSIX_PATTERN = re.compile(r"/tmp/_MEI[A-Za-z0-9_.-]+")
_WIN_USER_BS_PATTERN = re.compile(
    r"(?i)[A-Za-z]:\\Users\\(?:[^\\/\r\n:'\",;<>|?*]{1,104}(?=[\\/])|[^\\/\s:'\",;<>|\r\n]+)"
)
_WIN_USER_FS_PATTERN = re.compile(
    r"(?i)[A-Za-z]:/Users/(?:[^\\/\r\n:'\",;<>|?*]{1,104}(?=[\\/])|[^\\/\s:'\",;<>|\r\n]+)"
)
_POSIX_USER_PATTERN = re.compile(
    r"(?:(?<=^)|(?<=[\s:'\"(,=]))/(?:Users|home)/[^/\s:'\",;<>|\r\n]+"
)


def register_secret(value, *, pinned=False):
    """Register a runtime secret so future logs and API errors scrub it.

    System-critical secrets (such as ``API_TOKEN``) can pass ``pinned=True`` so
    FIFO rotation at ``_MAX_SECRETS`` never evicts them.
    """
    if not isinstance(value, str):
        return
    candidates = [value]
    if len(value) >= 8:
        quoted = urllib.parse.quote(value, safe="")
        if quoted != value and len(quoted) >= 8:
            candidates.append(quoted)
    with _LOCK:
        for candidate in candidates:
            if len(candidate) < 8:
                continue
            if pinned:
                if candidate not in _PINNED_SECRETS:
                    _PINNED_SECRETS.append(candidate)
                if candidate in _SECRETS:
                    _SECRETS.remove(candidate)
                continue
            if candidate in _PINNED_SECRETS or candidate in _SECRETS:
                continue
            _SECRETS.append(candidate)
            if len(_SECRETS) > _MAX_SECRETS:
                del _SECRETS[: len(_SECRETS) - _MAX_SECRETS]


def clear_secrets(*, include_pinned=False):
    """Clear registered secrets (primarily for isolated tests)."""
    with _LOCK:
        _SECRETS.clear()
        if include_pinned:
            _PINNED_SECRETS.clear()


def register_path_root(path, placeholder):
    """Register a directory root to mask when redacting error/diagnostic paths."""
    if not isinstance(path, str) or not path.strip():
        return
    normalized = os.path.normpath(os.path.abspath(path.strip()))
    with _LOCK:
        _PATH_ROOTS[normalized] = placeholder


def _mask_registered_paths(text):
    with _LOCK:
        roots = sorted(_PATH_ROOTS.items(), key=lambda item: len(item[0]), reverse=True)
    for root, placeholder in roots:
        variants = {root, root.replace("\\", "/")}
        for variant in variants:
            if not variant:
                continue
            escaped = re.escape(variant)
            flags = re.IGNORECASE if os.name == "nt" else 0
            text = re.sub(rf"{escaped}(?=$|[\\/\s:'\",;<>|)\]])", placeholder, text, flags=flags)
    return text


def redact_text(value, *, redact_paths=False, extra_secrets=()):
    """Scrub secrets (and optionally user/runtime filesystem paths) from text."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""

    with _LOCK:
        secrets_snapshot = list(_PINNED_SECRETS) + list(_SECRETS)
    for item in extra_secrets:
        if isinstance(item, str) and len(item) >= 8:
            secrets_snapshot.append(item)
            quoted = urllib.parse.quote(item, safe="")
            if quoted != item and len(quoted) >= 8:
                secrets_snapshot.append(quoted)

    for secret in sorted(set(secrets_snapshot), key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "[REDACTED]")

    for pattern in _KEY_PATTERNS:
        text = pattern.sub("[REDACTED_KEY]", text)
    text = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _TOKEN_HEADER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _COOKIE_HEADER_PATTERN.sub(r"\1[REDACTED_COOKIE]", text)
    text = _SENSITIVE_PARAM_PATTERN.sub(r"\1[REDACTED]", text)
    text = _URL_SIGNATURE_PATTERN.sub(r"\1[REDACTED]", text)
    text = _ENCRYPTED_BLOB_PATTERN.sub("[REDACTED_SECRET]", text)

    if redact_paths:
        text = _mask_registered_paths(text)
        text = _MEI_WIN_PATTERN.sub("<APP_RUNTIME>", text)
        text = _MEI_POSIX_PATTERN.sub("<APP_RUNTIME>", text)
        text = _WIN_USER_BS_PATTERN.sub("<USER_HOME>", text)
        text = _WIN_USER_FS_PATTERN.sub("<USER_HOME>", text)
        text = _POSIX_USER_PATTERN.sub("<USER_HOME>", text)

    return text


def redact_error(error, *, extra_secrets=()):
    """Scrub secrets and sensitive local paths from exception/diagnostic text."""
    return redact_text(error, redact_paths=True, extra_secrets=extra_secrets)
