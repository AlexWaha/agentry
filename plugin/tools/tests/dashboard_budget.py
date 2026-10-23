"""The JavaScript size budgets of the dashboard build, shared by the dist tests (NFR-1).

NFR-1 first capped all of `dist/assets/*.js` at 204800 bytes gzip. Task-0152 added a 3D graph view whose
three.js scene is a lazy chunk (`graph3d-<hash>.js`, loaded only when a 3D graph is opened), so the
budget is two numbers: 204800 bytes for every chunk except that one, and 256000 bytes for the 3D
chunk(s). A chunk counts as 3D by its file name prefix, which the build derives from the module that
imports the library (`graph3d.ts`).
"""

from __future__ import annotations

import gzip
from pathlib import Path

JS_GZIP_BUDGET = 204_800
GRAPH3D_GZIP_BUDGET = 256_000
GRAPH3D_PREFIX = "graph3d-"


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


def is_graph3d(path: Path) -> bool:
    return path.name.startswith(GRAPH3D_PREFIX)


def gzip_totals(assets: Path) -> tuple[int, int]:
    """(bytes of every chunk but the 3D ones, bytes of the 3D chunks), all gzip level 9."""
    rest = sum(gzip_size(p) for p in assets.glob("*.js") if not is_graph3d(p))
    three = sum(gzip_size(p) for p in assets.glob("*.js") if is_graph3d(p))
    return rest, three
