"""QA (backend) end-to-end checks of the dashboard server, run against the real server.py (task-0149).

Every test starts server.py as a child process on a sandbox project built with the real harness
writers and talks to it over a socket, the way a browser would. The checks are adversarial: auth,
Host, Origin and Sec-Fetch-Site variants, encoded and junction paths on static serving, every verb on
every route, paging bounds, stores that are corrupt or odd, a stream of hostile WebSocket frames,
the read-only guarantee under abuse, and the idle clock.

Two tests reproduce defects found by QA and fail until the owning module is fixed:

  * test_the_idle_clock_restarts_when_the_last_websocket_client_leaves   (core, server.py watch_idle)
  * test_a_deeply_nested_message_is_answered_and_leaves_no_traceback     (realtime, ws.py decode_message)
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import unittest
from http import HTTPStatus
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

import dashboard_sandbox as sb
import tmproot

SAFE_ROUTES = (
    "/api/v1/status",
    "/api/v1/runs",
    "/api/v1/tasks",
    "/api/v1/tasks/task-0106",
    "/api/v1/epics",
    "/api/v1/events",
    "/api/v1/metrics",
    "/api/v1/memory",
    "/api/v1/memory/stats",
    "/api/v1/graphs/memory",
    "/api/v1/codegraph",
    "/api/v1/codegraph/root/graph",
    "/api/v1/harness",
    "/api/v1/handoffs",
    "/api/v1/handoffs/task-0106",
)
ID_ROUTES = ("/api/v1/tasks/task-0106", "/api/v1/handoffs/task-0106", "/api/v1/codegraph/root/graph")
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
NULLABLE = {"needs_you", "supervisor", "run", "handoff", "memory_stamped_at", "lead_time_s", "timeout"}
TIMESTAMP_KEYS = {
    "at",
    "since",
    "created",
    "updated",
    "completed",
    "last_sync",
    "seen",
    "stamped_at",
    "memory_stamped_at",
}
OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x2, 0x8, 0x9, 0xA
WS_KEY = base64.b64encode(b"0123456789abcdef").decode()


# --------------------------------------------------------------------------- helpers


def exchange(
    server,
    method: str,
    path: str,
    headers: dict | None = None,
    host: str | None = None,
    token: bool = True,
    body: bytes | None = None,
):
    """One request with full control of the Host header; returns (status, headers, body bytes)."""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{server.port}")
        merged = {"Authorization": f"Bearer {server.token}"} if token else {}
        merged.update(headers or {})
        for name, value in merged.items():
            if value is not None:
                conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        return response.status, response.msg, response.read()
    finally:
        conn.close()


def code_of(body: bytes) -> str:
    return json.loads(body.decode("utf-8")).get("code", "")


def raw_exchange(server, data: bytes, timeout: float = 5.0) -> bytes:
    """Send raw bytes and read until the server closes the connection or the timeout passes."""
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=timeout)
    try:
        sock.sendall(data)
        out = b""
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                out += chunk
        except TimeoutError:
            pass
        return out
    finally:
        sock.close()


def masked_frame(opcode: int, payload: bytes = b"", fin: bool = True) -> bytes:
    key = b"\x01\x02\x03\x04"
    size = len(payload)
    if size < 126:
        head = bytes([(0x80 if fin else 0) | opcode, 0x80 | size])
    elif size < 65536:
        head = bytes([(0x80 if fin else 0) | opcode, 0x80 | 126]) + struct.pack(">H", size)
    else:
        head = bytes([(0x80 if fin else 0) | opcode, 0x80 | 127]) + struct.pack(">Q", size)
    return head + key + bytes(byte ^ key[i % 4] for i, byte in enumerate(payload))


class WsClient:
    """A minimal RFC 6455 client over a raw socket."""

    def __init__(self, server, headers: dict | None = None, path: str | None = None, origin: bool = True):
        self.sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        merged = {
            "Host": f"127.0.0.1:{server.port}",
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": WS_KEY,
            "Sec-WebSocket-Version": "13",
        }
        if origin:
            merged["Origin"] = f"http://127.0.0.1:{server.port}"
        merged.update(headers or {})
        request = f"GET {path or '/ws?token=' + server.token} HTTP/1.1\r\n"
        request += "".join(f"{k}: {v}\r\n" for k, v in merged.items() if v is not None) + "\r\n"
        self.sock.sendall(request.encode("latin-1"))
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
        head, _, self.buffer = buffer.partition(b"\r\n\r\n")
        self.head = head.decode("latin-1")
        self.status = int(self.head.split(" ", 2)[1])
        self.closed = False

    def header(self, name: str) -> str | None:
        for line in self.head.split("\r\n")[1:]:
            key, _, value = line.partition(":")
            if key.strip().lower() == name.lower():
                return value.strip()
        return None

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def frame(self, timeout: float = 3.0):
        """The next server frame as (opcode, payload), or None when nothing arrives in time."""
        deadline = time.monotonic() + timeout
        self.sock.settimeout(0.2)
        while True:
            parsed = self._parse()
            if parsed is not None:
                return parsed
            if time.monotonic() >= deadline or self.closed:
                return None
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                self.closed = True
                continue
            if not chunk:
                self.closed = True
                continue
            self.buffer += chunk

    def _parse(self):
        buf = self.buffer
        if len(buf) < 2:
            return None
        size, offset = buf[1] & 0x7F, 2
        if size == 126:
            if len(buf) < 4:
                return None
            size, offset = struct.unpack(">H", buf[2:4])[0], 4
        elif size == 127:
            if len(buf) < 10:
                return None
            size, offset = struct.unpack(">Q", buf[2:10])[0], 10
        if len(buf) < offset + size:
            return None
        self.buffer = buf[offset + size :]
        return buf[0] & 0x0F, buf[offset : offset + size]

    def message(self, timeout: float = 3.0) -> dict | None:
        frame = self.frame(timeout)
        return json.loads(frame[1]) if frame and frame[0] == OP_TEXT else None

    def close_code(self, timeout: float = 3.0) -> int | None:
        """The status of the close frame the server sends next (skipping text frames), or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.frame(max(0.05, deadline - time.monotonic()))
            if frame is None:
                return None
            if frame[0] == OP_CLOSE:
                return struct.unpack(">H", frame[1][:2])[0] if len(frame[1]) >= 2 else 0
        return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


PROJECT: Path | None = None


def setUpModule():
    """The small sandbox dataset grown to paging volumes: 120 events, 45 lessons, 60 handoffs and a
    code index at the project root. Built once; every class that mutates anything works on a copy."""
    global PROJECT
    root = Path(tmproot.mkdtemp("dash_qa_e2e_"))
    unittest.addModuleCleanup(tmproot.rmtree, root)
    PROJECT = sb.build_project(root / "project")
    sb.seed(PROJECT, {"bulk_events": 120})
    sb.seed(PROJECT, {"bulk_lessons": 45})
    for number in range(1, 59):
        sb.write_handoff(
            PROJECT, f"task-{number:04d}", valid=number % 4 != 0, date=f"2026-09-{number % 27 + 1:02d}"
        )
    sb.make_codegraph(
        PROJECT,
        ["a/x.py", "a/y.py", "b/z.py", "c/w.py"],
        [("a/x.py", "b/z.py", 3), ("b/z.py", "c/w.py", 2), ("a/y.py", "a/x.py", 1)],
    )


def copy_project(case, source: Path, name: str) -> Path:
    target = tmproot.sandbox(case, f"dash_qa_{name}_") / "project"
    shutil.copytree(source, target)
    return target


# --------------------------------------------------------------------------- authentication


class AuthenticationMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT
        cls.server = sb.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def test_every_route_needs_a_correct_bearer_token(self):
        wrong = {
            "none": None,
            "wrong token": "Bearer not-the-token",
            "basic scheme": "Basic {token}",
            "bare token": "{token}",
            "double space": "Bearer  {token}",
            "trailing space": "Bearer {token} ",
            "token prefix": "Bearer {prefix}",
            "empty bearer": "Bearer ",
            "empty header": "",
        }
        for route in SAFE_ROUTES:
            for label, template in wrong.items():
                value = (
                    None
                    if template is None
                    else template.format(token=self.server.token, prefix=self.server.token[:20])
                )
                with self.subTest(route=route, header=label):
                    status, head, body = exchange(
                        self.server, "GET", route, {"Authorization": value}, token=False
                    )
                    self.assertEqual((HTTPStatus.UNAUTHORIZED, "unauthorized"), (status, code_of(body)))
                    self.assertNotIn(self.server.token.encode(), body)
                    self.assertIsNone(head.get("Access-Control-Allow-Origin"))

    def test_the_token_is_accepted_in_a_header_only_never_in_the_query_of_an_api_route(self):
        for route in SAFE_ROUTES:
            with self.subTest(route=route):
                joiner = "&" if "?" in route else "?"
                status, _, body = exchange(
                    self.server, "GET", f"{route}{joiner}token={self.server.token}", token=False
                )
                self.assertEqual((HTTPStatus.UNAUTHORIZED, "unauthorized"), (status, code_of(body)))

    def test_the_right_token_opens_every_route(self):
        for route in SAFE_ROUTES:
            with self.subTest(route=route):
                self.assertEqual(HTTPStatus.OK, exchange(self.server, "GET", route)[0])

    def test_the_scheme_name_is_case_insensitive(self):
        status, _, _ = exchange(
            self.server,
            "GET",
            "/api/v1/status",
            {"Authorization": f"bearer {self.server.token}"},
            token=False,
        )
        self.assertEqual(HTTPStatus.OK, status)

    def test_static_files_and_the_spa_fallback_need_no_token(self):
        for path in ("/", "/board", "/assets/app-abc123.js", "/favicon.svg", "/favicon.ico"):
            with self.subTest(path=path):
                self.assertEqual(HTTPStatus.OK, exchange(self.server, "GET", path, token=False)[0])

    def test_the_error_body_of_an_unknown_api_path_is_gated_by_the_token_like_any_other(self):
        status, _, body = exchange(self.server, "GET", "/api/v1/nope", token=False)
        self.assertEqual((HTTPStatus.UNAUTHORIZED, "unauthorized"), (status, code_of(body)))
        status, _, body = exchange(self.server, "GET", "/api/v1/nope")
        self.assertEqual((HTTPStatus.NOT_FOUND, "not_found"), (status, code_of(body)))


# --------------------------------------------------------------------------- host, origin, fetch metadata


class HostOriginFetchSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT
        cls.server = sb.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def foreign_hosts(self):
        port = self.server.port
        return (
            f"localhost:{port}",
            "127.0.0.1",
            f"127.0.0.1:{port}.evil.example",
            f"127.0.0.1:{port}x",
            f"127.0.0.1:{port} ",
            f"127.0.0.1:{port},evil.example",
            f"[::1]:{port}",
            f"0.0.0.0:{port}",
            f"127.0.0.1:{port + 1}",
            "",
            "evil.example",
        )

    def test_every_foreign_host_is_403_forbidden_host_on_api_static_and_ws(self):
        for host in self.foreign_hosts():
            for path in ("/api/v1/status", "/", "/assets/app-abc123.js", "/nope", "/ws"):
                with self.subTest(host=host, path=path):
                    headers = {"Origin": f"http://127.0.0.1:{self.server.port}"} if path == "/ws" else None
                    status, _, body = exchange(self.server, "GET", path, headers, host=host)
                    self.assertEqual((HTTPStatus.FORBIDDEN, "forbidden_host"), (status, code_of(body)))

    def test_a_http_1_0_request_without_a_host_is_refused(self):
        request = (
            b"GET /api/v1/status HTTP/1.0\r\nAuthorization: Bearer "
            + self.server.token.encode()
            + b"\r\n\r\n"
        )
        self.assertIn(b" 403 ", raw_exchange(self.server, request).split(b"\r\n", 1)[0])

    def test_only_the_exact_origin_is_accepted_on_api_routes(self):
        origin = f"http://127.0.0.1:{self.server.port}"
        refused = (
            origin + "/",
            origin.upper(),
            origin.replace("127.0.0.1", "localhost"),
            "null",
            "",
            "http://evil.example",
            origin + ".evil.example",
            f"https://127.0.0.1:{self.server.port}",
        )
        for value in refused:
            with self.subTest(origin=value):
                status, _, body = exchange(self.server, "GET", "/api/v1/status", {"Origin": value})
                self.assertEqual((HTTPStatus.FORBIDDEN, "forbidden_origin"), (status, code_of(body)))
        self.assertEqual(HTTPStatus.OK, exchange(self.server, "GET", "/api/v1/status", {"Origin": origin})[0])
        self.assertEqual(HTTPStatus.OK, exchange(self.server, "GET", "/api/v1/status")[0])

    def test_sec_fetch_site_must_be_exactly_same_origin_when_it_is_sent(self):
        for value in ("cross-site", "same-site", "none", "SAME-ORIGIN", "", "same-origin, cross-site"):
            with self.subTest(value=value):
                status, _, body = exchange(self.server, "GET", "/api/v1/status", {"Sec-Fetch-Site": value})
                self.assertEqual((HTTPStatus.FORBIDDEN, "forbidden_origin"), (status, code_of(body)))
        status, _, _ = exchange(self.server, "GET", "/api/v1/status", {"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(HTTPStatus.OK, status)

    def test_the_origin_checks_run_before_the_token_check(self):
        status, _, body = exchange(
            self.server, "GET", "/api/v1/status", {"Origin": "http://evil.example"}, token=False
        )
        self.assertEqual((HTTPStatus.FORBIDDEN, "forbidden_origin"), (status, code_of(body)))

    def test_the_websocket_refusals_come_in_the_documented_order_and_never_send_101(self):
        origin = f"http://127.0.0.1:{self.server.port}"
        cases = (
            ("wrong host beats everything", {"Origin": "http://evil.example"}, "evil.example", "/ws", 403),
            ("missing origin", {}, None, f"/ws?token={self.server.token}", 403),
            (
                "foreign origin",
                {"Origin": "http://evil.example"},
                None,
                f"/ws?token={self.server.token}",
                403,
            ),
            (
                "cross-site fetch",
                {"Origin": origin, "Sec-Fetch-Site": "cross-site"},
                None,
                f"/ws?token={self.server.token}",
                403,
            ),
            ("missing token", {"Origin": origin}, None, "/ws", 401),
            ("wrong token", {"Origin": origin}, None, "/ws?token=wrong", 401),
            (
                "header token only",
                {"Origin": origin, "Authorization": f"Bearer {self.server.token}"},
                None,
                "/ws",
                401,
            ),
        )
        for label, headers, host, path, expected in cases:
            with self.subTest(case=label):
                upgrade = {
                    "Upgrade": "websocket",
                    "Connection": "Upgrade",
                    "Sec-WebSocket-Key": WS_KEY,
                    "Sec-WebSocket-Version": "13",
                    **headers,
                }
                status, _, _ = exchange(self.server, "GET", path, upgrade, host=host, token=False)
                self.assertEqual(expected, status)


# --------------------------------------------------------------------------- verbs


class VerbMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT
        cls.server = sb.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def test_every_verb_but_get_is_405_with_allow_get_on_every_route(self):
        for route in SAFE_ROUTES:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"):
                with self.subTest(route=route, method=method):
                    status, head, body = exchange(
                        self.server,
                        method,
                        route,
                        body=b'{"a":1}' if method in ("POST", "PUT", "PATCH") else None,
                    )
                    self.assertEqual(HTTPStatus.METHOD_NOT_ALLOWED, status)
                    self.assertEqual("GET", head["Allow"])
                    self.assertFalse([k for k in head.keys() if k.lower().startswith("access-control-")])
                    if method != "HEAD":
                        self.assertEqual("read_only", code_of(body))

    def test_a_write_verb_is_refused_before_the_token_and_host_are_looked_at(self):
        status, head, _ = exchange(self.server, "POST", "/api/v1/tasks", token=False, host="evil.example")
        self.assertEqual((HTTPStatus.METHOD_NOT_ALLOWED, "GET"), (status, head["Allow"]))

    def test_static_paths_refuse_write_verbs_too(self):
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                self.assertEqual(HTTPStatus.METHOD_NOT_ALLOWED, exchange(self.server, method, "/")[0])

    def test_a_refused_request_body_is_never_read_as_the_next_request(self):
        body = b'{"smuggled":true}'
        request = (
            f"POST /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{self.server.port}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        request += (
            f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{self.server.port}\r\n"
            f"Authorization: Bearer {self.server.token}\r\n\r\n"
        ).encode()
        answer = raw_exchange(self.server, request)
        self.assertEqual(1, answer.count(b"HTTP/1.1 "), answer[:300])
        self.assertIn(b" 405 ", answer.split(b"\r\n", 1)[0])

    def test_a_get_with_a_body_does_not_desynchronise_the_connection(self):
        request = (
            f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{self.server.port}\r\n"
            f"Authorization: Bearer {self.server.token}\r\nContent-Length: 40\r\n\r\n"
            f"GET /api/v1/runs HTTP/1.1\r\nHost: x\r\n\r\n"
        ).encode()
        answer = raw_exchange(self.server, request)
        self.assertEqual(1, answer.count(b"HTTP/1.1 "))

    def test_keep_alive_survives_a_404_and_a_422_on_one_connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        self.addCleanup(conn.close)
        headers = {"Authorization": f"Bearer {self.server.token}"}
        statuses = []
        for path in ("/api/v1/nope", "/api/v1/tasks?limit=0", "/api/v1/status", "/missing.js", "/"):
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            response.read()
            statuses.append(response.status)
        self.assertEqual([404, 422, 200, 404, 200], statuses)

    def test_oversized_and_malformed_request_lines_get_a_json_error_and_the_server_lives_on(self):
        self.assertEqual(HTTPStatus.REQUEST_URI_TOO_LONG, exchange(self.server, "GET", "/" + "a" * 70000)[0])
        garbage = raw_exchange(self.server, b"\x00\x01 not http\r\n\r\n")
        self.assertIn(b"bad_request", garbage)
        many = (
            f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{self.server.port}\r\n"
            + "".join(f"X-{i}: 1\r\n" for i in range(150))
            + "\r\n"
        )
        self.assertIn(b" 431 ", raw_exchange(self.server, many.encode()).split(b"\r\n", 1)[0])
        self.assertEqual(HTTPStatus.OK, exchange(self.server, "GET", "/api/v1/status")[0])

    def test_no_response_carries_a_cors_header(self):
        for route in (*SAFE_ROUTES, "/", "/nope", "/api/v1/nope"):
            with self.subTest(route=route):
                _, head, _ = exchange(
                    self.server, "GET", route, {"Origin": f"http://127.0.0.1:{self.server.port}"}
                )
                self.assertFalse([k for k in head.keys() if k.lower().startswith("access-control-")])


# --------------------------------------------------------------------------- static serving


class StaticServingAttackTest(unittest.TestCase):
    def setUp(self):
        base = tmproot.sandbox(self, "dash_qa_static_")
        self.dist = sb.fixture_dist(base / "dist")
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
        (self.outside / "secret.js").write_text("TOP SECRET JS", encoding="utf-8")
        (base / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
        self.project = sb.build_project(base / "project")
        self.server = sb.start_server(self.project, ("--dist-dir", str(self.dist)))
        self.addCleanup(self.server.stop)

    def test_encoded_and_dotted_paths_never_leave_dist(self):
        for path in (
            "/..%2f..%2fsecret.txt",
            "/%2e%2e/%2e%2e/secret.txt",
            "/a%5cb",
            "/..%5c..%5csecret.txt",
            "/c:/windows/win.ini",
            "/a%00b",
            "/./assets/app-abc123.js",
            "/assets/../index.html",
            "/assets/%2e%2e/%2e%2e/secret.txt",
            "/assets%2f..%2f..%2fsecret.txt",
            "/%c0%ae%c0%ae/secret.txt",
            "/%ff",
            "/%2e",
            "/%2E%2E",
            "/assets/app-abc123.js::$DATA",
            "/index.html::$INDEX_ALLOCATION",
            "/assets/app-abc123.js:Zone.Identifier",
            "/assets/app-abc123.js%00.png",
        ):
            with self.subTest(path=path):
                status, _, body = exchange(self.server, "GET", path, token=False)
                self.assertEqual((HTTPStatus.NOT_FOUND, "not_found"), (status, code_of(body)))
                self.assertNotIn(b"TOP SECRET", body)
                self.assertNotIn(b"console.log", body)

    def test_double_encoding_is_decoded_once_so_it_names_a_file_that_does_not_exist(self):
        status, _, body = exchange(self.server, "GET", "/%252e%252e/secret.txt", token=False)
        self.assertEqual(HTTPStatus.NOT_FOUND, status)
        self.assertNotIn(b"TOP SECRET", body)

    def test_a_junction_or_symlink_that_points_outside_dist_is_not_followed(self):
        link = self.dist / "link"
        if os.name == "nt":
            made = (
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(self.outside)],
                    capture_output=True,
                    text=True,
                ).returncode
                == 0
            )
        else:
            try:
                os.symlink(self.outside, link, target_is_directory=True)
                made = True
            except OSError:
                made = False
        if not made:
            self.skipTest("neither a junction nor a symlink could be created here")
        self.addCleanup(lambda: os.rmdir(link) if os.name == "nt" else os.unlink(link))
        for path in ("/link/secret.txt", "/link/secret.js", "/link", "/link/", "/assets/../link/secret.txt"):
            with self.subTest(path=path):
                status, _, body = exchange(self.server, "GET", path, token=False)
                self.assertEqual(HTTPStatus.NOT_FOUND, status)
                self.assertNotIn(b"TOP SECRET", body)

    def test_a_missing_file_with_an_extension_is_404_and_an_extensionless_path_is_the_spa(self):
        for path in ("/missing.js", "/missing.map", "/assets/gone.css", "/deep/nested/missing.png"):
            with self.subTest(path=path):
                self.assertEqual(HTTPStatus.NOT_FOUND, exchange(self.server, "GET", path, token=False)[0])
        for path in ("/board", "/board/x/y", "/tasks/task-0001", "/assets", "/assets/"):
            with self.subTest(path=path):
                status, head, body = exchange(self.server, "GET", path, token=False)
                self.assertEqual((HTTPStatus.OK, "no-store"), (status, head["Cache-Control"]))
                self.assertIn(b'<div id="root">', body)

    def test_characters_a_file_system_would_reject_do_not_crash_the_server(self):
        for path in (
            "/a*b",
            "/a%3Cb",
            "/a%7Cb",
            "/a%22b",
            "/con.txt",
            "/a%0Ab",
            "/" + "a" * 300,
            "/" + "a/" * 200 + "x.js",
            "/%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82",
            "/%20",
            "/a%20",
            "/~",
            "/PROGRA~1",
        ):
            with self.subTest(path=path[:40]):
                status, _, _ = exchange(self.server, "GET", path, token=False)
                self.assertIn(status, (HTTPStatus.OK, HTTPStatus.NOT_FOUND))
        self.assertEqual("", self.server.stderr_text())

    def test_every_static_response_carries_the_security_headers_and_the_right_cache_policy(self):
        for path, cache in (
            ("/", "no-store"),
            ("/board", "no-store"),
            ("/favicon.ico", "no-store"),
            ("/assets/app-abc123.js", "public, max-age=31536000, immutable"),
            ("/assets/note.txt", "public, max-age=31536000, immutable"),
            ("/missing.js", "no-store"),
        ):
            with self.subTest(path=path):
                _, head, _ = exchange(self.server, "GET", path, token=False)
                self.assertEqual(cache, head["Cache-Control"])
                self.assertEqual("nosniff", head["X-Content-Type-Options"])
                self.assertEqual("no-referrer", head["Referrer-Policy"])
                self.assertEqual("DENY", head["X-Frame-Options"])
                directives = {
                    d.split(" ", 1)[0]: d
                    for d in (p.strip() for p in head["Content-Security-Policy"].split(";"))
                }
                self.assertEqual("script-src 'self'", directives["script-src"])
                self.assertEqual("style-src 'self' 'unsafe-inline'", directives["style-src"])
                self.assertEqual(
                    f"connect-src 'self' ws://127.0.0.1:{self.server.port}", directives["connect-src"]
                )
                self.assertEqual("frame-ancestors 'none'", directives["frame-ancestors"])
                for name, directive in directives.items():
                    self.assertNotRegex(directive, r"https?:|\*|unsafe-eval", name)


# --------------------------------------------------------------------------- paging and filters

# route -> (default limit, maximum limit)
PAGED_ROUTES = {
    "/api/v1/tasks": (25, 500),
    "/api/v1/events": (25, 500),
    "/api/v1/memory": (20, 200),
    "/api/v1/handoffs": (25, 200),
}


class PagingBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT
        cls.server = sb.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def get(self, path: str):
        status, _, body = exchange(self.server, "GET", path)
        return status, json.loads(body.decode("utf-8"))

    def test_the_default_limit_and_a_zero_offset_are_echoed(self):
        for route, (default, _) in PAGED_ROUTES.items():
            with self.subTest(route=route):
                status, body = self.get(route)
                self.assertEqual(HTTPStatus.OK, status)
                self.assertEqual((default, 0), (body["meta"]["limit"], body["meta"]["offset"]))
                self.assertLessEqual(len(body["data"]), default)

    def test_the_maximum_limit_is_accepted_and_one_above_it_is_422_naming_limit(self):
        for route, (_, maximum) in PAGED_ROUTES.items():
            with self.subTest(route=route, edge="maximum"):
                status, body = self.get(f"{route}?limit={maximum}")
                self.assertEqual((HTTPStatus.OK, maximum), (status, body["meta"]["limit"]))
            with self.subTest(route=route, edge="above"):
                status, body = self.get(f"{route}?limit={maximum + 1}")
                self.assertEqual(
                    (HTTPStatus.UNPROCESSABLE_ENTITY, "validation_failed"), (status, body["code"])
                )
                self.assertIn("limit", body["errors"])

    def test_invalid_limits_and_offsets_are_422_and_never_a_500(self):
        for route in PAGED_ROUTES:
            for query, field in (
                ("limit=0", "limit"),
                ("limit=-1", "limit"),
                ("limit=abc", "limit"),
                ("limit=1.5", "limit"),
                ("limit=1e2", "limit"),
                ("limit=0x10", "limit"),
                ("limit=%2B5", "limit"),
                ("offset=-1", "offset"),
                ("offset=abc", "offset"),
                ("offset=1000000001", "offset"),
                ("offset=99999999999999999999", "offset"),
                ("limit=0&offset=-1", "limit"),
            ):
                with self.subTest(route=route, query=query):
                    status, body = self.get(f"{route}?{query}")
                    self.assertEqual(
                        (HTTPStatus.UNPROCESSABLE_ENTITY, "validation_failed"), (status, body["code"])
                    )
                    self.assertIn(field, body["errors"])

    def test_every_invalid_parameter_is_reported_in_one_answer(self):
        _, body = self.get("/api/v1/tasks?limit=0&offset=-1&folder=bogus&type=bad%20type")
        self.assertEqual({"limit", "offset", "folder", "type"}, set(body["errors"]))

    def test_an_offset_at_or_beyond_the_total_is_an_empty_page_with_the_true_total(self):
        for route in PAGED_ROUTES:
            _, everything = self.get(f"{route}?limit={PAGED_ROUTES[route][1]}")
            total = everything["meta"]["total"]
            for offset in (total, total + 1, 1_000_000_000):
                with self.subTest(route=route, offset=offset):
                    status, body = self.get(f"{route}?offset={offset}")
                    self.assertEqual(
                        (HTTPStatus.OK, [], total, offset),
                        (status, body["data"], body["meta"]["total"], body["meta"]["offset"]),
                    )

    def test_walking_the_pages_returns_every_row_once_and_the_total_is_exact(self):
        keys = {
            "/api/v1/tasks": "id",
            "/api/v1/events": "id",
            "/api/v1/memory": "id",
            "/api/v1/handoffs": "task",
        }
        for route, key in keys.items():
            _, whole = self.get(f"{route}?limit={PAGED_ROUTES[route][1]}")
            total = whole["meta"]["total"]
            expected = [row[key] for row in whole["data"]]
            self.assertEqual(total, len(expected), route)
            for size in (1, 7, 20):
                seen = []
                for offset in range(0, total + size, size):
                    _, page = self.get(f"{route}?limit={size}&offset={offset}")
                    self.assertLessEqual(len(page["data"]), size)
                    self.assertEqual(total, page["meta"]["total"])
                    seen += [row[key] for row in page["data"]]
                with self.subTest(route=route, size=size):
                    self.assertEqual(expected, seen)

    def test_a_filter_changes_the_total_to_the_filtered_count(self):
        _, all_events = self.get("/api/v1/events?limit=500")
        _, gates = self.get("/api/v1/events?kind=gate&limit=500")
        _, failed = self.get("/api/v1/events?kind=gate&result=fail&limit=500")
        self.assertEqual(len(all_events["data"]), all_events["meta"]["total"])
        self.assertEqual(len(gates["data"]), gates["meta"]["total"])
        self.assertEqual(len(failed["data"]), failed["meta"]["total"])
        self.assertLess(failed["meta"]["total"], gates["meta"]["total"])
        self.assertLess(gates["meta"]["total"], all_events["meta"]["total"])
        self.assertTrue(all(e["kind"] == "gate" and e["outcome"] == "fail" for e in failed["data"]))

    def test_the_first_occurrence_of_a_repeated_parameter_wins_and_a_blank_one_is_the_default(self):
        _, body = self.get("/api/v1/tasks?limit=2&limit=500")
        self.assertEqual(2, body["meta"]["limit"])
        for blank in ("limit=", "limit=%20", "offset="):
            _, body = self.get(f"/api/v1/tasks?{blank}")
            self.assertEqual((25, 0), (body["meta"]["limit"], body["meta"]["offset"]), blank)

    def test_text_filters_treat_sql_and_glob_characters_literally(self):
        for query in ("q=%25", "q=_", "q=%27%20OR%201%3D1--", "q=%5C", "q=%22"):
            with self.subTest(query=query):
                status, body = self.get(f"/api/v1/tasks?{query}")
                self.assertEqual((HTTPStatus.OK, 0), (status, body["meta"]["total"]))
        for query in ("q=%25", "q=_", "q=%27", "q=%22", "q=NEAR%28", "q=AND", "q=-alpha", "q=alpha%2A"):
            with self.subTest(query=query):
                self.assertEqual(HTTPStatus.OK, self.get(f"/api/v1/memory?{query}")[0])
        for query in ("task=%25", "task=_", "task=a%20b", "task=" + "a" * 33):
            with self.subTest(query=query):
                self.assertEqual(HTTPStatus.UNPROCESSABLE_ENTITY, self.get(f"/api/v1/events?{query}")[0])

    def test_a_huge_query_text_is_answered_not_crashed_on(self):
        status, body = self.get("/api/v1/memory?q=" + "a" * 8000)
        self.assertEqual((HTTPStatus.OK, 0), (status, body["meta"]["total"]))

    def test_metrics_window_edges(self):
        for query, expected in (
            ("days=1", 200),
            ("days=90", 200),
            ("days=0", 422),
            ("days=91", 422),
            ("since=2026-10-01", 200),
            ("since=2026-02-30", 422),
            ("since=2026-13-01", 422),
            ("since=20261001", 422),
            ("since=2026-10-01&days=7", 422),
            ("since=2999-01-01", 200),
            ("days=abc&since=2026-10-01", 422),
        ):
            with self.subTest(query=query):
                self.assertEqual(expected, self.get(f"/api/v1/metrics?{query}")[0])

    def test_codegraph_limits(self):
        for limit, expected in ((9, 422), (10, 200), (400, 200), (401, 422)):
            with self.subTest(limit=limit):
                self.assertEqual(expected, self.get(f"/api/v1/codegraph/root/graph?limit={limit}")[0])
        self.assertEqual(
            HTTPStatus.UNPROCESSABLE_ENTITY, self.get("/api/v1/codegraph/root/graph?level=bad")[0]
        )
        self.assertEqual(HTTPStatus.UNPROCESSABLE_ENTITY, self.get("/api/v1/codegraph/bogus/graph")[0])
        self.assertEqual(HTTPStatus.UNPROCESSABLE_ENTITY, self.get("/api/v1/codegraph/..%2f/graph")[0])

    def test_path_ids_are_validated_and_decoded_once(self):
        for path, expected in (
            ("/api/v1/tasks/task-0106", 200),
            ("/api/v1/tasks/task-%30106", 200),
            ("/api/v1/tasks/task-9999", 404),
            ("/api/v1/tasks/task-12", 422),
            ("/api/v1/tasks/TASK-0106", 422),
            ("/api/v1/tasks/task-0106%2F..", 422),
            ("/api/v1/tasks/..%2f..%2fetc", 422),
            ("/api/v1/tasks/task-0106/", 404),
            ("/api/v1/handoffs/task-9999", 404),
            ("/api/v1/handoffs/task-0106.md", 422),
            ("/api/v1/handoffs/..", 422),
        ):
            with self.subTest(path=path):
                self.assertEqual(expected, self.get(path)[0])


# --------------------------------------------------------------------------- failing stores


class StoreFailureMatrixTest(unittest.TestCase):
    """No store, however broken, may turn a route into a 500 or take the server down."""

    @classmethod
    def setUpClass(cls):
        cls.master = PROJECT

    def run_scenario(self, name: str, damage) -> dict[str, int]:
        project = copy_project(self, self.master, name)
        damage(project)
        server = sb.start_server(project)
        statuses: dict[str, int] = {}
        try:
            for route in SAFE_ROUTES:
                status, head, body = exchange(server, "GET", route)
                statuses[route] = status
                with self.subTest(scenario=name, route=route):
                    self.assertIn(
                        status,
                        (HTTPStatus.OK, HTTPStatus.NOT_FOUND, HTTPStatus.SERVICE_UNAVAILABLE),
                        body[:200],
                    )
                    if status == HTTPStatus.SERVICE_UNAVAILABLE:
                        self.assertEqual(("1", "unavailable"), (head["Retry-After"], code_of(body)))
            with self.subTest(scenario=name, check="still serving"):
                self.assertEqual(HTTPStatus.OK, exchange(server, "GET", "/api/v1/tasks")[0])
        finally:
            server.stop()
        with self.subTest(scenario=name, check="stderr"):
            self.assertNotIn("Traceback", server.stderr_text())
        return statuses

    def test_a_corrupt_journal_is_503_where_it_is_read_and_200_everywhere_else(self):
        statuses = self.run_scenario(
            "corrupt_events", lambda p: (p / ".agentry/state/events.db").write_bytes(b"x" * 4000)
        )
        self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, statuses["/api/v1/events"])
        self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, statuses["/api/v1/metrics"])
        self.assertEqual(HTTPStatus.OK, statuses["/api/v1/memory"])
        self.assertEqual(HTTPStatus.OK, statuses["/api/v1/tasks"])

    def test_a_corrupt_memory_store_is_503_on_exactly_the_memory_routes(self):
        statuses = self.run_scenario(
            "corrupt_memory", lambda p: (p / ".agentry/memory/memory.db").write_bytes(b"x" * 4000)
        )
        for route in ("/api/v1/memory", "/api/v1/memory/stats", "/api/v1/graphs/memory"):
            self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, statuses[route], route)
        self.assertEqual(HTTPStatus.OK, statuses["/api/v1/events"])

    def test_a_corrupt_run_store_is_503_where_runs_are_read(self):
        statuses = self.run_scenario(
            "corrupt_run", lambda p: (p / ".agentry/state/run.db").write_bytes(b"x" * 4000)
        )
        self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, statuses["/api/v1/runs"])

    def test_one_corrupt_lane_store_does_not_hide_the_others_silently(self):
        statuses = self.run_scenario(
            "corrupt_lane", lambda p: (p / ".agentry/state/run.fix.db").write_bytes(b"x" * 4000)
        )
        self.assertEqual(HTTPStatus.SERVICE_UNAVAILABLE, statuses["/api/v1/runs"])

    def test_zero_byte_stores_read_as_empty(self):
        def damage(project):
            for relative in (
                ".agentry/state/events.db",
                ".agentry/state/run.db",
                ".agentry/memory/memory.db",
            ):
                (project / relative).write_bytes(b"")

        statuses = self.run_scenario("zero_byte", damage)
        self.assertEqual(HTTPStatus.OK, statuses["/api/v1/events"])
        self.assertEqual(HTTPStatus.OK, statuses["/api/v1/memory"])

    def test_stores_with_an_unrelated_schema_read_as_empty(self):
        def damage(project):
            for relative in (
                ".agentry/state/events.db",
                ".agentry/state/run.db",
                ".agentry/memory/memory.db",
            ):
                target = project / relative
                for sidecar in target.parent.glob(target.name + "-*"):
                    sidecar.unlink()
                target.unlink()
                conn = sqlite3.connect(target)
                conn.execute("CREATE TABLE unrelated (a)")
                conn.commit()
                conn.close()

        statuses = self.run_scenario("unrelated_schema", damage)
        self.assertTrue(
            all(status != HTTPStatus.SERVICE_UNAVAILABLE for status in statuses.values()), statuses
        )

    def test_a_run_store_from_an_older_schema_is_read_with_defaults(self):
        def damage(project):
            conn = sqlite3.connect(project / ".agentry/state/run.db")
            conn.execute("CREATE TABLE slim AS SELECT task, stage FROM runs")
            conn.execute("DROP TABLE runs")
            conn.execute("ALTER TABLE slim RENAME TO runs")
            conn.commit()
            conn.close()

        statuses = self.run_scenario("old_schema", damage)
        self.assertEqual(HTTPStatus.OK, statuses["/api/v1/runs"])

    def test_broken_configuration_and_junk_task_files_do_not_break_any_route(self):
        def damage(project):
            (project / ".agentry/pipeline.json").write_text("{not json", encoding="utf-8")
            done = project / ".agentry/tasks/done"
            (done / "task-0999.md").write_bytes(b"\xff\xfe\x00 binary junk")
            (done / "task-0998.md").write_bytes(b"")
            (done / "task-0997.md").write_bytes(b"---\ntitle: [unclosed\n")
            (done / "task-0996.md").write_bytes(b"---\n" + b"title: x\n" * 5 + b"no closing fence")
            (project / ".agentry/tasks/active/task-0995.md").mkdir()

        statuses = self.run_scenario("junk", damage)
        self.assertTrue(all(status in (200, 404) for status in statuses.values()), statuses)


