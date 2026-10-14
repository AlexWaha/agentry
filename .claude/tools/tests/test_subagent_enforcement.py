"""Subagent enforcement (task-0116): paths are judged where they resolve, the live
harness is out of reach of dev and docs agents, readonly and docs Bash is default-deny,
and a heredoc body is data.

The failure this closes: agent_gate and pretool_gate recognised writes LEXICALLY, so a
Python one-liner, a script the agent wrote itself, an NTFS stream suffix, a junction or
a `cd` reached `.agentry/pipeline.json` and `.agentry/state/` (the User's approval
records) while every check passed. Scope decided by the User (plan
2026-10-09-task-0116-options.md, D1 a, D3, D4):

  * readonly and docs: PREVENTED. Default-deny argv0 allowlist (agent_gate.ALLOWED_PROGRAMS)
    plus `--allow-script NAME` on three agents' hook lines.
  * the file tools (Write/Edit/MultiEdit/NotebookEdit) for every profile: PREVENTED, by
    resolved path (samefile / realpath / stream suffix / payload cwd).
  * dev: an interpreter one-liner is NOT COVERED. The tests labelled NOT COVERED pin the
    exit codes as they are today, so closing the gap later is a decision that has to
    change a test, and the residual is stated in rules/orchestration.md "Subagent
    enforcement" rather than discovered.

Every fixture project is a sandbox with `state.ROOT`, `state.STATE_DIR` and
`state.PIPELINE_PATH` patched onto it, so nothing here touches the real tree.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate
import pretool_gate
import state
import tmproot

PROFILES = {"dev": agent_gate.handle_dev, "readonly": agent_gate.handle_readonly,
            "docs": agent_gate.handle_docs}
HARNESS_FILES = (
    ".claude/tools/pipeline/pretool_gate.py", ".claude/tools/pipeline/agent_gate.py",
    ".claude/tools/memory/memory.py", ".claude/tools/__pycache__/x.pyc",
    ".claude/settings.json", ".claude/settings.local.json",
    ".claude/agents/reviewer.md", ".mcp.json",
)


def make_project(case: unittest.TestCase) -> Path:
    """A throwaway project root, installed as state.ROOT for the length of `case`."""
    proj = tmproot.sandbox(case, "enf-") / "proj"
    for rel in (".agentry/state", ".agentry/tasks/backlog", ".claude/tools/pipeline",
                ".claude/tools/memory", ".claude/agents", ".claude/rules", "docs",
                "src/.claude/tools", "src/.claude/agents", "app"):
        (proj / rel).mkdir(parents=True)
    for rel, text in ((".agentry/pipeline.json", "{}"), (".agentry/state/approvals", "manual"),
                      (".agentry/state/trunk_push", ""), (".claude/settings.json", "{}"),
                      (".claude/tools/memory/memory.py", "pass\n"),
                      (".claude/tools/pipeline/state.py", "pass\n"),
                      (".claude/tools/pipeline/approvals.py", "pass\n"),
                      (".claude/tools/pipeline/mode.py", "pass\n"),
                      (".claude/tools/pipeline/pretool_gate.py", "pass\n"),
                      (".claude/agents/reviewer.md", "x\n"), (".mcp.json", "{}"),
                      ("docs/forge.py", "print(1)\n"), ("docs/memory.py", "print(1)\n"),
                      ("docs/git", "#!/bin/sh\n"), ("docs/a.md", "a\n"),
                      ("app/main.py", "pass\n")):
        (proj / rel).write_text(text, encoding="utf-8")
    for name, value in (("ROOT", proj), ("STATE_DIR", proj / ".agentry" / "state"),
                        ("PIPELINE_PATH", proj / ".agentry" / "pipeline.json")):
        patcher = unittest.mock.patch.object(state, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    return proj


def call(handler, tool: str, ti: dict, cwd: str = "", allow: tuple = ()) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = handler(tool, ti, cwd=cwd, allow_scripts=allow)
    return code, err.getvalue()


def bash(profile: str, command: str, cwd: str = "", allow: tuple = ()) -> tuple[int, str]:
    return call(PROFILES[profile], "Bash", {"command": command}, cwd, allow)


def write(profile: str, path: str, cwd: str = "", tool: str = "Write") -> tuple[int, str]:
    field = "content" if tool == "Write" else "new_string"
    return call(PROFILES[profile], tool, {"file_path": path, field: "x"}, cwd)


def make_junction(case: unittest.TestCase, link: Path, target: Path) -> None:
    if os.name != "nt":
        case.skipTest("directory junctions are a Windows feature")
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        case.skipTest(f"cannot create a junction here: {done.stdout} {done.stderr}")
    case.addCleanup(os.rmdir, link)  # registered after the sandbox, so it runs first


def short_path(path: Path) -> str:
    """The 8.3 spelling of an existing path, '' when the volume makes none."""
    if os.name != "nt":
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return buf.value if size and buf.value.lower() != str(path).lower() else ""


class ProtectedPathResolutionTest(unittest.TestCase):
    """is_protected_state() answers for where a path RESOLVES, not how it is spelled."""

    def setUp(self):
        self.proj = make_project(self)
        self.cwd = str(self.proj)

    def assertProtected(self, path: str, cwd: str = ""):
        self.assertTrue(agent_gate.is_protected_state(path, cwd or self.cwd), path)

    def test_an_ntfs_stream_suffix_names_the_same_file(self):
        for path in (".agentry/pipeline.json::$DATA", ".agentry/pipeline.json:hidden",
                     ".agentry/pipeline.json::$DATA.", ".agentry/state::$INDEX_ALLOCATION/approvals",
                     ".agentry/state/approvals::$DATA",
                     str(self.proj / ".agentry" / "pipeline.json") + "::$DATA"):
            with self.subTest(path=path):
                self.assertProtected(path)

    def test_case_dotdot_and_trailing_dots_do_not_hide_the_file(self):
        for path in (".AGENTRY/State/../state/Approvals", "docs/../.agentry/pipeline.json",
                     ".agentry/pipeline.json.", ".agentry\\state\\approvals"):
            with self.subTest(path=path):
                self.assertProtected(path)

    def test_a_relative_path_resolves_against_the_payload_cwd(self):
        self.assertProtected("pipeline.json", str(self.proj / ".agentry"))
        self.assertProtected("approvals", str(self.proj / ".agentry" / "state"))
        self.assertProtected("../pipeline.json", str(self.proj / ".agentry" / "state"))
        self.assertFalse(agent_gate.is_protected_state("pipeline.json", str(self.proj / "docs")))

    def test_a_junction_onto_the_tree_does_not_hide_it(self):
        make_junction(self, self.proj / "docs" / "lnk", self.proj / ".agentry")
        for path in ("docs/lnk/pipeline.json", "docs/lnk/state/approvals", "docs/lnk/state/new"):
            with self.subTest(path=path):
                self.assertProtected(path)
        code, _ = write("docs", "docs/lnk/state/approvals", self.cwd)
        self.assertEqual(2, code)

    def test_an_8_3_short_name_does_not_hide_the_tree(self):
        short = short_path(self.proj / ".agentry")
        if not short:
            self.skipTest("this volume generates no 8.3 short names")
        for tail in ("pipeline.json", "state/approvals", "state/new-file"):
            with self.subTest(tail=tail):
                self.assertProtected(f"{short}\\{tail.replace('/', os.sep)}")

    def test_a_posix_style_drive_path_names_the_same_file(self):
        # Git Bash spells E:/x as /e/x, which native Windows reads as a path on the
        # current drive; the gate has to read it the way the shell will.
        if os.name != "nt":
            self.skipTest("drive letters are a Windows feature")
        msys = f"/{self.proj.drive[0].lower()}{self.proj.as_posix()[2:]}"
        for tail in (".agentry/pipeline.json", ".agentry/state/approvals", ".agentry/state/new"):
            with self.subTest(tail=tail):
                self.assertProtected(f"{msys}/{tail}")
        self.assertTrue(pretool_gate.is_live_harness(
            f"{msys}/.claude/tools/pipeline/agent_gate.py", self.cwd))
        for command in (f"cd {msys}/.agentry && sed -i s/a/b/ pipeline.json",
                        f"echo x > {msys}/.agentry/state/approvals",
                        f"cp docs/a.md {msys}/.claude/tools/pipeline/agent_gate.py"):
            with self.subTest(command=command):
                self.assertTrue(agent_gate.writes_protected_state(command, cwd=self.cwd), command)

    def test_a_hard_link_to_pipeline_json_is_the_same_file(self):
        alias = self.proj / "docs" / "alias.json"
        os.link(self.proj / ".agentry" / "pipeline.json", alias)
        self.assertProtected("docs/alias.json")
        code, _ = write("docs", "docs/alias.json", self.cwd)
        self.assertEqual(2, code)

    def test_siblings_and_lookalikes_stay_open(self):
        for path in (".agentry/pipeline.json.bak", ".agentry/pipeline.jsonl", "docs/pipeline.json",
                     ".agentry/plans/pipeline.json", ".agentry/tasks/backlog/task-0001.md"):
            with self.subTest(path=path):
                self.assertFalse(agent_gate.is_protected_state(path, self.cwd), path)

    def test_the_file_tools_are_denied_for_every_profile_in_every_spelling(self):
        for profile in PROFILES:
            for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                for path in (".agentry/pipeline.json::$DATA", ".AGENTRY/state/approvals",
                             "docs/../.agentry/state/trunk_push"):
                    with self.subTest(profile=profile, tool=tool, path=path):
                        if tool == "NotebookEdit":
                            code, err = call(PROFILES[profile], tool,
                                             {"notebook_path": path, "new_source": "x"}, self.cwd)
                        elif tool == "MultiEdit":
                            code, err = call(PROFILES[profile], tool,
                                             {"file_path": path, "edits": [{"new_string": "x"}]},
                                             self.cwd)
                        else:
                            code, err = write(profile, path, self.cwd, tool)
                        self.assertEqual(2, code, err)

    def test_bash_writes_are_judged_after_a_cd_and_for_a_relative_payload_cwd(self):
        commands = (
            ("cd .agentry && sed -i s/false/true/ pipeline.json", ""),
            ("cd .agentry/state; echo auto > approvals", ""),
            ("cd docs && cp forge.py ../.agentry/pipeline.json", ""),
            ("pushd .agentry >x; tee pipeline.json", ""),
            ("sed -i s/false/true/ pipeline.json", str(self.proj / ".agentry")),
            ("echo auto > approvals", str(self.proj / ".agentry" / "state")),
            ("rm .agentry/pipeline.js*", ""),
            ("rm .agentry/sta*/appr*", ""),
            ("echo x > .agentry/state/trunk_push::$DATA", ""),
        )
        for command, cwd in commands:
            written = agent_gate.writes_protected_state(command, cwd=cwd or self.cwd)
            with self.subTest(command=command):
                self.assertTrue(written, command)
            for profile in PROFILES:
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, cwd or self.cwd)
                    self.assertEqual(2, code, err)

    def test_reads_and_writes_elsewhere_after_a_cd_stay_open_for_dev(self):
        for command in ("cd docs && cat a.md", "cd .agentry && cat pipeline.json",
                        "cd app && echo x > out.txt", "cd docs; sed -i s/a/b/ a.md"):
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.writes_protected_state(command, cwd=str(self.proj)))


class LiveHarnessTest(unittest.TestCase):
    """The gate code, hook wiring and agent definitions of the project ROOT are not
    writable by dev or docs agents (option 5); a checkout under src/ is (D4)."""

    def setUp(self):
        self.proj = make_project(self)
        self.cwd = str(self.proj)

    def test_the_live_files_are_recognised_in_every_spelling(self):
        for rel in HARNESS_FILES:
            for path in (rel, str(self.proj / rel), rel.upper(), rel + "::$DATA",
                         "docs/../" + rel, rel.replace("/", "\\")):
                with self.subTest(path=path):
                    self.assertTrue(pretool_gate.is_live_harness(path, self.cwd), path)

    def test_a_relative_path_resolves_against_the_payload_cwd(self):
        self.assertTrue(pretool_gate.is_live_harness("pipeline/agent_gate.py",
                                                     str(self.proj / ".claude" / "tools")))
        self.assertFalse(pretool_gate.is_live_harness("pipeline/agent_gate.py",
                                                      str(self.proj / "src" / ".claude" / "tools")))

    def test_a_junction_onto_the_harness_does_not_hide_it(self):
        make_junction(self, self.proj / "docs" / "h", self.proj / ".claude")
        self.assertTrue(pretool_gate.is_live_harness("docs/h/tools/pipeline/pretool_gate.py", self.cwd))
        self.assertTrue(pretool_gate.is_live_harness("docs/h/settings.json", self.cwd))

    def test_the_src_checkout_and_the_rest_of_claude_are_not_the_live_harness(self):
        for path in ("src/.claude/tools/pipeline/pretool_gate.py", "src/.claude/agents/reviewer.md",
                     "src/.claude/settings.json", "src/.claude/rules/testing.md",
                     "src/.claude/skills/new-task/SKILL.md", ".claude/CLAUDE.md",
                     ".claude/agents/sub/x.md", ".claude/agents/reviewer.txt",
                     ".claude/tools-notes.md", "docs/.claude/tools/x.py", "app/.mcp.json",
                     ".agentry/tasks/backlog/task-0001.md"):
            with self.subTest(path=path):
                self.assertFalse(pretool_gate.is_live_harness(path, self.cwd), path)

    def test_dev_and_docs_are_denied_write_and_edit_on_the_live_files(self):
        for profile in ("dev", "docs"):
            for rel in HARNESS_FILES:
                for tool in ("Write", "Edit"):
                    with self.subTest(profile=profile, tool=tool, path=rel):
                        code, err = write(profile, rel, self.cwd, tool)
                        self.assertEqual(2, code, err)
                        self.assertIn("live harness", err)

    def test_dev_and_docs_may_still_write_the_src_checkout(self):
        for profile in ("dev", "docs"):
            for rel in ("src/.claude/tools/pipeline/pretool_gate.py", "src/.claude/agents/reviewer.md",
                        "src/.claude/settings.json"):
                with self.subTest(profile=profile, path=rel):
                    code, err = write(profile, rel, self.cwd)
                    self.assertEqual(0, code, err)

    def test_docs_may_still_write_rules_skills_and_work_product(self):
        # the checkout's trees (src/.claude); the live workspace's own are LiveInstructionsTest
        for rel in ("src/.claude/rules/testing.md", "src/.claude/skills/new-task/SKILL.md",
                    ".agentry/tasks/backlog/task-0001.md", "docs/a.md", "README.md"):
            with self.subTest(path=rel):
                code, err = write("docs", rel, self.cwd)
                self.assertEqual(0, code, err)

    def test_the_settings_level_gate_denies_any_subagent_but_not_the_main_thread(self):
        for rel in HARNESS_FILES:
            with self.subTest(path=rel):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(2, pretool_gate.handle_edit(rel, "x", orch=False, cwd=self.cwd))
                    self.assertEqual(0, pretool_gate.handle_edit(rel, "x", orch=True, cwd=self.cwd))

    def test_explicit_path_bash_writes_are_denied_for_every_profile(self):
        commands = (
            "cp docs/forge.py .claude/tools/pipeline/agent_gate.py",
            "sed -i s/a/b/ .claude/settings.json",
            "echo x > .claude/agents/reviewer.md",
            "tee .claude/tools/pipeline/pretool_gate.py",
            "rm .claude/tools/pipeline/pretool_gate.py",
            "mv docs/forge.py .claude/tools/pipeline/pretool_gate.py",
            "cd .claude/tools/pipeline && sed -i s/a/b/ agent_gate.py",
            "cp docs/forge.py .claude/tools/pipe*/agent_gate.py",
            "echo x > .mcp.json",
        )
        for command in commands:
            for profile in PROFILES:
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)

    def test_reading_the_live_files_and_writing_src_from_bash_stays_open_for_dev(self):
        for command in ("cat .claude/tools/pipeline/pretool_gate.py",
                        "cp .claude/tools/pipeline/state.py src/.claude/tools/state.py",
                        "grep -n deny .claude/settings.json",
                        "echo x > src/.claude/agents/new.md"):
            with self.subTest(command=command):
                self.assertEqual("", agent_gate.writes_protected_state(command, cwd=self.cwd))


class DefaultDenyBashTest(unittest.TestCase):
    """The readonly and docs profiles run read-only utilities, git, codegraph and the
    harness scripts, nothing else. These are the spellings the task named, plus the
    programs the old denylist forgot."""

    # Each of these can write .agentry/pipeline.json or .agentry/state/* and none is a
    # shell redirect, `sed -i`, `cp` or any other command the lexical checks know.
    FORGERIES = (
        "python -c \"import approvals; approvals.write('auto')\"",
        "python3 -c \"open('.agentry/state/trunk_push','w').write('{}')\"",
        "py -3 -c \"import json\"",
        "python -Ic \"print(1)\"",
        "python - <<'EOF'\nopen('.agentry/pipeline.json','w').write('{}')\nEOF",
        "python <<EOF\nprint(1)\nEOF",
        "python - < docs/forge.py",
        "cat docs/forge.py | python",
        "python docs/forge.py",
        "python ./docs/forge.py --flag",
        "python docs/memory.py --query x",
        "cd docs && python -m forge",
        "cd docs && python -m memory",
        "python -m json.tool .agentry/pipeline.json",
        "python",
        "xargs python docs/forge.py",
        "env python docs/forge.py",
        "PYTHONPATH=docs python .claude/tools/memory/memory.py --query x",
        "python .claude/tools/pipeline/pretool_gate.py",
        "python .claude/tools/hooks/dangerous_patterns.py",
        "node -e \"require('fs').writeFileSync('.agentry/state/approvals','auto')\"",
        "awk 'BEGIN{print \"auto\" > \".agentry/state/approvals\"}'",
        "perl -i -pe s/false/true/ .agentry/pipeline.json",
        "dd of=.agentry/state/trunk_push if=docs/a.md",
        "curl -o .agentry/state/trunk_push http://localhost/x",
        "sed -n 'w .agentry/state/trunk_push' docs/a.md",
        "bash docs/forge.sh",
        "sh -c 'echo auto'",
        "bash -c 'cat docs/a.md'",
        "eval cat docs/a.md",
        "source docs/forge.sh",
        ". docs/forge.sh",
        "./docs/git log",
        "docs/git log",
        "echo $(python -c 1)",
        "echo `python docs/forge.py`",
        "x=$(awk 1 docs/a.md); echo $x",
        "ls | xargs rm",
        "cat docs/a.md | tee out.txt",
        "npm run build",
        "make",
        "powershell -c 1",
        "cmd /c del docs\\a.md",
        "nohup python docs/forge.py &",
        "timeout 5 python docs/forge.py",
        "sudo -u root git log",
        "CLAUDE_PROJECT_DIR=docs python \"$CLAUDE_PROJECT_DIR/.claude/tools/memory/memory.py\" --query x",
    )
    ALLOWED = (
        "ls -la docs", "cat docs/a.md", "head -n 5 docs/a.md | wc -l", "grep -rn forge docs",
        "git log --oneline -5", "git diff HEAD -- docs", "git status --porcelain",
        "git show HEAD:docs/a.md", "git branch --list", "git rev-parse HEAD",
        "wc -l docs/a.md | sort | uniq -c", "find docs -name '*.md'", "find . -maxdepth 1 -type d",
        "echo hello", "printf '%s\\n' hi", "cd docs && ls", "pwd", "date", "which python",
        "diff docs/a.md docs/forge.py", "codegraph explore \"where is the gate\"",
        "python .claude/tools/memory/memory.py --query lesson",
        "python .claude/tools/pipeline/state.py --show",
        "python .claude/tools/pipeline/approvals.py --show",
        "python3 .claude/tools/pipeline/mode.py",
        "py -3 .claude/tools/pipeline/state.py --show",
        "python \"$CLAUDE_PROJECT_DIR/.claude/tools/pipeline/state.py\" --show",
        "cd \"$CLAUDE_PROJECT_DIR\" && python .claude/tools/pipeline/state.py --show",
        "cd .claude/tools/pipeline && python state.py --show",
        "cd .claude/tools/pipeline && python -m state --show",
        "python -B -X utf8 .claude/tools/memory/memory.py --stats",
        "x=$(git rev-parse HEAD); echo $x", "level=$(python .claude/tools/pipeline/approvals.py --show)",
        "for f in docs/*.md; do wc -l $f; done", "[ -f docs/a.md ] && cat docs/a.md",
        "jq . .agentry/pipeline.json", "git log --oneline | head -3", "cat docs/a.md 2>&1",
        "echo a; echo b", "if [ -f docs/a.md ]; then cat docs/a.md; fi",
        "xargs -n1 echo", "ls | wc -l",
    )

    def setUp(self):
        self.proj = make_project(self)
        self.cwd = str(self.proj)

    def test_every_forgery_spelling_is_refused_for_readonly_and_docs(self):
        for profile in ("readonly", "docs"):
            for command in self.FORGERIES:
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)

    def test_the_refusal_names_the_allowlist_and_how_to_extend_it(self):
        code, err = bash("readonly", "python -c 1", self.cwd)
        self.assertEqual(2, code)
        self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)
        self.assertIn("python -c", err)
        self.assertIn("--allow-script", err)

    def test_the_read_only_toolset_is_allowed_for_readonly_and_docs(self):
        for profile in ("readonly", "docs"):
            for command in self.ALLOWED:
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(0, code, err)

    def test_the_allowlist_is_per_profile_and_names_only_plain_programs(self):
        self.assertEqual({"readonly", "docs"}, set(agent_gate.ALLOWED_PROGRAMS))
        for profile, programs in agent_gate.ALLOWED_PROGRAMS.items():
            for forbidden in ("python", "node", "bash", "sh", "awk", "sed", "perl", "curl", "dd",
                              "rm", "cp", "mv", "tee", "npx", "npm", "make", "eval", "source"):
                with self.subTest(profile=profile, program=forbidden):
                    self.assertNotIn(forbidden, programs)
        self.assertEqual({"memory.py", "state.py", "handoff.py", "events.py", "approvals.py",
                          "mode.py", "swarm.py", "repo_scan.py"}, set(agent_gate.ALLOWED_SCRIPTS))

    def test_a_program_with_a_write_or_exec_mode_is_allowed_only_without_it(self):
        for command in ("find . -delete", "find docs -exec rm {} ;", "find . -fprint docs/out",
                        "find . -ok rm {} ;", "sort -o docs/out docs/a.md", "sort -ro docs/out docs/a.md",
                        "sort --output=docs/out docs/a.md", "uniq docs/a.md docs/out",
                        "rg --pre ./docs/git x docs", "rg --pre=cmd x"):
            for profile in ("readonly", "docs"):
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)

    def test_text_the_tokeniser_cannot_read_is_refused_with_its_own_reason(self):
        for profile in ("dev", "readonly", "docs"):
            for command in ("echo it's", "cat \"docs/a.md", "python - <<'EOF'\nit's bash\nEOF"):
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(2, code)
                    self.assertIn("cannot be tokenised", err)
                    self.assertNotIn("orchestrator-only", err)

    def test_a_program_named_on_the_hook_line_is_allowed_for_that_agent_only(self):
        for command in ("k6 run load.js", "k6.exe run load.js", "npx lighthouse http://localhost"):
            with self.subTest(command=command):
                self.assertEqual(2, bash("readonly", command, self.cwd)[0])
                self.assertEqual(0, bash("readonly", command, self.cwd, allow=("k6", "npx"))[0])
        code, err = bash("readonly", "curl http://localhost", self.cwd, allow=("k6", "npx"))
        self.assertEqual(2, code, err)
        self.assertEqual(0, bash("docs", "k6 run load.js", self.cwd, allow=("k6",))[0])

    def test_a_named_extra_does_not_unlock_the_state_or_setter_checks(self):
        for command in ("k6 run x.js > .agentry/state/approvals",
                        "python .claude/tools/pipeline/approvals.py auto",
                        "k6 run x.js; python .claude/tools/pipeline/approve.py --task t --gate commit"):
            with self.subTest(command=command):
                self.assertEqual(2, bash("readonly", command, self.cwd, allow=("k6",))[0])

    def test_the_allowed_scripts_must_live_under_the_live_tools_directory(self):
        planted = self.proj / "docs" / ".claude" / "tools" / "memory"
        planted.mkdir(parents=True)
        (planted / "memory.py").write_text("print(1)\n", encoding="utf-8")
        for command in ("python docs/.claude/tools/memory/memory.py --query x",
                        "cd docs/.claude/tools/memory && python memory.py --query x",
                        "cd docs/.claude/tools/memory && python -m memory --query x"):
            with self.subTest(command=command):
                self.assertEqual(2, bash("docs", command, self.cwd)[0])

    def test_hook_lines_with_repeated_allow_script_flags_parse(self):
        parser_args = ["--profile", "readonly", "--allow-script", "k6", "--allow-script", "npx"]
        with unittest.mock.patch.object(sys, "argv", ["agent_gate", *parser_args]), \
                unittest.mock.patch.object(pretool_gate, "read_payload", return_value={
                    "tool_name": "Bash", "tool_input": {"command": "k6 run x.js"},
                    "cwd": self.cwd}):
            self.assertEqual(0, agent_gate.evaluate())
        with unittest.mock.patch.object(sys, "argv", ["agent_gate", "--profile", "readonly"]), \
                unittest.mock.patch.object(pretool_gate, "read_payload", return_value={
                    "tool_name": "Bash", "tool_input": {"command": "k6 run x.js"},
                    "cwd": self.cwd}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, agent_gate.evaluate())


class TotalityTest(unittest.TestCase):
    """Every main() here treats an exception as an allow, so a parser that raises on a
    crafted string switches the gate off. The new path and allowlist code must be total
    over arbitrary text; the readonly/docs allowlist additionally fails closed."""

    TEXTS = (
        "", " ", "\x00", "ls \x00", "ls *\x00*", "python \x00.py", "`", "$(", "$((", "<<", "<<EOF",
        "'", "\"", "\\", "|", "&&", ";;", "(", "{", "((", "cd", "cd ", "cd -", "cd ~", "cd \x00",
        "python", "python -", "python -m", "python -c", "python -mx", "python -X", "FOO=", "= =",
        "x" * 5000, "ls $(" * 50, "echo $(echo $(echo $(echo $(ls))))", "cat <<EOF\n$(ls\nEOF",
        "..", "a/../../..", "C:", "::", ":::$DATA", "/e", "/", "//", "\n\n", "ls ${", "ls $(()",
        "echo `echo `echo ` `", "for", "for i in", "[[", "]]", "!", "! ! !", "ls >", "ls > >",
    )

    def setUp(self):
        self.proj = make_project(self)

    def test_no_handler_raises_on_odd_text(self):
        for text in self.TEXTS:
            for profile, handler in PROFILES.items():
                with self.subTest(profile=profile, text=text[:40]):
                    code, _ = call(handler, "Bash", {"command": text}, str(self.proj))
                    self.assertIn(code, (0, 2))
                for tool in ("Write", "Edit"):
                    with self.subTest(profile=profile, tool=tool, path=text[:40]):
                        code, _ = call(handler, tool, {"file_path": text, "content": "x"},
                                       str(self.proj))
                        self.assertIn(code, (0, 2))

    def test_the_allowlist_fails_closed_when_it_raises(self):
        with unittest.mock.patch.object(agent_gate, "program_denied", side_effect=KeyError("x")):
            for profile in ("readonly", "docs"):
                code, err = bash(profile, "ls", str(self.proj))
                self.assertEqual(2, code)
                self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)
        with unittest.mock.patch.object(agent_gate, "program_denied", side_effect=KeyError("x")):
            self.assertEqual(0, bash("dev", "ls", str(self.proj))[0])


class RealHookTest(unittest.TestCase):
    """The same refusals through the process Claude Code actually starts."""

    @staticmethod
    def run_hook(profile: str, payload: dict, *flags: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PIPELINE_DIR / "agent_gate.py"), "--profile", profile, *flags],
            input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=60)

    def bash(self, profile: str, command: str, *flags: str):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(state.ROOT)}
        return self.run_hook(profile, payload, *flags)

    def test_a_python_one_liner_and_a_planted_script_cannot_reach_the_approval_records(self):
        record = (state.ROOT / ".agentry" / "state" / "trunk_push").as_posix()
        for profile in ("readonly", "docs"):
            for command in (f"python -c \"open('{record}','w').write('{{}}')\"",
                            f"python - <<'EOF'\nopen('{record}','w').write('x')\nEOF",
                            "python docs/forge.py", "python -m forge", "node -e 1"):
                with self.subTest(profile=profile, command=command):
                    proc = self.bash(profile, command)
                    self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_the_file_tools_cannot_write_the_approval_records_in_any_spelling(self):
        for profile in ("dev", "readonly", "docs"):
            for rel in (".agentry/state/trunk_push", ".agentry/state/approvals::$DATA",
                        ".agentry/pipeline.json", ".AGENTRY/State/../State/run.db"):
                with self.subTest(profile=profile, path=rel):
                    payload = {"tool_name": "Write", "tool_input": {"file_path": rel, "content": "x"},
                               "cwd": str(state.ROOT)}
                    self.assertEqual(2, self.run_hook(profile, payload).returncode)

    def test_the_live_harness_is_refused_to_dev_and_docs_through_the_hook(self):
        for profile in ("dev", "docs"):
            for rel in (".claude/tools/pipeline/pretool_gate.py", ".claude/settings.json"):
                with self.subTest(profile=profile, path=rel):
                    payload = {"tool_name": "Edit", "cwd": str(state.ROOT),
                               "tool_input": {"file_path": rel, "new_string": "x"}}
                    proc = self.run_hook(profile, payload)
                    self.assertEqual(2, proc.returncode)
                    self.assertIn("live harness", proc.stderr.decode("utf-8", "replace"))

    def test_the_flag_on_the_hook_line_is_what_unlocks_a_named_tool(self):
        self.assertEqual(2, self.bash("readonly", "k6 version").returncode)
        self.assertEqual(0, self.bash("readonly", "k6 version", "--allow-script", "k6").returncode)
        self.assertEqual(0, self.bash("readonly", "ls").returncode)


class DevInterpreterResidualTest(unittest.TestCase):
    """NOT COVERED (User decision D1 a, 2026-10-09). A dev agent's job is to run code, so
    the dev profile cannot be default-deny: an interpreter one-liner, a script the agent
    wrote, `python -m` and `node -e` all pass the gate with exit 0 and can write
    `.agentry/pipeline.json` or `.agentry/state/*`. The residual is bounded only
    off-box (server-side branch protection, which the User declined, D2), so a push by a
    dev agent is stopped by the commit/push approval gates and the never-granted list,
    not by this profile. These tests PIN that exit 0: if one turns red, the gap was
    closed or a regression made the gate stricter - update rules/orchestration.md
    "Subagent enforcement" in the same change, or the document lies."""

    SPELLINGS = (
        "python -c \"open('.agentry/pipeline.json','w').write('{}')\"",
        "python3 -c \"import approvals; approvals.write('auto')\"",
        "python - <<'EOF'\nopen('.agentry/state/trunk_push','w').write('{}')\nEOF",
        "python docs/forge.py",
        "cd docs && python -m forge",
        "node -e \"require('fs').writeFileSync('.agentry/state/approvals','auto')\"",
        "awk 'BEGIN{print \"auto\" > \".agentry/state/approvals\"}'",
        "perl -i -pe s/false/true/ .agentry/pipeline.json",
        "dd of=.agentry/state/trunk_push if=docs/a.md",
    )

    def setUp(self):
        self.proj = make_project(self)

    def test_dev_interpreter_spellings_pass_the_gate_not_covered(self):
        for command in self.SPELLINGS:
            with self.subTest(command=command):
                code, err = bash("dev", command, str(self.proj))
                self.assertEqual(0, code, f"NOT COVERED spelling is now refused: {err}")

    def test_dev_still_cannot_use_the_lexically_visible_spellings(self):
        for command in ("echo auto > .agentry/state/approvals",
                        "sed -i s/false/true/ .agentry/pipeline.json",
                        "cp docs/a.md .agentry/state/trunk_push",
                        "tee .agentry/state/approvals"):
            with self.subTest(command=command):
                self.assertEqual(2, bash("dev", command, str(self.proj))[0])


class OrchestratorGateHeredocTest(unittest.TestCase):
    """orch_check_bash scanned the raw command text, so a task file written through a
    heredoc whose body DESCRIBES a `sed -i` edit was denied as that edit (2026-10-09)."""

    def run_orch(self, command: str) -> int:
        err = io.StringIO()
        with unittest.mock.patch.object(pretool_gate, "orch_enabled", return_value=True), \
                contextlib.redirect_stderr(err):
            return pretool_gate.orch_check_bash(command)

    def test_a_heredoc_body_that_names_a_forbidden_edit_is_data(self):
        task = ".agentry/tasks/backlog/task-0116.md"
        for body in ("Run sed -i s/a/b/ on the file.", "Use tee out.txt then git apply x.patch.",
                     "patch -p1 < fix.diff", "the gate said: don't use sed -i here"):
            for opener in (f"cat >> {task} <<'EOF'", f"cat >> {task} <<EOF", f"cat > {task} <<-EOF"):
                with self.subTest(body=body, opener=opener):
                    self.assertEqual(0, self.run_orch(f"{opener}\n{body}\nEOF"))

    def test_the_same_words_outside_a_heredoc_are_still_denied(self):
        for command in ("sed -i s/a/b/ app/main.py", "tee app/main.py", "git apply x.patch",
                        "cat <<'EOF' > app/main.py\nx\nEOF", "echo hi\nsed -i s/a/b/ app/main.py"):
            with self.subTest(command=command):
                self.assertEqual(2, self.run_orch(command))

    def test_a_shell_consuming_the_heredoc_keeps_the_body_in_scope(self):
        self.assertEqual(2, self.run_orch("bash <<EOF\nsed -i s/a/b/ app/main.py\nEOF"))
        self.assertEqual(2, self.run_orch("bash <<'EOF'\nit's here\nsed -i s/a/b/ app/main.py\nEOF"))
        self.assertEqual(2, self.run_orch("'s'h <<'EOF'\nit's here\nsed -i s/a/b/ app/main.py\nEOF"))

    def test_a_shell_hidden_by_quotes_in_unbalanced_text_is_still_a_shell(self):
        for command in ("'s'h <<'EOF'\nit's\nEOF", "ba\"sh\" <<'EOF'\nit's\nEOF",
                        "C:\\Git\\bin\\bash.exe <<'EOF'\nit's\nEOF", "env zsh <<'EOF'\nit's\nEOF"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.runs_a_shell(command))
                self.assertEqual(command, pretool_gate.strip_heredocs(command))
        for command in ("python - <<'PY'\n# it's a test\nprint(1)\nPY", "cat <<'EOF'\nit's fine\nEOF"):
            with self.subTest(command=command):
                self.assertFalse(pretool_gate.runs_a_shell(command))

    def test_a_shell_append_to_a_bookkeeping_file_is_allowed(self):
        # 2026-10-09: a printf append to a task file under .agentry/tasks/backlog/ was
        # reported denied. It is allowed (the redirect target is on the allowlist);
        # the heredoc case above was the real false positive.
        root = Path(state.ROOT).as_posix()
        for command in (f"printf 'a\\n- sed -i x\\n' >> {root}/.agentry/tasks/backlog/task-0116.md",
                        "printf '%s' note >> .agentry/tasks/backlog/task-0116.md",
                        "echo x >> .agentry/tasks/swarm/task-0120/board.md"):
            with self.subTest(command=command):
                self.assertEqual(0, self.run_orch(command))


class UnparseableMessageTest(unittest.TestCase):
    """task-0118 QA: a heredoc body with an unbalanced apostrophe made the dev gate say
    "approve.py is orchestrator-only". It is data now, and a command that really cannot
    be tokenised says so."""

    def test_the_quoted_heredoc_with_an_apostrophe_is_no_longer_read_as_approve_py(self):
        command = "python - <<'PY'\n# it's a test\nprint(1)\nPY"
        self.assertIsNotNone(pretool_gate.executed_names(command))
        self.assertFalse(pretool_gate.runs_approve_script(command))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, agent_gate.handle_dev("Bash", {"command": command}, cwd="."))

    def test_a_real_parse_failure_names_the_parse_not_a_script(self):
        for handler in (agent_gate.handle_dev, agent_gate.handle_readonly, agent_gate.handle_docs):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = handler("Bash", {"command": "echo it's"}, cwd=".")
            self.assertEqual(2, code)
            self.assertIn("cannot be tokenised", err.getvalue())
            self.assertNotIn("approve.py", err.getvalue())

    def test_approve_py_in_a_heredoc_with_an_apostrophe_is_still_refused(self):
        command = "python .claude/tools/pipeline/approve.py --task t --gate commit <<'EOF'\nit's\nEOF"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(2, agent_gate.handle_dev("Bash", {"command": command}, cwd="."))
        self.assertIn("orchestrator-only", err.getvalue())


if __name__ == "__main__":
    unittest.main()
