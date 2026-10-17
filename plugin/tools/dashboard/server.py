#!/usr/bin/env python3
"""Agentry dashboard server: a read-only, stdlib-only view of one project (spec-0002).

Started by `/agentry:dashboard`; one process per project. It serves the committed SPA build
(`dist/`), the versioned REST API under /api/v1 and the WebSocket at /ws (ws.py, watch.py).
Every SQLite store is opened `mode=ro` (stores.py) and no code path writes anything.

Security model (FR-54 to FR-63): binds 127.0.0.1 only and exclusively, a per-launch token in
memory compared with hmac.compare_digest, Host / Origin / Sec-Fetch-Site checks, no CORS,
a strict CSP and static paths that cannot leave dist/. The token appears once, inside the
launch URL printed to stdout; nothing else is ever written to stdout. Logs are silent and
a traceback goes to stderr only.

Exit codes: 0 normal stop (Ctrl+C, SIGTERM, SIGBREAK, idle timeout), 2 invalid environment
(CLAUDE_PROJECT_DIR unset, no .agentry/ folder, dist integrity check failed).
"""

from __future__ import annotations

import argparse
import hmac
import importlib
import json
import os
import re
import secrets
import signal
import socket
import socketserver
import sqlite3
import sys
import threading
import time
import traceback
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _folder in (HERE.parent, HERE):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

import dist_manifest  # noqa: E402
import paths  # noqa: E402
import stores  # noqa: E402

# (route template, views module, function). The OpenAPI file lists the same templates.
ROUTE_TABLE = (
    ("/api/v1/status", "views_pipeline", "status"),
    ("/api/v1/runs", "views_pipeline", "runs"),
    ("/api/v1/tasks", "views_pipeline", "tasks"),
    ("/api/v1/tasks/{id}", "views_pipeline", "task_detail"),
    ("/api/v1/epics", "views_pipeline", "epics"),
    ("/api/v1/events", "views_pipeline", "events_list"),
    ("/api/v1/metrics", "views_pipeline", "metrics"),
    ("/api/v1/memory", "views_memory", "memory_rows"),
    ("/api/v1/memory/stats", "views_memory", "memory_stats"),
    ("/api/v1/graphs/memory", "views_memory", "memory_graph"),
    ("/api/v1/codegraph", "views_memory", "codegraph"),
    ("/api/v1/codegraph/{key}/graph", "views_memory", "codegraph_graph"),
    ("/api/v1/harness", "views_harness", "harness"),
    ("/api/v1/handoffs", "views_harness", "handoffs"),
    ("/api/v1/handoffs/{id}", "views_harness", "handoff_doc"),
)
ROUTE_TEMPLATES = tuple(template for template, _, _ in ROUTE_TABLE)

MIME = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
JSON_TYPE = "application/json; charset=utf-8"
NO_STORE = "no-store"
IMMUTABLE = "public, max-age=31536000, immutable"
METHODS = ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT")

PORT_BASE = 41000
PORT_SPAN = 1000
PORT_FALLBACKS = 9
IDLE_SECONDS = 7200.0
MAX_CONNECTIONS = 64  # handler threads, WebSocket sessions included
REQUEST_TIMEOUT = 30.0  # seconds a connection may sit silent between bytes of a request
SERVER_NAME = "Agentry-Dashboard"

_routes_cache: list | None = None


def route_pattern(template: str) -> re.Pattern:
    """`/api/v1/tasks/{id}` -> a regex whose `{name}` placeholders are one path segment."""
    return re.compile("^" + "([^/]+)".join(re.escape(p) for p in re.split(r"\{\w+\}", template)) + "$")


def load_routes() -> list:
    """[(compiled pattern, view function)]; the views are imported on first use."""
    global _routes_cache
    if _routes_cache is None:
        _routes_cache = [(route_pattern(template), getattr(importlib.import_module(module), name))
                         for template, module, name in ROUTE_TABLE]
    return _routes_cache


def preferred_port(project_root: Path) -> int:
    """41000 + crc32(casefolded project root) % 1000: stable per project, so the origin (and
    with it the saved theme and token in localStorage) survives a relaunch."""
    return PORT_BASE + zlib.crc32(str(project_root).casefold().encode("utf-8")) % PORT_SPAN


