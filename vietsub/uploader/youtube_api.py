"""Optional lightweight YouTube Data API v3 uploader using stdlib + requests.

Implements:
- OAuth2 Installed App (Desktop) Authorization Code flow with PKCE (S256) over
  an ephemeral localhost loopback server (`127.0.0.1:<port>/oauth2callback`).
- DPAPI-encrypted local token & client_secret storage inside each YouTube
  profile directory (`profiles/<uuid>/youtube/youtube_oauth.json`, 0o600).
- Google Cloud `client_secret.json` (`installed`) parser for quick UI setup.
- Chunked Resumable Upload Protocol (8 MiB chunks, multiple of 256 KiB) with
  strict HTTPS hostname allowlist (`www.googleapis.com`, `oauth2.googleapis.com`),
  `allow_redirects=False`, real percent progress, cancel between chunks,
  `before_publish()` checkpoint immediately before the final chunk, automatic
  token refresh, and optional local `ffprobe` vertical Shorts detection.
"""

import base64
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

import requests

from vietsub import redaction, storage
from .adapters import (
    UnsupportedMode,
    UploadCancelled,
    UploadError,
    VerificationRequired,
    validate_platform_metadata,
)
from .profiles import _assert_plain_path, _atomic_json, _open_private

OAUTH_SCHEMA_VERSION = 1
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
ALLOWED_GOOGLE_HOSTS = frozenset({"www.googleapis.com", "oauth2.googleapis.com"})
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB (multiple of 256 KiB)
OAUTH_TIMEOUT_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 60
NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".flv"})


class YouTubeAPIError(UploadError):
    """Sanitized YouTube Data API v3 operational error."""


class YouTubeAuthError(VerificationRequired):
    """OAuth2 credentials are missing, expired, or revoked; user must reconnect."""

    def __init__(self, message, *, detail="oauth_expired"):
        super().__init__(message)
        self.session_detail = detail


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _signalled(flag):
    if flag is None:
        return False
    return bool(flag.is_set()) if hasattr(flag, "is_set") else bool(flag())


def _assert_google_url(url):
    if not isinstance(url, str) or not url:
        raise YouTubeAPIError("URL đích của YouTube API không hợp lệ.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_GOOGLE_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise YouTubeAPIError("Đã chặn kết nối tới miền ngoài Google API cho phép.")
    return url


def validate_client_credentials(client_id, client_secret):
    """Validate Desktop OAuth2 client_id and client_secret strings."""
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise ValueError("Cần nhập đầy đủ Client ID và Client Secret dạng văn bản.")
    cid = client_id.strip()
    csec = client_secret.strip()
    if not (10 <= len(cid) <= 256) or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in cid):
        raise ValueError("Client ID không hợp lệ (không chứa khoảng trắng hay ký tự điều khiển).")
    if not cid.endswith(".apps.googleusercontent.com"):
        raise ValueError("Client ID Google OAuth2 phải có hậu tố .apps.googleusercontent.com.")
    if not (6 <= len(csec) <= 256) or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in csec):
        raise ValueError("Client Secret không hợp lệ (6–256 ký tự, không có khoảng trắng).")
    redaction.register_secret(csec)
    return cid, csec


