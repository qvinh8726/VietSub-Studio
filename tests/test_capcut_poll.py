"""CapCut deadline/retry/cancellation tests; fake HTTP only, no service traffic."""
from __future__ import annotations

import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from vietsub.editor import capcut_tts as capcut


VOICE = "BV421_vivn_streaming"
AUDIO = b"ID3" + b"test-audio"


def created():
    return {"ret": 0, "data": {"tasks": [{"id": "task", "token": "private-task-token"}]}}


def result(state="success", **fields):
    return {"ret": 0, "data": {"tasks": [{"status": state, **fields,
            "payload": {"audio_subtitles": [{"speech_url": "https://audio.example.org/speech.mp3"}]}}]}}


class Response(io.BytesIO):
    def __init__(self, value, status=200):
        super().__init__(json.dumps(value).encode() if isinstance(value, (dict, list)) else value)
        self.status = status
        self.closed_event = threading.Event()

    def close(self):
        super().close()
        self.closed_event.set()


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    # Deliberately preserve the old fake signature: no tuple timeout / kwargs.
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item() if callable(item) else item


class BlockingResponse(Response):
    def __init__(self, value):
        super().__init__(value)
        self.entered = threading.Event()
        self.release = threading.Event()

    def read1(self, size):
        self.entered.set()
        self.release.wait(2)
        return super().read1(size)


class CapCutPollTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capcut-poll-")
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "audio.mp3"
        self.events = []

    def client(self, responses, **options):
        transport = Transport(responses)
        options.setdefault("poll_interval", .005)
        options.setdefault("progress", self.events.append)
        client = capcut.CapCutClient(transport=transport, payload_signer=lambda *args: "private-signature", **options)
        return client, transport

    def synthesize(self, client):
        return client.synthesize("Nội dung riêng của người dùng", VOICE, self.output)

    def queries(self, transport):
        return [request for request, _ in transport.calls if urlsplit(request.full_url).path.endswith("/query")]

    def test_hard_caps_clamp_legacy_settings_and_keep_float_timeout(self):
        client, transport = self.client([Response(created()), Response(result()), Response(AUDIO)],
                                        timeout=60, read_timeout=60, task_timeout=600, retries=3)
        self.assertEqual((client.timeout, client.read_timeout, client.task_timeout, client.retries), (8, 15, 20, 1))
        self.synthesize(client)
        self.assertTrue(all(isinstance(timeout, float) and 0 < timeout <= 8 for _, timeout in transport.calls))
        self.assertEqual(self.output.read_bytes(), AUDIO)

    def test_https_socket_switches_from_connect_to_read_deadline(self):
        state = capcut._HTTPState(time.monotonic() + 20, 15)
        connection = capcut._TimedHTTPSConnection("example.org", timeout=8)
        connection.sock = Mock()
        capcut._HTTP_CONTEXT.state = state
        try:
            with patch("http.client.HTTPSConnection.connect"):
                connection.connect()
        finally:
            del capcut._HTTP_CONTEXT.state
        self.assertEqual(connection.sock.settimeout.call_args.args, (15,))
        self.assertIs(state.socket, connection.sock)
        state.abort()
        connection.sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    def test_running_poll_expires_within_sentence_budget(self):
        client, transport = self.client([Response(created())] + [Response(result("running")) for _ in range(100)],
                                        task_timeout=.09)
        start = time.monotonic()
        with self.assertRaises(capcut.TTSTimeout):
            self.synthesize(client)
        self.assertLess(time.monotonic() - start, .5)
        self.assertLess(len(self.queries(transport)), 100)
        self.assertFalse(self.output.exists())
        self.assertIn("thất bại", self.events[-1]["message"])
        self.assertIn("status=running", self.events[-1]["message"])

    def test_blocking_open_obeys_wall_clock_and_closes_late_response(self):
        release = threading.Event()
        late = Response(result("running"))

        def blocked():
            release.wait(2)
            return late

        client, transport = self.client([Response(created()), blocked], timeout=.06, task_timeout=.08)
        start = time.monotonic()
        try:
            with self.assertRaises(capcut.TTSTimeout):
                self.synthesize(client)
            self.assertLess(time.monotonic() - start, .5)
            self.assertEqual(len(self.queries(transport)), 1)
            self.assertFalse(self.output.exists())
            event_count = len(self.events)
        finally:
            release.set()
            self.assertTrue(late.closed_event.wait(.5))
        self.assertEqual(len(self.events), event_count)

    def test_blocking_body_has_independent_read_deadline(self):
        blocked = BlockingResponse(result())
        client, _ = self.client([Response(created()), blocked], read_timeout=.06, task_timeout=1, retries=0)
        start = time.monotonic()
        try:
            with self.assertRaises(capcut.TTSTimeout) as error:
                self.synthesize(client)
            self.assertLess(time.monotonic() - start, .5)
            self.assertIn("HTTP 200", str(error.exception))
            self.assertFalse(self.output.exists())
        finally:
            blocked.release.set()
            self.assertTrue(blocked.closed_event.wait(.5))

    def test_slow_drip_body_cannot_extend_deadline(self):
        class Drip(Response):
            def read1(self, size):
                time.sleep(.01)
                return b" "

        client, _ = self.client([Response(created()), Drip(b"")], read_timeout=.06, task_timeout=1, retries=0)
        start = time.monotonic()
        with self.assertRaises(capcut.TTSTimeout):
            self.synthesize(client)
        self.assertLess(time.monotonic() - start, .5)
        self.assertFalse(self.output.exists())

    def test_unknown_state_retries_once_then_fails_with_safe_codes(self):
        for state in ("unknown", "new_untrusted_state", None, 17, {"token": "private-task-token"}):
            with self.subTest(state=state):
                self.events.clear()
                reply = result(state, code=901)
                client, transport = self.client([Response(created()), Response(reply), Response(reply)], retries=3)
                with patch.object(capcut, "bounded_wait"), self.assertRaises(capcut.CapCutProtocolError) as error:
                    self.synthesize(client)
                self.assertEqual(len(self.queries(transport)), 2)
                self.assertEqual(sum(event.get("retry", 0) for event in self.events), 1)
                self.assertIn("HTTP 200; ret=0; code=901", str(error.exception))
                self.assertFalse(self.output.exists())
                self.assertNotIn("private-task-token", json.dumps(self.events))

    def test_unknown_state_can_recover_on_the_single_retry(self):
        client, transport = self.client([Response(created()), Response(result("unknown")), Response(result()), Response(AUDIO)])
        with patch.object(capcut, "bounded_wait"):
            self.synthesize(client)
        self.assertEqual(len(self.queries(transport)), 2)
        self.assertEqual(self.output.read_bytes(), AUDIO)

    def test_all_5xx_429_and_timeout_retry_only_once(self):
        cases = [429, 500, 501, 502, 503, 504, 599, "timeout", "urltimeout"]
        for status in cases:
            for raised in (False, True) if isinstance(status, int) else (False,):
                with self.subTest(status=status, raised=raised):
                    self.events.clear()
                    def failure():
                        if status == "timeout":
                            return TimeoutError("private-secret")
                        if status == "urltimeout":
                            return URLError(TimeoutError("private-secret"))
                        body = {"ret": 1024, "code": 87, "status": "error", "errmsg": "private-secret"}
                        return (HTTPError("https://example.org/private-secret", status, "private-secret", {}, Response(body))
                                if raised else Response(body, status))
                    client, transport = self.client([Response(created()), failure(), failure()], retries=3)
                    with patch.object(capcut, "bounded_wait"), self.assertRaises(capcut.TTSError) as error:
                        self.synthesize(client)
                    self.assertEqual(len(self.queries(transport)), 2)
                    self.assertEqual(sum(event.get("retry", 0) for event in self.events), 1)
                    self.assertNotIn("private-secret", json.dumps(self.events) + str(error.exception))
                    if isinstance(status, int):
                        self.assertIn(f"HTTP {status}; ret=1024; code=87; status=error", str(error.exception))

    def test_retry_is_shared_between_http_unknown_and_download(self):
        cases = [
            [Response({}, 503), Response(result("unknown"))],
            [Response(result("unknown")), Response({}, 429)],
            [Response({}, 503), Response(result("running")), Response({}, 503)],
            [Response(result("unknown")), Response(result()), Response({}, 503)],
        ]
        for replies in cases:
            with self.subTest(length=len(replies)):
                self.events.clear()
                client, transport = self.client([Response(created()), *replies], retries=3)
                with patch.object(capcut, "bounded_wait"), self.assertRaises(capcut.TTSError):
                    self.synthesize(client)
                self.assertEqual(len(transport.calls), len(replies) + 1)
                self.assertEqual(sum(event.get("retry", 0) for event in self.events), 1)

    def test_retry_wait_and_second_http_share_original_deadline(self):
        release = threading.Event()
        late = Response(result())
        def blocked():
            release.wait(2)
            return late
        client, transport = self.client([Response(created()), Response({}, 429), blocked], task_timeout=.32)
        start = time.monotonic()
        try:
            with self.assertRaises(capcut.TTSTimeout):
                self.synthesize(client)
            self.assertLess(time.monotonic() - start, .65)
            self.assertEqual(len(self.queries(transport)), 2)
            self.assertLess(transport.calls[-1][1], .1)
        finally:
            release.set()
            self.assertTrue(late.closed_event.wait(.5))

    def test_creation_is_never_retried_even_on_retryable_failure(self):
        for failure in (TimeoutError("private-secret"), Response({}, 429), Response({}, 503)):
            with self.subTest(failure=type(failure).__name__):
                client, transport = self.client([failure, Response(created())])
                with self.assertRaises(capcut.TTSError):
                    self.synthesize(client)
                self.assertEqual(len(transport.calls), 1)

    def test_permanent_http_and_failed_task_do_not_retry(self):
        for reply in (Response({}, 400), Response({}, 401), Response({}, 403), Response({}, 404),
                      Response(result("failed", code=701)), Response({"ret": 1014, "code": 18})):
            with self.subTest(reply=reply):
                self.events.clear()
                client, transport = self.client([Response(created()), reply])
                with self.assertRaises(capcut.TTSError):
                    self.synthesize(client)
                self.assertEqual(len(self.queries(transport)), 1)
                self.assertFalse(any(event.get("retry") for event in self.events))

    def test_diagnostics_never_echo_credentials_text_or_arbitrary_raw_strings(self):
        secrets = ("private-task-token", "private-signature", "Bearer private-header", "Nội dung riêng của người dùng")
        reply = {"ret": secrets[0], "code": {"secret": secrets[1]}, "status": secrets[3], "errmsg": secrets[2]}
        client, _ = self.client([Response(reply)], headers={"Authorization": secrets[2]})
        with self.assertRaises(capcut.TTSError) as error:
            self.synthesize(client)
        emitted = json.dumps(self.events, ensure_ascii=False) + str(error.exception)
        for secret in secrets:
            self.assertNotIn(secret, emitted)
        self.assertIn("ret=[đã ẩn]; code=[đã ẩn]; status=[đã ẩn]", emitted)
        self.assertTrue(all("message" in event and event["stage"] in {"tts_http", "tts_poll"} for event in self.events))

    def test_unknown_status_and_error_identifiers_remain_visible(self):
        client, _ = self.client([Response(created()), Response(result("unexpected_state", code="FUTURE_ERROR-42"))], retries=0)
        with self.assertRaises(capcut.CapCutProtocolError) as error:
            self.synthesize(client)
        self.assertIn("code=FUTURE_ERROR-42; status=unexpected_state", str(error.exception))
        self.assertTrue(any(event["status"] == "unexpected_state" for event in self.events))
        for value in ("x" * 49, "https://example.org/token", "two words", "bad\nstatus", "trạng_thái"):
            self.assertEqual(capcut._safe_code(value, identifier=True), "[đã ẩn]")
        self.assertEqual(capcut._safe_code("short_state-17", identifier=True), "short_state-17")

    def test_known_credential_and_speech_identifiers_are_redacted(self):
        for secret in ("private-task-token", "private-signature", "headerCredential123", "cookieCredential123", "speech_identifier"):
            with self.subTest(secret=secret):
                self.events.clear()
                client, _ = self.client([Response(created()), Response(result(secret, code=secret))], retries=0,
                                        headers={"Authorization": "Bearer headerCredential123", "Cookie": "session=cookieCredential123"})
                with self.assertRaises(capcut.CapCutProtocolError) as error:
                    client.synthesize("speech_identifier", VOICE, self.output)
                self.assertNotIn(secret, str(error.exception) + json.dumps(self.events))
                self.assertIn("code=[đã ẩn]; status=[đã ẩn]", str(error.exception))
                self.assertEqual(capcut._DIAGNOSTIC_CONTEXT.secrets, ())

    def test_bad_http_status_line_and_transport_errors_are_sanitized(self):
        for failure in (http.client.BadStatusLine("private-secret"), ValueError("private-secret")):
            with self.subTest(failure=type(failure).__name__):
                self.events.clear()
                client, _ = self.client([failure])
                with self.assertRaises(capcut.TTSError) as error:
                    self.synthesize(client)
                self.assertNotIn("private-secret", str(error.exception) + json.dumps(self.events))

    def test_abandoned_transports_cannot_accumulate_unbounded_threads(self):
        release = threading.Event()
        late_responses = [Response(result()), Response(result())]
        def blocked(response):
            def open_late():
                release.wait(2)
                return response
            return open_late
        client, transport = self.client([Response(created()), *[blocked(item) for item in late_responses]],
                                        timeout=.04, task_timeout=1)
        try:
            with patch.object(capcut, "bounded_wait"), self.assertRaises(capcut.TTSTimeout):
                self.synthesize(client)
            self.assertEqual(len(self.queries(transport)), 2)
            with self.assertRaises(capcut.ProviderUnavailable) as error:
                self.synthesize(client)
            self.assertIn("tránh tích lũy", str(error.exception))
            self.assertEqual(len(transport.calls), 3)
        finally:
            release.set()
            for response in late_responses:
                self.assertTrue(response.closed_event.wait(.5))

    def test_cancel_interrupts_open_body_poll_wait_and_retry_wait(self):
        for phase in ("open", "body", "poll_wait", "retry_wait"):
            with self.subTest(phase=phase):
                self.events.clear()
                cancel, entered, release = threading.Event(), threading.Event(), threading.Event()
                blocked_body = BlockingResponse(result())
                late = Response(result())
                def blocked_open():
                    entered.set()
                    release.wait(2)
                    return late
                def progress(event):
                    self.events.append(event)
                    if phase == "poll_wait" and "vẫn đang xử lý" in event["message"]:
                        entered.set()
                    if phase == "retry_wait" and event.get("retry"):
                        entered.set()
                reply = {"open": blocked_open, "body": blocked_body,
                         "poll_wait": Response(result("running")), "retry_wait": Response({}, 503)}[phase]
                client, transport = self.client([Response(created()), reply], cancel=cancel, progress=progress, poll_interval=10)
                errors = []
                def run():
                    try:
                        self.synthesize(client)
                    except Exception as error:
                        errors.append(error)
                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                try:
                    self.assertTrue((blocked_body.entered if phase == "body" else entered).wait(1))
                    start = time.monotonic()
                    cancel.set()
                    thread.join(.5)
                    self.assertFalse(thread.is_alive(), "Cancellation must not await the socket timeout")
                    self.assertLess(time.monotonic() - start, .5)
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], capcut.TTSCancelled)
                    self.assertFalse(self.output.exists())
                    self.assertEqual(len(self.queries(transport)), 1)
                finally:
                    cancel.set()
                    release.set()
                    blocked_body.release.set()
                    thread.join(2)
                    if phase == "open":
                        self.assertTrue(late.closed_event.wait(.5))
                    if phase == "body":
                        self.assertTrue(blocked_body.closed_event.wait(.5))


if __name__ == "__main__":
    unittest.main()
