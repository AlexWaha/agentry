"""task-0141: WebSocket sessions of the dashboard over a real loopback socket.

The server half here is a bare ThreadingHTTPServer whose handler does nothing
but call ws.handle_upgrade, so these tests pin what ws.py owns: the upgrade
checks after the token (FR-39 from the 400 on), the 101 (FR-40), framing and
close codes (FR-41 to FR-43), the bounded queue (FR-44), the timers (FR-45), the
message envelope and refusals (FR-46, FR-47) and the client cap (FR-48). The
refusals that server.py owns (404 to 401) are in the second half of this module,
against the real server through the sandbox helper.

This module also holds the raw-socket kit (RawClient, InProcessServer,
make_project, add_event) that test_dashboard_watch.py and
test_dashboard_readonly_bytes.py import.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import base64
import json
import socket
import sqlite3
import struct
import sys
import threading
import time
import unittest
import unittest.mock
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR / "dashboard"))
sys.path.insert(0, str(TOOLS_DIR / "pipeline"))
sys.path.insert(0, str(TESTS_DIR))

import events
import stores
import tmproot
import ws

RFC_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
WAIT = 5.0


# ---------------------------------------------------------------- kit
def make_project(root: Path, wal: bool = True) -> Path:
    """A minimal project: the state and task folders and an empty events.db."""
    for part in ("state", "memory", "tasks/backlog", "tasks/active", "tasks/done", "tasks/epics",
                 "tasks/handoffs"):
        (root / ".agentry" / part).mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "rules").mkdir(parents=True, exist_ok=True)
    create_events_db(root, wal)
    return root


def create_events_db(root: Path, wal: bool = True) -> None:
    path = stores.events_db(root)
    with closing(sqlite3.connect(path)) as conn:
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
        for statement in events._SCHEMA:
            conn.execute(statement)
        conn.commit()


def add_event(root: Path, task: str = "task-0001", kind: str = "transition", stage: str = "implement",
              detail: dict | None = None) -> None:
    with closing(sqlite3.connect(stores.events_db(root))) as conn:
        conn.execute(events._INSERT, (
            "2026-10-16T10:00:00Z", "", task, "build", kind, stage, "plan", "", "advance",
            json.dumps(detail or {})))
        conn.commit()


def add_events(root: Path, count: int) -> None:
    with closing(sqlite3.connect(stores.events_db(root))) as conn:
        conn.executemany(events._INSERT, [
            ("2026-10-16T10:00:00Z", "", "task-0001", "build", "transition", "implement", "plan", "",
             "advance", "{}")] * count)
        conn.commit()


def remove_events_db(root: Path) -> None:
    db = stores.events_db(root)
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ctx: stores.Ctx

    def log_message(self, fmt, *args):
        pass

    def send_json_error(self, status, code, message, headers=None):
        body = json.dumps({"message": message, "code": code}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        ws.handle_upgrade(self, self.ctx)


class InProcessServer:
    """ws.handle_upgrade behind a bare HTTP server, in this process."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (_Handler,), {}))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.ctx = stores.Ctx(
            project_root=project, plugin_root=project / "plugin", port=self.port,
            origin=f"http://127.0.0.1:{self.port}", token="t", dist_dir=project / "dist")
        self.httpd.RequestHandlerClass.ctx = self.ctx
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def start(self) -> InProcessServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        ws.close_all(1001)
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(WAIT)


