"""Pool/fork regressions using fake sockets/transports only; no live services."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

from vietsub.editor import capcut_tts as capcut
from tests.test_capcut_poll import AUDIO, VOICE, Response, Transport, created, result


class FakeSocket:
    """HTTP framing exercises the real requests/urllib3 pool without TCP/TLS."""
    def __init__(self, server):
        self.server = server
        self.sent = b""
        self.timeout = None
        self.shutdown_event = threading.Event()
        self.shutdown_count = 0
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return self.timeout

    def sendall(self, data):
        self.sent += bytes(data)

    def makefile(self, *args, **kwargs):
        request, self.sent = self.sent, b""
        self.server.requests.append(request)
        status, body, extra = self.server.reply(request)
        raw = json.dumps(body).encode()
        return self.server.stream(f"HTTP/1.1 {status} Test\r\nContent-Length: {len(raw)}\r\n{extra}\r\n".encode() + raw, self)

    def shutdown(self, how):
        self.shutdown_count += 1
        self.shutdown_event.set()

    def close(self):
        self.closed = True


class FakeServer:
    def __init__(self, reply=None):
        self.reply = reply or (lambda request: (200, {"ret": 0}, ""))
        self.stream = lambda raw, sock: io.BytesIO(raw)
        self.requests, self.sockets, self.states = [], [], []

    def connect(self, connection):
        connection.sock = FakeSocket(self)
        connection.is_verified = True
        self.sockets.append(connection.sock)
        self.states.append(capcut._HTTP_CONTEXT.state)
        capcut._connected(connection.sock)


class ForkAndPollTests(unittest.TestCase):
    def test_default_poll_delays_are_adaptive_with_one_shared_deadline(self):
        transport = Transport([Response(created()), *[Response(result("running")) for _ in range(5)],
                               Response(result()), Response(AUDIO)])
        client = capcut.CapCutClient(transport=transport, payload_signer=lambda *args: "test")
        with tempfile.TemporaryDirectory() as root, patch.object(capcut, "bounded_wait") as wait:
            client.synthesize("Fixture", VOICE, Path(root) / "audio.mp3")
        self.assertEqual([call.args[0] for call in wait.call_args_list], [.15, .25, .4, .8, .8])
        self.assertEqual(len({call.kwargs["deadline"] for call in wait.call_args_list}), 1)
        self.assertEqual(client.task_timeout, 20)

    def test_explicit_legacy_poll_interval_stays_fixed(self):
        transport = Transport([Response(created()), Response(result("running")), Response(result("running")),
                               Response(result()), Response(AUDIO)])
        client = capcut.CapCutClient(transport=transport, payload_signer=lambda *args: "test", poll_interval=.007)
        with tempfile.TemporaryDirectory() as root, patch.object(capcut, "bounded_wait") as wait:
            client.synthesize("Fixture", VOICE, Path(root) / "audio.mp3")
        self.assertEqual([call.args[0] for call in wait.call_args_list], [.007, .007])

    def test_callable_cancel_is_checked_every_50ms_in_waits(self):
        cancelled = [False]
        def sleep(seconds):
            self.assertLessEqual(seconds, .05)
            cancelled[0] = True
        with patch.object(capcut.time, "sleep", side_effect=sleep) as slept, self.assertRaises(capcut.TTSCancelled):
            capcut.bounded_wait(10, lambda: cancelled[0])
        slept.assert_called_once()

    def test_fork_copies_configuration_and_scopes_callbacks_without_parent_mutation(self):
        transport, cancel, events = Transport([]), threading.Event(), []
        parent = capcut.CapCutClient(transport=transport, cancel=cancel, progress=events.append,
                                    headers={"Authorization": "test-private"})
        child_events = []
        child = parent.fork(cancel=None, progress=child_events.append, task_timeout=.3)
        inherited = parent.clone()
        self.assertIsNone(child.cancel)
        self.assertIs(inherited.cancel, cancel)
        self.assertIs(inherited.progress, parent.progress)
        self.assertEqual((parent.task_timeout, child.task_timeout), (20, .3))
        self.assertIs(child.transport, transport)
        self.assertIs(child._http_family_slots, parent._http_family_slots)
        self.assertIsNot(child._http_slots, parent._http_slots)
        self.assertIs(child._transport_lock, inherited._transport_lock)
        child.headers["Authorization"] = "different"
        child.device["lan"] = "en-US"
        child._notify("tts_http", "Fixture", capcut._diagnostics(429))
        self.assertEqual(child_events[0]["http_status"], 429)
        self.assertEqual(events, [])
        self.assertEqual(parent.headers["Authorization"], "test-private")
        self.assertEqual(parent.device["lan"], "vi-VN")
        self.assertEqual(parent.clone(task_timeout=600).task_timeout, 20)
        self.assertIsNone(parent.fork(progress=None).progress)
        for value in (0, -1, True, float("nan"), 601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parent.fork(task_timeout=value)

    def test_concurrency_contract_requires_custom_transport_opt_in(self):
        transport = Transport([])
        parent = capcut.CapCutClient(transport=transport)
        self.assertFalse(parent.concurrent_safe)
        self.assertFalse(parent.fork().concurrent_safe)
        transport.thread_safe = True
        concurrent = capcut.CapCutClient(transport=transport).fork()
        self.assertTrue(concurrent.concurrent_safe)
        self.assertIs(concurrent.transport, transport)
        self.assertIs(concurrent.artifact_transport, transport)
        self.assertIsNone(concurrent._transport_lock)
        self.assertIsNone(concurrent._artifact_lock)
        native = capcut.CapCutClient()
        self.assertTrue(native.concurrent_safe)
        if isinstance(native.transport, capcut._RequestsTransport):
            native.transport.close()

    def test_forks_serialize_injected_transport_and_scope_429(self):
        entered, release = threading.Event(), threading.Event()
        def blocked():
            entered.set()
            release.wait(1)
            return Response({"ret": 0})
        transport = Transport([blocked, Response({}, 429)])
        parent = capcut.CapCutClient(transport=transport)
        events = [[], []]
        children = [parent.fork(progress=items.append) for items in events]
        def exchange(child):
            return child._bytes(Request(capcut.BASE_URL), deadline=time.monotonic() + 1,
                                limit=1024, safe_retry=False)
        with ThreadPoolExecutor(2) as workers:
            first = workers.submit(exchange, children[0])
            self.assertTrue(entered.wait(.5))
            second = workers.submit(exchange, children[1])
            # The second exchange cannot enter a non-thread-safe fake while its
            # first body is still owned. Release immediately; no timing sleeps.
            self.assertEqual(len(transport.calls), 1)
            release.set()
            self.assertEqual(json.loads(first.result(1)), {"ret": 0})
            with self.assertRaises(capcut.ProviderUnavailable):
                second.result(1)
        self.assertEqual([event["http_status"] for event in events[0]], [200])
        self.assertEqual([event["http_status"] for event in events[1]], [429])

    def test_missing_requests_keeps_offline_and_fake_compatibility(self):
        with patch.object(capcut, "_RequestsTransport", side_effect=ImportError("optional dependency")):
            client = capcut.CapCutClient()
        self.assertFalse(client.availability()["available"])
        self.assertTrue(any(isinstance(handler, capcut._PublicHTTPSHandler)
                            for handler in client.artifact_transport.handlers))


@unittest.skipUnless(importlib.util.find_spec("requests"), "optional requests package unavailable")
class RequestsPoolTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Any mistake in the fake setup must fail, never reach a real resolver.
        self.stack.enter_context(patch("socket.getaddrinfo", side_effect=AssertionError("No live DNS in tests")))

    def client(self, server=None, **options):
        client = capcut.CapCutClient(**options)
        self.assertIsInstance(client.transport, capcut._RequestsTransport)
        self.addCleanup(client.transport.close)
        server = server or FakeServer()
        pool = client.transport.sessions[0].adapters["https://"].poolmanager.connection_from_url(capcut.BASE_URL)
        self.stack.enter_context(patch.object(pool.ConnectionCls, "connect", lambda connection: server.connect(connection)))
        self.stack.enter_context(patch("urllib3.connectionpool.is_connection_dropped",
                                       lambda connection: connection.sock is None or connection.sock.shutdown_event.is_set()))
        return client, server

    def exchange(self, client, *, deadline=None, safe_retry=False):
        request = client.build_query_request("fixture-id", "fixture-token")
        return client._bytes(request, deadline=deadline or time.monotonic() + 1,
                             limit=4096, safe_retry=safe_retry)

    def test_real_pool_reuses_connection_and_detaches_completed_watchdogs(self):
        client, server = self.client()
        self.exchange(client)
        self.exchange(client.fork())
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(len(server.sockets), 1, "Warm API requests should reuse the same TLS connection")
        self.assertEqual(server.sockets[0].shutdown_count, 0)
        self.assertIsNone(server.states[0].socket)
        server.states[0].abort()
        self.exchange(client)
        self.assertEqual(len(server.sockets), 1)
        self.assertEqual(server.sockets[0].shutdown_count, 0, "Old watchdog must not abort a later cue's socket")

    def test_three_sessions_work_concurrently_with_isolated_cue_callbacks(self):
        rendezvous = threading.Barrier(3)
        def reply(request):
            rendezvous.wait(1)
            cue = next(line for line in request.decode().split("\r\n") if line.lower().startswith("x-cue:"))
            return 200, {"ret": 0, "code": cue.split(":", 1)[1].strip()}, ""
        parent, server = self.client(FakeServer(reply))
        events = [[], [], []]
        children = [parent.fork(progress=items.append) for items in events]
        for index, child in enumerate(children):
            child.headers["x-cue"] = f"cue{index}"
        with ThreadPoolExecutor(3) as workers:
            futures = [workers.submit(self.exchange, child) for child in children]
            self.assertEqual([json.loads(future.result(2))["code"] for future in futures], ["cue0", "cue1", "cue2"])
        self.assertEqual(len(server.sockets), 3)
        self.assertEqual([items[0]["code"] for items in events], ["cue0", "cue1", "cue2"])
        self.assertNotIn("x-cue", parent.headers)
        self.assertEqual(parent.transport.available.qsize(), 3)

    def test_sessions_never_forward_response_cookies_or_environment_auth(self):
        client, server = self.client(FakeServer(lambda request: (200, {"ret": 0}, "Set-Cookie: secret=value; Path=/\r\n")))
        for session in client.transport.sessions:
            self.assertFalse(session.trust_env)
            adapter = session.adapters["https://"]
            self.assertEqual(adapter.max_retries.total, 0)
            self.assertTrue(adapter._pool_block)
        with patch.dict("os.environ", {"HTTPS_PROXY": "https://127.0.0.1:9999"}):
            self.exchange(client)
            self.exchange(client)
        for raw in server.requests:
            self.assertNotIn(b"Cookie:", raw)
            self.assertNotIn(b"Authorization:", raw)
        self.assertTrue(all(not session.cookies for session in client.transport.sessions))

    def test_redirect_is_not_followed_and_api_transport_rejects_artifacts(self):
        client, server = self.client(FakeServer(lambda request: (302, {}, "Location: https://evil.example.org/\r\n")),
                                     headers={"Authorization": "Bearer fixture-secret"})
        with self.assertRaises(capcut.ProviderUnavailable):
            self.exchange(client)
        self.assertEqual(len(server.requests), 1)
        with self.assertRaises(capcut.CapCutProtocolError):
            client.transport.open(Request("https://audio.example.org/file"), timeout=.1)
        self.assertEqual(len(server.requests), 1)
        for adapter in (client, client.fork()):
            self.assertIsNot(adapter.artifact_transport, adapter.transport)
            self.assertTrue(any(isinstance(handler, capcut._PublicHTTPSHandler)
                                for handler in adapter.artifact_transport.handlers))
            self.assertTrue(any(isinstance(handler, capcut._NoRedirect)
                                for handler in adapter.artifact_transport.handlers))

    def test_429_notifies_cue_scheduler_and_only_one_application_retry(self):
        events, parent_events = [], []
        parent, server = self.client(FakeServer(lambda request: (429, {"ret": 7}, "")), progress=parent_events.append)
        child = parent.fork(progress=events.append)
        with patch.object(capcut, "bounded_wait"), self.assertRaises(capcut.ProviderUnavailable):
            self.exchange(child, safe_retry=True)
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(sum(event.get("retry", 0) for event in events), 1)
        self.assertTrue(all(event["http_status"] == 429 for event in events))
        self.assertEqual(parent_events, [])

    def test_requests_errors_are_mapped_safely_and_release_session(self):
        import requests
        for error_type in (requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError):
            with self.subTest(error_type=error_type):
                client = capcut.CapCutClient()
                self.addCleanup(client.transport.close)
                error = capcut.TTSTimeout if issubclass(error_type, requests.Timeout) else capcut.ProviderUnavailable
                with patch.object(requests.Session, "send", side_effect=error_type("private-secret")) as send:
                    with self.assertRaises(error) as raised:
                        self.exchange(client)
                self.assertEqual(send.call_count, 1)
                self.assertNotIn("private-secret", str(raised.exception))
                self.assertEqual(client.transport.available.qsize(), 3)

    def test_cancel_interrupts_reused_socket_and_releases_session_without_late_events(self):
        cancel, entered, returned = threading.Event(), threading.Event(), threading.Event()
        parent, server = self.client()
        self.exchange(parent)  # Warm a reusable TLS connection before cancellation.
        events = []
        child = parent.fork(cancel=cancel, progress=events.append)

        class BlockedBody(io.BytesIO):
            def read1(self, size):
                entered.set()
                server.sockets[0].shutdown_event.wait(1)
                return super().read1(size)

        server.stream = lambda raw, sock: BlockedBody(raw)
        release_session = parent.transport.release
        def release(session):
            release_session(session)
            returned.set()

        with patch.object(parent.transport, "release", side_effect=release), ThreadPoolExecutor(1) as workers:
            future = workers.submit(self.exchange, child)
            self.assertTrue(entered.wait(.5))
            started = time.monotonic()
            cancel.set()
            with self.assertRaises(capcut.TTSCancelled):
                future.result(.5)
            self.assertLess(time.monotonic() - started, .3)
            event_count = len(events)
            self.assertTrue(returned.wait(.5))
        self.assertEqual(len(events), event_count)
        self.assertEqual(parent.transport.available.qsize(), 3)
        self.assertEqual(server.sockets[0].shutdown_count, 1)
        server.stream = lambda raw, sock: io.BytesIO(raw)
        self.exchange(parent)
        self.assertEqual(len(server.sockets), 2, "The cancelled connection must be replaced")

    def test_urllib3_body_timeout_is_sanitized_and_does_not_poison_pool(self):
        from urllib3.exceptions import ReadTimeoutError
        client, server = self.client()

        class FailedBody(io.BytesIO):
            def read1(self, size):
                raise ReadTimeoutError(None, None, "private-secret")

        server.stream = lambda raw, sock: FailedBody(raw)
        with self.assertRaises(capcut.TTSTimeout) as error:
            self.exchange(client)
        self.assertNotIn("private-secret", str(error.exception))
        self.assertEqual(client.transport.available.qsize(), 3)
        server.stream = lambda raw, sock: io.BytesIO(raw)
        self.exchange(client)
        self.assertEqual(len(server.sockets), 2)

    def test_pool_wait_obeys_deadline_without_opening_another_connection(self):
        client, server = self.client()
        leased = [client.transport.available.get_nowait() for _ in range(3)]
        started = time.monotonic()
        try:
            with self.assertRaises(capcut.TTSTimeout):
                self.exchange(client, deadline=time.monotonic() + .06)
            self.assertLess(time.monotonic() - started, .4)
            self.assertEqual(server.requests, [])
        finally:
            for session in leased:
                client.transport.release(session)


if __name__ == "__main__":
    unittest.main()
