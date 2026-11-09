"""OpenAI-compatible chat-completions client used for subtitle translation."""

import json
import time
import urllib.error
import urllib.request

from vietsub.constants import (
    APP_VERSION,
    GITHUB_REPOSITORY,
    TRANSLATION_API_MAX_RESPONSE_BYTES,
    TRANSLATION_API_TIMEOUT_SECONDS,
)

# 4xx statuses that can succeed on a later attempt; every other 4xx means the
# request itself (key, model, base URL, payload) is wrong and must not be retried.
RETRYABLE_CLIENT_STATUSES = frozenset({408, 409, 425, 429})


class TranslationApiError(RuntimeError):
    """A translation API failure that carries an explicit retry decision."""

    def __init__(self, message, *, status=None, retryable=True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def is_retryable_http_status(status):
    return status >= 500 or status in RETRYABLE_CLIENT_STATUSES


def chat_completions_url(base_url):
    normalized = base_url.rstrip("/")
    if normalized.lower().endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _content_text(value):
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict) and item.get("text")
        )
    return value


def _truncated_output_error():
    return TranslationApiError(
        "API đã cắt câu trả lời vì chạm giới hạn output token.",
        retryable=False,
    )


def extract_chat_completion_content(payload):
    if not isinstance(payload, dict):
        raise TranslationApiError("API dịch trả về JSON không hợp lệ.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TranslationApiError("API dịch không trả về lựa chọn nào.")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise TranslationApiError("API dịch trả về lựa chọn không hợp lệ.")
    if choice.get("finish_reason") == "length":
        raise _truncated_output_error()
    message = choice.get("message")
    content = _content_text(message.get("content") if isinstance(message, dict) else choice.get("text"))
    if not isinstance(content, str) or not content.strip():
        raise TranslationApiError("API dịch trả về nội dung trống.")
    return content.strip()


def read_streaming_chat_completion(response, *, check_cancel=None):
    content_parts = []
    total_bytes = 0
    deadline = time.monotonic() + TRANSLATION_API_TIMEOUT_SECONDS * 2
    for raw_line in iter(response.readline, b""):
        if check_cancel:
            check_cancel()
        if time.monotonic() >= deadline:
            raise TranslationApiError("Phản hồi streaming từ API dịch vượt quá thời gian chờ tối đa.")
        if isinstance(raw_line, bytes):
            total_bytes += len(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
        else:
            raw_str = str(raw_line)
            total_bytes += len(raw_str.encode("utf-8", errors="replace"))
            line = raw_str.strip()
        if total_bytes > TRANSLATION_API_MAX_RESPONSE_BYTES:
            raise TranslationApiError("Phản hồi API dịch vượt quá giới hạn an toàn 16 MiB.")
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("error"):
            detail = payload["error"]
            if isinstance(detail, dict):
                detail = detail.get("message") or detail.get("code") or str(detail)
            raise TranslationApiError(f"API dịch trả lỗi streaming: {str(detail)[:500]}")
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("finish_reason") == "length":
            raise _truncated_output_error()
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        piece = delta.get("content")
        if piece is None:
            message = choice.get("message")
            piece = message.get("content") if isinstance(message, dict) else choice.get("text")
        piece = _content_text(piece)
        if isinstance(piece, str):
            content_parts.append(piece)
    content = "".join(content_parts).strip()
    if not content:
        raise TranslationApiError("API dịch streaming không trả về nội dung.")
    return content


def _http_error_detail(error):
    raw_error = error.read(128 * 1024)
    try:
        error_payload = json.loads(raw_error.decode("utf-8", errors="replace"))
        detail = error_payload.get("error", error_payload) if isinstance(error_payload, dict) else error_payload
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("code") or str(detail)
        return str(detail)
    except (json.JSONDecodeError, UnicodeError):
        return raw_error.decode("utf-8", errors="replace").strip()


def call_openai_compatible_api(
    runtime_config,
    messages,
    *,
    max_tokens=16_000,
    stream=False,
    check_cancel=None,
):
    body = {
        "model": runtime_config["model"],
        "messages": messages,
        "temperature": 0.1,
        "stream": bool(stream),
        "max_tokens": max_tokens,
    }
    if runtime_config.get("provider") == "deepseek":
        # DeepSeek enables reasoning by default; disable it so the output budget is
        # spent on the translated SRT instead of hidden chain-of-thought tokens.
        body["thinking"] = {"type": "disabled"}
        body["response_format"] = {"type": "text"}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"VietSub-Studio/{APP_VERSION}",
    }
    if runtime_config.get("api_key"):
        headers["Authorization"] = f"Bearer {runtime_config['api_key']}"
    if runtime_config.get("provider") == "openrouter":
        headers["HTTP-Referer"] = f"https://github.com/{GITHUB_REPOSITORY}"
        headers["X-Title"] = "VietSub Studio"
    request_object = urllib.request.Request(
        chat_completions_url(runtime_config["base_url"]),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request_object,
            timeout=TRANSLATION_API_TIMEOUT_SECONDS,
        ) as response:
            content_type = ""
            response_headers = getattr(response, "headers", None)
            if response_headers is not None:
                content_type = str(response_headers.get("Content-Type", "")).lower()
            if stream and "text/event-stream" in content_type:
                return read_streaming_chat_completion(response, check_cancel=check_cancel)
            raw = response.read(TRANSLATION_API_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        detail = _http_error_detail(error)
        raise TranslationApiError(
            f"API dịch trả lỗi HTTP {error.code}: {detail[:500] or error.reason}",
            status=error.code,
            retryable=is_retryable_http_status(error.code),
        ) from error
    except urllib.error.URLError as error:
        raise TranslationApiError(f"Không kết nối được API dịch: {error.reason}") from error
    if len(raw) > TRANSLATION_API_MAX_RESPONSE_BYTES:
        raise TranslationApiError("Phản hồi API dịch vượt quá giới hạn an toàn 16 MiB.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise TranslationApiError("API dịch không trả về JSON UTF-8 hợp lệ.") from error
    return extract_chat_completion_content(payload)