class NeedsYouReasonTest(unittest.TestCase):
    """The reason shown on a Needs-you card has to belong to the checkpoint that is waiting now."""

    def test_a_checkpoint_does_not_borrow_the_reason_of_an_older_block(self):
        """DEFECT (core, views_pipeline._stop_reasons): the newest blocked-or-parked row WITH a reason
        wins, and the harness writes a reason on 2 of the 29 such rows in this repository's own
        journal. A task that was once blocked for "gate failed twice" and now waits for the commit
        approval is shown with the old block reason instead of the commit text."""
        project = copy_project(self, PROJECT, "needs_reason")
        sb.seed(
            project,
            {
                "runs": [{"task": "task-0110", "stage": "ready", "fields": {"awaiting_human": "commit"}}],
                "events": [
                    {
                        "kind": "blocked",
                        "task": "task-0110",
                        "stage": "test",
                        "detail": {"reason": "gate failed twice"},
                    },
                    {"kind": "parked", "task": "task-0110", "stage": "ready", "outcome": "commit"},
                ],
            },
        )
        server = sb.start_server(project)
        self.addCleanup(server.stop)
        _, _, body = exchange(server, "GET", "/api/v1/runs")
        run = next(r for r in json.loads(body)["data"] if r["task"] == "task-0110")
        self.assertEqual("commit", run["needs_you"]["kind"])
        self.assertEqual("Read the diff and approve the commit", run["needs_you"]["reason"])