def parse_client_secret_json(raw_text):
    """Extract (client_id, client_secret) from Google Cloud Desktop client_secret.json."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Nội dung client_secret.json trống.")
    if len(raw_text.encode("utf-8")) > 64 * 1024:
        raise ValueError("Tệp client_secret.json vượt quá giới hạn 64 KiB.")
    try:
        payload = json.loads(raw_text)
    except ValueError:
        raise ValueError("Nội dung client_secret.json không phải JSON hợp lệ.") from None
    if not isinstance(payload, dict):
        raise ValueError("Cấu trúc client_secret.json không hợp lệ.")
    if "installed" in payload and isinstance(payload["installed"], dict):
        block = payload["installed"]
    elif "web" in payload:
        raise ValueError(
            "Tệp JSON là loại 'Web application'. Hãy tạo OAuth Client ID loại 'Desktop app' trên Google Cloud Console."
        )
    else:
        block = payload
    return validate_client_credentials(block.get("client_id", ""), block.get("client_secret", ""))


def _read_oauth_file(oauth_path):
    path = _assert_plain_path(oauth_path, kind="file")
    if not path.exists():
        return None
    descriptor = _open_private(path, os.O_RDONLY)
    try:
        with os.fdopen(descriptor, "rb") as handle:
            raw = handle.read(256 * 1024 + 1)
    except OSError as error:
        raise YouTubeAPIError("Không thể đọc cấu hình YouTube OAuth2 cục bộ.") from error
    if len(raw) > 256 * 1024:
        raise YouTubeAPIError("Tệp YouTube OAuth2 cục bộ vượt quá kích thước cho phép.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise YouTubeAPIError("Tệp YouTube OAuth2 cục bộ bị hỏng.") from error
    if not isinstance(data, dict) or data.get("schema_version") != OAUTH_SCHEMA_VERSION:
        raise YouTubeAPIError("Phiên bản tệp YouTube OAuth2 cục bộ không được hỗ trợ.")
    return data


def _extract_google_error(response):
    status = getattr(response, "status_code", 0)
    reason = ""
    message = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            if isinstance(body.get("error"), dict):
                err = body["error"]
                message = str(err.get("message") or "")
                errors = err.get("errors")
                if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                    reason = str(errors[0].get("reason") or "")
            elif isinstance(body.get("error"), str):
                reason = body["error"]
                message = str(body.get("error_description") or "")
    except Exception:
        pass
    return status, reason, redaction.redact_error(message)[:300]


def _detect_shorts(video_path, ffprobe_resolver=None):
    """Use local ffprobe to check if video qualifies as YouTube Shorts (vertical & <= 180s)."""
    ffprobe = ffprobe_resolver() if callable(ffprobe_resolver) else None
    if not ffprobe:
        return False
    try:
        raw = subprocess.check_output(
            [
                os.fspath(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                os.fspath(video_path),
            ],
            text=True,
            encoding="utf-8",
            timeout=15,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW_FLAGS,
        )
        data = json.loads(raw)
        streams = data.get("streams") or []
        stream = streams[0] if streams and isinstance(streams[0], dict) else {}
        fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = float(fmt.get("duration") or 0)
        return width > 0 and height > width and math.isfinite(duration) and 0 < duration <= 180.0
    except Exception:
        return False


def _parse_range_end(range_header):
    """Parse Google 308 Range header like 'bytes=0-8388607' -> 8388608 (next offset)."""
    if not isinstance(range_header, str):
        return None
    match = re.search(r"bytes=0-(\d+)", range_header)
    if not match:
        return None
    return int(match.group(1)) + 1


class YouTubeAPIClient:
    """Profile-scoped YouTube Data API v3 OAuth2 & Resumable Upload client."""

    def __init__(
        self,
        *,
        protect_secret,
        unprotect_secret,
        ffprobe_resolver=None,
        http_session=None,
        open_browser=None,
        log=None,
    ):
        self.protect_secret = protect_secret
        self.unprotect_secret = unprotect_secret
        self.ffprobe_resolver = ffprobe_resolver
        self._http = http_session or requests
        self._open_browser = open_browser or (lambda url: webbrowser.open(url, new=2))
        self.log = log

    def _log(self, identifier, message):
        if self.log is not None:
            text = f"[Publish Hub · YouTube API] {identifier} · {redaction.redact_error(message)}"
            try:
                self.log(text, "system")
            except TypeError:
                try:
                    self.log(text)
                except Exception:
                    pass
            except Exception:
                pass

    def _decrypt(self, value):
        if not value:
            return ""
        plain = self.unprotect_secret(value)
        if plain:
            redaction.register_secret(plain)
        return plain

    def _encrypt(self, value):
        if not value:
            return ""
        redaction.register_secret(value)
        return self.protect_secret(value)

    def oauth_status(self, oauth_path):
        """Return safe, non-secret status summary for UI/API responses."""
        try:
            data = _read_oauth_file(oauth_path)
        except Exception:
            return {
                "configured": False,
                "connected": False,
                "channel_title": "",
                "token_expires_at": None,
                "scopes": [],
            }
        if not data:
            return {
                "configured": False,
                "connected": False,
                "channel_title": "",
                "token_expires_at": None,
                "scopes": [],
            }
        configured = bool(data.get("client_id") and data.get("client_secret_protected"))
        connected = bool(
            configured
            and (data.get("refresh_token_protected") or data.get("access_token_protected"))
        )
        expires = data.get("token_expires_at")
        return {
            "configured": configured,
            "connected": connected,
            "channel_title": str(data.get("channel_title") or ""),
            "token_expires_at": float(expires) if isinstance(expires, (int, float)) and math.isfinite(expires) else None,
            "scopes": [str(s) for s in data.get("scopes", []) if isinstance(s, str)],
        }

    def save_client_config(self, oauth_path, *, client_id="", client_secret="", client_secret_json=""):
        """Save DPAPI-encrypted Desktop OAuth2 client credentials."""
        if client_secret_json:
            cid, csec = parse_client_secret_json(client_secret_json)
        else:
            cid, csec = validate_client_credentials(client_id, client_secret)
        existing = None
        try:
            existing = _read_oauth_file(oauth_path)
        except Exception:
            existing = None
        keep_tokens = bool(existing and existing.get("client_id") == cid)
        payload = {
            "schema_version": OAUTH_SCHEMA_VERSION,
            "client_id": cid,
            "client_secret_protected": self._encrypt(csec),
            "access_token_protected": existing.get("access_token_protected", "") if keep_tokens else "",
            "refresh_token_protected": existing.get("refresh_token_protected", "") if keep_tokens else "",
            "token_expires_at": existing.get("token_expires_at") if keep_tokens else None,
            "channel_title": existing.get("channel_title", "") if keep_tokens else "",
            "scopes": existing.get("scopes", []) if keep_tokens else [],
            "updated_at": _now_iso(),
        }
        _atomic_json(Path(oauth_path), payload)
        return self.oauth_status(oauth_path)

    def disconnect(self, oauth_path, *, keep_client=False):
        """Remove stored OAuth2 tokens (or entire file if keep_client=False)."""
        path = _assert_plain_path(oauth_path, kind="file")
        if not path.exists():
            return self.oauth_status(oauth_path)
        if keep_client:
            data = _read_oauth_file(path) or {}
            if data.get("client_id") and data.get("client_secret_protected"):
                data.update(
                    access_token_protected="",
                    refresh_token_protected="",
                    token_expires_at=None,
                    channel_title="",
                    scopes=[],
                    updated_at=_now_iso(),
                )
                _atomic_json(path, data)
                return self.oauth_status(path)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return self.oauth_status(oauth_path)

    def _http_request(self, method, url, **kwargs):
        _assert_google_url(url)
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        kwargs["allow_redirects"] = False
        return self._http.request(method, url, **kwargs)

    def _fetch_channel_title(self, access_token):
        try:
            resp = self._http_request(
                "GET",
                YOUTUBE_CHANNELS_URL,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
            if resp.status_code == 200:
                items = (resp.json() or {}).get("items") or []
                if items and isinstance(items[0], dict):
                    return str((items[0].get("snippet") or {}).get("title") or "")[:120]
        except Exception:
            pass
        return ""

    def start_oauth_flow(self, profile_id, oauth_path, *, cancel=None, timeout_seconds=OAUTH_TIMEOUT_SECONDS):
        """Run OAuth2 Desktop PKCE (S256) loopback flow on 127.0.0.1:<ephemeral_port>."""
        data = _read_oauth_file(oauth_path)
        if not data or not data.get("client_id") or not data.get("client_secret_protected"):
            raise ValueError("Hãy lưu Google OAuth Client ID và Client Secret trước khi kết nối.")
        client_id = data["client_id"]
        client_secret = self._decrypt(data["client_secret_protected"])

        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        expected_state = secrets.token_urlsafe(32)
        redaction.register_secret(code_verifier)

        received = threading.Event()
        callback_result = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                # Never log OAuth authorization code or state query params.
                return

            def _send_html(self, status_code, body_text):
                raw = body_text.encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parsed = urlsplit(self.path)
                # Actively ignore auxiliary requests like /favicon.ico without ending wait loop.
                if parsed.path != "/oauth2callback":
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                params = parse_qs(parsed.query, keep_blank_values=False)
                error = (params.get("error") or [""])[0]
                state = (params.get("state") or [""])[0]
                code = (params.get("code") or [""])[0]
                if error:
                    callback_result["error"] = error
                    received.set()
                    self._send_html(
                        200,
                        "<!doctype html><meta charset='utf-8'><title>VietSub Studio</title>"
                        "<h2>Đã huỷ hoặc từ chối cấp quyền YouTube</h2>"
                        "<p>Bạn có thể đóng tab này và quay lại VietSub Studio.</p>",
                    )
                    return
                if not state or not secrets.compare_digest(state, expected_state) or not code:
                    callback_result["error"] = "invalid_state"
                    received.set()
                    self._send_html(
                        400,
                        "<!doctype html><meta charset='utf-8'><title>VietSub Studio</title>"
                        "<h2>Phản hồi xác thực không hợp lệ (state mismatch)</h2>"
                        "<p>Vui lòng đóng tab này và thử lại từ ứng dụng.</p>",
                    )
                    return
                callback_result["code"] = code
                received.set()
                self._send_html(
                    200,
                    "<!doctype html><meta charset='utf-8'><title>VietSub Studio</title>"
                    "<h2>Đã nhận xác thực Google OAuth2 cho VietSub Studio</h2>"
                    "<p>Bạn có thể đóng tab trình duyệt này và quay lại ứng dụng.</p>",
                )

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        server.timeout = 0.25
        try:
            port = server.server_port
            redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
            auth_params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(YOUTUBE_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "state": expected_state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
            auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(auth_params)}"
            self._log(profile_id, "Mở trình duyệt đăng nhập Google OAuth2 (PKCE loopback).")
            self._open_browser(auth_url)

            deadline = time.monotonic() + float(timeout_seconds)
            while not received.is_set():
                if _signalled(cancel):
                    raise UploadCancelled("Đã huỷ kết nối Google OAuth2.")
                if time.monotonic() >= deadline:
                    raise YouTubeAPIError("Hết thời gian chờ đăng nhập Google OAuth2 (5 phút).")
                server.handle_request()
        finally:
            server.server_close()

        if _signalled(cancel):
            raise UploadCancelled("Đã huỷ kết nối Google OAuth2.")
        if callback_result.get("error"):
            err_code = callback_result["error"]
            if err_code == "access_denied":
                raise UploadCancelled("Người dùng đã từ chối cấp quyền Google OAuth2.")
            raise YouTubeAuthError(f"Xác thực Google OAuth2 không hợp lệ ({err_code}).", detail="oauth_revoked")

        auth_code = callback_result.get("code") or ""
        redaction.register_secret(auth_code)
        resp = self._http_request(
            "POST",
            GOOGLE_TOKEN_URL,
            data={
                "code": auth_code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            status, reason, detail = _extract_google_error(resp)
            raise YouTubeAuthError(
                f"Google từ chối đổi mã OAuth2 (HTTP {status}{': ' + reason if reason else ''}). {detail}".strip(),
                detail="oauth_expired",
            )
        token_data = resp.json()
        access_token = str(token_data.get("access_token") or "")
        refresh_token = str(token_data.get("refresh_token") or "")
        if not access_token:
            raise YouTubeAuthError("Phản hồi Google OAuth2 không chứa access_token.", detail="oauth_expired")
        redaction.register_secret(access_token)
        if refresh_token:
            redaction.register_secret(refresh_token)
        else:
            refresh_token = self._decrypt(data.get("refresh_token_protected", ""))

        expires_in = token_data.get("expires_in", 3600)
        try:
            expires_at = time.time() + max(60, int(expires_in))
        except (TypeError, ValueError):
            expires_at = time.time() + 3600

        channel_title = self._fetch_channel_title(access_token)
        granted_scopes = str(token_data.get("scope") or "").split() or list(YOUTUBE_SCOPES)
        updated = {
            "schema_version": OAUTH_SCHEMA_VERSION,
            "client_id": client_id,
            "client_secret_protected": data["client_secret_protected"],
            "access_token_protected": self._encrypt(access_token),
            "refresh_token_protected": self._encrypt(refresh_token) if refresh_token else "",
            "token_expires_at": expires_at,
            "channel_title": channel_title,
            "scopes": granted_scopes,
            "connected_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        _atomic_json(Path(oauth_path), updated)
        self._log(profile_id, "Kết nối YouTube Data API v3 thành công.")
        return self.oauth_status(oauth_path)

    def ensure_access_token(self, oauth_path, *, force_refresh=False):
        """Return a valid access token, refreshing automatically when near expiry."""
        data = _read_oauth_file(oauth_path)
        if not data or not data.get("client_id") or not data.get("client_secret_protected"):
            raise YouTubeAuthError("Chưa cấu hình Google OAuth2 cho profile YouTube này.", detail="oauth_expired")
        access_token = self._decrypt(data.get("access_token_protected", ""))
        refresh_token = self._decrypt(data.get("refresh_token_protected", ""))
        expires_at = data.get("token_expires_at") or 0
        if access_token and not force_refresh and isinstance(expires_at, (int, float)) and expires_at > time.time() + 90:
            return access_token
        if not refresh_token:
            raise YouTubeAuthError(
                "Phiên YouTube Data API v3 đã hết hạn và không có refresh_token. Vui lòng kết nối lại OAuth2.",
                detail="oauth_expired",
            )
        client_id = data["client_id"]
        client_secret = self._decrypt(data["client_secret_protected"])
        resp = self._http_request(
            "POST",
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            status, reason, _detail = _extract_google_error(resp)
            detail_code = "oauth_revoked" if reason == "invalid_grant" else "oauth_expired"
            raise YouTubeAuthError(
                f"Không thể làm mới token YouTube Data API v3 (HTTP {status}{': ' + reason if reason else ''}). "
                "Vui lòng kết nối lại OAuth2.",
                detail=detail_code,
            )
        token_data = resp.json()
        new_access = str(token_data.get("access_token") or "")
        if not new_access:
            raise YouTubeAuthError("Phản hồi làm mới token YouTube không hợp lệ.", detail="oauth_expired")
        redaction.register_secret(new_access)
        new_refresh = str(token_data.get("refresh_token") or "") or refresh_token
        redaction.register_secret(new_refresh)
        expires_in = token_data.get("expires_in", 3600)
        try:
            new_expires_at = time.time() + max(60, int(expires_in))
        except (TypeError, ValueError):
            new_expires_at = time.time() + 3600
        data["access_token_protected"] = self._encrypt(new_access)
        data["refresh_token_protected"] = self._encrypt(new_refresh)
        data["token_expires_at"] = new_expires_at
        data["updated_at"] = _now_iso()
        _atomic_json(Path(oauth_path), data)
        return new_access

    def _emit(self, progress, step, phase, percent):
        if progress is not None:
            progress({"step": step, "phase": phase, "percent": percent})

    def upload(
        self,
        profile_id,
        oauth_path,
        video_path,
        metadata,
        *,
        visibility,
        cancel=None,
        progress=None,
        before_publish=None,
        chunk_size=DEFAULT_CHUNK_SIZE,
    ):
        """Upload video via YouTube Data API v3 Resumable Upload Protocol."""
        if visibility not in ("public", "private"):
            raise UnsupportedMode("YouTube Data API v3 chỉ hỗ trợ chế độ Công khai (public) hoặc Riêng tư (private).")
        title, body, _caption, tags = validate_platform_metadata("youtube", metadata, visibility=visibility)
        if not storage.safe_regular(str(video_path)):
            raise YouTubeAPIError("Tệp video cục bộ không hợp lệ hoặc đã bị xoá.")
        path = Path(video_path).resolve(strict=True)
        if path.suffix.lower() not in _VIDEO_EXTENSIONS:
            raise YouTubeAPIError("Định dạng tệp video không được hỗ trợ.")
        total_size = path.stat().st_size
        if total_size <= 0:
            raise YouTubeAPIError("Tệp video rỗng; không thể tải lên YouTube.")
        if _signalled(cancel):
            raise UploadCancelled("Đã huỷ tải lên YouTube API.")

        self._emit(progress, "open_studio", "uploading", 5)
        access_token = self.ensure_access_token(oauth_path)
        self._emit(progress, "check_session", "uploading", 12)
        if _signalled(cancel):
            raise UploadCancelled("Đã huỷ tải lên YouTube API.")

        is_short = _detect_shorts(path, self.ffprobe_resolver)
        description = body
        if is_short and "#shorts" not in description.casefold():
            description = (description + "\n\n#Shorts").strip()
        snippet = {
            "title": title,
            "description": description,
            "categoryId": "22",
        }
        if tags:
            snippet["tags"] = tags[:30]
        resource = {
            "snippet": snippet,
            "status": {
                "privacyStatus": visibility,
                "selfDeclaredMadeForKids": False,
            },
        }
        self._emit(progress, "fill_metadata", "uploading", 20)

        init_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(total_size),
            "X-Upload-Content-Type": "video/mp4",
        }
        self._emit(progress, "set_visibility", "uploading", 25)
        init_resp = self._http_request(
            "POST",
            YOUTUBE_UPLOAD_INIT_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers=init_headers,
            json=resource,
            timeout=30,
        )
        if init_resp.status_code == 401:
            access_token = self.ensure_access_token(oauth_path, force_refresh=True)
            init_headers["Authorization"] = f"Bearer {access_token}"
            init_resp = self._http_request(
                "POST",
                YOUTUBE_UPLOAD_INIT_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers=init_headers,
                json=resource,
                timeout=30,
            )
        if init_resp.status_code not in (200, 201):
            status, reason, detail = _extract_google_error(init_resp)
            if status == 401:
                raise YouTubeAuthError("Phiên YouTube Data API v3 hết hạn; vui lòng kết nối lại OAuth2.")
            if status == 403 and reason in {"quotaExceeded", "uploadLimitExceeded"}:
                raise YouTubeAPIError(
                    "YouTube Data API v3 đã hết hạn mức quota ngày (mặc định ~10.000 units ≈ 6 video/ngày). "
                    "Hãy thử lại vào ngày mai hoặc chuyển profile sang chế độ Trình duyệt."
                )
            raise YouTubeAPIError(
                f"Khởi tạo tải lên YouTube API thất bại (HTTP {status}{': ' + reason if reason else ''}). {detail}".strip()
            )
        upload_uri = init_resp.headers.get("Location") or init_resp.headers.get("location") or ""
        _assert_google_url(upload_uri)

        effective_chunk = max(256 * 1024, int(chunk_size))
        offset = 0
        final_response = None
        committed = False

        with open(path, "rb") as stream:
            while offset < total_size:
                if _signalled(cancel):
                    raise UploadCancelled(
                        "Đã huỷ sau bước gửi phần cuối; kiểm tra kênh YouTube trước khi thử lại."
                        if committed
                        else "Đã huỷ tải lên YouTube API trước khi hoàn tất."
                    )
                stream.seek(offset)
                chunk = stream.read(effective_chunk)
                if not chunk:
                    raise YouTubeAPIError("Đọc tệp video bị ngắt quãng khi tải lên YouTube API.")
                end = offset + len(chunk)
                is_final_chunk = end >= total_size

                if is_final_chunk:
                    self._emit(progress, "submit", "uploading", 88)
                    if before_publish is not None:
                        before_publish()
                    if _signalled(cancel):
                        raise UploadCancelled("Đã huỷ trước khi gửi chunk cuối lên YouTube.")
                    committed = True

                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Length": str(len(chunk)),
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {offset}-{end - 1}/{total_size}",
                }

                attempts = 1 if is_final_chunk else 3
                resp = None
                for attempt in range(attempts):
                    try:
                        resp = self._http_request(
                            "PUT",
                            upload_uri,
                            headers=headers,
                            data=chunk,
                            timeout=HTTP_TIMEOUT_SECONDS,
                        )
                    except requests.RequestException as error:
                        if is_final_chunk:
                            raise VerificationRequired(
                                "Mất kết nối ở phần tải lên cuối cùng của YouTube API; hãy kiểm tra kênh trước khi thử lại."
                            ) from None
                        if attempt + 1 >= attempts:
                            raise YouTubeAPIError(
                                f"Lỗi mạng khi tải video lên YouTube API: {redaction.redact_error(error)}"
                            ) from None
                        time.sleep(min(2.0 * (attempt + 1), 4.0))
                        continue

                    if resp.status_code == 401 and not is_final_chunk and attempt == 0:
                        access_token = self.ensure_access_token(oauth_path, force_refresh=True)
                        headers["Authorization"] = f"Bearer {access_token}"
                        continue
                    if resp.status_code in (429, 500, 502, 503, 504) and not is_final_chunk and attempt + 1 < attempts:
                        # Query server offset before resuming the next attempt.
                        probe = self._http_request(
                            "PUT",
                            upload_uri,
                            headers={
                                "Authorization": f"Bearer {access_token}",
                                "Content-Length": "0",
                                "Content-Range": f"bytes */{total_size}",
                            },
                            timeout=30,
                        )
                        if probe.status_code == 308:
                            acked = _parse_range_end(probe.headers.get("Range") or probe.headers.get("range"))
                            if acked is not None and acked > offset:
                                offset = acked
                                resp = probe
                                break
                        time.sleep(min(1.5 * (attempt + 1), 4.0))
                        continue
                    break

                if resp is None:
                    raise YouTubeAPIError("Không nhận được phản hồi từ máy chủ YouTube API.")
                if resp.status_code in (200, 201):
                    final_response = resp
                    offset = total_size
                    break
                if resp.status_code == 308:
                    acked = _parse_range_end(resp.headers.get("Range") or resp.headers.get("range"))
                    offset = acked if acked is not None else end
                    ratio = min(1.0, offset / total_size)
                    percent = round(25 + ratio * 60, 1)
                    self._emit(progress, "upload_file", "uploading", min(85.0, percent))
                    continue

                status, reason, detail = _extract_google_error(resp)
                if committed:
                    raise VerificationRequired(
                        f"YouTube API phản hồi bất thường ở phần cuối (HTTP {status}{': ' + reason if reason else ''}); "
                        "hãy kiểm tra kênh trước khi thử lại."
                    )
                if status == 401:
                    raise YouTubeAuthError("Phiên YouTube Data API v3 hết hạn trong lúc tải lên.")
                if status == 403 and reason in {"quotaExceeded", "uploadLimitExceeded"}:
                    raise YouTubeAPIError(
                        "YouTube Data API v3 đã hết hạn mức quota ngày (10.000 units/ngày)."
                    )
                raise YouTubeAPIError(
                    f"Tải video lên YouTube API thất bại (HTTP {status}{': ' + reason if reason else ''}). {detail}".strip()
                )

        if final_response is None:
            raise VerificationRequired("Không nhận được xác nhận hoàn tất từ YouTube Data API v3.")

        self._emit(progress, "confirm", "confirming", 95)
        try:
            body_json = final_response.json()
        except ValueError:
            raise VerificationRequired("Phản hồi xác nhận từ YouTube API không phải JSON hợp lệ.") from None
        video_id = str((body_json or {}).get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise VerificationRequired("YouTube API không trả về mã video hợp lệ; kiểm tra kênh trước khi thử lại.")

        result_url = (
            f"https://www.youtube.com/shorts/{video_id}"
            if is_short
            else f"https://www.youtube.com/watch?v={video_id}"
        )
        self._emit(progress, "confirm", "confirming", 100)
        self._log(profile_id, f"Đã tải lên YouTube Data API v3 thành công ({video_id}).")
        return {
            "status": "success",
            "url": result_url,
            "message": "Đã đăng lên YouTube qua YouTube Data API v3.",
        }
