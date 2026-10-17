"""server.py as a launched process: environment errors, the port, the browser, idle exit and stop
(spec-0002 FR-65 to FR-71, FR-54).

Real child processes for what only a process can show (exit codes, signals, a held port); the
browser test runs main() on a thread of this process so webbrowser.open can be replaced.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import base64
import contextlib
import io
import os
import random
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock
import webbrowser
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import dashboard_sandbox as sb
import server
import tmproot

LAUNCH_LINE = re.compile(r"^http://127\.0\.0\.1:\d+/#token=[A-Za-z0-9_-]{43}$")


class FixedPath:
    """Stands in for a Path whose text is fixed, so the port test is the same on every OS."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


def run_server(args: tuple[str, ...], env: dict, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(sb.SERVER), *args], env=env, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "PIPELINE_LANE")}
    env.update(extra)
    return env


def free_ports(count: int) -> int:
    """The first of `count` consecutive TCP ports on 127.0.0.1 that are all free right now."""
    for _ in range(200):
        base = random.randint(43000, 60000)
        held = []
        try:
            for port in range(base, base + count):
                probe = socket.socket()
                probe.bind(("127.0.0.1", port))
                held.append(probe)
            return base
        except OSError:
            continue
        finally:
            for probe in held:
                probe.close()
    raise RuntimeError("no free port range")