class LockedStoreTest(unittest.TestCase):
    """A store another process holds exclusively is 503 within the FR-14 budget, never a hang, and the
    route recovers by itself. (events.db is in WAL mode, where a writer never blocks a reader, so the
    stores that can be locked against the dashboard are the rollback-journal ones.)"""

    BUDGET_SECONDS = 1.5

    def lock(self, path: Path) -> sqlite3.Connection:
        locker = sqlite3.connect(path, timeout=0.1, isolation_level=None)
        locker.execute("BEGIN EXCLUSIVE")
        self.addCleanup(locker.close)
        return locker

    def test_a_locked_memory_store_is_503_in_time_and_recovers(self):
        project = copy_project(self, PROJECT, "lock_memory")
        server = sb.start_server(project)
        self.addCleanup(server.stop)
        locker = self.lock(project / ".agentry" / "memory" / "memory.db")
        for route in (
            "/api/v1/memory",
            "/api/v1/memory?q=alpha",
            "/api/v1/memory/stats",
            "/api/v1/graphs/memory",
        ):
            with self.subTest(route=route):
                started = time.monotonic()
                status, head, body = exchange(server, "GET", route)
                self.assertLess(time.monotonic() - started, self.BUDGET_SECONDS)
                self.assertEqual(
                    (HTTPStatus.SERVICE_UNAVAILABLE, "1", "unavailable"),
                    (status, head["Retry-After"], code_of(body)),
                )
        self.assertEqual(HTTPStatus.OK, exchange(server, "GET", "/api/v1/tasks")[0])
        locker.execute("ROLLBACK")
        self.assertEqual(HTTPStatus.OK, exchange(server, "GET", "/api/v1/memory")[0])

    def test_a_locked_run_store_is_503_on_runs_and_the_other_routes_keep_answering(self):
        project = copy_project(self, PROJECT, "lock_runs")
        server = sb.start_server(project)
        self.addCleanup(server.stop)
        locker = self.lock(project / ".agentry" / "state" / "run.db")
        started = time.monotonic()
        status, head, _ = exchange(server, "GET", "/api/v1/runs")
        self.assertLess(time.monotonic() - started, self.BUDGET_SECONDS)
        self.assertEqual((HTTPStatus.SERVICE_UNAVAILABLE, "1"), (status, head["Retry-After"]))
        for route in (
            "/api/v1/tasks",
            "/api/v1/events",
            "/api/v1/memory",
            "/api/v1/harness",
        ):
            with self.subTest(route=route):
                self.assertEqual(HTTPStatus.OK, exchange(server, "GET", route)[0])
        locker.execute("ROLLBACK")
        self.assertEqual(HTTPStatus.OK, exchange(server, "GET", "/api/v1/runs")[0])

    def test_a_write_locked_journal_in_wal_mode_does_not_block_a_reader(self):
        project = copy_project(self, PROJECT, "lock_events")
        server = sb.start_server(project)
        self.addCleanup(server.stop)
        self.lock(project / ".agentry" / "state" / "events.db")
        started = time.monotonic()
        status, _, body = exchange(server, "GET", "/api/v1/events")
        self.assertLess(time.monotonic() - started, self.BUDGET_SECONDS)
        self.assertEqual((HTTPStatus.OK, 120), (status, json.loads(body)["meta"]["total"]))


