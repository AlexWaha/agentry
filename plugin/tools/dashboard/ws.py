"""WebSocket transport for the dashboard: RFC 6455 on the stdlib, read-only.

Layout:
- Pure codec (no sockets): accept_key, valid_key, encode_frame, encode_close,
  parse_frame, Assembler, decode_message, message. FR-40 to FR-43, NFR-9.
- Sessions: handle_upgrade(handler, ctx) answers the upgrade checks that follow
  the server's own (FR-39), sends the 101 and runs one session on the calling
  handler thread, which is that client's only writer. A second thread per client
  reads. broadcast, client_count and close_all are the module API for the server
  and the watcher.

What a handler must provide: .headers (case-insensitive get), .connection (the
socket), .close_connection (settable) and .send_json_error(status, code,
message, headers=None). This module never imports the server.
"""
from __future__ import annotations

import base64
import binascii
import collections
import hashlib
import json
import select
import socket
import sqlite3
import struct
import threading
import time
from collections.abc import Callable
from typing import Any, NamedTuple

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

PROTOCOL = 1
MAX_CLIENTS = 8  # FR-48
MAX_MESSAGE = 65536  # incoming message and frame cap, NFR-10
QUEUE_LIMIT = 256  # FR-44
# Timers are module globals so a test can scale them down (FR-45).
PING_INTERVAL = 25.0
SILENCE_LIMIT = 60.0
SEND_TIMEOUT = 10.0  # a client that cannot take one frame in this long is dropped
READ_POLL = 0.25  # how often an idle reader looks at the silence clock
CLOSE_GRACE = 2.0  # how long a closing session drains the client before dropping it
CLOSE_ALL_WAIT = 2.0  # how long close_all waits for sessions to send their close frame

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
_OPCODES = (OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG)


