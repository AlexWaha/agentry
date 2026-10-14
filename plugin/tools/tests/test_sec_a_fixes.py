"""SEC-A of task-0120: the subagent gate's allowlist is closed, the gate fails closed, .git
and the live instruction trees are out of reach, and the zone map survives a broken run.db.

  * H1  readonly and docs: every program on READ_ONLY_PROGRAMS runs with an option allowlist
        (FLAG_SPECS), not a denylist. `sort --compress-program=PROG` and `RIPGREP_CONFIG_PATH=f
        rg` ran code on the old gate; so did `rg --search-zip`, `date -s`, `file -C`.
  * H2  the allowlist is the FIRST check, and a crash in any later check refuses the tool for
        readonly and docs (evaluate() used to turn every exception into an allow).
  * M3  a subagent cannot write inside any .git directory (a hook there runs as the
        orchestrator at its next commit); a docs agent cannot write the live workspace's
        .claude/rules, skills or commands.
  * M5  a locked or corrupt run.db no longer reads as "the swarm is finished": zones.task_stage
        returns STAGE_UNREADABLE and the zone gate denies on it.
  * zones.py --release: removes the frozen map of ONE task, orchestrator only.

Every gate test runs on a sandbox project (state.ROOT patched), so nothing touches the real tree.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = TESTS_DIR.parents[0] / "pipeline"
for _dir in (PIPELINE_DIR, TESTS_DIR):
    sys.path.insert(0, str(_dir))

import agent_gate
import pretool_gate
import state
import test_subagent_enforcement as enforcement
import zones
from test_subagent_enforcement import bash, call, make_project, write

READ_PROFILES = ("readonly", "docs")
PIPE = "plugin/tools/pipeline"


class FlagAllowlistTest(unittest.TestCase):
    """H1: an option the allowlist does not name is refused, whatever the program."""

    # Each one ran a program or wrote a file, or can be made to, on the denylist gate.
    REFUSED = (
        # the two reported repros
        "sort --compress-program=docs/x docs/a.md", "sort --compress-program docs/x docs/a.md",
        "RIPGREP_CONFIG_PATH=docs/c rg x", "env RIPGREP_CONFIG_PATH=docs/c rg x",
        # sort: write and exec options, every abbreviation getopt would accept
        "sort -o docs/out docs/a.md", "sort -ro docs/out docs/a.md", "sort --output=docs/out f",
        "sort --out=docs/out f", "sort --outp docs/out f", "sort -T docs f", "sort --parallel=4 f",
        "sort --files0-from=docs/list", "sort --compress=docs/x f",
        # rg: runs a program per file or a decompressor
        "rg --pre ./x pat", "rg --pre=cmd pat", "rg --pre-glob '*.gz' pat", "rg --search-zip pat",
        "rg -z pat", "rg -uz pat", "rg --hostname-bin=x pat", "rg --generate man", "rg --pr=x pat",
        # find: every action that runs or writes
        "find . -exec rm {} ;", "find . -execdir rm {} ;", "find . -ok rm {} ;",
        "find . -okdir rm {} ;", "find . -delete", "find . -fprint docs/o", "find . -fprint0 docs/o",
        "find . -fprintf docs/o %p", "find . -fls docs/o", "find . -files0-from docs/l",
        # date, file, hostname: the clock, a compiled magic file, the host name
        "date -s '2020-01-01'", "date --set=2020-01-01", "date 010112002020", "file -C -m docs/m",
        "file --compile", "file -z docs/a.md", "file -Z docs/a.md", "file --uncompress docs/a.md",
        "hostname newname",
        # uniq's second operand is its output file
        "uniq docs/a.md docs/out", "uniq -c docs/a.md docs/out", "uniq -- a b",
        # diff pipes through pr; tail and head hang on follow
        "diff -l a b", "diff --paginate a b", "tail -f docs/a.md", "tail -F docs/a.md",
        "tail --follow docs/a.md", "tail --pid=1 docs/a.md",
        # programs deliberately off the list
        "less docs/a.md", "more docs/a.md", "xargs less", "xargs sed -i s/a/b/ f", "awk 1 docs/a.md",
        "sed s/a/b/ docs/a.md", "tar tf docs/x.tar", "tar --to-command=cat -xf x", "tar -I prog -cf x y",
        "ls | xargs sort -o docs/out",
        # an option not named for a listed program
        "cat --foo docs/a.md", "ls --hyperlink", "stat --foo f", "du --files0-from=docs/l",
        "wc --files0-from=docs/l", "grep --exec x f", "jq --foo x", "seq -z 1 2", "id --foo",
        "realpath --foo f", "which --foo python",
        # codegraph: everything but the read commands
        "codegraph init", "codegraph uninit", "codegraph index", "codegraph sync", "codegraph install",
        "codegraph uninstall", "codegraph serve --mcp", "codegraph daemon", "codegraph unlock",
        "codegraph upgrade", "codegraph telemetry off",
        # NAME=value in front of ANY listed program, and a standalone assignment that reaches one
        "FOO=1 ls", "LESS=x cat f", "GIT_PAGER=x git log", "PAGER=x git log", "x=1 git log",
        "PATH=docs/bin ls", "PATH=docs/bin; ls", "path=docs/bin; ls", "temp=docs; ls", "windir=x; ls",
        "IFS=x; ls", "PYTHONPATH=docs python plugin/tools/pipeline/state.py --show",
        "FOO=1 python plugin/tools/pipeline/state.py --show", "env A=b ls",
        # a variable the environment already exports, assigned by a loop or read
        "for PATH in docs; do ls; done", "read PATH < docs/a.md", "printf -v PATH x", "export A=1",
        # an expansion the gate cannot read, in a program with a write or exec option
        "x=-o; sort $x docs/a.md", "x=--output=docs/o; sort $x f", "sort $(echo -o) f",
        "sort `echo -o` f", "sort ${x} f", "sort {-o,f}", "git log $(echo --output=docs/o)",
        "git log $x", "find $x -name y", "uniq $x", "date $x", "rg $x pat", "rg \"$x\" docs",
        "codegraph $x",
    )
    ALLOWED = (
        "ls -la docs", "ls -1 --color=auto docs", "cat -n docs/a.md", "head -n 5 docs/a.md",
        "head -5 docs/a.md",
        "tail -n +3 docs/a.md", "grep -rn --include=*.md forge docs", "grep -rniE 'a|b' docs",
        "grep -A3 -B2 x docs/a.md", "grep -e x -e y docs/a.md", "grep \"$term\" docs",
        "rg -n --hidden -g '*.py' foo", "rg -e 'foo$' docs", "rg -C3 -t py x", "rg -- \"$term\" docs",
        "sort -u docs/a.md", "sort -t, -k2,2n docs/a.md", "sort -rn docs/a.md", "uniq -c docs/a.md",
        "uniq -f 1 docs/a.md", "uniq", "find . -name '*.md' -type f",
        "find docs -maxdepth 2 -newer docs/a.md",
        "find . -newermt 2024-01-01 -print", "wc -l docs/a.md", "jq -r .a docs/a.md",
        "jq --arg k v . docs/a.md", "diff -u docs/a.md docs/forge.py", "diff -r docs app", "cmp -s a b",
        "date +%s", "date -u +%FT%TZ", "date -d yesterday +%F", "stat -c %s docs/a.md", "file -b docs/a.md",
        "du -sh docs", "echo -n hi", "echo --- title ---", "printf '%s\\n' hi", "git log --oneline -5",
        "git diff HEAD -- docs", "git show HEAD:docs/a.md", "git branch --list", "cd docs && ls",
        "for f in docs/*.md; do wc -l $f; done", "x=$(git rev-parse HEAD); echo $x",
        "read -r line < docs/a.md", "codegraph explore \"where is the gate\"", "codegraph callers foo -j",
        "codegraph --version", "seq 1 5", "basename -s .md docs/a.md", "realpath -m docs", "readlink -f docs",
        "uname -a", "id -un", "hostname", "pwd -P", "cut -d: -f1 docs/a.md", "nl -ba docs/a.md",
        "tac docs/a.md",
        "paste -sd, docs/a.md", "which -a python", "type -a ls", "test -f docs/a.md", "[ -d docs ] && echo y",
        "sleep 1", "expr 1 - 1", "xargs -n1 echo", "env", "ls | sort | uniq -c | sort -rn | head -3",
        "level=$(python plugin/tools/pipeline/state.py --show)",
        "find . -mtime -7 -size -10k -perm -644", "diff <(git show HEAD:docs/a.md) docs/a.md",
        "date -I", "du --time docs",
    )

    def setUp(self):
        self.proj = make_project(self)
        self.cwd = str(self.proj)

    def test_every_refused_spelling_is_refused_for_readonly_and_docs(self):
        for profile in READ_PROFILES:
            for command in self.REFUSED:
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)

    def test_the_read_only_commands_agents_use_still_run(self):
        for profile in READ_PROFILES:
            for command in self.ALLOWED:
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, self.cwd)
                    self.assertEqual(0, code, err)

    def test_the_two_reported_repros_name_the_option_or_the_prefix(self):
        code, err = bash("readonly", "sort --compress-program=docs/x docs/a.md", self.cwd)
        self.assertEqual(2, code)
        self.assertIn("sort --compress-program=docs/x", err)
        code, err = bash("docs", "RIPGREP_CONFIG_PATH=docs/c rg x", self.cwd)
        self.assertEqual(2, code)
        self.assertIn("RIPGREP_CONFIG_PATH= prefix", err)

    def test_every_listed_program_has_an_allowlist_and_none_is_left_unchecked(self):
        judged = set(agent_gate.FLAG_SPECS) | agent_gate.ANY_FLAGS
        judged |= {"git", "find", "codegraph", "printf", "for"}  # each has a check of its own
        self.assertEqual(set(), set(agent_gate.READ_ONLY_PROGRAMS) - judged)
        self.assertEqual(set(), (set(agent_gate.FLAG_SPECS) | agent_gate.ANY_FLAGS)
                         - set(agent_gate.READ_ONLY_PROGRAMS))

    def test_a_listed_program_without_an_allowlist_fails_closed(self):
        with unittest.mock.patch.dict(agent_gate.FLAG_SPECS, clear=False):
            del agent_gate.FLAG_SPECS["tac"]
            self.assertIn("no flag allowlist", agent_gate.unsafe_args("tac", ["docs/a.md"]))

    def test_the_programs_with_an_exec_or_pager_mode_are_not_on_the_list(self):
        for name in ("less", "more", "most", "awk", "gawk", "sed", "tar", "xargs", "dd", "tee", "curl",
                     "wget", "python", "node", "perl", "ruby", "sh", "bash", "env", "nice", "timeout"):
            with self.subTest(program=name):
                self.assertNotIn(name, agent_gate.READ_ONLY_PROGRAMS)

    def test_the_exec_and_write_options_are_named_nowhere_in_the_specs(self):
        # The point of an allowlist: these never appear, so they can only be refused.
        forbidden = {"sort": ("--output", "--compress-program", "--temporary-directory", "--files0-from"),
                     "rg": ("--pre", "--pre-glob", "--search-zip", "--hostname-bin", "--generate"),
                     "file": ("--compile", "--uncompress"), "date": ("--set",), "diff": ("--paginate",),
                     "tail": ("--follow", "--pid", "--retry"), "du": ("--files0-from",),
                     "wc": ("--files0-from",)}
        for name, options in forbidden.items():
            spec = agent_gate.FLAG_SPECS[name]
            for option in options:
                with self.subTest(program=name, option=option):
                    self.assertNotIn(option, spec.long)
                    self.assertNotIn(option, spec.long_value)
        self.assertNotIn("o", agent_gate.FLAG_SPECS["sort"].short)
        self.assertNotIn("o", agent_gate.FLAG_SPECS["sort"].short_value)
        self.assertNotIn("z", agent_gate.FLAG_SPECS["rg"].short)
        self.assertNotIn("C", agent_gate.FLAG_SPECS["file"].short)
        self.assertFalse(agent_gate.FIND_ALLOWED & {"-exec", "-execdir", "-ok", "-okdir", "-delete",
                                                    "-fprint", "-fprint0", "-fprintf", "-fls"})

    def test_scan_flags_reads_clusters_values_and_the_end_of_options(self):
        spec = agent_gate.FLAG_SPECS["sort"]
        self.assertEqual(("", ["f"]), agent_gate.scan_flags(spec, ["-rn", "f"]))
        self.assertEqual(("", []), agent_gate.scan_flags(spec, ["-t,", "-k", "2,2n"]))
        self.assertEqual(("", []), agent_gate.scan_flags(spec, ["-k2,2n"]))
        self.assertEqual(("-ro", []), agent_gate.scan_flags(spec, ["-ro"]))
        self.assertEqual(("--out=x", []), agent_gate.scan_flags(spec, ["--out=x"]))
        self.assertEqual(("", ["-o", "x"]), agent_gate.scan_flags(spec, ["--", "-o", "x"]))
        self.assertEqual(("", ["-"]), agent_gate.scan_flags(spec, ["-"]))
        self.assertEqual(("", []), agent_gate.scan_flags(agent_gate.FLAG_SPECS["jq"],
                                                         ["--arg", "-o", "-v", "."][:3]))

    def test_a_dynamic_word_is_allowed_after_the_end_of_options_only_where_that_is_safe(self):
        self.assertEqual("", agent_gate.dynamic_arg(["-e", "--", "$x"], anywhere=False))
        self.assertEqual("$x", agent_gate.dynamic_arg(["--", "$x"], anywhere=True))
        self.assertEqual("$x", agent_gate.dynamic_arg(["-e", "$x"], anywhere=False))
        self.assertEqual("", agent_gate.dynamic_arg(["foo$", "a|b$", "^x$"], anywhere=True))

    def test_the_real_hook_refuses_the_reported_repros(self):
        for profile in READ_PROFILES:
            for command in ("sort --compress-program=docs/x docs/a.md", "RIPGREP_CONFIG_PATH=docs/c rg x"):
                with self.subTest(profile=profile, command=command):
                    payload = {"tool_name": "Bash", "tool_input": {"command": command},
                               "cwd": str(state.ROOT)}
                    proc = enforcement.RealHookTest.run_hook(profile, payload)
                    self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_dev_is_not_default_deny_and_keeps_its_options(self):
        for command in ("sort -o docs/out docs/a.md", "find . -name x -exec echo {} ;", "x=1 ls"):
            with self.subTest(command=command):
                self.assertEqual(0, bash("dev", command, self.cwd)[0])


class FailClosedTest(unittest.TestCase):
    """H2: a crash in any check refuses the tool for readonly and docs; dev keeps failing open."""

    # name on agent_gate or pretool_gate -> where it lives
    PRE_STEPS = (
        (agent_gate, "unparseable"), (agent_gate, "program_hit"), (agent_gate, "runs_approve_script"),
        (agent_gate, "runs_waive"), (agent_gate, "sets_pipeline_state"),
        (pretool_gate, "check_piped_test_suite"), (agent_gate, "writes_protected_state"),
        (agent_gate, "memory_target_deny"), (agent_gate, "bash_mutates"),
        (pretool_gate, "check_nul_redirect"), (agent_gate, "lexical_bash_checks"),
        (agent_gate, "restricted_bash"),
    )

    def setUp(self):
        self.proj = make_project(self)

    def run_evaluate(self, profile: str, payload: dict) -> tuple[int, str]:
        payload = {"cwd": str(self.proj), **payload}
        err = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["agent_gate", "--profile", profile]), \
                unittest.mock.patch.object(pretool_gate, "read_payload", return_value=payload), \
                contextlib.redirect_stderr(err):
            return agent_gate.evaluate(), err.getvalue()

    def bash_payload(self, command: str) -> dict:
        return {"tool_name": "Bash", "tool_input": {"command": command}}

    def test_a_crash_in_each_pre_step_refuses_bash_for_readonly_and_docs(self):
        for module, name in self.PRE_STEPS:
            for profile in READ_PROFILES:
                with self.subTest(step=name, profile=profile), \
                        unittest.mock.patch.object(module, name, side_effect=RuntimeError("boom")):
                    code, err = self.run_evaluate(profile, self.bash_payload("ls docs"))
                    self.assertEqual(2, code, err)
                    self.assertIn("gate failed", err)

    def test_every_pre_step_is_actually_reached_by_an_allowed_command(self):
        # A raise-injection proves nothing if the step never runs: each patched step must be
        # called at least once for a command the allowlist accepts.
        for module, name in self.PRE_STEPS:
            with self.subTest(step=name), unittest.mock.patch.object(
                    module, name, wraps=getattr(module, name)) as spy:
                code, _ = self.run_evaluate("docs", self.bash_payload("ls docs"))
                self.assertEqual(0, code)
                self.assertTrue(spy.called, f"{name} was never called")

    def test_a_crash_in_the_file_tool_check_refuses_a_docs_write(self):
        for name in ("is_protected", "is_live_instructions", "is_docs_path"):
            for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                with self.subTest(step=name, tool=tool), \
                        unittest.mock.patch.object(agent_gate, name, side_effect=RuntimeError("boom")):
                    ti = {"file_path": "docs/a.md", "content": "x", "notebook_path": "docs/a.ipynb",
                          "edits": []}
                    code, err = self.run_evaluate("docs", {"tool_name": tool, "tool_input": ti})
                    self.assertEqual(2, code, err)

    def test_a_crash_in_the_dispatch_itself_refuses_bash_and_edits_but_not_reads(self):
        boom = unittest.mock.Mock(side_effect=RuntimeError("boom"))
        for profile in READ_PROFILES:
            with unittest.mock.patch.dict(agent_gate.HANDLERS, {profile: boom}):
                self.assertEqual(2, self.run_evaluate(profile, self.bash_payload("ls"))[0])
                self.assertEqual(2, self.run_evaluate(profile, {
                    "tool_name": "Write", "tool_input": {"file_path": "docs/a.md"}})[0])
                self.assertEqual(0, self.run_evaluate(profile, {"tool_name": "Read", "tool_input": {}})[0])

    def test_dev_still_fails_open(self):
        for name in ("runs_approve_script", "runs_waive", "sets_pipeline_state", "writes_protected_state"):
            with self.subTest(step=name), \
                    unittest.mock.patch.object(agent_gate, name, side_effect=RuntimeError("boom")):
                self.assertEqual(0, self.run_evaluate("dev", self.bash_payload("ls docs"))[0])
        with unittest.mock.patch.object(agent_gate, "is_protected", side_effect=RuntimeError("boom")):
            code, _ = self.run_evaluate("dev", {"tool_name": "Write",
                                                "tool_input": {"file_path": "app/x.py", "content": "x"}})
            self.assertEqual(0, code)

    def test_the_allowlist_decides_before_any_lexical_check_can_matter(self):
        # Every lexical check raises; the refusal for an unlisted program is still the
        # allowlist's own, reached by calling the handler directly (no evaluate()).
        steps = ("runs_approve_script", "runs_waive", "sets_pipeline_state", "writes_protected_state",
                 "memory_target_deny", "bash_mutates")
        with contextlib.ExitStack() as stack:
            for name in steps:
                stack.enter_context(unittest.mock.patch.object(agent_gate, name,
                                                               side_effect=RuntimeError("boom")))
            stack.enter_context(unittest.mock.patch.object(
                pretool_gate, "check_piped_test_suite", side_effect=RuntimeError("boom")))
            for profile in READ_PROFILES:
                with self.subTest(profile=profile):
                    code, err = bash(profile, "python -c 1", str(self.proj))
                    self.assertEqual(2, code)
                    self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)

    def test_a_refusal_still_names_the_boundary_the_command_also_crosses(self):
        for profile in READ_PROFILES:
            code, err = bash(profile, f"python {PIPE}/approvals.py auto", str(self.proj))
            self.assertEqual(2, code)
            self.assertIn("approvals.py is orchestrator-only", err)

    def test_the_hook_process_refuses_when_a_check_crashes(self):
        # The real entry point, with the crash injected through the module under test.
        code = (
            f"import sys, unittest.mock as m; sys.path.insert(0, {str(PIPELINE_DIR)!r}); "
            "import agent_gate, pretool_gate; "
            "sys.argv = ['agent_gate', '--profile', 'readonly']; "
            "p = {'tool_name': 'Bash', 'tool_input': {'command': 'ls'}, 'cwd': '.'}; "
            "pretool_gate.read_payload = lambda: p; "
            "agent_gate.runs_waive = m.Mock(side_effect=RuntimeError('boom')); "
            "raise SystemExit(agent_gate.main())")
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
        self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))


class GitInternalsTest(unittest.TestCase):
    """M3: no profile writes inside a .git directory."""

    GIT_PATHS = (".git/hooks/pre-commit", ".git/config", ".git/info/exclude", "app/.git/hooks/post-commit",
                 "src/.git/hooks/pre-push", ".GIT/hooks/pre-commit", ".git./hooks/pre-commit",
                 "docs/../.git/config", ".git", "app/vendor/pkg/.git/config")
    BASH_WRITES = ("echo x > .git/hooks/pre-commit", "tee .git/hooks/pre-commit",
                   "cp docs/a.md .git/hooks/pre-commit",
                   "sed -i s/a/b/ .git/config", "chmod +x .git/hooks/pre-commit", "rm -rf .git",
                   "mv docs/a.md app/.git/hooks/pre-push", "mkdir .git/hooks2", "echo x >> app/.git/config",
                   "cd .git && echo x > config", "cd .git/hooks && tee pre-commit")
    ORDINARY = (".gitignore", ".github/workflows/ci.yml", ".githooks/pre-push", "docs/git/a.md",
                "app/gitops.py", "docs/.gitkeep", "legit.git/readme", "app/main.py")

    def setUp(self):
        self.proj = make_project(self)
        for rel in (".git/hooks", ".git/info", "app/.git/hooks", "app/vendor/pkg/.git", ".githooks"):
            (self.proj / rel).mkdir(parents=True, exist_ok=True)
        self.cwd = str(self.proj)

    def test_the_file_tools_cannot_write_into_a_git_directory_in_any_profile(self):
        for profile in ("dev", "docs", "readonly"):
            for rel in self.GIT_PATHS:
                for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                    with self.subTest(profile=profile, path=rel, tool=tool):
                        ti = {"file_path": rel, "notebook_path": rel, "content": "x", "new_string": "x",
                              "new_source": "x", "edits": [{"new_string": "x"}]}
                        code, err = call(enforcement.PROFILES[profile], tool, ti, self.cwd)
                        self.assertEqual(2, code, err)

    def test_the_rule_holds_without_pretool_gates_live_harness_predicate(self):
        # pretool_gate.is_live_harness covers .git too (SEC-B). This module's own rule must not
        # depend on that edit surviving, so it is proven with the other predicate disabled.
        with unittest.mock.patch.object(pretool_gate, "is_live_harness", return_value=False):
            for profile in ("dev", "docs"):
                for rel in (".git/hooks/pre-commit", "app/.git/config"):
                    with self.subTest(profile=profile, path=rel):
                        self.assertEqual(2, write(profile, rel, self.cwd)[0])
            for command in ("echo x > .git/hooks/pre-commit", "tee app/.git/config"):
                with self.subTest(command=command):
                    self.assertEqual(2, bash("dev", command, self.cwd)[0])
            self.assertEqual(2, write("docs", ".claude/rules/testing.md", self.cwd)[0])

    def test_the_deny_names_the_git_directory(self):
        for profile in ("dev", "docs"):
            code, err = write(profile, ".git/hooks/pre-commit", self.cwd)
            self.assertEqual(2, code)
            self.assertIn(".git directory", err)

    def test_dev_bash_cannot_write_a_named_path_inside_a_git_directory(self):
        for command in self.BASH_WRITES:
            with self.subTest(command=command):
                code, err = bash("dev", command, self.cwd)
                self.assertEqual(2, code, err)
                self.assertIn(".git directory", err)

    def test_docs_and_readonly_bash_cannot_either(self):
        for profile in READ_PROFILES:
            for command in self.BASH_WRITES:
                with self.subTest(profile=profile, command=command):
                    self.assertEqual(2, bash(profile, command, self.cwd)[0])

    def test_git_writing_options_aimed_at_a_git_directory_are_refused_for_dev(self):
        for command in ("git log --output=.git/hooks/pre-commit", "git format-patch -o .git/hooks -1"):
            with self.subTest(command=command):
                self.assertEqual(2, bash("dev", command, self.cwd)[0])

    def test_a_junction_onto_a_git_directory_is_judged_where_it_resolves(self):
        link = self.proj / "docs" / "innocent"
        enforcement.make_junction(self, link, self.proj / ".git")
        for profile in ("dev", "docs"):
            self.assertEqual(2, write(profile, "docs/innocent/hooks/pre-commit", self.cwd)[0])

    def test_ordinary_files_with_git_in_the_name_stay_writable_for_dev(self):
        for rel in self.ORDINARY:
            with self.subTest(path=rel):
                self.assertFalse(agent_gate.is_git_internals(rel, self.cwd))
                self.assertFalse(agent_gate.is_protected(rel, self.cwd))
        # the full dev handler agrees for the ones that are plain source
        for rel in ("app/main.py", "app/gitops.py", "docs/git/a.md"):
            with self.subTest(handler_path=rel):
                self.assertEqual(0, write("dev", rel, self.cwd)[0])

    def test_reading_a_git_directory_is_not_a_write(self):
        for command in ("cat .git/config", "ls .git/hooks", "grep -r x .git/info", "git log -1"):
            with self.subTest(command=command):
                self.assertEqual(0, bash("docs", command, self.cwd)[0])
                self.assertEqual(0, bash("dev", command, self.cwd)[0])


class LiveInstructionsTest(unittest.TestCase):
    """M3: a docs agent does not write the live workspace's rules, skills or commands."""

    LIVE = (".claude/rules/testing.md", ".claude/skills/new-task/SKILL.md", ".claude/commands/review.md",
            ".claude/rules", ".CLAUDE/Rules/x.md", "docs/../.claude/rules/x.md", ".claude/skills/a/b/c.md")
    STILL_DOCS = ("src/.claude/rules/testing.md", "src/.claude/skills/new-task/SKILL.md",
                  "src/.claude/commands/review.md", ".claude/CLAUDE.md", ".agentry/specs/spec-0001.md",
                  "docs/a.md", "README.md", "CLAUDE.md", ".claude/rules-draft.md")

    def setUp(self):
        self.proj = make_project(self)
        for rel in (".claude/skills/new-task", ".claude/commands", "src/.claude/rules"):
            (self.proj / rel).mkdir(parents=True, exist_ok=True)
        self.cwd = str(self.proj)

    def test_docs_cannot_write_the_live_instruction_trees(self):
        for rel in self.LIVE:
            for tool in ("Write", "Edit"):
                with self.subTest(path=rel, tool=tool):
                    code, err = write("docs", rel, self.cwd, tool)
                    self.assertEqual(2, code, err)
                    self.assertIn("live workspace", err)

    def test_the_checkout_and_the_rest_of_the_docs_scope_stay_writable(self):
        for rel in self.STILL_DOCS:
            with self.subTest(path=rel):
                code, err = write("docs", rel, self.cwd)
                self.assertEqual(0, code, err)

    def test_the_predicate_is_the_project_root_copy_only(self):
        self.assertTrue(agent_gate.is_live_instructions(".claude/rules/a.md", self.cwd))
        self.assertTrue(agent_gate.is_live_instructions(str(self.proj / ".claude" / "skills" / "x.md")))
        self.assertFalse(agent_gate.is_live_instructions("src/.claude/rules/a.md", self.cwd))
        self.assertFalse(agent_gate.is_live_instructions("app/.claude/rules/a.md", self.cwd))

    def test_the_orchestrator_is_not_held_by_it(self):
        self.assertEqual(0, pretool_gate.handle_edit(".claude/rules/testing.md", "x", orch=True,
                                                     cwd=self.cwd))