# --------------------------------------------------------------------------- read-only guarantee


class ReadOnlyUnderAbuseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT

    def test_hostile_traffic_changes_no_byte_of_the_project(self):
        before = sb.hash_tree(self.project)
        server = sb.start_server(self.project)
        try:
            for route in SAFE_ROUTES:
                exchange(server, "GET", route)
                for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
                    exchange(server, method, route, body=b"{}" if method != "OPTIONS" else None)
                exchange(server, "GET", route, token=False)
                exchange(
                    server, "GET", route + ("&" if "?" in route else "?") + "limit=0&offset=-1&q=%00&kind=%ff"
                )
            for path in (
                "/..%2f..%2f.agentry%2fpipeline.json",
                "/a%5cb",
                "/assets/%2e%2e/index.html",
                "/nope.js",
            ):
                exchange(server, "GET", path, token=False)
            client = WsClient(server)
            client.frame()
            client.send(
                masked_frame(
                    OP_TEXT, json.dumps({"type": "command", "v": 1, "payload": {"do": "approve"}}).encode()
                )
            )
            refusal = client.message()
            self.assertEqual("command.refused", refusal["type"])
            client.send(masked_frame(OP_BINARY, b"\x00"))
            client.close()
        finally:
            server.stop()
        after = sb.hash_tree(self.project)
        self.assertEqual(
            {},
            {
                k: (before.get(k), after.get(k))
                for k in set(before) | set(after)
                if before.get(k) != after.get(k)
            },
        )

    def test_an_empty_project_is_not_given_a_single_file_or_folder(self):
        empty = tmproot.sandbox(self, "dash_qa_empty_") / "project"
        (empty / ".agentry").mkdir(parents=True)
        before = sb.hash_tree(empty)
        server = sb.start_server(empty)
        try:
            for route in SAFE_ROUTES:
                status, _, _ = exchange(server, "GET", route)
                self.assertIn(status, (HTTPStatus.OK, HTTPStatus.NOT_FOUND), route)
        finally:
            server.stop()
        self.assertEqual(before, sb.hash_tree(empty))

    def test_no_store_or_document_stays_open_after_any_response_including_the_error_ones(self):
        server = sb.start_server(self.project)
        self.addCleanup(server.stop)
        stores = [
            self.project / ".agentry/state/run.db",
            self.project / ".agentry/state/run.fix.db",
            self.project / ".agentry/state/events.db",
            self.project / ".agentry/memory/memory.db",
            self.project / ".agentry/tasks/done/task-0106.md",
            self.project / ".agentry/tasks/handoffs/task-0106.md",
            self.project / ".agentry/pipeline.json",
            self.project / ".codegraph/codegraph.db",
        ]
        urls = (
            *SAFE_ROUTES,
            "/api/v1/tasks?limit=0",
            "/api/v1/nope",
            "/api/v1/tasks/task-9999",
            "/api/v1/codegraph/root/graph?level=file",
            "/api/v1/memory?q=alpha&retired=include",
        )
        for route in urls:
            exchange(server, "GET", route)
            for store in stores:
                moved = store.with_name(store.name + ".moved")
                with self.subTest(route=route, store=store.name):
                    os.replace(store, moved)
                    os.replace(moved, store)

    def test_the_server_never_writes_a_token_or_a_line_outside_the_launch_line(self):
        server = sb.start_server(self.project)
        try:
            for route in SAFE_ROUTES:
                exchange(server, "GET", route)
                exchange(server, "GET", route, token=False)
            exchange(server, "GET", "/api/v1/tasks?limit=0")
            exchange(server, "FOO", "/api/v1/status")
        finally:
            server.stop()
        self.assertEqual(1, len(server.stdout_lines))
        self.assertEqual("", server.stderr_text())
        leaked = [
            p for p in self.project.rglob("*") if p.is_file() and server.token.encode() in p.read_bytes()
        ]
        self.assertEqual([], leaked)


# --------------------------------------------------------------------------- values in every response


class ResponseValueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT
        cls.server = sb.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def problems(self, route: str, node, path: str = "") -> list:
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if value is None and key not in NULLABLE:
                    found.append((route, f"{path}.{key}", "null"))
                if key in TIMESTAMP_KEYS and isinstance(value, str) and value and not ISO_Z.match(value):
                    found.append((route, f"{path}.{key}", value))
                found += self.problems(route, value, f"{path}.{key}")
        elif isinstance(node, list):
            for item in node:
                found += self.problems(route, item, f"{path}[]")
        return found

    def test_every_timestamp_is_iso_utc_and_no_text_field_is_null_on_every_route_and_every_id(self):
        routes = [
            *SAFE_ROUTES,
            "/api/v1/tasks?limit=500&include_superseded=true",
            "/api/v1/events?limit=500",
            "/api/v1/memory?kind=pattern",
            "/api/v1/memory?kind=module",
            "/api/v1/memory?retired=include",
            "/api/v1/handoffs?limit=200",
            "/api/v1/metrics?days=90",
        ]
        _, _, listing = exchange(self.server, "GET", "/api/v1/tasks?limit=500&include_superseded=true")
        routes += [f"/api/v1/tasks/{t['id']}" for t in json.loads(listing)["data"]]
        _, _, docs = exchange(self.server, "GET", "/api/v1/handoffs?limit=200")
        routes += [f"/api/v1/handoffs/{d['task']}" for d in json.loads(docs)["data"]]
        found = []
        for route in routes:
            status, _, body = exchange(self.server, "GET", route)
            self.assertEqual(HTTPStatus.OK, status, route)
            found += self.problems(route, json.loads(body)["data"])
        self.assertEqual([], found)

    def test_every_body_is_strict_json_with_the_documented_envelope(self):
        for route in SAFE_ROUTES:
            with self.subTest(route=route):
                _, head, body = exchange(self.server, "GET", route)
                self.assertEqual("application/json; charset=utf-8", head["Content-Type"])
                self.assertEqual(str(len(body)), head["Content-Length"])
                parsed = json.loads(body.decode("utf-8"), parse_constant=self.fail)
                self.assertIn("data", parsed)
                self.assertEqual("no-store", head["Cache-Control"])

    def test_the_documented_error_shape_holds_for_every_error_status(self):
        cases = (
            (401, "/api/v1/status", {"token": False}),
            (404, "/api/v1/nope", {}),
            (422, "/api/v1/tasks?limit=0", {}),
            (405, "/api/v1/status", {"method": "POST"}),
            (403, "/api/v1/status", {"host": "evil.example"}),
        )
        for expected, path, options in cases:
            with self.subTest(status=expected):
                status, _, body = exchange(
                    self.server,
                    options.get("method", "GET"),
                    path,
                    token=options.get("token", True),
                    host=options.get("host"),
                )
                parsed = json.loads(body)
                self.assertEqual(expected, status)
                self.assertTrue({"message", "code"} <= set(parsed))
                self.assertNotIn("Traceback", parsed["message"])


# --------------------------------------------------------------------------- websocket


class WebSocketAbuseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT
        cls.server = sb.start_server(cls.project)
        cls.addClassCleanup(cls.server.stop)

    def connect(self, **kwargs) -> WsClient:
        client = WsClient(self.server, **kwargs)
        self.addCleanup(client.close)
        return client

    def ready(self) -> WsClient:
        """A connected client that has read its hello. A slot freed by an earlier test may take a
        moment to be released by the server, so a 503 is retried briefly."""
        deadline = time.monotonic() + 3
        client = self.connect()
        while client.status == HTTPStatus.SERVICE_UNAVAILABLE and time.monotonic() < deadline:
            client.close()
            time.sleep(0.1)
            client = self.connect()
        self.assertEqual(101, client.status)
        self.assertEqual("hello", client.message()["type"])
        return client

    def test_the_server_never_grants_a_subprotocol_or_an_extension_the_client_asked_for(self):
        client = self.connect(
            headers={"Sec-WebSocket-Protocol": "chat", "Sec-WebSocket-Extensions": "permessage-deflate"}
        )
        self.assertEqual(101, client.status)
        self.assertIsNone(client.header("Sec-WebSocket-Protocol"))
        self.assertIsNone(client.header("Sec-WebSocket-Extensions"))
        rfc = self.connect(headers={"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
        self.assertEqual("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", rfc.header("Sec-WebSocket-Accept"))

    def test_malformed_upgrades_are_refused_with_the_documented_status_and_no_101(self):
        cases = (
            ("upgrade is not websocket", {"Upgrade": "h2c"}, 400),
            ("connection lacks upgrade", {"Connection": "keep-alive"}, 400),
            ("version 8", {"Sec-WebSocket-Version": "8"}, 426),
            ("no version", {"Sec-WebSocket-Version": None}, 426),
            ("key too short", {"Sec-WebSocket-Key": "abc"}, 400),
            ("key not base64", {"Sec-WebSocket-Key": "!" * 24}, 400),
            ("key of 17 bytes", {"Sec-WebSocket-Key": base64.b64encode(b"x" * 17).decode()}, 400),
            ("no key", {"Sec-WebSocket-Key": None}, 400),
        )
        for label, headers, expected in cases:
            with self.subTest(case=label):
                client = self.connect(headers=headers)
                self.assertEqual(expected, client.status)
                if expected == 426:
                    self.assertEqual("13", client.header("Sec-WebSocket-Version"))

    def test_header_variants_that_are_legal_are_accepted(self):
        for headers in (
            {"Upgrade": "WebSocket"},
            {"Connection": "keep-alive, Upgrade"},
            {"Connection": "UPGRADE"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(101, self.connect(headers=headers).status)

    def test_the_first_token_of_a_repeated_query_parameter_is_the_one_checked(self):
        self.assertEqual(101, self.connect(path=f"/ws?token={self.server.token}&token=x").status)
        self.assertEqual(401, self.connect(path=f"/ws?token=x&token={self.server.token}").status)

    def test_protocol_violations_close_with_the_documented_code(self):
        cases = (
            ("reserved bit", bytes([0xC1, 0x80]) + b"\0\0\0\0", 1002),
            ("unknown opcode", masked_frame(0x3, b"x"), 1002),
            ("fragmented control frame", masked_frame(OP_PING, b"x", fin=False), 1002),
            ("control frame over 125 bytes", masked_frame(OP_PING, b"x" * 126), 1002),
            ("continuation without a start", masked_frame(0x0, b"x"), 1002),
            (
                "new message inside a fragmented one",
                masked_frame(OP_TEXT, b"{", fin=False) + masked_frame(OP_TEXT, b"}"),
                1002,
            ),
            ("close with a one-byte payload", masked_frame(OP_CLOSE, b"\x03"), 1002),
            ("close with the reserved code 1005", masked_frame(OP_CLOSE, struct.pack(">H", 1005)), 1002),
            ("close with the reserved code 1006", masked_frame(OP_CLOSE, struct.pack(">H", 1006)), 1002),
            (
                "close reason that is not utf-8",
                masked_frame(OP_CLOSE, struct.pack(">H", 1000) + b"\xff"),
                1007,
            ),
            ("unmasked client frame", b"\x81\x02hi", 1002),
            ("binary frame", masked_frame(OP_BINARY, b"\x00"), 1003),
            ("text that is not utf-8", masked_frame(OP_TEXT, b"\xff\xfe"), 1007),
            ("text that is not json", masked_frame(OP_TEXT, b"not json"), 1007),
            ("frame of 65537 bytes", masked_frame(OP_TEXT, b"x" * 65537), 1009),
            (
                "frame claiming 2**63 bytes",
                bytes([0x81, 0xFF]) + struct.pack(">Q", 2**63) + b"\0\0\0\0",
                1009,
            ),
            (
                "three fragments adding up to 70000 bytes",
                masked_frame(OP_TEXT, b"a" * 30000, fin=False)
                + masked_frame(0x0, b"a" * 30000, fin=False)
                + masked_frame(0x0, b"a" * 10000),
                1009,
            ),
        )
        for label, data, expected in cases:
            with self.subTest(case=label):
                client = self.ready()
                client.send(data)
                self.assertEqual(expected, client.close_code(), label)
                client.close()

    def test_a_message_of_exactly_the_limit_is_accepted(self):
        client = self.ready()
        padding = 65536 - len(b'{"type":"command","pad":""}')
        client.send(masked_frame(OP_TEXT, b'{"type":"command","pad":"' + b"a" * padding + b'"}'))
        self.assertEqual("command.refused", client.message()["type"])

    def test_close_frames_are_echoed_with_their_code(self):
        for code in (1000, 1001, 3000, 4999):
            with self.subTest(code=code):
                client = self.ready()
                client.send(masked_frame(OP_CLOSE, struct.pack(">H", code)))
                self.assertEqual(code, client.close_code())
        client = self.ready()
        client.send(masked_frame(OP_CLOSE, b""))
        self.assertEqual(0, client.close_code())

    def test_pings_are_answered_even_between_the_fragments_of_a_message(self):
        client = self.ready()
        client.send(masked_frame(OP_TEXT, b'{"type":"comm', fin=False))
        client.send(masked_frame(OP_PING, b"abc"))
        client.send(masked_frame(0x0, b'and"}'))
        self.assertEqual((OP_PONG, b"abc"), client.frame())
        self.assertEqual("command.refused", client.message()["type"])

    def test_every_client_message_shape_gets_an_answer_and_the_connection_survives(self):
        shapes = {
            b"[]": "error",
            b"null": "error",
            b"NaN": "error",
            b'{"type":1}': "error",
            b'{"type":"x"}': "error",
            b"{}": "error",
            b'{"type":"command","v":true}': "error",
            b'{"type":"command","v":2}': "error",
            b'{"type":"command","v":1.0}': "command.refused",
            b'{"type":"command"}': "command.refused",
            b'{"type":"command","payload":{"approve":"task-0106"}}': "command.refused",
        }
        client = self.ready()
        for data, expected in shapes.items():
            with self.subTest(data=data):
                client.send(masked_frame(OP_TEXT, data))
                self.assertEqual(expected, client.message()["type"])
        client.send(masked_frame(OP_PING, b"alive"))
        self.assertEqual((OP_PONG, b"alive"), client.frame())

    def test_the_ninth_client_is_refused_and_a_freed_slot_is_reusable(self):
        clients = [self.ready() for _ in range(8)]
        ninth = self.connect()
        self.assertEqual(503, ninth.status)
        self.assertEqual("1", ninth.header("Retry-After"))
        clients[0].send(masked_frame(OP_CLOSE, struct.pack(">H", 1000)))
        self.assertEqual(1000, clients[0].close_code())
        deadline = time.monotonic() + 3
        status = 503
        while status == 503 and time.monotonic() < deadline:
            probe = self.connect()
            status = probe.status
            if status == 503:
                time.sleep(0.1)
        self.assertEqual(101, status)

    def test_a_deeply_nested_message_is_answered_and_leaves_no_traceback(self):
        """DEFECT (realtime, ws.py decode_message): the reader thread dies on RecursionError, the
        connection goes mute (no answer, no pong, no silence timeout) and a traceback reaches stderr."""
        project = copy_project(self, self.project, "ws_deep")
        server = sb.start_server(project)
        self.addCleanup(server.stop)
        client = WsClient(server)
        self.addCleanup(client.close)
        self.assertEqual("hello", client.message()["type"])
        client.send(masked_frame(OP_TEXT, b"[" * 30000 + b"]" * 30000))
        answer = client.frame(timeout=3)
        self.assertIsNotNone(answer, "no answer and no close: the reader thread is dead")
        time.sleep(0.3)
        self.assertNotIn("Traceback", server.stderr_text())

    def test_a_client_that_vanishes_mid_frame_frees_its_slot(self):
        for _ in range(3):
            client = self.ready()
            client.send(b"\x81")
            client.close()
        deadline = time.monotonic() + 5
        freed = False
        while not freed and time.monotonic() < deadline:
            probes = [WsClient(self.server) for _ in range(8)]
            freed = all(probe.status == 101 for probe in probes)
            for probe in probes:
                probe.close()
            if not freed:
                time.sleep(0.2)
        self.assertTrue(freed, "slots were not freed after clients vanished")


# --------------------------------------------------------------------------- process lifecycle


class LifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT

    def test_the_idle_clock_restarts_when_the_last_websocket_client_leaves(self):
        """DEFECT (core, server.py watch_idle): only an HTTP request moves the idle clock, so after
        a long WebSocket session the first moment the count reaches zero (a closed tab, a reload,
        a reconnect after the laptop slept) satisfies 'idle for the whole timeout' and the server
        exits within a poll interval, taking the dashboard with it."""
        server = sb.start_server(self.project, ("--idle-timeout", "3"))
        self.addCleanup(server.stop)
        exchange(server, "GET", "/api/v1/status")
        client = WsClient(server)
        self.assertEqual("hello", client.message()["type"])
        time.sleep(4.5)
        self.assertTrue(server.alive(), "a connected client did not keep the server up")
        client.close()
        time.sleep(1.2)
        self.assertTrue(server.alive(), "the server exited as soon as the last client left")
        deadline = time.monotonic() + 8
        while server.alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(server.alive(), "the server never went idle")
        self.assertEqual(0, server.proc.returncode)

    def test_a_connected_websocket_client_holds_the_server_past_the_idle_timeout(self):
        server = sb.start_server(self.project, ("--idle-timeout", "2"))
        self.addCleanup(server.stop)
        client = WsClient(server)
        self.addCleanup(client.close)
        self.assertEqual("hello", client.message()["type"])
        time.sleep(3.5)
        self.assertTrue(server.alive())

    def test_a_silent_server_with_no_client_exits_zero_after_the_timeout(self):
        server = sb.start_server(self.project, ("--idle-timeout", "1"))
        self.addCleanup(server.stop)
        deadline = time.monotonic() + 6
        while server.alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(server.alive())
        self.assertEqual(0, server.proc.returncode)

    def test_half_open_connections_do_not_block_a_clean_stop(self):
        server = sb.start_server(self.project)
        hung = [socket.create_connection(("127.0.0.1", server.port)) for _ in range(5)]
        hung[0].sendall(b"GET /api/v1/sta")
        hung[1].sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\nX-Slow: ".encode())
        client = WsClient(server)
        self.assertEqual("hello", client.message()["type"])
        started = time.monotonic()
        code = server.stop(timeout=10)
        elapsed = time.monotonic() - started
        for sock in hung:
            sock.close()
        client.close()
        self.assertEqual(0, code)
        self.assertLess(elapsed, 6.0)

    def test_the_server_needs_no_node_on_the_path(self):
        python_dir = os.path.dirname(sys.executable)
        self.assertIsNone(shutil.which("node", path=python_dir))
        server = sb.start_server(self.project, env_extra={"PATH": python_dir})
        self.addCleanup(server.stop)
        self.assertEqual(HTTPStatus.OK, exchange(server, "GET", "/api/v1/status")[0])

    def test_a_project_path_full_of_url_and_shell_metacharacters_is_served_in_full(self):
        weird = (
            tmproot.sandbox(self, "dash_qa_path_")
            / "dir #1 %41 & co+x 'q' [b] {c} ;d =e @f ~g \u043f\u0440\u0438\u043c\u0435\u0440"
        )
        sb.build_project(weird)
        server = sb.start_server(weird)
        self.addCleanup(server.stop)
        for route in SAFE_ROUTES:
            with self.subTest(route=route):
                # the weird project carries no code index, so its graph route is the one 404
                expected = (
                    HTTPStatus.NOT_FOUND
                    if route.endswith("/graph") and "codegraph" in route
                    else HTTPStatus.OK
                )
                self.assertEqual(expected, exchange(server, "GET", route)[0])
        _, _, body = exchange(server, "GET", "/api/v1/events")
        self.assertEqual(20, json.loads(body)["meta"]["total"])
        _, _, body = exchange(server, "GET", "/api/v1/memory")
        self.assertEqual(2, json.loads(body)["meta"]["total"])


if __name__ == "__main__":
    unittest.main()