def parse_server_frame(buf: bytes):
    """(fin, opcode, payload, bytes used) of the first server frame, or None."""
    if len(buf) < 2:
        return None
    b1, b2 = buf[0], buf[1]
    if b2 & 0x80:
        raise AssertionError("a server frame must not be masked")
    n, off = b2 & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, off = struct.unpack(">H", buf[2:4])[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, off = struct.unpack(">Q", buf[2:10])[0], 10
    if len(buf) < off + n:
        return None
    return bool(b1 & 0x80), b1 & 0x0F, buf[off:off + n], off + n


MASK = b"\x01\x02\x03\x04"


def client_frame(opcode: int, payload: bytes = b"", fin: bool = True, mask: bytes | None = MASK) -> bytes:
    n = len(payload)
    flag = 0x80 if mask else 0
    head = bytes([(0x80 if fin else 0) | opcode])
    if n < 126:
        head += bytes([flag | n])
    elif n < 65536:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class RawClient:
    """A WebSocket client written against the RFC, not against ws.py."""

    def __init__(self, port: int, headers: dict[str, str | None] | None = None, path: str = "/ws?token=t",
                 method: str = "GET", key: str = RFC_KEY) -> None:
        self.port = port
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.buf = b""
        request = {
            "Host": f"127.0.0.1:{port}", "Upgrade": "websocket", "Connection": "Upgrade",
            "Sec-WebSocket-Key": key, "Sec-WebSocket-Version": "13", "Origin": f"http://127.0.0.1:{port}",
        }
        request.update(headers or {})
        lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in request.items() if v is not None]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        self.status, self.headers, self.body = self._read_response()

    def _read_response(self):
        while b"\r\n\r\n" not in self.buf:
            self._fill(WAIT)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1])
        headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:])}
        body = b""
        if status != 101 and "content-length" in headers:
            need = int(headers["content-length"])
            while len(self.buf) < need:
                self._fill(WAIT)
            body, self.buf = self.buf[:need], self.buf[need:]
        return status, headers, body

    def _fill(self, timeout: float) -> bool:
        """Read more bytes. False on EOF; raises TimeoutError when nothing arrives."""
        self.sock.settimeout(timeout)
        data = self.sock.recv(65536)
        self.buf += data
        return bool(data)

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def send_json(self, obj, **kw) -> None:
        self.send(client_frame(0x1, json.dumps(obj).encode(), **kw))

    def frame(self, timeout: float = WAIT):
        """(opcode, payload) of the next server frame; None on EOF."""
        deadline = time.monotonic() + timeout
        while True:
            parsed = parse_server_frame(self.buf)
            if parsed:
                self.buf = self.buf[parsed[3]:]
                return parsed[1], parsed[2]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no frame within the timeout")
            try:
                if not self._fill(remaining):
                    return None
            except (ConnectionResetError, ConnectionAbortedError):
                return None

    def message(self, timeout: float = WAIT) -> dict:
        """The next text message as parsed JSON; pings are skipped."""
        deadline = time.monotonic() + timeout
        while True:
            got = self.frame(max(0.01, deadline - time.monotonic()))
            if got is None:
                raise AssertionError("connection ended while waiting for a message")
            if got[0] == 0x1:
                return json.loads(got[1])
            if got[0] != 0x9:
                raise AssertionError(f"unexpected opcode {got[0]}")

    def drain_messages(self, quiet: float = 0.4) -> list[dict]:
        """Every text message that arrives until the line goes quiet."""
        out = []
        while True:
            try:
                out.append(self.message(quiet))
            except TimeoutError:
                return out

    def close_code(self, timeout: float = WAIT) -> int | None:
        """The status code of the close frame the server sends, then asserts EOF follows."""
        deadline = time.monotonic() + timeout
        code = None
        while True:
            got = self.frame(max(0.01, deadline - time.monotonic()))
            if got is None:
                return code
            if got[0] == 0x8:
                code = struct.unpack(">H", got[1][:2])[0] if len(got[1]) >= 2 else None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def wait_until(predicate, timeout: float = WAIT, step: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class WsTestCase(unittest.TestCase):
    """Starts an in-process server over a fresh project and always leaves no client behind."""

    def setUp(self):
        self.project = make_project(tmproot.sandbox(self, "ws-session"))
        self.server = InProcessServer(self.project).start()
        self.clients: list[RawClient] = []
        self.addCleanup(self._teardown)

    def _teardown(self):
        for client in self.clients:
            client.close()
        self.server.stop()
        self.assertTrue(wait_until(lambda: ws.client_count() == 0), "a session outlived its test")

    def connect(self, **kw) -> RawClient:
        client = RawClient(self.server.port, **kw)
        self.clients.append(client)
        return client

    def connect_ready(self) -> RawClient:
        client = self.connect()
        self.assertEqual(client.status, 101)
        self.assertEqual(client.message()["type"], "hello")
        return client


# ---------------------------------------------------------------- tests
class UpgradeRefusalTest(WsTestCase):
    """FR-39 from the upgrade-header check on, and FR-48's ninth client."""

    def assertRefused(self, client: RawClient, status: int, code: str):
        self.assertEqual(client.status, status)
        self.assertNotIn(b"101", client.body)
        self.assertEqual(json.loads(client.body)["code"], code)
        self.assertIsNone(client.frame(1.0), "the connection must end after a refusal")

    def test_missing_upgrade_header_is_400(self):
        self.assertRefused(self.connect(headers={"Upgrade": None}), 400, "bad_request")

    def test_upgrade_header_that_is_not_websocket_is_400(self):
        self.assertRefused(self.connect(headers={"Upgrade": "h2c"}), 400, "bad_request")

    def test_missing_connection_upgrade_is_400(self):
        self.assertRefused(self.connect(headers={"Connection": "keep-alive"}), 400, "bad_request")

    def test_connection_header_with_several_tokens_is_accepted(self):
        client = self.connect(headers={"Connection": "keep-alive, Upgrade", "Upgrade": "WebSocket"})
        self.assertEqual(client.status, 101)

    def test_version_other_than_13_is_426_and_names_version_13(self):
        for version in ("12", "8", "", None):
            with self.subTest(version=version):
                client = self.connect(headers={"Sec-WebSocket-Version": version})
                self.assertRefused(client, 426, "upgrade_required")
                self.assertEqual(client.headers["sec-websocket-version"], "13")

    def test_key_that_is_not_base64_of_16_bytes_is_400(self):
        for key in ("", "short", "!!!!not-base64!!!!", base64.b64encode(b"x" * 15).decode(),
                    base64.b64encode(b"x" * 17).decode()):
            with self.subTest(key=key):
                self.assertRefused(self.connect(key=key), 400, "bad_request")

    def test_version_is_checked_before_the_key(self):
        client = self.connect(headers={"Sec-WebSocket-Version": "12"}, key="bad")
        self.assertEqual(client.status, 426)

    def test_ninth_client_gets_503_before_any_101_and_a_freed_slot_is_reusable(self):
        first = [self.connect_ready() for _ in range(ws.MAX_CLIENTS)]
        self.assertEqual(ws.client_count(), ws.MAX_CLIENTS)
        ninth = self.connect()
        self.assertRefused(ninth, 503, "unavailable")
        first[0].send(client_frame(0x8, struct.pack(">H", 1000)))
        self.assertEqual(first[0].close_code(), 1000)
        self.assertTrue(wait_until(lambda: ws.client_count() == ws.MAX_CLIENTS - 1))
        self.assertEqual(self.connect().status, 101)


class HandshakeTest(WsTestCase):
    def test_accept_value_matches_the_rfc_and_no_protocol_or_extensions_are_granted(self):
        client = self.connect(headers={
            "Sec-WebSocket-Protocol": "chat", "Sec-WebSocket-Extensions": "permessage-deflate"})
        self.assertEqual(client.status, 101)
        self.assertEqual(client.headers["sec-websocket-accept"], "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertEqual(client.headers["upgrade"].lower(), "websocket")
        self.assertIn("upgrade", client.headers["connection"].lower())
        self.assertNotIn("sec-websocket-protocol", client.headers)
        self.assertNotIn("sec-websocket-extensions", client.headers)

    def test_first_message_is_hello_with_the_latest_event_id(self):
        add_event(self.project)
        add_event(self.project)
        hello = self.connect().message()
        self.assertEqual(hello, {"type": "hello", "v": 1, "payload": {
            "protocol": 1, "read_only": True, "latest_event_id": 2}})

    def test_hello_reports_zero_when_the_journal_is_missing(self):
        remove_events_db(self.project)
        self.assertEqual(self.connect().message()["payload"]["latest_event_id"], 0)

    def test_hello_is_first_even_when_a_broadcast_races_the_handshake(self):
        client = self.connect()
        ws.broadcast(ws.message("invalidate", {"topics": ["tasks"]}))
        self.assertEqual(client.message()["type"], "hello")
        self.assertEqual(client.message()["type"], "invalidate")


class FrameBehaviourTest(WsTestCase):
    """FR-41 to FR-43 end to end."""

    def test_large_server_messages_arrive_intact_in_the_16_and_64_bit_forms(self):
        client = self.connect_ready()
        for size in (200, 70000):
            with self.subTest(size=size):
                ws.broadcast(ws.message("invalidate", {"topics": ["x" * size]}))
                self.assertEqual(client.message()["payload"]["topics"], ["x" * size])

    def test_unmasked_client_frame_closes_with_1002(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b"{}", mask=None))
        self.assertEqual(client.close_code(), 1002)

    def test_continued_message_over_64k_closes_with_1009(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b"a" * 30000, fin=False))
        client.send(client_frame(0x0, b"a" * 30000, fin=False))
        client.send(client_frame(0x0, b"a" * 10000, fin=True))
        self.assertEqual(client.close_code(), 1009)

    def test_single_frame_over_64k_closes_with_1009(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b"a" * 70000))
        self.assertEqual(client.close_code(), 1009)

    def test_binary_frame_closes_with_1003(self):
        client = self.connect_ready()
        client.send(client_frame(0x2, b"\x00\x01"))
        self.assertEqual(client.close_code(), 1003)

    def test_text_that_is_not_valid_utf8_closes_with_1007(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b"\xff\xfe"))
        self.assertEqual(client.close_code(), 1007)

    def test_text_that_is_not_json_closes_with_1007(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b"not json"))
        self.assertEqual(client.close_code(), 1007)

    def test_deeply_nested_json_closes_with_1007_frees_the_slot_and_prints_no_traceback(self):
        client = self.connect_ready()
        with unittest.mock.patch("threading.excepthook") as hook:
            client.send(client_frame(0x1, b"[" * 30000 + b"]" * 30000))
            self.assertEqual(client.close_code(), 1007)
            self.assertTrue(wait_until(lambda: ws.client_count() == 0))
        hook.assert_not_called()

    def test_an_unexpected_reader_error_drops_the_client_instead_of_leaving_it_mute(self):
        client = self.connect_ready()
        with unittest.mock.patch("threading.excepthook") as hook, \
                unittest.mock.patch.object(ws, "client_reply", side_effect=RuntimeError("boom")):
            client.send_json({"type": "command", "v": 1})
            self.assertIsNone(client.frame())
            self.assertTrue(wait_until(lambda: ws.client_count() == 0))
        hook.assert_not_called()

    def test_three_continuation_frames_are_one_message(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b'{"type":', fin=False))
        client.send(client_frame(0x0, b'"command"', fin=False))
        client.send(client_frame(0x0, b"}", fin=True))
        self.assertEqual(client.message()["type"], "command.refused")
        self.assertEqual(client.drain_messages(0.3), [], "one message in, one answer out")

    def test_ping_is_answered_with_a_pong_carrying_the_same_payload(self):
        client = self.connect_ready()
        client.send(client_frame(0x9, b"abc"))
        self.assertEqual(client.frame(), (0xA, b"abc"))

    def test_ping_between_fragments_does_not_break_the_message(self):
        client = self.connect_ready()
        client.send(client_frame(0x1, b'{"type":', fin=False))
        client.send(client_frame(0x9, b"mid"))
        client.send(client_frame(0x0, b'"command"}', fin=True))
        got = [client.frame(), client.frame()]
        self.assertIn((0xA, b"mid"), got)
        self.assertTrue(any(op == 0x1 for op, _ in got))

    def test_close_frame_is_echoed_with_the_same_code_and_the_connection_ends(self):
        client = self.connect_ready()
        client.send(client_frame(0x8, struct.pack(">H", 1000)))
        self.assertEqual(client.close_code(), 1000)
        self.assertTrue(wait_until(lambda: ws.client_count() == 0))

    def test_close_frame_with_an_invalid_code_closes_with_1002(self):
        client = self.connect_ready()
        client.send(client_frame(0x8, struct.pack(">H", 1005)))
        self.assertEqual(client.close_code(), 1002)

    def test_client_that_vanishes_frees_its_slot(self):
        client = self.connect_ready()
        self.assertEqual(ws.client_count(), 1)
        client.close()
        self.assertTrue(wait_until(lambda: ws.client_count() == 0))