class ProtocolError(Exception):
    """A violation that ends the session with a close frame carrying `code`."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class Frame(NamedTuple):
    fin: bool
    opcode: int
    payload: bytes


# ---------------------------------------------------------------- pure codec
def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def valid_key(key: str | None) -> bool:
    """True for the base64 form of exactly 16 bytes (RFC 6455 4.1)."""
    if not key:
        return False
    try:
        return len(base64.b64decode(key.encode("ascii"), validate=True)) == 16
    except (binascii.Error, UnicodeEncodeError):
        return False


def encode_frame(opcode: int, payload: bytes = b"") -> bytes:
    """One server frame: FIN set, unmasked, 7-, 16- or 64-bit length (FR-41)."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def encode_text(obj: dict) -> bytes:
    return encode_frame(OP_TEXT, json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def encode_close(code: int | None, reason: bytes = b"") -> bytes:
    return encode_frame(OP_CLOSE, b"" if code is None else struct.pack(">H", code) + reason)


def unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if not n:
        return b""
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


def parse_frame(buf: bytes, max_payload: int = MAX_MESSAGE) -> tuple[Frame, int] | None:
    """Decode the first client frame in `buf`.

    Returns (frame, bytes consumed), or None when `buf` holds only part of one.
    Raises ProtocolError as soon as the header is enough to decide: an unmasked
    frame, reserved bits, an unknown opcode or a bad control frame is 1002; a
    length over `max_payload` is 1009 before any payload byte is awaited.
    """
    if len(buf) < 2:
        return None
    b1, b2 = buf[0], buf[1]
    fin, rsv, opcode = bool(b1 & 0x80), b1 & 0x70, b1 & 0x0F
    n = b2 & 0x7F
    if rsv:
        raise ProtocolError(1002, "reserved bits set")
    if opcode not in _OPCODES:
        raise ProtocolError(1002, "unknown opcode")
    if not b2 & 0x80:
        raise ProtocolError(1002, "client frames must be masked")
    if opcode >= OP_CLOSE and (n > 125 or not fin):
        raise ProtocolError(1002, "bad control frame")
    off = 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, off = struct.unpack(">H", buf[2:4])[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, off = struct.unpack(">Q", buf[2:10])[0], 10
    if n > max_payload:
        raise ProtocolError(1009, "frame too large")
    end = off + 4 + n
    if len(buf) < end:
        return None
    return Frame(fin, opcode, unmask(buf[off + 4:end], buf[off:off + 4])), end


class Assembler:
    """Joins data frames into messages (FR-42). feed() returns the finished
    message bytes, or None while a fragmented message is still open."""

    def __init__(self, limit: int = MAX_MESSAGE) -> None:
        self._limit = limit
        self._parts: list[bytes] = []
        self._size = 0
        self._open = False

    def feed(self, frame: Frame) -> bytes | None:
        if frame.opcode == OP_CONT:
            if not self._open:
                raise ProtocolError(1002, "continuation without a message")
        else:
            if self._open:
                raise ProtocolError(1002, "new message inside a fragmented one")
            if frame.opcode == OP_BINARY:
                raise ProtocolError(1003, "binary frames are not accepted")
            self._open = True
        self._size += len(frame.payload)
        if self._size > self._limit:
            raise ProtocolError(1009, "message too large")
        self._parts.append(frame.payload)
        if not frame.fin:
            return None
        data = b"".join(self._parts)
        self._parts, self._size, self._open = [], 0, False
        return data


def decode_message(data: bytes) -> Any:
    """UTF-8 then JSON; either failing is 1007 (FR-42). Nesting deep enough to
    exhaust the stack raises RecursionError, which is not a ValueError."""
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProtocolError(1007, "message is not valid UTF-8 JSON") from None


def message(kind: str, payload: dict) -> dict:
    """The envelope of every server message (FR-46)."""
    return {"type": kind, "v": PROTOCOL, "payload": payload}


def _close_code_ok(code: int) -> bool:
    return 1000 <= code <= 1003 or 1007 <= code <= 1014 or 3000 <= code <= 4999


def parse_close(payload: bytes) -> int | None:
    """Status code of a client close frame, None for an empty one (FR-43)."""
    if not payload:
        return None
    if len(payload) == 1:
        raise ProtocolError(1002, "close payload of one byte")
    code = struct.unpack(">H", payload[:2])[0]
    if not _close_code_ok(code):
        raise ProtocolError(1002, "invalid close code")
    try:
        payload[2:].decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(1007, "close reason is not UTF-8") from None
    return code


def client_reply(obj: Any) -> dict:
    """The answer to one parsed client message (FR-47)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        return message("error", {"code": "bad_message", "message": "a message needs a string type"})
    v = obj.get("v", PROTOCOL)
    if isinstance(v, bool) or v != PROTOCOL:
        return message("error", {"code": "bad_message", "message": "unsupported protocol version"})
    if obj["type"] == "command":
        return message("command.refused", {"code": "read_only", "message": "this dashboard is read-only"})
    return message("error", {"code": "bad_message", "message": "unknown message type"})


# ---------------------------------------------------------------- sessions
_lock = threading.Lock()
_gone = threading.Condition(_lock)
_clients: set[_Client] = set()
_prime_hooks: list[Callable[[Any], None]] = []


def add_prime_hook(hook: Callable[[Any], None]) -> None:
    """Register a callable(ctx) run before each hello (the watcher baselines here)."""
    if hook not in _prime_hooks:
        _prime_hooks.append(hook)


def _forget(client: _Client) -> None:
    with _gone:
        _clients.discard(client)
        _gone.notify_all()


def _send_all(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


class _Client:
    """One connection. The handler thread writes (run); a reader thread reads.
    The reader never touches the socket for writing: it leaves a pong, a close
    or a reply message for the writer."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        sock.settimeout(SEND_TIMEOUT)  # bounds sendall; the reader only recv()s after select()
        self.last_rx = time.monotonic()
        self.dead = False
        self.closing = False
        self._cv = threading.Condition()
        self._queue: collections.deque[bytes] = collections.deque()
        self._pong: bytes | None = None
        self._close: tuple[int | None, bytes] | None = None
        self._reader_done = threading.Event()

    # -- producers (any thread)
    def enqueue(self, frame: bytes) -> None:
        with self._cv:
            if self._close is not None or self.dead:
                return
            if len(self._queue) >= QUEUE_LIMIT:
                self._queue.clear()
                frame = encode_text(message("resync", {"reason": "overflow"}))
            self._queue.append(frame)
            self._cv.notify()

    def prepend(self, frame: bytes) -> None:
        """Put the hello ahead of anything a broadcast queued since registration."""
        with self._cv:
            self._queue.appendleft(frame)
            self._cv.notify()

    def request_close(self, code: int | None, reason: bytes = b"") -> None:
        with self._cv:
            if self._close is None:
                self._close = (code, reason)
            self._cv.notify()

    def abort(self) -> None:
        with self._cv:
            self.dead = True
            self._cv.notify()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    # -- writer (handler thread)
    def run(self) -> None:
        reader = threading.Thread(target=self._read_loop, name="ws-reader", daemon=True)
        reader.start()
        next_ping = time.monotonic() + PING_INTERVAL
        try:
            while True:
                kind, data = self._next(next_ping)
                if kind == "dead":
                    return
                if kind == "ping":
                    _send_all(self.sock, encode_frame(OP_PING))
                    next_ping = time.monotonic() + PING_INTERVAL
                elif kind == "close":
                    code, reason = data
                    self.closing = True
                    _send_all(self.sock, encode_close(code, reason))
                    _forget(self)  # a closing client no longer counts toward the cap
                    self.sock.shutdown(socket.SHUT_WR)
                    self._reader_done.wait(CLOSE_GRACE)
                    return
                elif kind == "pong":
                    _send_all(self.sock, encode_frame(OP_PONG, data))
                else:
                    _send_all(self.sock, data)
        except OSError:
            pass
        finally:
            self.dead = True
            reader.join(1.0)

    def _next(self, next_ping: float) -> tuple[str, Any]:
        with self._cv:
            while True:
                if self.dead:
                    return "dead", None
                if self._close is not None:
                    return "close", self._close
                if self._pong is not None:
                    pong, self._pong = self._pong, None
                    return "pong", pong
                wait = next_ping - time.monotonic()
                if wait <= 0:
                    return "ping", None
                if self._queue:
                    return "frame", self._queue.popleft()
                self._cv.wait(wait)

    # -- reader thread
    def _read_loop(self) -> None:
        buf = b""
        asm = Assembler()
        try:
            while not self.dead:
                ready, _, _ = select.select([self.sock], [], [], READ_POLL)
                if not ready:
                    if not self.closing and time.monotonic() - self.last_rx > SILENCE_LIMIT:
                        self.request_close(1001, b"silent")
                        self.closing = True
                        self._drain()
                        self.abort()  # also frees a writer stuck sending to a client that never reads
                    continue
                data = self.sock.recv(65536)
                if not data:
                    self.abort()
                    return
                if self.closing:
                    continue  # a close is under way: read and ignore so the peer is not reset
                buf += data
                while True:
                    parsed = parse_frame(buf)
                    if parsed is None:
                        break
                    frame, used = parsed
                    buf = buf[used:]
                    self.last_rx = time.monotonic()
                    self._on_frame(frame, asm)
        except ProtocolError as exc:
            self.request_close(exc.code, exc.reason.encode("ascii", "replace")[:100])
            self.closing = True
            self._drain()
            self.abort()
        except Exception:  # backstop: a dead reader must never leave a mute client holding a slot
            self.abort()
        finally:
            self._reader_done.set()

    def _drain(self) -> None:
        """After a protocol error keep reading (and ignoring) until the peer hangs up
        or the grace runs out, so unread bytes do not turn our close into a reset."""
        deadline = time.monotonic() + CLOSE_GRACE
        try:
            while not self.dead and time.monotonic() < deadline:
                ready, _, _ = select.select([self.sock], [], [], READ_POLL)
                if ready and not self.sock.recv(65536):
                    return
        except (OSError, ValueError):
            pass

    def _on_frame(self, frame: Frame, asm: Assembler) -> None:
        if frame.opcode == OP_CLOSE:
            self.request_close(parse_close(frame.payload))
            self.closing = True
        elif frame.opcode == OP_PING:
            with self._cv:
                self._pong = frame.payload
                self._cv.notify()
        elif frame.opcode != OP_PONG:
            data = asm.feed(frame)
            if data is not None:
                self.enqueue(encode_text(client_reply(decode_message(data))))


def latest_event_id(ctx: Any) -> int:
    """max(id) of events.db, 0 when the journal is absent or unreadable."""
    import stores

    try:
        with stores.store(stores.events_db(ctx.project_root)) as con:
            return 0 if con is None else con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    except (sqlite3.Error, OSError):
        return 0


def _hello(ctx: Any) -> dict:
    for hook in list(_prime_hooks):
        try:
            hook(ctx)
        except Exception:  # a failing watcher must not refuse a client
            pass
    latest = latest_event_id(ctx)
    return message("hello", {"protocol": PROTOCOL, "read_only": True, "latest_event_id": latest})


def _switching(key: str) -> bytes:
    """The 101 response: no Sec-WebSocket-Protocol, no Sec-WebSocket-Extensions (FR-40)."""
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
    ).encode("ascii")


def handle_upgrade(handler: Any, ctx: Any) -> None:
    """Answer an upgrade request that passed the server's checks and run its session."""
    headers = handler.headers
    connection = [t.strip().lower() for t in headers.get("Connection", "").split(",")]
    upgrade = [t.strip().lower() for t in headers.get("Upgrade", "").split(",")]
    if "websocket" not in upgrade or "upgrade" not in connection:
        return handler.send_json_error(400, "bad_request", "WebSocket upgrade headers are missing")
    if headers.get("Sec-WebSocket-Version", "").strip() != "13":
        return handler.send_json_error(
            426, "upgrade_required", "Sec-WebSocket-Version must be 13", {"Sec-WebSocket-Version": "13"})
    key = headers.get("Sec-WebSocket-Key", "").strip()
    if not valid_key(key):
        return handler.send_json_error(400, "bad_request", "Sec-WebSocket-Key must be base64 of 16 bytes")

    client = _Client(handler.connection)
    with _lock:
        full = len(_clients) >= MAX_CLIENTS
        if not full:
            _clients.add(client)
    if full:
        return handler.send_json_error(
            503, "unavailable", "too many WebSocket clients", {"Retry-After": "1"})
    handler.close_connection = True
    try:
        client.prepend(encode_text(_hello(ctx)))
        handler.connection.sendall(_switching(key))
        client.run()
    except OSError:
        pass
    finally:
        client.abort()
        _forget(client)


def broadcast(msg: dict) -> None:
    """Queue `msg` for every connected client (encoded once)."""
    frame = encode_text(msg)
    with _lock:
        clients = list(_clients)
    for client in clients:
        client.enqueue(frame)


def client_count() -> int:
    with _lock:
        return len(_clients)


def close_all(code: int = 1001) -> None:
    """Send every client a close frame and wait (bounded) for the sessions to end."""
    with _lock:
        clients = list(_clients)
    for client in clients:
        client.request_close(code)
    deadline = time.monotonic() + CLOSE_ALL_WAIT
    with _gone:
        while _clients and time.monotonic() < deadline:
            _gone.wait(deadline - time.monotonic())
        stragglers = list(_clients)
    for client in stragglers:
        client.abort()
