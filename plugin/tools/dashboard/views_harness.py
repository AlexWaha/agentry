"""Harness inventory and handoff read model (spec-0002 FR-34 to FR-37, FR-123).

Agents, rules, skills and hooks are read from the project's `.claude/`, the plugin's own
skills and hooks.json, and the roster tables of `.claude/CLAUDE.md`; handoff documents come
from the harness's own chain_docs / validate_handoff, so `valid` is exactly what the
Stop hook enforces. Document text is returned as data and never interpreted.
"""

from __future__ import annotations

import os
import re
from itertools import pairwise
from pathlib import Path

import stores

stores.ensure_tool_paths()

import handoff  # noqa: E402  (harness modules: the tool folders were put on sys.path above)
import main_thread_rules  # noqa: E402

HANDOFF_FILE = re.compile(r"^task-.*\.md$")
ROSTER_ROW = re.compile(r"^\|\s*([\w-]+)\s*\|\s*(dev|docs|readonly)\s*\|")
CORE_RULE = re.compile(r"^@rules/([\w.-]+\.md)\s*$", re.MULTILINE)
SCRIPT_TOKEN = re.compile(r"""([^\s"']{1,300}\.(?:py|sh|js|mjs|cjs))""")
# A roster line or hook command longer than this is not matched: the patterns stay linear.
MAX_ROSTER_LINE = 400
MAX_COMMAND_SCAN = 2000
PLUGIN_VAR = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?")
PROJECT_VAR = re.compile(r"\$\{?CLAUDE_PROJECT_DIR\}?|%CLAUDE_PROJECT_DIR%")


def _as_list(value) -> list[str]:
    """A frontmatter value as a list: block list, `[a, b]` flow list or `a, b` string."""
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part.strip().strip("\"'") for part in text.split(",") if part.strip()]


def _front(path: Path) -> dict:
    return stores.parse_head(stores.split_frontmatter(stores.read_capped(path, stores.HEAD_CAP_BYTES))[0])


def _text(front: dict, key: str) -> str:
    value = front.get(key, "")
    return value if isinstance(value, str) else ""


def _department_heading(line: str) -> str:
    """`**Technical department [ACTIVE]:**` -> `Technical`; "" for any other line. A linear
    scan for the word; the former lazy regex over `**<text> department` backtracked quadratically."""
    lowered = line.lower()
    if not line.startswith("**") or len(lowered) != len(line):
        return ""
    at = lowered.find("department", 3)
    while at != -1:
        end = at + len("department")
        before = line[2:at]
        if before[-1:].isspace() and before.strip() and not (line[end:end + 1].isalnum()
                                                               or line[end:end + 1] == "_"):
            return before.strip()
        at = lowered.find("department", at + 1)
    return ""


def _roster(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """agent -> department and agent -> gate profile from the roster tables."""
    department, profile = {}, {}
    current = ""
    for line in text.splitlines():
        if len(line) > MAX_ROSTER_LINE:
            continue
        heading = _department_heading(line)
        if heading:
            current = heading
            continue
        row = ROSTER_ROW.match(line)
        if row:
            department[row.group(1)] = current
            profile[row.group(1)] = row.group(2)
    return department, profile


def _install_kind(plugin_root: Path) -> str:
    parts = [p.casefold() for p in plugin_root.absolute().parts]
    return "cache" if any(a == "plugins" and b == "cache" for a, b in pairwise(parts)) else "directory"


def _agents(claude: Path, department: dict, profile: dict) -> list[dict]:
    out = []
    for path in sorted((claude / "agents").glob("*.md")):
        front = _front(path)
        name = _text(front, "name") or path.stem
        out.append({
            "name": name,
            "description": _text(front, "description"),
            "department": department.get(name, ""),
            "profile": profile.get(name) or _text(front, "profile"),
            "model": _text(front, "model"),
            "tools": _as_list(front.get("tools", "")),
            "rules": _as_list(front.get("rules", "")),
            "skills": _as_list(front.get("skills", "")),
        })
    return out


def _rules(claude: Path, roster: str, agents: list[dict]) -> list[dict]:
    core = set(CORE_RULE.findall(roster))
    main_thread = set(main_thread_rules.RULES)
    out = []
    for path in sorted((claude / "rules").glob("*.md")):
        users = [a["name"] for a in agents if path.name in a["rules"]]
        if path.name in core:
            delivery = "core"
        elif path.name in main_thread:
            delivery = "main-thread"
        else:
            delivery = "targeted" if users else "unused"
        out.append({"name": path.name, "bytes": path.stat().st_size, "delivery": delivery,
                    "agents": users})
    return out


def _skills(skill_dir: Path, source: str, prefix: str, agents: list[dict]) -> list[dict]:
    out = []
    for path in sorted(skill_dir.glob("*/SKILL.md")):
        front = _front(path)
        name = _text(front, "name") or path.parent.name
        out.append({
            "name": name,
            "description": _text(front, "description"),
            "source": source,
            "command": f"/{prefix}{name}",
            "user_only": _text(front, "disable-model-invocation").lower() == "true",
            "preloaded_by": [a["name"] for a in agents if name in a["skills"]],
        })
    return out


def _script(command: str, plugin_root: Path, project_root: Path) -> tuple[str, bool]:
    """(script as written relative to its root, whether the file exists)."""
    found = SCRIPT_TOKEN.search(command[:MAX_COMMAND_SCAN])
    if not found:
        return "", True
    token = found.group(1)
    if PLUGIN_VAR.match(token):
        shown = PLUGIN_VAR.sub("", token).lstrip("/\\")
        return shown.replace("\\", "/"), (plugin_root / shown).is_file()
    if PROJECT_VAR.match(token):
        shown = PROJECT_VAR.sub("", token).lstrip("/\\")
        return shown.replace("\\", "/"), (project_root / shown).is_file()
    path = Path(token)
    return token, (path if path.is_absolute() else project_root / path).is_file()


def _hooks(source: str, config: dict, plugin_root: Path, project_root: Path) -> list[dict]:
    blocks = config.get("hooks")
    out = []
    for event, groups in (blocks.items() if isinstance(blocks, dict) else []):
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []):
                if not isinstance(hook, dict):
                    continue
                command = str(hook.get("command") or "")
                script, exists = _script(command, plugin_root, project_root)
                timeout = hook.get("timeout")
                out.append({
                    "source": source, "event": str(event), "matcher": str(group.get("matcher") or ""),
                    "command": command, "script": script, "script_exists": exists,
                    "timeout": timeout if isinstance(timeout, (int, float))
                    and not isinstance(timeout, bool) else None,
                })
    return out