class WsClient:
    """A raw WebSocket client: connects, checks for the 101, then reads unmasked server frames."""

    def __init__(self, srv: sb.RunningServer) -> None:
        self.sock = socket.create_connection(("127.0.0.1", srv.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /ws?token={srv.token} HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\n"
                           f"Origin: http://127.0.0.1:{srv.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError(f"connection closed before the 101: {data!r}")
            data += chunk
        if not data.startswith(b"HTTP/1.1 101"):
            raise AssertionError(f"no 101: {data[:200]!r}")
        self.buffer = data.split(b"\r\n\r\n", 1)[1]

    def take(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AssertionError("connection closed mid-frame")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def frame(self) -> tuple[int, bytes]:
        """One server frame: (opcode, payload)."""
        head = self.take(2)
        size = head[1] & 0x7F
        if size == 126:
            size = struct.unpack(">H", self.take(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", self.take(8))[0]
        return head[0] & 0x0F, self.take(size)

    def close(self) -> None:
        self.sock.close()


class EnvironmentErrorsTest(unittest.TestCase):

    def assertRefused(self, done: subprocess.CompletedProcess) -> None:
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("", done.stdout)
        self.assertEqual(1, len(done.stderr.strip().splitlines()), done.stderr)

    def test_help_exits_0_without_a_project(self):
        done = run_server(("--help",), clean_env())
        self.assertEqual(0, done.returncode, done.stderr)
        for word in ("--no-open", "--idle-timeout", "SECONDS"):
            self.assertIn(word, done.stdout)
        self.assertNotIn("--dist-dir", done.stdout)  # the test seam is not documented

    def test_an_unset_project_directory_exits_2_with_one_line_on_stderr(self):
        done = run_server(("--no-open",), clean_env())
        self.assertRefused(done)
        self.assertIn("CLAUDE_PROJECT_DIR", done.stderr)

    def test_a_blank_project_directory_is_unset(self):
        self.assertRefused(run_server(("--no-open",), clean_env(CLAUDE_PROJECT_DIR="  ")))

    def test_a_directory_without_agentry_exits_2_and_binds_nothing(self):
        root = tmproot.sandbox(self, "dash_launch_")
        done = run_server(("--no-open",), clean_env(CLAUDE_PROJECT_DIR=str(root)))
        self.assertRefused(done)
        self.assertIn(".agentry", done.stderr)
        self.assertFalse(self.port_is_open(server.preferred_port(root.resolve())))

    def test_a_tampered_dist_exits_2_and_binds_nothing(self):
        root = tmproot.sandbox(self, "dash_launch_")
        (root / ".agentry").mkdir()
        dist = sb.fixture_dist(root.parent / (root.name + "_dist"))
        self.addCleanup(tmproot.rmtree, dist)
        (dist / "assets" / "app-abc123.js").write_text("alert('tampered')\n", encoding="utf-8")
        done = run_server(("--no-open", "--dist-dir", str(dist)), clean_env(CLAUDE_PROJECT_DIR=str(root)))
        self.assertRefused(done)
        self.assertIn("integrity", done.stderr)
        self.assertIn("app-abc123.js", done.stderr)
        self.assertFalse(self.port_is_open(server.preferred_port(root.resolve())))

    def test_a_non_positive_idle_timeout_is_refused(self):
        done = run_server(("--idle-timeout", "0"), clean_env())
        self.assertEqual(2, done.returncode)

    @staticmethod
    def port_is_open(port: int) -> bool:
        try:
            with contextlib.closing(socket.create_connection(("127.0.0.1", port), timeout=1)):
                return True
        except OSError:
            return False


class PortTest(unittest.TestCase):

    def test_the_port_formula_matches_three_fixed_values(self):
        self.assertEqual(41364, server.preferred_port(FixedPath("C:\\Users\\Alex\\Project")))
        self.assertEqual(41424, server.preferred_port(FixedPath("/home/alex/project")))
        self.assertEqual(41062, server.preferred_port(FixedPath("/Users/Alex/Work/Proj")))

    def test_a_path_that_differs_only_by_case_gets_the_same_port(self):
        self.assertEqual(server.preferred_port(FixedPath("C:\\Users\\Alex\\Project")),
                         server.preferred_port(FixedPath("c:\\users\\alex\\project")))

    def test_the_port_is_in_the_documented_range(self):
        for text in ("a", "b" * 100, "/x/y/z", "\u00e9\u00e8"):
            self.assertTrue(41000 <= server.preferred_port(FixedPath(text)) < 42000)

    def occupy(self, ports) -> list[socket.socket]:
        held = []
        for port in ports:
            probe = socket.socket()
            probe.bind(("127.0.0.1", port))
            held.append(probe)
            self.addCleanup(probe.close)
        return held

    def bound(self, preferred: int) -> int:
        httpd = server.bind_server(preferred)
        self.addCleanup(httpd.server_close)
        return httpd.server_address[1]

    def test_the_preferred_port_is_taken_when_it_is_free(self):
        base = free_ports(1)
        self.assertEqual(base, self.bound(base))

    def test_an_occupied_preferred_port_falls_to_the_next_free_one(self):
        base = free_ports(10)
        self.occupy([base])
        self.assertEqual(base + 1, self.bound(base))

    def test_a_run_of_occupied_ports_is_skipped_in_order(self):
        base = free_ports(10)
        self.occupy(range(base, base + 4))
        self.assertEqual(base + 4, self.bound(base))

    def test_with_all_ten_occupied_the_operating_system_picks(self):
        base = free_ports(10)
        self.occupy(range(base, base + 10))
        port = self.bound(base)
        self.assertNotIn(port, range(base, base + 10))
        self.assertGreater(port, 0)

    def test_a_relaunch_while_the_port_is_free_gets_the_same_port(self):
        root = tmproot.sandbox(self, "dash_relaunch_")
        sb.build_project(root)
        first = sb.start_server(root)
        port = first.port
        first.get("/api/v1/status")
        first.get("/")
        self.assertEqual(0, first.stop())
        second = sb.start_server(root)
        self.addCleanup(second.stop)
        self.assertEqual(port, second.port)


class BrowserTest(unittest.TestCase):

    def launch(self, *args: str) -> tuple[int, str, unittest.mock.MagicMock]:
        root = tmproot.sandbox(self, "dash_browser_")
        sb.make_project(root)
        dist = sb.fixture_dist(root.parent / (root.name + "_dist"))
        self.addCleanup(tmproot.rmtree, dist)
        out = io.StringIO()
        result = {}

        def run() -> None:
            result["code"] = server.main(["--idle-timeout", "1", "--dist-dir", str(dist), *args])

        with unittest.mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(root)}), \
                unittest.mock.patch.object(webbrowser, "open") as opener, contextlib.redirect_stdout(out):
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(30)
            self.assertFalse(thread.is_alive(), "main() did not return after the idle timeout")
        return result["code"], out.getvalue(), opener

    def test_no_open_never_calls_the_browser(self):
        code, out, opener = self.launch("--no-open")
        self.assertEqual(0, code)
        opener.assert_not_called()
        self.assertRegex(out.strip(), LAUNCH_LINE)
        self.assertEqual(1, len(out.splitlines()))

    def test_without_no_open_the_browser_gets_the_launch_url_once(self):
        code, out, opener = self.launch()
        self.assertEqual(0, code)
        opener.assert_called_once_with(out.strip())

    def test_a_browser_that_cannot_open_does_not_stop_the_server(self):
        root = tmproot.sandbox(self, "dash_browser_")
        sb.make_project(root)
        dist = sb.fixture_dist(root.parent / (root.name + "_dist"))
        self.addCleanup(tmproot.rmtree, dist)
        result = {}
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(root)}), \
                unittest.mock.patch.object(webbrowser, "open", side_effect=OSError("no browser")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            thread = threading.Thread(target=lambda: result.update(
                code=server.main(["--idle-timeout", "1", "--dist-dir", str(dist)])))
            thread.start()
            thread.join(30)
        self.assertEqual(0, result.get("code"))


class IdleTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_idle_")
        sb.build_project(self.root)

    def start(self) -> sb.RunningServer:
        srv = sb.start_server(self.root, ("--idle-timeout", "1"))
        self.addCleanup(srv.stop)
        return srv

    def test_a_server_with_no_traffic_exits_0_within_three_seconds(self):
        srv = self.start()
        started = time.monotonic()
        self.assertEqual(0, srv.proc.wait(timeout=10))
        self.assertLess(time.monotonic() - started, 3.5)
        self.assertEqual("", srv.stderr_text())
        self.assertEqual(1, len(srv.stdout_lines))

    def test_a_request_every_half_second_keeps_it_alive_and_silence_ends_it(self):
        srv = self.start()
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            self.assertEqual(200, srv.get("/api/v1/status")[0])
            self.assertTrue(srv.alive())
            time.sleep(0.5)
        self.assertEqual(0, srv.proc.wait(timeout=10))

    def test_a_connected_websocket_client_keeps_it_alive(self):
        srv = self.start()
        client = WsClient(srv)
        self.addCleanup(client.close)
        time.sleep(3.2)
        self.assertTrue(srv.alive())
        client.close()
        self.assertEqual(0, srv.proc.wait(timeout=15))


class StopTest(unittest.TestCase):

    def setUp(self):
        self.root = tmproot.sandbox(self, "dash_stop_")
        sb.build_project(self.root)

    def test_the_os_stop_signal_exits_0(self):
        srv = sb.start_server(self.root)
        self.assertEqual(0, srv.stop())
        self.assertEqual("", srv.stderr_text())

    def test_a_connected_client_is_closed_with_1001_before_the_process_ends(self):
        srv = sb.start_server(self.root)
        self.addCleanup(srv.stop)
        client = WsClient(srv)
        self.addCleanup(client.close)
        opcode, payload = client.frame()
        self.assertEqual(0x1, opcode)
        self.assertIn(b'"hello"', payload)
        stopper = threading.Thread(target=srv.stop)
        stopper.start()
        opcode, payload = client.frame()
        stopper.join(15)
        self.assertEqual(0x8, opcode)
        self.assertEqual(1001, struct.unpack(">H", payload[:2])[0])
        self.assertEqual(0, srv.proc.returncode)

    @unittest.skipIf(os.name == "nt", "Ctrl+C cannot be delivered to a child in its own process group")
    def test_ctrl_c_exits_0(self):
        srv = sb.start_server(self.root)
        self.addCleanup(srv.stop)
        srv.proc.send_signal(signal.SIGINT)
        self.assertEqual(0, srv.proc.wait(timeout=15))

    def test_the_server_is_gone_after_the_stop(self):
        srv = sb.start_server(self.root)
        port = srv.port
        srv.stop()
        with self.assertRaises(OSError), contextlib.closing(
                socket.create_connection(("127.0.0.1", port), timeout=2)):
            pass


if __name__ == "__main__":
    unittest.main()