class DashboardServer(ThreadingHTTPServer):
    """Loopback only. No SO_REUSEADDR, and on Windows SO_EXCLUSIVEADDRUSE: a second
    process can never bind a port this one holds (FR-54)."""

    allow_reuse_address = False
    request_queue_size = 64
    ctx: stores.Ctx | None = None
    ws = None

    def __init__(self, address, handler) -> None:
        self.last_activity = time.monotonic()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def process_request(self, request, client_address) -> None:
        """At most MAX_CONNECTIONS handler threads at once; one more is closed unanswered."""
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)  # HTTPServer's would do a reverse DNS lookup
        self.server_name, self.server_port = self.server_address[:2]

    def mark_active(self) -> None:
        self.last_activity = time.monotonic()

    def idle_for(self) -> float:
        return time.monotonic() - self.last_activity

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], OSError):
            return  # the client went away mid-response; nothing to report
        traceback.print_exc(file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True
    timeout = REQUEST_TIMEOUT  # ws.py sets its own socket timeout once a session starts

    def version_string(self) -> str:
        return SERVER_NAME

    def log_message(self, format, *args) -> None:
        """The per-request access log is silenced (FR-63)."""

    # ---- responses ---------------------------------------------------------------

    def _security_headers(self) -> list[tuple[str, str]]:
        port = self.server.ctx.port if self.server.ctx else 0
        csp = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
               f"img-src 'self' data:; connect-src 'self' ws://127.0.0.1:{port}; "
               "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'")
        return [("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY")]

    def _respond(self, status: int, body: bytes, content_type: str, cache: str = NO_STORE,
                 headers: dict[str, str] | None = None, close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in self._security_headers():
            self.send_header(name, value)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if close or self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, headers: dict[str, str] | None = None,
              close: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace")
        self._respond(status, body, JSON_TYPE, headers=headers, close=close)

    def _error(self, status: int, code: str, message: str, errors: dict | None = None,
               headers: dict[str, str] | None = None, close: bool = False) -> None:
        payload: dict = {"message": message, "code": code}
        if errors:
            payload["errors"] = errors
        self._json(status, payload, headers, close)

    def send_json_error(self, status: int, code: str, message: str,
                        headers: dict[str, str] | None = None) -> None:
        """The error body every refusal uses; ws.py calls it for its upgrade refusals. The
        connection is closed afterwards."""
        self._error(status, code, message, headers=headers, close=True)

    def send_error(self, code, message=None, explain=None) -> None:
        """Parse-level failures from http.server (bad request line, oversized header) get
        the same JSON body and headers as everything else."""
        self.close_connection = True
        if int(code) == 501:  # a verb http.server has no do_ method for (FOO, lowercase get)
            return self._error(405, "read_only", "This dashboard is read-only; only GET is allowed",
                               headers={"Allow": "GET"}, close=True)
        self._error(int(code), "bad_request", str(message or "Bad request"), close=True)

    # ---- verbs ---------------------------------------------------------------------

    def _refuse_verb(self) -> None:
        """Every verb but GET: 405 with Allow: GET. The request body, if any, was never
        read, so the connection is closed rather than reused."""
        self.close_connection = True
        self._error(405, "read_only", "This dashboard is read-only; only GET is allowed",
                    headers={"Allow": "GET"}, close=True)

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _refuse_verb

    def do_GET(self) -> None:
        if self.headers.get("Content-Length", "0").strip() not in ("", "0") \
                or self.headers.get("Transfer-Encoding"):
            self.close_connection = True  # a GET with a body: do not trust the stream afterwards
        path, _, query = self.path.partition("?")
        path = path.partition("#")[0]
        ctx = self.server.ctx
        if self.headers.get("Host", "") != f"127.0.0.1:{ctx.port}":
            return self._error(403, "forbidden_host", "Host header not allowed")
        is_ws = path == "/ws"
        if not is_ws and "websocket" in self.headers.get("Upgrade", "").lower():
            return self._not_found()  # only /ws upgrades (FR-39)
        if is_ws or path == "/api" or path.startswith("/api/"):
            denied = self._check_origin(ctx, require_origin=is_ws)
            if denied:
                return self._error(403, "forbidden_origin", denied)
            if not self._token_ok(ctx, query, is_ws):
                return self._error(401, "unauthorized", "Missing or invalid token",
                                   headers={"WWW-Authenticate": "Bearer"})
            self.server.mark_active()  # only a request that passed Host, Origin and token counts
            if is_ws:
                return self._upgrade(ctx)
            return self._api(ctx, path, query)
        self.server.mark_active()
        return self._static(ctx, path)

    def _check_origin(self, ctx: stores.Ctx, require_origin: bool) -> str:
        origin = self.headers.get("Origin")
        if (origin is None and require_origin) or (origin is not None and origin != ctx.origin):
            return "Origin not allowed"
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            return "Cross-site request not allowed"
        return ""

    def _token_ok(self, ctx: stores.Ctx, query: str, is_ws: bool) -> bool:
        if is_ws:
            given = stores.parse_query(query).get("token", "")
        else:
            header = self.headers.get("Authorization", "")
            given = header[7:] if header[:7].lower() == "bearer " else ""
        return hmac.compare_digest(given.encode("utf-8"), ctx.token.encode("utf-8"))

    def _upgrade(self, ctx: stores.Ctx) -> None:
        self.close_connection = True
        self.server.ws.handle_upgrade(self, ctx)

    # ---- API -------------------------------------------------------------------------

    def _api(self, ctx: stores.Ctx, path: str, query: str) -> None:
        try:
            for pattern, view in load_routes():
                match = pattern.match(path)
                if not match:
                    continue
                groups = [stores.percent_decode(g) for g in match.groups()]
                if None in groups:
                    raise stores.not_found()
                data, meta = view(ctx, stores.Params(stores.parse_query(query)), *groups)
                body = {"data": data} if meta is None else {"data": data, "meta": meta}
                return self._json(200, body)
            raise stores.not_found()
        except stores.ApiError as err:
            self._error(err.status, err.code, err.message, err.errors, err.headers)
        except sqlite3.Error:
            error = stores.unavailable()
            self._error(error.status, error.code, error.message, headers=error.headers)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(500, "internal", "Internal error")

    # ---- static files ------------------------------------------------------------------

    def _static(self, ctx: stores.Ctx, path: str) -> None:
        decoded = stores.percent_decode(path)
        if decoded is None or any(ch in decoded for ch in ("\\", ":", "\x00")):
            return self._not_found()
        segments = [s for s in decoded.split("/") if s]
        if any(s in (".", "..") for s in segments):
            return self._not_found()
        dist = ctx.dist_dir.resolve()
        target = (dist / "/".join(segments)).resolve() if segments else dist / "index.html"
        if target != dist / "index.html" and dist not in target.parents:
            return self._not_found()
        if not target.is_file():
            if segments and Path(segments[-1]).suffix:
                return self._not_found()
            target = dist / "index.html"
            if not target.is_file():
                return self._not_found()
        try:
            body = target.read_bytes()
        except OSError:
            return self._not_found()
        immutable = bool(segments) and segments[0] == "assets" and target != dist / "index.html"
        self._respond(200, body, MIME.get(target.suffix.lower(), "application/octet-stream"),
                      cache=IMMUTABLE if immutable else NO_STORE)

    def _not_found(self) -> None:
        self._error(404, "not_found", "Not found")


# --------------------------------------------------------------------------- process

def bind_server(preferred: int) -> DashboardServer:
    """Bind the preferred port, else the next nine, else an OS-assigned one (FR-68)."""
    last: OSError | None = None
    for port in [*range(preferred, preferred + PORT_FALLBACKS + 1), 0]:
        try:
            return DashboardServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            last = exc
    raise last  # type: ignore[misc]  # the OS port 0 failed too


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server.py", description="Agentry dashboard: a read-only local view of this project.")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    parser.add_argument("--idle-timeout", type=float, default=IDLE_SECONDS, metavar="SECONDS",
                        help="exit after this many seconds with no request and no WebSocket "
                             "client (default %(default)s)")
    parser.add_argument("--dist-dir", help=argparse.SUPPRESS)  # test seam: serve another build
    return parser


