"""Narrow check for scripts/docs_preview.py: allowlist, Host check, traversal refusal, markdown wrapper."""

from __future__ import annotations

import http.client
import re
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path

import docs_preview


def _png() -> bytes:
    """A valid 1x1 PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class DocsPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp_root = Path(__file__).resolve().parent.parent / "tmp"  # inside the repo, gitignored
        tmp_root.mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(dir=tmp_root)
        base = Path(cls.tmp.name)
        cls.root = base / "repo"
        (cls.root / "docs").mkdir(parents=True)
        (cls.root / ".git").mkdir()
        (cls.root / "tmp").mkdir()
        (cls.root / "tmp" / "scratch.md").write_text("scratch", encoding="utf-8")
        (cls.root / "LICENSE").write_text("MIT License", encoding="utf-8")
        (cls.root / "CHANGELOG.md").write_text("# Changelog", encoding="utf-8")
        (cls.root / "other.md").write_text("not allowed", encoding="utf-8")
        (cls.root / "README.md").write_text("# Hello\n\n```mermaid\ngraph TD; A-->B\n```\n", encoding="utf-8")
        (cls.root / "docs" / "a.md").write_text("# Page A\n\npage a </script>", encoding="utf-8")
        (cls.root / "docs" / "b.md").write_text("# Page B <b>\n", encoding="utf-8")
        (cls.root / "docs" / "z.md").write_text("# Page Z\n", encoding="utf-8")
        (cls.root / "docs" / "README.md").write_text(
            "# Docs index\n\n| [B](b.md) |\n| [A](a.md#top) |\n| [Escape](../README.md) |\n", encoding="utf-8"
        )
        (cls.root / "docs" / "assets").mkdir()
        cls.png = _png()
        (cls.root / "docs" / "assets" / "dot.png").write_bytes(cls.png)
        (cls.root / ".git" / "config").write_text("secret", encoding="utf-8")
        (base / "outside.txt").write_text("outside", encoding="utf-8")

        handler = type("H", (docs_preview.Handler,), {"root": cls.root})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base_url + path) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, ""

    def get_with_host(self, host):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", host)
        conn.endheaders()
        status = conn.getresponse().status
        conn.close()
        return status

    def test_root_serves_readme_wrapped_in_html(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype html>", body)
        self.assertIn("mermaid", body)
        self.assertIn("# Hello", body)

    def test_sidebar_lists_pages_in_index_order_with_current_marked(self):
        _, body = self.get("/docs/a.md")
        sidebar = body.split('<nav class="sidebar">')[1].split("</nav>")[0]
        links = re.findall(r'<a href="([^"]+)"( aria-current="page")?>([^<]*)</a>', sidebar)
        self.assertEqual(
            [(href, title) for href, _, title in links],
            [
                ("/README.md", "Agentry"),
                ("/docs/README.md", "Docs index"),
                ("/docs/b.md", "Page B &lt;b&gt;"),
                ("/docs/a.md", "Page A"),
                ("/docs/z.md", "Page Z"),
                ("/CHANGELOG.md", "Changelog"),
            ],
        )
        self.assertEqual([href for href, mark, _ in links if mark], ["/docs/a.md"])

    def test_pager_links_previous_and_next_in_the_same_order(self):
        _, body = self.get("/docs/a.md")
        self.assertIn('class="prev" href="/docs/b.md"', body)
        self.assertIn('class="next" href="/docs/z.md"', body)
        _, first = self.get("/")
        self.assertNotIn('class="prev"', first)
        self.assertIn('class="next" href="/docs/README.md"', first)
        _, last = self.get("/CHANGELOG.md")
        self.assertNotIn('class="next"', last)
        self.assertIn('class="prev" href="/docs/z.md"', last)

    def test_markdown_cannot_close_the_script_block(self):
        status, body = self.get("/docs/a.md")
        self.assertEqual(status, 200)
        self.assertNotIn("page a </script>", body)

    def test_traversal_and_dot_dirs_are_refused(self):
        for path in ("/../outside.txt", "/%2e%2e/outside.txt", "/docs/../../outside.txt", "/.git/config"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertIsNone(docs_preview.resolve(self.root, "/../outside.txt"))

    def test_backslash_and_drive_paths_are_refused(self):
        paths = ("/docs%5Ca.md", "/C:/Windows/win.ini", "/C%3A/x", "/%5C%5Chost%5Cshare%5Cx", "/docs/C:a.md")
        for path in paths:
            self.assertEqual(self.get(path)[0], 404, path)
            self.assertIsNone(docs_preview.resolve(self.root, path), path)
        self.assertIsNone(docs_preview.resolve(self.root, "/docs\\a.md"))

    def test_png_round_trip(self):
        with urllib.request.urlopen(self.base_url + "/docs/assets/dot.png") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertEqual(r.read(), self.png)

    def test_only_allowlisted_paths_are_served(self):
        for path in ("/README.md", "/CHANGELOG.md", "/LICENSE", "/docs/a.md"):
            self.assertEqual(self.get(path)[0], 200, path)
        for path in ("/tmp/scratch.md", "/other.md", "/docs", "/missing"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_license_is_plain_text(self):
        with urllib.request.urlopen(self.base_url + "/LICENSE") as r:
            self.assertTrue(r.headers["Content-Type"].startswith("text/plain"))
            self.assertEqual(r.read().decode(), "MIT License")

    def test_host_header_must_be_loopback_with_port(self):
        port = self.server.server_port
        self.assertEqual(self.get_with_host(f"127.0.0.1:{port}"), 200)
        self.assertEqual(self.get_with_host(f"localhost:{port}"), 200)
        for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1.evil.example:{port}"):
            self.assertEqual(self.get_with_host(host), 403, host)


if __name__ == "__main__":
    unittest.main()