class MessageContractTest(WsTestCase):
    """FR-46 and FR-47."""

    def test_command_message_is_refused_read_only_and_the_connection_stays_open(self):
        client = self.connect_ready()
        client.send_json({"type": "command", "v": 1, "payload": {"do": "reset"}})
        reply = client.message()
        self.assertEqual(reply, {"type": "command.refused", "v": 1, "payload": {
            "code": "read_only", "message": reply["payload"]["message"]}})
        self.assertIsInstance(reply["payload"]["message"], str)
        client.send(client_frame(0x9, b"alive"))
        self.assertEqual(client.frame(), (0xA, b"alive"))

    def test_unknown_missing_and_wrong_version_messages_get_error_and_keep_the_connection(self):
        client = self.connect_ready()
        for obj in ({"type": "x"}, {}, {"type": "command", "v": 2}, [1, 2], 7):
            with self.subTest(obj=obj):
                client.send_json(obj)
                reply = client.message()
                self.assertEqual(reply["type"], "error")
                self.assertEqual(reply["v"], 1)
                self.assertEqual(reply["payload"]["code"], "bad_message")
        client.send(client_frame(0x9, b"still"))
        self.assertEqual(client.frame(), (0xA, b"still"))

    def test_every_server_message_is_the_envelope(self):
        client = self.connect_ready()
        ws.broadcast(ws.message("resync", {"reason": "burst"}))
        client.send_json({"type": "nope"})
        for msg in (client.message(), client.message()):
            self.assertEqual(sorted(msg), ["payload", "type", "v"])
            self.assertEqual(msg["v"], 1)
            self.assertIsInstance(msg["payload"], dict)


