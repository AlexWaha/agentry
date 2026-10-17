"""The security model of the dashboard server (spec-0002 FR-54 to FR-63).

Most checks run against a DashboardServer started on a thread in this process (fast, and it lets
a test patch a route or the mimetypes registry); the token, log-silence and launch-line checks run
against the real server.py as a child process.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import http.client
import io
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import dashboard_sandbox as sb
import server
import stores
import tmproot

TOKEN = "t" * 43
REFUSED_PATHS = ("/..%2f..%2fsecret.txt", "/a%5cb", "/c:/x", "/a%00b", "/./assets/app-abc123.js",
                 "/../secret.txt", "/assets/../../secret.txt", "/%ff", "/assets/%2e%2e/%2e%2e/secret.txt",
                 "/%2e%2e/secret.txt")


class LocalServer:
    """A DashboardServer on a thread of this process, with a recording stand-in for ws."""

    def __init__(self, case: unittest.TestCase, project: Path | None = None) -> None:
        base = tmproot.sandbox(case, "dash_sec_")
        self.dist = sb.fixture_dist(base / "dist")
        (base / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
        self.project = project or (base / "project")
        (self.project / ".agentry").mkdir(parents=True, exist_ok=True)
        self.upgrades: list = []
        self.httpd = server.DashboardServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.ctx = stores.Ctx(self.project, TOOLS_DIR.parent, self.port, self.origin, TOKEN, self.dist)
        self.httpd.ctx = self.ctx
        self.httpd.ws = SimpleNamespace(handle_upgrade=self._upgrade, client_count=lambda: 0)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                  daemon=True)
        thread.start()
        case.addCleanup(self.close, thread)

    def _upgrade(self, handler, ctx) -> None:
        self.upgrades.append(handler.path)
        handler.send_json_error(400, "bad_request", "stand-in for the websocket")

    def close(self, thread: threading.Thread) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        thread.join(5)

    def get(self, path: str, headers: dict | None = None, token: bool = True, method: str = "GET",
            host: str | None = None):
        merged = {"Authorization": f"Bearer {TOKEN}"} if token else {}
        merged.update(headers or {})
        merged = {k: v for k, v in merged.items() if v is not None}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                conn.putheader("Host", host)
            for name, value in merged.items():
                conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, response.msg, response.read(), response.version
        finally:
            conn.close()


def code_of(body: bytes) -> str:
    return json.loads(body.decode("utf-8"))["code"]


class HostOriginTest(unittest.TestCase):

    def setUp(self):
        self.srv = LocalServer(self)

    def test_a_foreign_host_is_403_forbidden_host_on_api_ws_and_static(self):
        for path in ("/api/v1/epics", "/ws", "/", "/assets/app-abc123.js", "/nope"):
            with self.subTest(path=path):
                headers = {"Origin": self.srv.origin} if path == "/ws" else {}
                status, _, body, _ = self.srv.get(path, headers, host="evil.example")
                self.assertEqual((403, "forbidden_host"), (status, code_of(body)))

    def test_the_right_host_with_the_wrong_port_is_refused(self):
        status, _, body, _ = self.srv.get("/api/v1/epics", host=f"127.0.0.1:{self.srv.port + 1}")
        self.assertEqual((403, "forbidden_host"), (status, code_of(body)))
        status, _, body, _ = self.srv.get("/api/v1/epics", host=f"localhost:{self.srv.port}")
        self.assertEqual((403, "forbidden_host"), (status, code_of(body)))

    def test_a_foreign_origin_is_403_forbidden_origin_on_api_and_ws(self):
        for path in ("/api/v1/epics", "/ws"):
            with self.subTest(path=path):
                status, _, body, _ = self.srv.get(path, {"Origin": "http://evil.example"})
                self.assertEqual((403, "forbidden_origin"), (status, code_of(body)))
        self.assertEqual([], self.srv.upgrades)

    def test_the_websocket_requires_an_origin(self):
        status, _, body, _ = self.srv.get(f"/ws?token={TOKEN}", token=False)
        self.assertEqual((403, "forbidden_origin"), (status, code_of(body)))
        self.assertEqual([], self.srv.upgrades)

    def test_an_api_request_without_an_origin_is_fine_with_the_token(self):
        self.assertEqual(200, self.srv.get("/api/v1/epics")[0])
        self.assertEqual(200, self.srv.get("/api/v1/epics", {"Origin": self.srv.origin})[0])

    def test_sec_fetch_site_must_be_same_origin_when_present(self):
        for value, expected in (("cross-site", 403), ("same-site", 403), ("none", 403), ("same-origin", 200)):
            with self.subTest(value=value):
                status, _, body, _ = self.srv.get("/api/v1/epics", {"Sec-Fetch-Site": value})
                self.assertEqual(expected, status)
                if expected == 403:
                    self.assertEqual("forbidden_origin", code_of(body))

    def test_static_files_are_not_subject_to_the_origin_checks(self):
        for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "none"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(200, self.srv.get("/", headers, token=False)[0])

    def test_host_is_checked_before_origin_and_origin_before_the_token(self):
        status, _, body, _ = self.srv.get("/api/v1/epics", {"Origin": "http://evil.example"},
                                          token=False, host="evil.example")
        self.assertEqual("forbidden_host", code_of(body))
        status, _, body, _ = self.srv.get("/api/v1/epics", {"Origin": "http://evil.example",
                                                            "Authorization": "Bearer wrong"})
        self.assertEqual((403, "forbidden_origin"), (status, code_of(body)))

    def test_a_request_with_every_header_right_reaches_the_upgrade_handler(self):
        status, _, _, _ = self.srv.get(f"/ws?token={TOKEN}", {"Origin": self.srv.origin,
                                                                   "Sec-Fetch-Site": "same-origin"},
                                          token=False)
        self.assertEqual(400, status)  # the stand-in answers; the point is that it was reached
        self.assertEqual([f"/ws?token={TOKEN}"], self.srv.upgrades)

    def test_the_websocket_token_comes_from_the_query_and_is_checked_before_the_upgrade(self):
        for query in ("", "?token=wrong", f"?token={TOKEN[:-1]}", "?token="):
            with self.subTest(query=query):
                status, _, body, _ = self.srv.get("/ws" + query, {"Origin": self.srv.origin}, token=False)
                self.assertEqual((401, "unauthorized"), (status, code_of(body)))
        self.assertEqual([], self.srv.upgrades)

    def test_the_websocket_does_not_accept_the_bearer_header_instead_of_the_query(self):
        status, _, _, _ = self.srv.get("/ws", {"Origin": self.srv.origin})
        self.assertEqual(401, status)

    def test_the_websocket_path_is_method_checked_first(self):
        status, head, body, _ = self.srv.get("/ws", {"Origin": self.srv.origin}, method="POST")
        self.assertEqual((405, "read_only", "GET"), (status, code_of(body), head["Allow"]))


class ConnectionLimitTest(unittest.TestCase):
    """A silent client cannot hold a thread for ever, and no flood can start unbounded threads."""

    def test_a_connection_that_sends_nothing_is_dropped_after_the_request_timeout(self):
        self.assertEqual(30.0, server.Handler.timeout)
        with unittest.mock.patch.object(server.Handler, "timeout", 0.3):
            srv = LocalServer(self)
            sock = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
            self.addCleanup(sock.close)
            self.assertEqual(b"", sock.recv(10))  # closed by the server, not by our timeout

    def test_connections_past_the_cap_are_closed_and_a_freed_slot_is_reused(self):
        with unittest.mock.patch.object(server, "MAX_CONNECTIONS", 2):
            srv = LocalServer(self)
        held = [socket.create_connection(("127.0.0.1", srv.port), timeout=5) for _ in range(2)]
        for sock in held:
            self.addCleanup(sock.close)
        extra = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
        self.addCleanup(extra.close)
        with contextlib.suppress(OSError):
            extra.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        try:
            self.assertEqual(b"", extra.recv(10))
        except ConnectionResetError:
            pass
        held[0].close()
        deadline = threading.Event()
        for _ in range(50):  # the freed handler thread notices the close within a moment
            status = None
            with contextlib.suppress(OSError):
                status = srv.get("/")[0]
            if status == 200:
                break
            deadline.wait(0.1)
        self.assertEqual(200, status)


class IdleClockTest(unittest.TestCase):
    """Only a request that passed the Host, Origin and token checks moves the idle clock."""

    def setUp(self):
        self.srv = LocalServer(self)
        self.srv.httpd.last_activity = 0.0

    def test_refused_requests_do_not_count_as_activity(self):
        self.srv.get("/api/v1/status", token=False)
        self.srv.get("/api/v1/status", host="evil.example")
        self.srv.get("/api/v1/status", {"Origin": "http://evil.example"})
        self.srv.get("/", method="POST")
        self.assertEqual(0.0, self.srv.httpd.last_activity)

    def test_a_served_request_counts(self):
        self.srv.get("/api/v1/status")
        self.assertGreater(self.srv.httpd.last_activity, 0.0)

    def test_a_served_static_file_counts(self):
        self.srv.get("/", token=False)
        self.assertGreater(self.srv.httpd.last_activity, 0.0)


class UpgradePathTest(unittest.TestCase):

    def test_an_upgrade_request_on_any_path_but_ws_is_a_404(self):
        srv = LocalServer(self)
        for path in ("/", "/api/v1/status", "/assets/app-abc123.js"):
            with self.subTest(path=path):
                status, _, body, _ = srv.get(path, {"Upgrade": "websocket", "Connection": "Upgrade"})
                self.assertEqual((404, "not_found"), (status, code_of(body)))
        self.assertEqual([], srv.upgrades)


class TokenTest(unittest.TestCase):

    def setUp(self):
        self.srv = LocalServer(self)

    def test_the_api_needs_the_bearer_token(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "},
                        {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"},
                        {"Authorization": f"Bearer {TOKEN}x"}, {"Authorization": f"Bearer x{TOKEN}"}):
            with self.subTest(headers=headers):
                status, _, body, _ = self.srv.get("/api/v1/epics", headers, token=False)
                self.assertEqual((401, "unauthorized"), (status, code_of(body)))

    def test_the_token_is_not_accepted_in_the_query_of_an_api_request(self):
        status, _, _, _ = self.srv.get(f"/api/v1/epics?token={TOKEN}", token=False)
        self.assertEqual(401, status)

    def test_the_right_token_is_200_and_the_scheme_is_case_insensitive(self):
        self.assertEqual(200, self.srv.get("/api/v1/epics")[0])
        self.assertEqual(200, self.srv.get("/api/v1/epics", {"Authorization": f"bearer {TOKEN}"})[0])

    def test_static_files_need_no_token(self):
        for path in ("/", "/assets/app-abc123.js", "/board", "/favicon.svg"):
            with self.subTest(path=path):
                self.assertEqual(200, self.srv.get(path, token=False)[0])

    def test_an_unknown_api_path_is_checked_for_the_token_before_it_is_404(self):
        self.assertEqual(401, self.srv.get("/api/v1/nope", token=False)[0])
        self.assertEqual(404, self.srv.get("/api/v1/nope")[0])

    def test_non_ascii_credentials_do_not_crash_the_comparison(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=10)
        self.addCleanup(conn.close)
        conn.putrequest("GET", "/api/v1/epics")
        conn.putheader("Authorization", "Bearer \xe9\xe9".encode("latin-1"))
        conn.endheaders()
        self.assertEqual(401, conn.getresponse().status)


class HeaderTest(unittest.TestCase):

    def setUp(self):
        self.srv = LocalServer(self)

    def responses(self):
        yield "api 200", self.srv.get("/api/v1/epics")
        yield "api 404", self.srv.get("/api/v1/nope")
        yield "api 401", self.srv.get("/api/v1/epics", token=False)
        yield "api 405", self.srv.get("/api/v1/epics", method="POST")
        yield "index", self.srv.get("/", token=False)
        yield "asset", self.srv.get("/assets/app-abc123.js", token=False)
        yield "fallback", self.srv.get("/board", token=False)
        yield "static 404", self.srv.get("/missing.js", token=False)
        yield "403", self.srv.get("/", host="evil.example")

    def test_every_response_carries_the_four_security_headers(self):
        for name, (status, head, _, _) in self.responses():
            with self.subTest(response=name):
                self.assertEqual("nosniff", head["X-Content-Type-Options"])
                self.assertEqual("no-referrer", head["Referrer-Policy"])
                self.assertEqual("DENY", head["X-Frame-Options"])
                self.assertIn("script-src 'self'", head["Content-Security-Policy"])

    def test_the_csp_has_the_required_directives_and_no_foreign_source(self):
        _, head, _, _ = self.srv.get("/api/v1/epics")
        directives = {}
        for part in head["Content-Security-Policy"].split(";"):
            name, *sources = part.split()
            directives[name] = sources
        self.assertEqual(["'self'"], directives["script-src"])
        self.assertEqual(["'self'", "'unsafe-inline'"], directives["style-src"])
        self.assertEqual(["'self'", f"ws://127.0.0.1:{self.srv.port}"], directives["connect-src"])
        self.assertEqual(["'none'"], directives["frame-ancestors"])
        allowed = {"'self'", "'none'", "'unsafe-inline'", "data:", f"ws://127.0.0.1:{self.srv.port}"}
        for name, sources in directives.items():
            with self.subTest(directive=name):
                self.assertTrue(set(sources) <= allowed, sources)
                if "'unsafe-inline'" in sources:
                    self.assertEqual("style-src", name)
                if "data:" in sources:
                    self.assertEqual("img-src", name)

    def test_cache_control(self):
        got = {name: head["Cache-Control"] for name, (_, head, _, _) in self.responses()}
        self.assertEqual("no-store", got["api 200"])
        self.assertEqual("no-store", got["index"])
        self.assertEqual("no-store", got["fallback"])
        self.assertEqual("public, max-age=31536000, immutable", got["asset"])

    def test_there_is_never_a_cors_header(self):
        for name, (_, head, _, _) in self.responses():
            with self.subTest(response=name):
                self.assertEqual([], [h for h in head if h.lower().startswith("access-control-")])
        _, head, _, _ = self.srv.get("/api/v1/epics", {"Origin": self.srv.origin})
        self.assertNotIn("Access-Control-Allow-Origin", head)

    def test_options_is_405_everywhere(self):
        for path in ("/api/v1/status", "/", "/assets/app-abc123.js", "/ws", "/nope"):
            with self.subTest(path=path):
                status, head, body, _ = self.srv.get(path, method="OPTIONS")
                self.assertEqual((405, "GET"), (status, head["Allow"]))
                self.assertEqual("read_only", code_of(body))

    def test_head_is_refused_without_a_body(self):
        status, _, body, _ = self.srv.get("/", method="HEAD")
        self.assertEqual((405, b""), (status, body))

    def test_every_response_is_http_1_1_with_a_content_length(self):
        for name, (_, head, body, version) in self.responses():
            with self.subTest(response=name):
                self.assertEqual(11, version)
                self.assertEqual(len(body), int(head["Content-Length"]))

    def test_two_requests_share_one_keep_alive_connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=10)
        self.addCleanup(conn.close)
        for _ in range(3):
            conn.request("GET", "/api/v1/epics", headers={"Authorization": f"Bearer {TOKEN}"})
            response = conn.getresponse()
            response.read()
            self.assertEqual(200, response.status)
            self.assertNotEqual("close", (response.getheader("Connection") or "").lower())
        conn.request("GET", "/", headers={})
        self.assertEqual(200, conn.getresponse().status)

    def test_json_errors_have_the_error_shape(self):
        for path, expected in (("/api/v1/nope", 404), ("/api/v1/tasks?limit=0", 422)):
            status, head, body, _ = self.srv.get(path)
            self.assertEqual(expected, status)
            self.assertEqual({"message", "code"} | ({"errors"} if expected == 422 else set()),
                             set(json.loads(body)))
            self.assertTrue(head["Content-Type"].startswith("application/json"))


class StaticTest(unittest.TestCase):

    def setUp(self):
        self.srv = LocalServer(self)
        self.index = (self.srv.dist / "index.html").read_bytes()

    def test_refused_paths_are_a_bare_404_with_no_file_content(self):
        for path in REFUSED_PATHS:
            with self.subTest(path=path):
                status, _, body, _ = self.srv.get(path, token=False)
                self.assertEqual(404, status, path)
                self.assertEqual("not_found", code_of(body))
                self.assertNotIn(b"TOPSECRET", body)
                self.assertNotIn(b"fixture", body)

    def test_a_missing_file_with_an_extension_is_404_and_a_missing_route_is_the_spa(self):
        self.assertEqual(404, self.srv.get("/missing.js", token=False)[0])
        self.assertEqual(404, self.srv.get("/assets/missing.css", token=False)[0])
        for path in ("/board", "/tasks/task-0001", "/handoffs", "/assets/", "/assets", "/deep/link/here"):
            with self.subTest(path=path):
                status, head, body, _ = self.srv.get(path, token=False)
                self.assertEqual(200, status)
                self.assertEqual(self.index, body)
                self.assertTrue(head["Content-Type"].startswith("text/html"))

    def test_the_index_is_served_at_the_root_and_by_name(self):
        for path in ("/", "/index.html", "/?x=1", "/#frag"):
            with self.subTest(path=path):
                status, head, body, _ = self.srv.get(path, token=False)
                self.assertEqual((200, self.index), (status, body))
                self.assertEqual("no-store", head["Cache-Control"])

    def test_every_extension_of_the_mime_map_is_served_with_its_type(self):
        expected = {"/assets/app-abc123.js": "text/javascript; charset=utf-8",
                    "/assets/app-abc123.css": "text/css; charset=utf-8",
                    "/index.html": "text/html; charset=utf-8",
                    "/assets/data.json": "application/json; charset=utf-8",
                    "/favicon.svg": "image/svg+xml", "/favicon.ico": "image/x-icon",
                    "/assets/pic.png": "image/png", "/assets/font.woff2": "font/woff2",
                    "/assets/note.txt": "text/plain; charset=utf-8"}
        for path, mime in expected.items():
            with self.subTest(path=path):
                self.assertEqual(mime, self.srv.get(path, token=False)[1]["Content-Type"])

    def test_the_registry_cannot_change_the_type_of_a_script(self):
        old = mimetypes.guess_type("x.js")[0]
        mimetypes.add_type("text/plain", ".js")
        self.addCleanup(mimetypes.add_type, old, ".js")
        self.assertEqual("text/plain", mimetypes.guess_type("x.js")[0])
        self.assertEqual("text/javascript; charset=utf-8",
                         self.srv.get("/assets/app-abc123.js", token=False)[1]["Content-Type"])

    def test_an_unknown_extension_is_a_download(self):
        (self.srv.dist / "assets" / "blob.bin").write_bytes(b"\x00\x01")
        self.assertEqual("application/octet-stream",
                         self.srv.get("/assets/blob.bin", token=False)[1]["Content-Type"])

    def test_a_link_that_resolves_outside_dist_is_refused(self):
        outside = self.srv.dist.parent / "outside"
        outside.mkdir()
        (outside / "inside.txt").write_text("TOPSECRET", encoding="utf-8")
        link = self.srv.dist / "assets" / "link"
        try:
            if os.name == "nt":
                made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                      capture_output=True, text=True)
                if made.returncode != 0:
                    raise OSError(made.stdout + made.stderr)
            else:
                os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"cannot create a link here: {exc}")
        status, _, body, _ = self.srv.get("/assets/link/inside.txt", token=False)
        self.assertEqual(404, status)
        self.assertNotIn(b"TOPSECRET", body)

    def test_a_missing_dist_index_is_a_404_not_a_crash(self):
        (self.srv.dist / "index.html").unlink()
        self.assertEqual(404, self.srv.get("/board", token=False)[0])


class FailureTest(unittest.TestCase):

    def setUp(self):
        self.srv = LocalServer(self)

    def routes(self, view):
        return unittest.mock.patch.object(
            server, "_routes_cache", [(re.compile("^/api/v1/boom$"), view)])

    def test_an_unhandled_error_is_a_500_without_a_traceback_and_stderr_gets_it(self):
        def boom(ctx, params):
            raise RuntimeError("boom secret detail")

        err = io.StringIO()
        with self.routes(boom), contextlib.redirect_stderr(err):
            status, _, body, _ = self.srv.get("/api/v1/boom")
        self.assertEqual(500, status)
        self.assertEqual({"message": "Internal error", "code": "internal"}, json.loads(body))
        self.assertNotIn(b"boom secret", body)
        self.assertNotIn(b"Traceback", body)
        self.assertIn("RuntimeError: boom secret detail", err.getvalue())
        self.assertIn("Traceback", err.getvalue())

    def test_the_server_keeps_answering_after_a_500(self):
        def boom(ctx, params):
            raise ValueError("x")

        with self.routes(boom), contextlib.redirect_stderr(io.StringIO()):
            self.srv.get("/api/v1/boom")
        self.assertEqual(200, self.srv.get("/api/v1/epics")[0])

    def test_a_malformed_request_line_is_answered_and_the_server_carries_on(self):
        raw = socket.create_connection(("127.0.0.1", self.srv.port), timeout=10)
        self.addCleanup(raw.close)
        raw.sendall(b"NOT A REQUEST\r\n\r\n")
        self.assertIn(b"bad_request", raw.recv(65536))
        self.assertEqual(200, self.srv.get("/api/v1/epics")[0])

    def test_an_unknown_verb_does_not_reach_a_handler(self):
        status, head, _, _ = self.srv.get("/api/v1/epics", method="BREW")
        self.assertIn(status, (405, 501))
        self.assertIn("X-Content-Type-Options", head)


class BindTest(unittest.TestCase):

    def test_the_listening_address_is_loopback_and_never_reusable(self):
        srv = LocalServer(self)
        self.assertEqual("127.0.0.1", srv.httpd.server_address[0])
        self.assertIs(False, server.DashboardServer.allow_reuse_address)

    def test_a_second_server_cannot_bind_a_port_the_first_holds(self):
        first = LocalServer(self)
        with self.assertRaises(OSError):
            server.DashboardServer(("127.0.0.1", first.port), server.Handler)

    def test_a_non_loopback_address_of_this_machine_does_not_connect(self):
        first = LocalServer(self)
        try:
            candidates = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        except OSError:
            candidates = set()
        others = sorted(ip for ip in candidates if not ip.startswith("127."))
        if not others:
            self.skipTest("this machine has no non-loopback IPv4 address")
        with self.assertRaises(OSError):
            socket.create_connection((others[0], first.port), timeout=3).close()

    def test_nothing_in_the_server_binds_a_wildcard_address(self):
        source = (TOOLS_DIR / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertTrue("0.0.0.0" not in source, "server.py names the wildcard address")
        binds = re.findall(r"DashboardServer\(\(([^,]*),", source)
        self.assertEqual(['"127.0.0.1"'], binds)

    @unittest.skipUnless(hasattr(socket, "SO_EXCLUSIVEADDRUSE"), "Windows only")
    def test_on_windows_the_exclusive_address_option_is_set_before_the_bind(self):
        source = (TOOLS_DIR / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertLess(source.index("SO_EXCLUSIVEADDRUSE"), source.index("TCPServer.server_bind(self)"))


class RealProcessTest(unittest.TestCase):
    """The real server.py as a child process: the token, the launch line and the silence."""

    @classmethod
    def setUpClass(cls):
        cls.root = tmproot.mkdtemp("dash_proc_")
        cls.addClassCleanup(tmproot.rmtree, cls.root)
        sb.build_project(cls.root)
        cls.first = sb.start_server(cls.root)
        cls.addClassCleanup(cls.first.stop)

    def exercise(self, srv: sb.RunningServer) -> None:
        paths = ["/api/v1/status", "/api/v1/runs", "/api/v1/tasks", "/api/v1/events", "/api/v1/metrics",
                 "/api/v1/memory", "/api/v1/memory/stats", "/api/v1/harness", "/api/v1/handoffs",
                 "/", "/board", "/assets/app-abc123.js", "/missing.js", "/api/v1/nope",
                 "/api/v1/tasks/task-9999", "/api/v1/tasks?limit=0"]
        for path in paths:
            srv.get(path, headers={"Authorization": None} if path == "/api/v1/runs" else None)
        for method in ("POST", "DELETE", "OPTIONS", "HEAD"):
            srv.request(method, "/api/v1/status")

    def test_stdout_holds_only_the_launch_line_and_stderr_stays_empty(self):
        self.exercise(self.first)
        self.assertEqual([self.first.url + "\n"], self.first.stdout_lines)
        self.assertRegex(self.first.url.strip(), r"^http://127\.0\.0\.1:\d+/#token=[A-Za-z0-9_-]{43}$")
        self.assertEqual("", self.first.stderr_text())

    def test_the_token_is_in_no_response_header_log_or_file(self):
        self.exercise(self.first)
        token = self.first.token.encode("utf-8")
        _, head, body = self.first.get("/api/v1/status")
        self.assertNotIn(token, body)
        self.assertNotIn(token, str(head).encode("utf-8"))
        self.assertNotIn(self.first.token, "".join(self.first.stderr_lines))
        self.assertEqual([], [line for line in self.first.stdout_lines[1:] if self.first.token in line])
        found = [str(p) for p in self.root.rglob("*") if p.is_file() and token in p.read_bytes()]
        self.assertEqual([], found)

    def test_two_launches_get_different_tokens(self):
        other = sb.start_server(self.root)
        self.addCleanup(other.stop)
        self.assertNotEqual(self.first.token, other.token)
        self.assertEqual(401, other.get("/api/v1/status", {"Authorization": f"Bearer {self.first.token}"})[0])

    def test_the_server_reads_the_project_named_by_the_environment(self):
        _, _, body = self.first.get("/api/v1/status")
        self.assertEqual(self.root.name, json.loads(body)["data"]["project"])


if __name__ == "__main__":
    unittest.main()