def harness(ctx: stores.Ctx, params: stores.Params):
    params.check()
    root, plugin_root = ctx.project_root, ctx.plugin_root
    claude = root / ".claude"
    roster = stores.read_capped(claude / "CLAUDE.md")
    department, profile = _roster(roster)
    agents = _agents(claude, department, profile)
    plugin = stores.plugin_manifest(plugin_root)
    name = str(plugin.get("name") or "")
    return {
        "plugin": {"name": name, "version": str(plugin.get("version") or ""),
                   "root": str(plugin_root), "install": _install_kind(plugin_root)},
        "agents": agents,
        "rules": _rules(claude, roster, agents),
        "skills": (_skills(plugin_root / "skills", "plugin", f"{name}:" if name else "", agents)
                   + _skills(claude / "skills", "project", "", agents)),
        "hooks": (_hooks("plugin", stores.read_json(plugin_root / "hooks" / "hooks.json"),
                         plugin_root, root)
                  + _hooks("settings", stores.read_json(claude / "settings.json"), plugin_root, root)),
    }, None


# --------------------------------------------------------------------------- handoffs

def _problems(path: Path, task: str) -> list[str]:
    try:
        return handoff.validate_handoff(path, task)
    except Exception:
        return ["the handoff document could not be validated"]


def _entry(path: Path, front: dict, problems: list[str]) -> dict:
    return {"task": path.stem, "title": _text(front, "title"), "author": _text(front, "author"),
            "date": _text(front, "date"), "bytes": _size(path), "valid": not problems}


def _mtime(entry) -> float:
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def chain(directory: Path) -> list[tuple[Path, dict]]:
    """Every handoff document with its frontmatter, newest first, in the order of
    handoff.chain_docs (date, then modification time, then name, all descending). The order
    is recomputed here because chain_docs re-reads every file on every call; this goes
    through the stat-keyed cache of stores, which is what keeps a 500-document list fast."""
    try:
        entries = [e for e in os.scandir(directory) if HANDOFF_FILE.match(e.name) and e.is_file()]
    except OSError:
        return []
    pairs = [(e, h) for e, h in zip(entries, stores.read_heads(entries)) if h is not None]
    ranked = sorted(pairs, key=lambda pair: (_text(pair[1], "date"), _mtime(pair[0]), pair[0].name),
                    reverse=True)
    return [(Path(entry.path), head) for entry, head in ranked]


def handoffs(ctx: stores.Ctx, params: stores.Params):
    limit, offset = params.page(25, 200)
    params.check()
    docs = chain(handoff.handoff_path("task-0000").parent)
    return ([_entry(path, head, _problems(path, path.stem)) for path, head in docs[offset:offset + limit]],
            {"total": len(docs), "limit": limit, "offset": offset})


def handoff_doc(ctx: stores.Ctx, params: stores.Params, task_id: str):
    stores.path_id(task_id)
    params.check()
    path = handoff.handoff_path(task_id)
    if not path.is_file():
        raise stores.not_found("No such handoff document")
    text = stores.read_capped(path)
    head, body = stores.split_frontmatter(text)
    problems = _problems(path, task_id)
    return {**_entry(path, stores.parse_head(head), problems),
            "body": stores.cap_text(body)[0], "problems": problems}, None