class BroadcastTest(WsTestCase):
    def test_broadcast_reaches_every_client_and_counts_are_tracked(self):
        clients = [self.connect_ready() for _ in range(3)]
        self.assertEqual(ws.client_count(), 3)
        ws.broadcast(ws.message("invalidate", {"topics": ["tasks"]}))
        for client in clients:
            self.assertEqual(client.message()["payload"], {"topics": ["tasks"]})

    def test_close_all_sends_1001_to_every_client_and_returns_after_they_end(self):
        clients = [self.connect_ready() for _ in range(2)]
        ws.close_all(1001)
        self.assertEqual(ws.client_count(), 0)
        for client in clients:
            self.assertEqual(client.close_code(), 1001)


class SlowClientTest(WsTestCase):
    """FR-44: a queue of 256, one resync on overflow, the connection stays."""

    def test_client_that_never_reads_gets_one_resync_and_stays_connected(self):
        gate, entered = threading.Event(), threading.Event()
        real_send = ws._send_all

        def blocked_send(sock, data):
            entered.set()
            gate.wait(WAIT)
            real_send(sock, data)

        with unittest.mock.patch.object(ws, "_send_all", blocked_send):
            client = self.connect()
            self.assertTrue(entered.wait(WAIT), "the writer should be stuck sending hello")
            for i in range(300):
                ws.broadcast(ws.message("invalidate", {"topics": [f"n{i}"]}))
            gate.set()
            messages = client.drain_messages(0.8)

        types = [m["type"] for m in messages]
        self.assertEqual(types.count("resync"), 1, types[:5])
        self.assertEqual(messages[0]["type"], "hello")
        resync = next(m for m in messages if m["type"] == "resync")
        self.assertEqual(resync["payload"], {"reason": "overflow"})
        self.assertLess(len(messages), 300, "the overflowed backlog must be dropped, not delivered")
        self.assertEqual(ws.client_count(), 1)
        ws.broadcast(ws.message("invalidate", {"topics": ["after"]}))
        self.assertEqual(client.message()["payload"], {"topics": ["after"]})

    def test_client_that_stops_reading_with_a_full_socket_is_dropped_after_the_send_timeout(self):
        with unittest.mock.patch.object(ws, "SEND_TIMEOUT", 1.0):
            self.connect()
            for _ in range(150):
                ws.broadcast(ws.message("invalidate", {"topics": ["x" * 100000]}))
            self.assertTrue(wait_until(lambda: ws.client_count() == 0, 6.0),
                            "a writer stuck on a client that never reads must give up")

    def test_queue_below_the_limit_loses_nothing(self):
        gate, entered = threading.Event(), threading.Event()
        real_send = ws._send_all

        def blocked_send(sock, data):
            entered.set()
            gate.wait(WAIT)
            real_send(sock, data)

        with unittest.mock.patch.object(ws, "_send_all", blocked_send):
            client = self.connect()
            self.assertTrue(entered.wait(WAIT))
            for i in range(ws.QUEUE_LIMIT):
                ws.broadcast(ws.message("invalidate", {"topics": [f"n{i}"]}))
            gate.set()
            messages = client.drain_messages(0.8)
        self.assertEqual([m["type"] for m in messages].count("resync"), 0)
        self.assertEqual(len(messages), 1 + ws.QUEUE_LIMIT)