def _fail(message: str) -> int:
    sys.stderr.write(message + "\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.idle_timeout <= 0:
        return _fail("--idle-timeout must be greater than 0")
    raw = os.environ.get(paths.PROJECT_ENV, "").strip()
    if not raw:
        return _fail(paths.UNSET_MESSAGE)
    root = Path(raw).resolve()
    if not (root / ".agentry").is_dir():
        return _fail(f"No .agentry/ folder in {root}: run the dashboard inside an Agentry project.")
    dist = Path(args.dist_dir).resolve() if args.dist_dir else dist_manifest.DIST_DIR
    problems = dist_manifest.check(dist, None if args.dist_dir else dist_manifest.SOURCE_DIR)
    if problems:
        return _fail("The dashboard build failed its integrity check: " + "; ".join(problems))

    load_routes()
    import watch
    import ws

    server = bind_server(preferred_port(root))
    port = server.server_address[1]
    ctx = stores.Ctx(project_root=root, plugin_root=paths.PLUGIN_ROOT, port=port,
                     origin=f"http://127.0.0.1:{port}", token=secrets.token_urlsafe(32),
                     dist_dir=dist)
    server.ctx, server.ws = ctx, ws
    stop = threading.Event()

    def request_stop(*_ignored) -> None:
        if not stop.is_set():
            stop.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    def watch_idle() -> None:
        interval = min(1.0, max(0.05, args.idle_timeout / 4))
        while not stop.wait(interval):
            if ws.client_count() > 0:
                server.mark_active()  # the idle clock runs from the moment the last client left
            elif server.idle_for() >= args.idle_timeout:
                request_stop()

    if threading.current_thread() is threading.main_thread():
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), request_stop)
    watch.start(ctx)
    threading.Thread(target=watch_idle, daemon=True).start()
    url = f"{ctx.origin}/#token={ctx.token}"
    sys.stdout.write(url + "\n")
    sys.stdout.flush()
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            traceback.print_exc(file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        stop.set()
        for step in (lambda: ws.close_all(1001), watch.stop):
            try:
                step()
            except Exception:
                traceback.print_exc(file=sys.stderr)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
