"""Local preview of README.md and docs/ rendered like GitHub does.

Usage:
    python scripts/docs_preview.py [--port 8765] [--no-open]

Serves the repository on 127.0.0.1 only. Every .md request returns one HTML page
that renders the markdown in the browser (marked, github-markdown-css, mermaid,
loaded from a pinned CDN version, so the page needs internet; nothing is installed).
Only README.md, CHANGELOG.md, LICENSE and docs/** are served (images under docs/
as they are, LICENSE and unknown file types as text/plain); everything else is a
404, as are paths outside the repository and dot directories such as .git. Requests
whose Host header is not 127.0.0.1:<port> or localhost:<port> are refused.
Every page gets a left sidebar (README, docs/README.md, the other docs pages in the order of
the index table in docs/README.md, then CHANGELOG) and a previous/next pair at the bottom.
Stop with Ctrl+C.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import webbrowser
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_FILES = {"README.md", "CHANGELOG.md", "LICENSE"}
ALLOWED_DIRS = {"docs"}

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5.8.1/github-markdown.min.css">
<style>
body { margin: 0; background: Canvas; color: CanvasText; }
#nav-toggle { display: none; }
.sidebar { position: fixed; top: 0; left: 0; bottom: 0; width: 260px; box-sizing: border-box;
  overflow-y: auto; padding: 16px 12px; background: Canvas;
  border-right: 1px solid color-mix(in srgb, CanvasText 20%, transparent);
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
.sidebar a { display: block; padding: 5px 10px; border-radius: 6px; color: inherit; text-decoration: none; }
.sidebar a:hover { background: color-mix(in srgb, CanvasText 8%, transparent); }
.sidebar a[aria-current="page"] {
  background: color-mix(in srgb, CanvasText 14%, transparent); font-weight: 600; }
.nav-button { display: none; }
.content { margin-left: 260px; }
.markdown-body { box-sizing: border-box; max-width: 980px; margin: 0 auto; padding: 45px; }
.pager { box-sizing: border-box; max-width: 980px; margin: 0 auto; padding: 0 45px 45px;
  display: flex; gap: 16px;
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
.pager a { color: #0969da; text-decoration: none; }
.pager a:hover { text-decoration: underline; }
.pager .next { margin-left: auto; text-align: right; }
@media (max-width: 800px) {
  .sidebar { display: none; z-index: 2; box-shadow: 0 0 12px rgba(0,0,0,.3); }
  #nav-toggle:checked ~ .sidebar { display: block; }
  .nav-button { display: block; position: sticky; top: 0; z-index: 1; padding: 8px 16px; cursor: pointer;
    background: Canvas; font: 14px sans-serif;
    border-bottom: 1px solid color-mix(in srgb, CanvasText 20%, transparent); }
  .content { margin-left: 0; }
  .markdown-body { padding: 24px 16px; }
  .pager { padding: 0 16px 24px; }
}
</style></head>
<body><input type="checkbox" id="nav-toggle"><label class="nav-button" for="nav-toggle">Pages</label>
<nav class="sidebar">__NAV__</nav>
<div class="content"><article id="out" class="markdown-body"></article>
__PAGER__</div>
<script src="https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js"></script>
<script>
const md = __MARKDOWN__;
const out = document.getElementById("out");
out.innerHTML = marked.parse(md, { gfm: true });
// GitHub-style heading ids so #anchor links work
const seen = {};
out.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach(h => {
  const id = h.textContent.toLowerCase().replace(/[^\\p{L}\\p{N}\\s_-]/gu, "").trim().replace(/\\s/g, "-");
  seen[id] = (seen[id] ?? -1) + 1;
  h.id = seen[id] ? id + "-" + seen[id] : id;
});
out.querySelectorAll("code.language-mermaid").forEach(c => {
  const d = document.createElement("div");
  d.className = "mermaid";
  d.textContent = c.textContent;
  c.parentElement.replaceWith(d);
});
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default" });
mermaid.run().then(() => {
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
});
</script></body></html>
"""


def resolve(root: Path, url_path: str) -> Path | None:
    """Map a URL path to an allowlisted file inside root, or None."""
    rel = unquote(urlsplit(url_path).path).lstrip("/") or "README.md"
    if "\\" in rel or ":" in rel:  # UNC and drive paths: refuse before the filesystem is touched
        return None
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        return None
    parts = target.relative_to(base).parts
    if not parts or any(part.startswith(".") for part in parts):
        return None
    if not (len(parts) == 1 and parts[0] in ALLOWED_FILES) and parts[0] not in ALLOWED_DIRS:
        return None
    return target if target.is_file() else None


def first_heading(path: Path) -> str:
    """Text of the first markdown heading outside a code fence, else the file name."""
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and (m := re.match(r"#{1,6}\s+(.+?)\s*#*\s*$", line)):
            return re.sub(r"[`*_]", "", m.group(1))
    return path.name


def nav_pages(root: Path) -> list[Path]:
    """README, docs/README.md, pages linked from its index in table order, other docs/*.md, CHANGELOG."""
    base = root.resolve()
    docs = base / "docs"
    index = docs / "README.md"
    linked = []
    if index.is_file():
        for link in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", index.read_text(encoding="utf-8")):
            target = (docs / link).resolve()
            if target.is_relative_to(docs):
                linked.append(target)
    candidates = [base / "README.md", index, *linked, *sorted(docs.glob("*.md")), base / "CHANGELOG.md"]
    pages: list[Path] = []
    for p in candidates:
        if p not in pages and p.is_file():
            pages.append(p)
    return pages


def render_page(path: Path, root: Path = ROOT) -> bytes:
    base = root.resolve()
    pages = nav_pages(base)
    cur = path.resolve()

    def href(p: Path) -> str:
        return "/" + quote(p.relative_to(base).as_posix())

    def title(p: Path) -> str:
        # the root README opens with a banner, so its first heading is not its name
        return "Agentry" if p == base / "README.md" else first_heading(p)

    links = []
    for p in pages:
        mark = ' aria-current="page"' if p == cur else ""
        links.append(f'<a href="{href(p)}"{mark}>{escape(title(p))}</a>')
    pager = ""
    if cur in pages:
        i = pages.index(cur)
        prev = nxt = ""
        if i:
            before = pages[i - 1]
            prev = f'<a class="prev" href="{href(before)}">&larr; {escape(title(before))}</a>'
        if i + 1 < len(pages):
            after = pages[i + 1]
            nxt = f'<a class="next" href="{href(after)}">{escape(title(after))} &rarr;</a>'
        pager = f'<nav class="pager">{prev}{nxt}</nav>'
    # "<" is escaped so a literal "</script>" inside the markdown cannot end the script block
    md = json.dumps(path.read_text(encoding="utf-8")).replace("<", "\\u003c")
    values = {"TITLE": escape(path.name), "NAV": "\n".join(links), "PAGER": pager, "MARKDOWN": md}
    # one pass, so text inside a substituted value is never substituted again
    return re.sub(r"__(TITLE|NAV|PAGER|MARKDOWN)__", lambda m: values[m.group(1)], PAGE).encode()


class Handler(BaseHTTPRequestHandler):
    root = ROOT

    def do_GET(self):
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = resolve(self.root, self.path)
        if path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path.suffix == ".md":
            body, ctype = render_page(path, self.root), "text/html; charset=utf-8"
        else:
            body = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "text/plain; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # requested port busy: take a free one
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Serving {ROOT} at {url} (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