class TimerTest(WsTestCase):
    """FR-45 through the scaled-down module timers."""

    def test_ping_arrives_at_the_ping_interval(self):
        with unittest.mock.patch.object(ws, "PING_INTERVAL", 0.3), \
                unittest.mock.patch.object(ws, "SILENCE_LIMIT", 30.0):
            client = self.connect_ready()
            start = time.monotonic()
            got = client.frame(2.0)
            elapsed = time.monotonic() - start
        self.assertEqual(got, (0x9, b""))
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 1.5)

    def test_client_that_answers_pings_is_kept_past_the_silence_limit(self):
        with unittest.mock.patch.object(ws, "PING_INTERVAL", 0.2), \
                unittest.mock.patch.object(ws, "SILENCE_LIMIT", 0.8):
            client = self.connect_ready()
            end = time.monotonic() + 2.0
            while time.monotonic() < end:
                got = client.frame(1.0)
                self.assertIsNotNone(got, "an answering client must not be closed")
                if got[0] == 0x9:
                    client.send(client_frame(0xA, got[1]))
            self.assertEqual(ws.client_count(), 1)

    def test_silent_client_is_closed_at_the_silence_limit(self):
        with unittest.mock.patch.object(ws, "PING_INTERVAL", 30.0), \
                unittest.mock.patch.object(ws, "SILENCE_LIMIT", 0.8):
            client = self.connect_ready()
            start = time.monotonic()
            code = client.close_code(4.0)
            elapsed = time.monotonic() - start
        self.assertEqual(code, 1001)
        self.assertGreaterEqual(elapsed, 0.7)
        self.assertLess(elapsed, 3.5)
        self.assertTrue(wait_until(lambda: ws.client_count() == 0))


