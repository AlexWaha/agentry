#!/usr/bin/env python3
"""Apply the v2 agent-system layers to a target project's .claude setup.

Idempotent, conflict-safe, fail-open per layer. Used for the fleet rollout
(one call per project). Layers:

  Layer 0 (cleanup)     - retire the legacy knowledge index: delete
                          tools/knowledge-index/ + knowledge.db, the
                          retrieval-discipline rule, the query-knowledge skill,
                          and strip its hooks from the target settings.json.
  Layer 1 (infra)       - copy tools/pipeline (incl. agent_gate.py), tools/hooks,
                          spec/epic/task templates, core skills and rules;
                          install project-root .mcp.json (codegraph MCP);
                          merge settings.json with the v2 hook set using
                          portable $CLAUDE_PROJECT_DIR paths; merge .gitignore.
  Layer 2 (frontmatter) - pin model IDs (opus -> claude-opus-5,
                          sonnet/haiku -> claude-sonnet-5), effort:max on rigor
                          agents, memory:project on learners, per-profile gate
                          hooks + maxTurns, mcpServers on code-facing agents.
                          Hook commands use $CLAUDE_PROJECT_DIR (clone-safe).
  Layer 3 (add agents)  - copy universal agents named via --add-agents when the
                          target has no agents dir yet.

Usage:
  python apply_optimization.py --target <project-root>
                               [--source <AI-team-universal/.claude>]
                               [--add-agents a,b,c]

Prints a single-line JSON report. Exit 0 on success, 2 on partial (see report).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# --- role maps (mirror the template's frontmatter state) --------------------

EFFORT_MAX = {"reviewer", "security-engineer", "qa-engineer"}
MEMORY_PROJECT = {
    "reviewer", "security-engineer", "qa-engineer", "architect",
    "senior-backend-dev", "senior-frontend-dev", "devops-engineer",
    "data-engineer", "incident-response-commander", "feedback-synthesizer",
    "spec-developer",
}
PROFILE_DEV = {"senior-backend-dev", "senior-frontend-dev", "qa-engineer",
               "devops-engineer", "data-engineer", "rapid-prototyper"}
PROFILE_READONLY = {"architect", "reviewer", "security-engineer",
                    "evidence-collector", "performance-benchmarker",
                    "ai-detector", "accessibility-auditor"}
PROFILE_DOCS = {"technical-writer", "product-manager", "spec-developer",
                "sprint-prioritizer", "content-manager", "content-writer",
                "editor", "seo-specialist", "geo-specialist", "humanizer"}
CODEGRAPH_AGENTS = {"architect", "senior-backend-dev", "senior-frontend-dev",
                    "qa-engineer", "reviewer", "security-engineer",
                    "devops-engineer", "data-engineer", "rapid-prototyper",
                    "incident-response-commander"}
MAX_TURNS = {"dev": 60, "readonly": 40, "docs": 30}

MODEL_PINS = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5",
              "haiku": "claude-sonnet-5"}

COPY_SKILLS = ["self-learning", "new-task", "new-epic"]
COPY_RULES = ["code-retrieval.md"]
PIPELINE_FILES = ["state.py", "gate.py", "advance.py", "approve.py",
                  "pretool_gate.py", "stop_gate.py", "agent_gate.py"]
HOOK_FILES = ["session_start.py", "subagent_stop.py", "dangerous_patterns.py"]

GITIGNORE_LINES = [
    "# Native agent memory: local tier is per-machine, never committed.",
    "agent-memory-local/",
    "# Personal local settings",
    "settings.local.json",
]

ROOT_GITIGNORE_LINES = [
    "# Codegraph index - per-machine artifact built by the codegraph CLI.",
    ".codegraph/",
]

MCP_JSON = {"mcpServers": {"codegraph": {"command": "codegraph",
                                         "args": ["serve", "--mcp"]}}}


def log(report: dict) -> None:
    print("REPORT " + json.dumps(report, ensure_ascii=False))


def py_hook(root: Path, rel: str, timeout: int, status: str | None = None,
            extra: dict | None = None) -> dict:
    cmd = f'python "$CLAUDE_PROJECT_DIR/.claude/{rel}"'
    h = {"type": "command", "command": cmd, "timeout": timeout}
    if status:
        h["statusMessage"] = status
    if extra:
        h.update(extra)
    return h


# --- layer 0: cleanup --------------------------------------------------------

def cleanup_knowledge_index(dst_claude: Path, report: dict) -> None:
    removed = []
    ki = dst_claude / "tools" / "knowledge-index"
    if ki.exists():
        shutil.rmtree(ki, ignore_errors=True)
        removed.append("tools/knowledge-index")
    rule = dst_claude / "rules" / "retrieval-discipline.md"
    if rule.exists():
        rule.unlink()
        removed.append("rules/retrieval-discipline.md")
    skill = dst_claude / "skills" / "query-knowledge"
    if skill.exists():
        shutil.rmtree(skill, ignore_errors=True)
        removed.append("skills/query-knowledge")
    report["cleanup"] = removed or "nothing-to-remove"


def strip_index_hooks(data: dict) -> bool:
    """Remove hook entries whose command references the retired index."""
    changed = False
    hooks = data.get("hooks", {})
    for event, groups in list(hooks.items()):
        new_groups = []
        for grp in groups:
            inner = [h for h in grp.get("hooks", [])
                     if "knowledge-index" not in str(h.get("command", ""))]
            if inner != grp.get("hooks", []):
                changed = True
            if inner:
                grp = dict(grp)
                grp["hooks"] = inner
                new_groups.append(grp)
            elif grp.get("hooks") is None:
                new_groups.append(grp)
            else:
                changed = True  # group dropped entirely
        hooks[event] = new_groups
    return changed


# --- layer 1: infra ----------------------------------------------------------

def copy_if_absent(src: Path, dst: Path) -> bool:
    if dst.exists() or not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def copy_always(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def install_infra(src_claude: Path, dst_claude: Path, target_root: Path,
                  report: dict, claude_only: bool = False) -> None:
    copied = []
    # Engine + hooks are versioned tooling - always refresh to source version.
    for name in PIPELINE_FILES:
        if copy_always(src_claude / "tools" / "pipeline" / name,
                       dst_claude / "tools" / "pipeline" / name):
            copied.append(f"tools/pipeline/{name}")
    for name in HOOK_FILES:
        if copy_always(src_claude / "tools" / "hooks" / name,
                       dst_claude / "tools" / "hooks" / name):
            copied.append(f"tools/hooks/{name}")
    # Templates / skills / rules are content - copy only if absent.
    if copy_if_absent(src_claude / "specs" / "_template.md",
                      dst_claude / "specs" / "_template.md"):
        copied.append("specs/_template.md")
    if copy_if_absent(src_claude / "tasks" / "templates" / "epic-template.md",
                      dst_claude / "tasks" / "templates" / "epic-template.md"):
        copied.append("tasks/templates/epic-template.md")
    if copy_if_absent(src_claude / "tasks" / "templates" / "task-template.md",
                      dst_claude / "tasks" / "templates" / "task-template.md"):
        copied.append("tasks/templates/task-template.md")
    (dst_claude / "tasks" / "epics").mkdir(parents=True, exist_ok=True)
    (dst_claude / "tasks" / "active").mkdir(parents=True, exist_ok=True)
    (dst_claude / "tasks" / "done").mkdir(parents=True, exist_ok=True)
    for name in COPY_SKILLS:
        if copy_if_absent(src_claude / "skills" / name / "SKILL.md",
                          dst_claude / "skills" / name / "SKILL.md"):
            copied.append(f"skills/{name}")
    for name in COPY_RULES:
        if copy_if_absent(src_claude / "rules" / name,
                          dst_claude / "rules" / name):
            copied.append(f"rules/{name}")
    if copy_if_absent(src_claude / "rules" / "self-learning.md",
                      dst_claude / "rules" / "self-learning.md"):
        copied.append("rules/self-learning.md")
    # pipeline.json only if absent (target's gates are project-specific)
    if copy_if_absent(src_claude / "pipeline.json", dst_claude / "pipeline.json"):
        copied.append("pipeline.json (placeholders - run onboarding Phase B)")
    # project-root .mcp.json (skip in claude_only mode: the project root file may
    # be git-tracked and must not be modified - e.g. a git-hidden .claude setup)
    mcp = target_root / ".mcp.json"
    if claude_only:
        report["mcp_json"] = "skipped(claude-only)"
    elif not mcp.exists():
        mcp.write_text(json.dumps(MCP_JSON, indent=2) + "\n", encoding="utf-8")
        copied.append(".mcp.json")
    else:
        try:
            data = json.loads(mcp.read_text(encoding="utf-8"))
            data.setdefault("mcpServers", {}).setdefault(
                "codegraph", MCP_JSON["mcpServers"]["codegraph"])
            mcp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            copied.append(".mcp.json (merged)")
        except (ValueError, OSError):
            report["mcp_json"] = "SKIPPED-invalid-json"
    report["infra"] = copied


def merge_root_gitignore(target_root: Path) -> None:
    path = target_root / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    have = {ln.strip() for ln in existing}
    if ".codegraph/" in have:
        return
    out = existing[:]
    if out and out[-1].strip():
        out.append("")
    out.extend(ROOT_GITIGNORE_LINES)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def merge_gitignore(dst_claude: Path) -> None:
    path = dst_claude / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    have = {ln.strip() for ln in existing}
    additions = [ln for ln in GITIGNORE_LINES
                 if ln.startswith("#") or ln.strip() not in have]
    # only add comment lines when their payload line is actually being added
    payload_new = [ln for ln in GITIGNORE_LINES
                   if not ln.startswith("#") and ln.strip() not in have]
    if not payload_new:
        return
    out = existing[:]
    if out and out[-1].strip():
        out.append("")
    out.extend(additions)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def merge_settings(dst_claude: Path, target_root: Path, report: dict) -> None:
    path = dst_claude / "settings.json"
    data: dict
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            report["settings"] = f"SKIPPED-invalid-json ({exc})"
            return
    else:
        data = {"permissions": {"allow": ["Read", "Edit", "Write"]}}

    changed = strip_index_hooks(data)
    hooks = data.setdefault("hooks", {})

    def ensure(event: str, matcher: str | None, hook: dict) -> None:
        nonlocal changed
        groups = hooks.setdefault(event, [])
        for grp in groups:
            for h in grp.get("hooks", []):
                if h.get("command") == hook["command"]:
                    return
        grp: dict = {"hooks": [hook]}
        if matcher:
            grp["matcher"] = matcher
        groups.append(grp)
        changed = True

    ensure("SessionStart", None,
           py_hook(target_root, "tools/hooks/session_start.py", 60,
                   "Session start: pipeline state, codegraph sync, memory health..."))
    ensure("PreToolUse", "Bash|Edit|Write",
           py_hook(target_root, "tools/pipeline/pretool_gate.py", 20))
    ensure("Stop", None,
           py_hook(target_root, "tools/pipeline/stop_gate.py", 20))
    ensure("SubagentStop", None,
           py_hook(target_root, "tools/hooks/subagent_stop.py", 10))
    ensure("PreCompact", None,
           {"type": "command",
            "command": 'python "$CLAUDE_PROJECT_DIR/.claude/tools/pipeline/state.py" --resume',
            "timeout": 10,
            "statusMessage": "Preserving pipeline state through compaction..."})
    ensure("PostToolUse", "Bash",
           {"type": "command", "command": "rm -f nul NUL", "timeout": 5,
            "statusMessage": "Cleaning up NUL files..."})

    if data.get("autoMemoryEnabled") is not True:
        data["autoMemoryEnabled"] = True
        changed = True
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    if "Bash(codegraph *)" not in allow:
        allow.append("Bash(codegraph *)")
        changed = True

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    report["settings"] = "merged" if changed else "already-current"


# --- layer 2: frontmatter -----------------------------------------------------

FM_RE = re.compile(r"^(---\n)(.*?)(\n---\n)(.*)$", re.DOTALL)


def profile_of(name: str) -> str | None:
    if name in PROFILE_DEV:
        return "dev"
    if name in PROFILE_READONLY:
        return "readonly"
    if name in PROFILE_DOCS:
        return "docs"
    return None


def hooks_yaml(profile: str, root: Path) -> str:
    gate = ('python "$CLAUDE_PROJECT_DIR/.claude/tools/pipeline/agent_gate.py"'
            f" --profile {profile}")
    danger = 'python "$CLAUDE_PROJECT_DIR/.claude/tools/hooks/dangerous_patterns.py"'
    lines = [
        "hooks:",
        "  PreToolUse:",
        '    - matcher: "Bash|Edit|Write"',
        "      hooks:",
        "        - type: command",
        f"          command: '{gate}'",
        "          timeout: 20",
    ]
    if profile == "dev":
        lines += [
            "  PostToolUse:",
            '    - matcher: "Edit|Write"',
            "      hooks:",
            "        - type: command",
            f"          command: '{danger}'",
            "          timeout: 15",
        ]
    return "\n".join(lines)


def patch_agent(path: Path, root: Path) -> list[str]:
    # utf-8-sig strips a BOM if present: a BOM before '---' breaks FM_RE and
    # the file silently misses every patch. The write below goes back BOM-less.
    s = path.read_text(encoding="utf-8-sig")
    m = FM_RE.match(s)
    if not m:
        return ["no-frontmatter"]
    head, fm, tail, body = m.groups()
    orig = fm
    name = path.stem
    changed: list[str] = []

    for alias, pin in MODEL_PINS.items():
        fm2 = re.sub(rf"^model:\s*{alias}\s*$", f"model: {pin}", fm, flags=re.MULTILINE)
        if fm2 != fm:
            changed.append(f"model={pin}")
            fm = fm2

    if name in EFFORT_MAX and not re.search(r"^effort:\s*max\s*$", fm, re.MULTILINE):
        if re.search(r"^effort:", fm, re.MULTILINE):
            fm = re.sub(r"^effort:.*$", "effort: max", fm, flags=re.MULTILINE)
        else:
            fm += "\neffort: max"
        changed.append("effort=max")

    if name in MEMORY_PROJECT and not re.search(r"^memory:", fm, re.MULTILINE):
        fm += "\nmemory: project"
        changed.append("memory=project")

    if name in CODEGRAPH_AGENTS and "mcpServers:" not in fm:
        fm += "\nmcpServers:\n  - codegraph"
        changed.append("mcpServers=codegraph")

    prof = profile_of(name)
    if prof and "agent_gate.py" not in fm:
        if "maxTurns:" not in fm:
            fm += f"\nmaxTurns: {MAX_TURNS[prof]}"
        fm += "\n" + hooks_yaml(prof, root)
        changed.append(f"gate={prof}")

    if fm != orig:
        path.write_text(head + fm + tail + body, encoding="utf-8")
    return changed


def patch_agents(dst_claude: Path, target_root: Path, report: dict) -> None:
    agents_dir = dst_claude / "agents"
    if not agents_dir.exists():
        report["agents_patched"] = "no-agents-dir"
        return
    results = {}
    for f in sorted(agents_dir.glob("*.md")):
        res = patch_agent(f, target_root)
        if res and res != ["no-frontmatter"]:
            results[f.stem] = "+".join(res)
    report["agents_patched"] = results


def add_agents(src_claude: Path, dst_claude: Path, names: list, target_root: Path,
               report: dict) -> None:
    if not names:
        report["agents_added"] = []
        return
    agents_dir = dst_claude / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for n in names:
        src = src_claude / "agents" / f"{n}.md"
        dst = agents_dir / f"{n}.md"
        if src.exists() and not dst.exists():
            text = src.read_text(encoding="utf-8")
            text = text.replace("{{WORKSPACE_ROOT}}", "$CLAUDE_PROJECT_DIR")
            dst.write_text(text, encoding="utf-8")
            added.append(n)
    report["agents_added"] = added


# --- verify -------------------------------------------------------------------

def verify(dst_claude: Path, report: dict) -> bool:
    ok = True
    proc = subprocess.run(
        [sys.executable, str(dst_claude / "tools" / "pipeline" / "state.py"), "--show"],
        capture_output=True, text=True)
    report["engine_ok"] = proc.returncode == 0
    ok &= proc.returncode == 0
    leftovers = []
    for f in [dst_claude / "settings.json", *sorted((dst_claude / "agents").glob("*.md"))
              ] if (dst_claude / "agents").exists() else [dst_claude / "settings.json"]:
        try:
            text = f.read_text(encoding="utf-8")
            if "{{WORKSPACE_ROOT}}" in text:
                leftovers.append(str(f.relative_to(dst_claude)))
            # A machine-local absolute hook path leaks into the repo and breaks
            # on every other machine - hooks must go through $CLAUDE_PROJECT_DIR.
            if re.search(r'[A-Za-z]:/[^"\'\s]*\.claude/', text):
                leftovers.append(f"{f.relative_to(dst_claude)} (absolute local path)")
        except OSError:
            pass
    report["workspace_root_leftovers"] = leftovers
    ok &= not leftovers
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="project root (parent of .claude)")
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[2]),
                        help="source .claude dir (default: AI-team-universal/.claude)")
    parser.add_argument("--add-agents", default="", help="comma-separated agent names to add")
    parser.add_argument("--claude-only", action="store_true",
                        help="only write under .claude/ - skip project-root "
                             ".mcp.json and root .gitignore (for git-hidden .claude)")
    args = parser.parse_args()

    src_claude = Path(args.source).resolve()
    target_root = Path(args.target).resolve()
    dst_claude = target_root / ".claude"
    report: dict = {"target": str(dst_claude)}

    if not src_claude.exists():
        report["error"] = f"source not found: {src_claude}"
        log(report)
        return 1
    dst_claude.mkdir(parents=True, exist_ok=True)

    add_list = [a.strip() for a in args.add_agents.split(",") if a.strip()]

    cleanup_knowledge_index(dst_claude, report)
    install_infra(src_claude, dst_claude, target_root, report, args.claude_only)
    merge_gitignore(dst_claude)
    if not args.claude_only:
        merge_root_gitignore(target_root)
    merge_settings(dst_claude, target_root, report)
    add_agents(src_claude, dst_claude, add_list, target_root, report)
    patch_agents(dst_claude, target_root, report)
    ok = verify(dst_claude, report)

    log(report)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
