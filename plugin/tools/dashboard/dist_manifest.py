#!/usr/bin/env python3
"""Integrity manifest for the committed dashboard build (spec-0002 FR-114 to FR-116).

`dist/` is built by the maintainer (`npm run build` in `dashboard/`) and committed, so
an end user needs no Node. This module is the one hash algorithm both halves share:

  python dist_manifest.py --write   run by `npm run build` after vite: writes
                                    dist/manifest.json
  check()                           called by the server at start and by the Python
                                    suite, without Node: returns a list of problems

`manifest.json` is `{"source_sha256": str, "files": {relative path: sha256}}`. It holds
no timestamp, tool version or absolute path, so the same source builds to the same
manifest. `source_sha256` covers every source file of `dashboard/` (sorted relative
paths and contents, line endings normalised so a Windows checkout and a Linux one agree,
`node_modules`, `coverage` and build output excluded). The source check is skipped when
`dashboard/` is absent, which is the case in an installed plugin.

The server reaches only check(); write() is reachable from this file's own __main__.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST_DIR = HERE / "dist"
# plugin/tools/dashboard -> plugin/tools -> plugin -> the repository root.
SOURCE_DIR = HERE.parents[2] / "dashboard"
MANIFEST = "manifest.json"

# Directories and files that are not source of the build.
EXCLUDED_DIRS = frozenset({"node_modules", "coverage", "dist", "build", ".vite", ".git", ".turbo"})
EXCLUDED_SUFFIXES = (".tsbuildinfo",)

_SCRIPT_TAG = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_SRC_ATTR = re.compile(r"\bsrc\s*=", re.IGNORECASE)
_EXTERNAL_URL = re.compile(r"""https?://[^\s"'<>)]+|["'(=]\s*//[A-Za-z0-9][^\s"'<>)]*""", re.IGNORECASE)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def dist_files(dist_dir: Path) -> dict[str, str]:
    """Relative posix path -> sha256 of every dist file except the manifest."""
    out: dict[str, str] = {}
    for current, _dirs, names in os.walk(dist_dir):
        for name in names:
            path = Path(current) / name
            rel = path.relative_to(dist_dir).as_posix()
            if rel != MANIFEST:
                out[rel] = _sha256(path)
    return dict(sorted(out.items()))


def source_sha256(source_dir: Path) -> str:
    """One hash over the sorted relative paths and contents of the dashboard source.

    CRLF is folded to LF before hashing: git may check text files out with either line
    ending and the manifest has to be the same on every machine."""
    digest = hashlib.sha256()
    entries: list[tuple[str, Path]] = []
    for current, dirs, names in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in names:
            if name.endswith(EXCLUDED_SUFFIXES):
                continue
            path = Path(current) / name
            entries.append((path.relative_to(source_dir).as_posix(), path))
    for rel, path in sorted(entries):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(f"{rel}\0{len(content)}\0".encode())
        digest.update(content)
    return digest.hexdigest()


def build_manifest(dist_dir: Path, source_dir: Path | None) -> dict:
    return {
        "source_sha256": source_sha256(source_dir) if source_dir is not None else "",
        "files": dist_files(dist_dir),
    }


def write(dist_dir: Path | None = None, source_dir: Path | None = SOURCE_DIR) -> Path:
    """Write dist/manifest.json. Bytes, not text mode: the file must not pick up
    CRLF on Windows, or the committed manifest would differ per machine."""
    dist_dir = dist_dir or DIST_DIR
    target = dist_dir / MANIFEST
    text = json.dumps(build_manifest(dist_dir, source_dir), indent=2, sort_keys=True) + "\n"
    target.write_bytes(text.encode("utf-8"))
    return target


def _index_problems(index: Path) -> list[str]:
    try:
        html = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["index.html is missing or unreadable"]
    problems = []
    if any(not _SRC_ATTR.search(attrs) for attrs in _SCRIPT_TAG.findall(html)):
        problems.append("index.html has an inline <script> (no src attribute)")
    external = _EXTERNAL_URL.search(html)
    if external:
        problems.append(f"index.html references an external URL: {external.group(0).strip()[:80]}")
    return problems


def check(dist_dir: Path | None = None, source_dir: Path | None = SOURCE_DIR) -> list[str]:
    """Problems with the dist, [] when it is whole and matches its manifest.

    `source_dir` that is None or not a directory skips the source check (an installed
    plugin carries no `dashboard/`)."""
    dist_dir = dist_dir or DIST_DIR
    if not dist_dir.is_dir():
        return [f"dist directory is missing: {dist_dir.name}"]
    try:
        manifest = json.loads((dist_dir / MANIFEST).read_text(encoding="utf-8"))
        recorded = manifest["files"]
        if not isinstance(recorded, dict):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError):
        return [f"{MANIFEST} is missing or unreadable", *_index_problems(dist_dir / "index.html")]

    problems: list[str] = []
    actual = dist_files(dist_dir)
    for rel in sorted(actual):
        if rel not in recorded:
            problems.append(f"file not in the manifest: {rel}")
        elif actual[rel] != recorded[rel]:
            problems.append(f"file modified since the build: {rel}")
    problems += [f"file missing from dist: {rel}" for rel in sorted(set(recorded) - set(actual))]
    problems += _index_problems(dist_dir / "index.html")
    if source_dir is not None and source_dir.is_dir():
        if source_sha256(source_dir) != manifest.get("source_sha256"):
            problems.append("dashboard/ source changed since the build (source_sha256 differs); "
                            "run npm run build")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the dashboard dist manifest.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="write dist/manifest.json")
    group.add_argument("--check", action="store_true", help="print problems, exit 1 if any")
    args = parser.parse_args(argv)
    if args.write:
        if not DIST_DIR.is_dir():
            sys.stderr.write("dist_manifest.py: dist/ does not exist; run vite build first\n")
            return 1
        write(DIST_DIR, SOURCE_DIR)
        return 0
    problems = check(DIST_DIR, SOURCE_DIR)
    for problem in problems:
        sys.stderr.write(problem + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