class ServerRefusalOrderTest(unittest.TestCase):
    """FR-39 against the real server: the first failing check decides, no 101 on a refusal.

    server.py answers the first six checks (path, method, Host, Origin, Sec-Fetch-Site,
    token) and hands the rest to ws.handle_upgrade; the order below is the spec's.
    """

    @classmethod
    def setUpClass(cls):
        import dashboard_sandbox as sandbox

        root = tmproot.mkdtemp("ws-refusal")
        cls.addClassCleanup(tmproot.rmtree, root)
        cls.server = sandbox.start_server(sandbox.make_project(root))
        cls.addClassCleanup(cls.server.stop)

    def setUp(self):
        self.clients: list[RawClient] = []
        self.addCleanup(lambda: [c.close() for c in self.clients])

    def attempt(self, token: str | None = "valid", path: str = "/ws", **kw) -> RawClient:
        if token == "valid":
            token = self.server.token
        query = f"?token={token}" if token is not None else ""
        client = RawClient(self.server.port, path=path + query, **kw)
        self.clients.append(client)
        return client

    def assertRefused(self, client: RawClient, status: int, code: str | None = None):
        self.assertEqual(client.status, status)
        self.assertNotIn(b"101", client.body)
        self.assertNotIn("sec-websocket-accept", client.headers)
        if code is not None:
            self.assertEqual(json.loads(client.body)["code"], code)

    def test_upgrade_request_to_a_path_other_than_ws_is_never_upgraded(self):
        bearer = {"Authorization": f"Bearer {self.server.token}"}
        self.assertRefused(self.attempt(token=None, path="/api/v1/ws", headers=bearer), 404, "not_found")
        for path in ("/socket.js", "/assets/ws.js"):
            with self.subTest(path=path):
                self.assertRefused(self.attempt(token=None, path=path), 404)
        for path in ("/ws/", "/socket"):
            with self.subTest(path=path):
                client = self.attempt(path=path)  # the SPA fallback answers, but never a 101
                self.assertNotEqual(client.status, 101)
                self.assertNotIn("sec-websocket-accept", client.headers)

    def test_method_other_than_get_is_405(self):
        for method in ("POST", "PUT", "DELETE"):
            with self.subTest(method=method):
                self.assertRefused(self.attempt(method=method), 405)

    def test_foreign_host_is_403_forbidden_host(self):
        self.assertRefused(self.attempt(headers={"Host": "evil.example"}), 403, "forbidden_host")

    def test_missing_or_foreign_origin_is_403_forbidden_origin(self):
        self.assertRefused(self.attempt(headers={"Origin": None}), 403, "forbidden_origin")
        self.assertRefused(self.attempt(headers={"Origin": "http://evil.example"}), 403, "forbidden_origin")

    def test_cross_site_fetch_metadata_is_403_and_same_origin_is_accepted(self):
        self.assertRefused(self.attempt(headers={"Sec-Fetch-Site": "cross-site"}), 403, "forbidden_origin")
        self.assertEqual(self.attempt(headers={"Sec-Fetch-Site": "same-origin"}).status, 101)

    def test_missing_or_wrong_token_is_401(self):
        self.assertRefused(self.attempt(token=None), 401, "unauthorized")
        self.assertRefused(self.attempt(token="wrong"), 401, "unauthorized")

    def test_upgrade_headers_version_and_key_refusals_follow_in_order(self):
        self.assertRefused(self.attempt(headers={"Upgrade": None}), 400, "bad_request")
        self.assertRefused(self.attempt(headers={"Connection": "close"}), 400, "bad_request")
        bad_version = self.attempt(headers={"Sec-WebSocket-Version": "8"})
        self.assertRefused(bad_version, 426, "upgrade_required")
        self.assertEqual(bad_version.headers["sec-websocket-version"], "13")
        self.assertRefused(self.attempt(key="short"), 400, "bad_request")

    def test_ninth_client_gets_503_and_no_101(self):
        connected = [self.attempt() for _ in range(8)]
        self.assertTrue(all(c.status == 101 for c in connected))
        self.assertRefused(self.attempt(), 503, "unavailable")

    def test_the_first_failing_check_decides_when_several_fail(self):
        foreign = {"Host": "evil.example", "Origin": "http://evil.example"}
        cases = [
            ("method before host", {"method": "POST", "headers": {"Host": "evil.example"}}, 405),
            ("host before origin", {"headers": foreign}, 403),
            ("origin before token", {"token": "wrong", "headers": {"Origin": "http://evil.example"}}, 403),
            ("fetch site before token", {"token": "wrong", "headers": {"Sec-Fetch-Site": "cross-site"}}, 403),
            ("token before upgrade headers", {"token": "wrong", "headers": {"Upgrade": None}}, 401),
            ("upgrade headers before version",
             {"headers": {"Upgrade": None, "Sec-WebSocket-Version": "8"}}, 400),
            ("version before key", {"headers": {"Sec-WebSocket-Version": "8"}, "key": "short"}, 426),
        ]
        for name, kw, status in cases:
            with self.subTest(name):
                self.assertRefused(self.attempt(**kw), status)
        connected = [self.attempt() for _ in range(8)]
        self.assertTrue(all(c.status == 101 for c in connected))
        with self.subTest("key before the client cap"):
            self.assertRefused(self.attempt(key="short"), 400)


if __name__ == "__main__":
    unittest.main()
