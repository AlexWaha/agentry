#!/usr/bin/env python3
"""CEO diff-review UI - the visual checkpoint before commit/push.

Serves an interactive side-by-side diff of the task branch vs its merge-base
with main on 127.0.0.1: left panel lists changed files (A/M/D badges, +/-
counts), the main area renders before/after panes with syntax highlighting,
line-anchored comments, and Approve / Request changes buttons. The verdict and
comments land in .claude/state/review/<task>.json, then the server shuts down -
it lives only for the duration of one review.

The pipeline consumes the verdict deterministically (advance.py 'interactive'
stage branch): approved -> stage advances; changes_requested -> comments are
appended to the task file and the task resets to implement.

CLI:
    python diff_review.py --task task-0007 [--repo <dir>] [--base main]
                          [--port 0] [--timeout 900] [--no-browser]

Exit codes: 0 verdict recorded, 3 timeout / browser closed without a verdict.
Stdlib only - no dependencies.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve()
CLAUDE_DIR = HERE.parents[2]
ROOT = HERE.parents[3]
REVIEW_DIR = CLAUDE_DIR / "state" / "review"
PAGE_TEMPLATE_PATH = HERE.parent / "diff_review_page.html"

MAX_FILE_BYTES = 2 * 1024 * 1024  # per-side cap; larger files render as placeholder


def _git(repo: Path, *args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout
    except Exception as exc:
        return 1, str(exc)


def resolve_base_branch(explicit: str) -> str:
    if explicit:
        return explicit
    try:
        sys.path.insert(0, str(CLAUDE_DIR / "tools" / "pipeline"))
        import state
        b = str(state.load_pipeline().get("main_branch", "main"))
        return "main" if b.startswith("{{") else b
    except Exception:
        return "main"


class DiffData:
    """Collects the branch diff once at startup; served from memory."""

    def __init__(self, repo: Path, base_branch: str, task: str):
        self.repo = repo
        self.task = task
        code, mb = _git(repo, "merge-base", base_branch, "HEAD")
        self.merge_base = mb.strip() if code == 0 else ""
        code, head = _git(repo, "rev-parse", "HEAD")
        self.head = head.strip() if code == 0 else ""
        self.files = self._file_list()

    def _file_list(self) -> list:
        if not self.merge_base:
            return []
        out = []
        code, names = _git(self.repo, "diff", "--name-status", "-M",
                           f"{self.merge_base}..HEAD")
        code2, stats = _git(self.repo, "diff", "--numstat",
                            f"{self.merge_base}..HEAD")
        counts = {}
        if code2 == 0:
            for line in stats.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    add, rm, path = parts[0], parts[1], parts[-1]
                    counts[path] = (add, rm)  # '-' for binary
        if code != 0:
            return out
        for line in names.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0][0]
            path = parts[-1]
            add, rm = counts.get(path, ("0", "0"))
            out.append({"path": path, "status": status,
                        "added": add, "removed": rm,
                        "binary": add == "-"})
        return out

    def _show(self, ref: str, path: str) -> list:
        code, text = _git(self.repo, "show", f"{ref}:{path}", timeout=30)
        if code != 0:
            return []
        if len(text.encode("utf-8", errors="replace")) > MAX_FILE_BYTES:
            return ["<file too large to render>"]
        return text.splitlines()

    def file_diff(self, path: str) -> dict:
        entry = next((f for f in self.files if f["path"] == path), None)
        if entry is None:
            return {"error": "unknown file"}
        if entry["binary"]:
            return {"path": path, "binary": True, "base_lines": [],
                    "head_lines": [], "opcodes": []}
        base_lines = [] if entry["status"] == "A" else self._show(self.merge_base, path)
        head_lines = [] if entry["status"] == "D" else self._show("HEAD", path)
        sm = difflib.SequenceMatcher(None, base_lines, head_lines, autojunk=False)
        return {"path": path, "binary": False,
                "base_lines": base_lines, "head_lines": head_lines,
                "opcodes": [list(op) for op in sm.get_opcodes()]}


def next_round(task: str) -> int:
    """1 + number of archived verdicts for this task."""
    try:
        return 1 + len(list(REVIEW_DIR.glob(f"{task}.round*.json")))
    except Exception:
        return 1


PAGE = PAGE_TEMPLATE_PATH.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    data: DiffData = None
    round_no: int = 1
    verdict_written = threading.Event()
    server_ref = None

    def log_message(self, *args):  # silence request logging
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            page = (PAGE
                    .replace("__TASK__", self.data.task)
                    .replace("__NFILES__", str(len(self.data.files)))
                    .replace("__BASE7__", self.data.merge_base[:7])
                    .replace("__HEAD7__", self.data.head[:7])
                    .replace("__ROUND__", str(self.round_no))
                    .replace("__FILES__", json.dumps(self.data.files)))
            self._send(200, page.encode("utf-8"), "text/html")
        elif self.path == "/api/files":
            self._send(200, json.dumps(self.data.files).encode("utf-8"))
        elif self.path.startswith("/api/diff"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            path = (q.get("path") or [""])[0]
            self._send(200, json.dumps(self.data.file_diff(path)).encode("utf-8"))
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path != "/api/verdict":
            self._send(404, b'{"error":"not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            verdict = payload.get("verdict")
            comments = payload.get("comments", [])
            if verdict not in ("approved", "changes_requested"):
                self._send(400, b'{"error":"bad verdict"}')
                return
            if verdict == "changes_requested" and not comments:
                self._send(400, b'{"error":"request changes needs at least one comment"}')
                return
            REVIEW_DIR.mkdir(parents=True, exist_ok=True)
            out = {
                "task": self.data.task,
                "verdict": verdict,
                "base": self.data.merge_base,
                "head": self.data.head,
                "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "round": self.round_no,
                "comments": comments,
            }
            (REVIEW_DIR / f"{self.data.task}.json").write_text(
                json.dumps(out, indent=2) + "\n", encoding="utf-8")
            self._send(200, b'{"ok":true}')
            print(f"diff-review: verdict '{verdict}' recorded with "
                  f"{len(comments)} comment(s)")
            Handler.verdict_written.set()
            threading.Timer(0.5, Handler.server_ref.shutdown).start()
        except Exception as exc:
            self._send(500, json.dumps({"error": str(exc)}).encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="CEO diff-review UI")
    parser.add_argument("--task", required=True)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--base", default="")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo)
    data = DiffData(repo, resolve_base_branch(args.base), args.task)
    if not data.merge_base or not data.head:
        print(f"diff-review: cannot resolve merge-base/HEAD in {repo} - is it a "
              f"git repo with the task branch checked out?")
        return 3
    if not data.files:
        print("diff-review: no changed files between merge-base and HEAD - nothing to review")
        return 3

    Handler.data = data
    Handler.round_no = next_round(args.task)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    Handler.server_ref = server
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"diff-review: {args.task} round {Handler.round_no} - "
          f"{len(data.files)} file(s) - serving {url} (timeout {args.timeout}s)")

    watchdog = threading.Timer(args.timeout, server.shutdown)
    watchdog.daemon = True
    watchdog.start()

    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    finally:
        watchdog.cancel()
        server.server_close()

    if Handler.verdict_written.is_set():
        return 0
    print("diff-review: no verdict recorded (timeout or window closed)")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