class ZoneStageUnreadableTest(unittest.TestCase):
    """M5: a run.db that cannot be read is not the same as a finished swarm."""

    TASK = "task-0120"
    ZONE_MAP = json.dumps({"task": TASK, "zones": {"Z1": ["app/a.py"]}, "shared": []})

    def setUp(self):
        self.proj = make_project(self)
        self.db = self.proj / ".agentry" / "state" / "run.db"
        patcher = unittest.mock.patch.object(state, "DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        zones.zones_path().write_text(self.ZONE_MAP, encoding="utf-8")

    def make_db(self, stage: str | None) -> None:
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE runs (task TEXT PRIMARY KEY, stage TEXT)")
            if stage is not None:
                conn.execute("INSERT INTO runs VALUES (?, ?)", (self.TASK, stage))
            conn.commit()
        finally:
            conn.close()

    def dev_write(self, path: str = "app/other.py") -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = agent_gate.handle_dev("Write", {"file_path": path, "content": "x"}, cwd=str(self.proj),
                                         payload={})
        return code, err.getvalue()

    def test_a_readable_stage_behaves_as_before(self):
        self.make_db("implement")
        self.assertEqual("implement", zones.task_stage(self.TASK))
        self.assertIsNotNone(zones.load())
        self.assertEqual(2, self.dev_write()[0])  # a zone map in force, no zone line in the prompt
        self.db.unlink()
        self.make_db("done")
        self.assertIsNone(zones.load())
        self.assertTrue(agent_gate.map_is_dormant())
        self.assertEqual(0, self.dev_write()[0])

    def test_no_run_db_and_no_run_row_still_mean_a_dormant_map(self):
        self.assertIsNone(zones.task_stage(self.TASK))  # no database at all
        self.assertTrue(agent_gate.map_is_dormant())
        self.make_db(None)  # a database, no row for the task
        self.assertIsNone(zones.task_stage(self.TASK))
        self.assertTrue(agent_gate.map_is_dormant())
        self.assertEqual(0, self.dev_write()[0])

    def test_a_corrupt_run_db_returns_the_sentinel_and_the_zone_gate_denies(self):
        self.db.write_bytes(b"this is not an sqlite database" * 64)
        self.assertEqual(zones.STAGE_UNREADABLE, zones.task_stage(self.TASK))
        self.assertIsNone(zones.load())
        self.assertFalse(agent_gate.map_is_dormant())
        code, err = self.dev_write()
        self.assertEqual(2, code)
        self.assertIn("run database", err)

    def test_a_locked_run_db_returns_the_sentinel_and_the_zone_gate_denies(self):
        self.make_db("done")
        holder = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        self.assertEqual(zones.STAGE_UNREADABLE, zones.task_stage(self.TASK))
        self.assertFalse(agent_gate.map_is_dormant())
        self.assertEqual(2, self.dev_write()[0])
        holder.execute("ROLLBACK")
        self.assertEqual("done", zones.task_stage(self.TASK))
        self.assertEqual(0, self.dev_write()[0])

    def test_prove_and_describe_say_the_database_is_unreadable(self):
        self.db.write_bytes(b"garbage" * 100)
        ok, lines = zones.prove(self.TASK)
        self.assertFalse(ok)
        self.assertIn("cannot be read", lines[0])
        self.assertIn("cannot be read", zones.describe()[0])

    def test_a_stage_that_flips_between_the_two_reads_is_refused_not_trusted(self):
        # load() read the database as unreadable; map_is_dormant() reads it again and finds
        # a live stage. Only a stage that is read AND is not live counts as finished.
        with unittest.mock.patch.object(zones, "task_stage", return_value="implement"):
            self.assertFalse(agent_gate.map_is_dormant())
        with unittest.mock.patch.object(zones, "task_stage", return_value="done"):
            self.assertTrue(agent_gate.map_is_dormant())
        with unittest.mock.patch.object(zones, "task_stage", return_value=zones.STAGE_UNREADABLE):
            self.assertFalse(agent_gate.map_is_dormant())


class ZonesReleaseTest(unittest.TestCase):
    """zones.py --release: the orchestrator unfreezes one task's map, no subagent runs it."""

    TASK = "task-0120"
    OTHER = "task-0121"
    RELEASE = f"python {PIPE}/zones.py --release --task {TASK}"

    def setUp(self):
        self.proj = make_project(self)
        self.map_file = zones.zones_path()
        self.map_file.write_text(json.dumps({"task": self.TASK, "zones": {"Z1": ["app/a.py"]}}),
                                 encoding="utf-8")

    def release(self, task: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = zones.main(["--release", "--task", task])
        return code, out.getvalue(), err.getvalue()

    def test_the_orchestrator_releases_the_map_of_its_own_task(self):
        code, out, _ = self.release(self.TASK)
        self.assertEqual(0, code)
        self.assertIn(self.TASK, out)
        self.assertFalse(self.map_file.exists())

    def test_another_tasks_map_is_refused_and_left_in_place(self):
        code, _, err = self.release(self.OTHER)
        self.assertEqual(1, code)
        self.assertIn(self.TASK, err)
        self.assertTrue(self.map_file.exists())

    def test_a_map_nobody_can_attribute_is_refused_and_left_in_place(self):
        for text in ("not json", "[]", "null", '{"zones": {}}'):
            with self.subTest(text=text):
                self.map_file.write_text(text, encoding="utf-8")
                code, _, err = self.release(self.TASK)
                self.assertEqual(1, code, err)
                self.assertTrue(self.map_file.exists())

    def test_nothing_to_release_is_not_an_error_and_a_bad_task_id_is(self):
        self.map_file.unlink()
        self.assertEqual(0, self.release(self.TASK)[0])
        self.assertEqual(1, self.release("../../etc")[0])
        self.assertEqual(1, self.release("task-12; rm")[0])

    def test_release_needs_a_task(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            zones.main(["--release"])

    def test_release_and_prove_exclude_each_other(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            zones.main(["--release", "--prove", "--task", self.TASK])

    def test_every_subagent_profile_is_refused_the_script(self):
        for command in (self.RELEASE, f"python {PIPE}/zones.py --task {self.TASK} --release",
                        f"cd {PIPE} && python zones.py --release --task {self.TASK}",
                        f"cd {PIPE} && python -m zones --release --task {self.TASK}",
                        f"python {PIPE}/zones.py --prove --task {self.TASK}",
                        f"python {PIPE}/zones.py --rel --task {self.TASK}",
                        f"cat {PIPE}/zones.py | python - --release --task {self.TASK}"):
            for profile in ("dev", "readonly", "docs"):
                with self.subTest(profile=profile, command=command):
                    code, err = bash(profile, command, str(self.proj))
                    self.assertEqual(2, code, err)
                    if profile == "dev":
                        self.assertIn("orchestrator-only", err)
        self.assertTrue(self.map_file.exists())

    def test_reading_the_map_stays_open_to_dev_and_the_orchestrator_may_release(self):
        self.assertEqual(0, bash("dev", f"python {PIPE}/zones.py --show", str(self.proj))[0])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pretool_gate.handle_bash(self.RELEASE, cwd=str(self.proj), orch=True)
        self.assertEqual(0, code, err.getvalue())

    def test_the_state_directory_file_cannot_be_deleted_by_hand_either(self):
        target = ".agentry/state/zones.json"
        for profile in ("dev", "docs", "readonly"):
            with self.subTest(profile=profile):
                self.assertEqual(2, bash(profile, f"rm {target}", str(self.proj))[0])
                self.assertEqual(2, write(profile, target, str(self.proj))[0])


if __name__ == "__main__":
    unittest.main()
