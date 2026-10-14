"""FX-3 of task-0120: an allowed program's own flag is a file write.

Two repros from the QA report, both exit 0 through the readonly and docs gates before
this fix, and both verified with the real tools:

  * `git log -1 --pretty=format:auto --output=.agentry/state/approvals` writes exactly
    `auto` into the approvals file; the same trick with a JSON `--pretty` writes a valid
    trunk-push marker (the User's one-shot trunk-push approval).
  * `python plugin/tools/memory/memory.py --export --out FILE` overwrites any file with a
    markdown export: pipeline.json, a state file or gate code (a broken gate script exits
    non-2, which is an allow). `--db FILE` creates a store at any path.

The fix is two layers. agent_gate refuses a git call that is not a read (default-deny
subcommand plus the options that write or run a program) for readonly and docs, and a
memory.py `--out`/`--db` outside .agentry/memory/ and tmp/ for every profile; memory.py
refuses the state tree, pipeline.json and .claude/ itself. Every gate test runs on a
sandbox project (state.ROOT patched), so nothing here touches the real tree.
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

TESTS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = TESTS_DIR.parents[0] / "pipeline"
MEMORY_DIR = TESTS_DIR.parents[0] / "memory"
for _dir in (PIPELINE_DIR, MEMORY_DIR, TESTS_DIR):
    sys.path.insert(0, str(_dir))

import agent_gate
import memory
import state
import test_subagent_enforcement as enforcement

MEMORY_PY = "plugin/tools/memory/memory.py"
FORGE_APPROVALS = "git log -1 --pretty=format:auto --output=.agentry/state/approvals"
FORGE_TRUNK_MARKER = ("git log -1 --pretty=format:'{\"trunk\":\"main\"}' "
                      "--output=.agentry/state/trunk_push")


class GitWritingCallTest(unittest.TestCase):
    """readonly and docs: a git call is a read or it is refused."""

    # Every spelling here writes a file or runs a program, and exits 0 on the old gate.
    WRITING = (
        FORGE_APPROVALS,
        FORGE_TRUNK_MARKER,
        "git log --output .agentry/state/approvals",
        "git log --output=.agentry/pipeline.json -1",
        "git diff --output=plugin/tools/pipeline/pretool_gate.py",
        "git show --output=docs/a.md HEAD",
        "git whatchanged --output=docs/a.md",
        "git diff-tree --output=docs/a.md HEAD",
        "git log --out=docs/a.md", "git log --outp docs/a.md", "git log --outpu=docs/a.md",
        "git -C docs log --output=a.md",
        "git --no-pager log --output=docs/a.md",
        "git status && git log --output=docs/a.md",
        "echo $(git log --output=docs/a.md)",
        "git archive -o tmp/x.tar HEAD", "git archive --output=tmp/x.tar HEAD",
        "git archive HEAD",
        "git format-patch -o tmp -1", "git format-patch --output-directory=tmp -1",
        "git format-patch -1",
        "git bundle create tmp/b.bundle HEAD", "git bundle unbundle tmp/b.bundle",
        "git fast-export --export-marks=tmp/m HEAD", "git index-pack -o tmp/i.idx tmp/p.pack",
        "git pack-objects tmp/pack", "git hash-object -w --stdin",
        "git commit-tree HEAD^{tree} -m x", "git update-ref refs/heads/x HEAD",
        "git update-index --add docs/a.md", "git checkout-index -a --prefix=tmp/",
        "git read-tree -u HEAD", "git worktree add tmp/w", "git clone . tmp/c",
        "printf x | git apply -", "git apply docs/a.patch", "git am docs/a.patch",
        "git -c core.fsmonitor=docs/forge.sh status",
        "git -c alias.x=!docs/forge.sh x", "git --config-env=core.pager=P log",
        "git -c core.pager=docs/forge.sh -p log", "git -p log", "git --paginate log",
        "git config alias.zz '!docs/forge.sh'", "git config --file docs/a.md k v",
        "git diff --ext-diff", "git log -p --ext-diff", "git diff --ext=1",
        "git grep --open-files-in-pager=docs/forge.sh x", "git grep -Odocs/forge.sh x",
        "git grep -nOdocs/forge.sh x", "git grep --open x",
        "git ls-remote --upload-pack=docs/forge.sh .", "git fetch", "git submodule foreach id",
        "git bisect run docs/forge.sh", "git difftool", "git help -w log",
        "git branch -D x", "git branch -m old new", "git branch newbranch", "git branch -f x HEAD",
        "git branch --set-upstream-to=origin/x", "git branch --edit-description",
        "git tag v1", "git tag -d v1", "git remote add o url", "git stash", "git stash push",
        "git stash pop", "git checkout main", "git switch main", "git notes add -m x",
        "GIT_EXTERNAL_DIFF=docs/forge.sh git diff", "GIT_PAGER=docs/forge.sh git log",
        "GIT_SSH_COMMAND=docs/forge.sh git log", "PAGER=docs/forge.sh git log",
        "EDITOR=docs/forge.sh git log", "env GIT_EXTERNAL_DIFF=docs/forge.sh git diff",
        "git_external_diff=docs/forge.sh git diff",
    )
    READS = (
        "git log --oneline -5", "git log -1 --pretty=format:%H", "git log --grep=add",
        "git log --author=x --since=1.day", "git log --oneline | head -3",
        "git log --output-indicator-new=+ -1", "git log --no-ext-diff -1",
        "git log --follow -- docs/a.md", "git log -L1,5:docs/a.md",
        "git diff HEAD -- docs", "git diff --stat", "git diff --cached",
        "git diff --no-index docs/a.md docs/forge.py",
        "git diff -O docs/order.txt", "git diff --name-only HEAD~1",
        "git show HEAD:docs/a.md", "git show --stat HEAD", "git status --porcelain",
        "git blame docs/a.md", "git annotate docs/a.md", "git shortlog -sn",
        "git rev-parse HEAD", "git rev-parse --show-toplevel", "git rev-list --count HEAD",
        "git merge-base --is-ancestor origin/main HEAD", "git describe --tags",
        "git ls-files", "git ls-files -o", "git ls-tree HEAD", "git cat-file -p HEAD",
        "git grep -n forge docs", "git grep -o forge docs", "git grep -e forge -- docs",
        "git for-each-ref refs/heads", "git show-ref", "git name-rev HEAD",
        "git branch", "git branch --list", "git branch --list 'feature/*'", "git branch -a",
        "git branch -r", "git branch -vv", "git branch --show-current",
        "git branch --contains HEAD", "git branch --merged main", "git branch --sort=-committerdate",
        "git stash list", "git stash show", "git stash show -p",
        "git -C docs log -1", "git --no-pager log -1", "git --git-dir=.git log -1",
        "git --work-tree docs status", "git --version", "git count-objects -v",
        "git check-ignore -v docs/a.md", "git diff-tree --no-commit-id --name-only -r HEAD",
    )

    def setUp(self):
        self.proj = enforcement.make_project(self)
        self.cwd = str(self.proj)

    def test_the_forgery_repros_are_refused_for_readonly_and_docs(self):
        for profile in ("readonly", "docs"):
            for command in (FORGE_APPROVALS, FORGE_TRUNK_MARKER):
                with self.subTest(profile=profile, command=command):
                    code, err = enforcement.bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)
                    self.assertIn("git", err)

    def test_every_writing_or_program_running_spelling_is_refused(self):
        for profile in ("readonly", "docs"):
            for command in self.WRITING:
                with self.subTest(profile=profile, command=command):
                    code, err = enforcement.bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)

    def test_the_read_only_git_calls_still_run(self):
        for profile in ("readonly", "docs"):
            for command in self.READS:
                with self.subTest(profile=profile, command=command):
                    code, err = enforcement.bash(profile, command, self.cwd)
                    self.assertEqual(0, code, err)

    def test_the_refusal_names_the_git_option_that_writes(self):
        code, err = enforcement.bash("readonly", FORGE_APPROVALS, self.cwd)
        self.assertEqual(2, code)
        self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)
        self.assertIn("git log --output=.agentry/state/approvals", err)

    def test_the_gate_does_not_run_the_command_it_refuses(self):
        target = self.proj / ".agentry" / "state" / "approvals"
        before = target.read_text(encoding="utf-8")
        enforcement.bash("readonly", FORGE_APPROVALS, self.cwd)
        self.assertEqual(before, target.read_text(encoding="utf-8"))

    def test_a_real_git_log_output_flag_does_write_which_is_why_it_is_refused(self):
        """The premise, measured: git itself writes the file the flag names."""
        out = self.proj / "docs" / "written.txt"
        repo = self.proj / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            done = subprocess.run(["git", "-C", str(repo), *argv], env=env, capture_output=True, timeout=60)
            if done.returncode != 0:
                self.skipTest("cannot build a throwaway git repository here")
        subprocess.run(["git", "-C", str(repo), "log", "-1", "--pretty=format:auto",
                        f"--output={out}"], env=env, capture_output=True, timeout=60)
        self.assertEqual("auto", out.read_text(encoding="utf-8"))
        self.assertEqual(2, enforcement.bash("readonly", f"git -C repo log -1 --pretty=format:auto "
                                                         f"--output={out.as_posix()}", self.cwd)[0])

    def test_the_new_checks_are_total_over_odd_text(self):
        for text in ("git", "git -", "git --", "git -c", "git -C", "git log --output", "git log --",
                     "git \"\"", "git ''", "git log \x00", "git log --output=\x00", "git ; git",
                     "git log | git", "git >", "git log >", "git 2>&1", "git -C docs", "git -p"):
            for profile in ("readonly", "docs"):
                with self.subTest(profile=profile, text=text):
                    code, _ = enforcement.bash(profile, text, self.cwd)
                    self.assertIn(code, (0, 2))

    def test_git_denied_judges_arguments_alone(self):
        self.assertEqual("", agent_gate.git_denied([]))
        self.assertEqual("", agent_gate.git_denied(["--version"]))
        self.assertEqual("-c", agent_gate.git_denied(["-c", "a=b", "log"]))
        self.assertEqual("log --output=x", agent_gate.git_denied(["log", "-1", "--output=x"]))
        self.assertEqual("archive", agent_gate.git_denied(["archive", "-o", "x", "HEAD"]))
        self.assertEqual("", agent_gate.git_denied(["-C", "dir", "log", "-1"]))
        self.assertEqual("", agent_gate.git_denied(["-C", "dir", "log"]))


class DevGitWriteTargetTest(unittest.TestCase):
    """FX-3b: the dev profile keeps git, but a git option that names a file git writes is
    judged like a redirect target - protected state, pipeline.json and the live harness are
    refused, a target the gate cannot resolve is refused, ordinary working files are not."""

    DENIED = (
        FORGE_APPROVALS,
        FORGE_TRUNK_MARKER,
        "git log --output=.agentry/pipeline.json -1",
        "git log --output .agentry/state/approvals",
        "git diff --output=plugin/tools/pipeline/pretool_gate.py",
        "git show --output=.claude/settings.json HEAD",
        "git log --out=.agentry/pipeline.json", "git log --outp .agentry/pipeline.json",
        "git log --o=.agentry/pipeline.json",
        "git log --output=tmp/../.agentry/pipeline.json",
        "git log --output=.agentry/state/approvals::$DATA",
        "git log --output=.agentry/state/appr*",
        "git archive -o .agentry/pipeline.json HEAD", "git archive -o.agentry/pipeline.json HEAD",
        "git archive --output=.agentry/state/approvals HEAD",
        "git format-patch -o plugin/tools/pipeline -1",
        "git format-patch -1 -no plugin/tools/pipeline",
        "git format-patch --output-directory=.agentry/state -1",
        "git format-patch --output-dir .agentry/state -1",
        "git bundle create .agentry/pipeline.json HEAD",
        "git bundle create --quiet .claude/settings.json HEAD",
        "git fast-export --export-marks=.agentry/state/approvals HEAD",
        "git index-pack -o plugin/tools/pipeline/pretool_gate.py tmp/p.pack",
        "git config --file .agentry/pipeline.json k v", "git config -f .agentry/pipeline.json k v",
        "git -C .agentry log --output=pipeline.json",
        "git -C .agentry/state log --output=approvals",
        "git -C plugin -C tools log --output=pipeline/pretool_gate.py",
        "cd .agentry && git log --output=pipeline.json",
        "cd plugin/tools && git format-patch -o pipeline -1",
        "git status && git log --output=.agentry/pipeline.json",
        "echo $(git log --output=.agentry/pipeline.json)",
        "x=$(git log --output=.agentry/pipeline.json); echo $x",
        "bash -c 'git log --output=.agentry/pipeline.json'",
        "sh -c \"git archive -o .agentry/pipeline.json HEAD\"",
        "eval 'git log --output=.agentry/state/approvals'",
        "env GIT_AUTHOR_NAME=x git log --output=.agentry/pipeline.json",
        "git log --output=$TARGET", "git log --output=\"${TARGET}\"", "git log --output=~/x.txt",
        "git log --output=$(echo .agentry/pipeline.json)", "git log --output=`echo x`",
        "git log --output", "git log --output=", "git archive -o", "git format-patch --output-directory",
        "git bundle create $OUT HEAD",
        "git -C \"$D\" log --output=tmp/x.txt", "git -C ~ log --output=x.txt",
        "GIT_WORK_TREE=.agentry git log --output=state/approvals",
        "export GIT_WORK_TREE=.agentry; git log --output=state/approvals",
        "git --work-tree=.agentry log --output=state/approvals",
        "git --work-tree .agentry log --output=state/approvals",
        "git -c core.worktree=.agentry log --output=state/approvals",
        "git --git-dir=x/.git log --output=tmp/x.txt",
    )
    ALLOWED = (
        "git format-patch -o tmp/p -1", "git format-patch -1 -o tmp/p", "git format-patch -1 -otmp/p",
        "git format-patch --output-directory=tmp/p -1", "git format-patch -1",
        "git archive -o tmp/x.zip HEAD", "git archive --output=dist/x.tar HEAD",
        "git archive HEAD", "git bundle create tmp/b.bundle HEAD", "git bundle verify tmp/b.bundle",
        "git log --output=tmp/log.txt", "git diff --output=docs/a.md", "git log --output docs/a.md",
        "git log --output=.agentry/memory/log.txt", "git log --output=app/out.txt",
        "git show --out=tmp/x.txt HEAD", "git fast-export --export-marks=tmp/m HEAD",
        "git index-pack -o tmp/i.idx tmp/p.pack", "git config --file tmp/c k v",
        "git config user.name x", "git config --get user.name", "git log --oneline -5",
        "git log --output-indicator-new=+ -1", "git ls-files -o", "git diff --stat",
        "git status --porcelain", "git log -- --output=.agentry/pipeline.json",
        "git -C docs log --output=a.md", "git -C app -C ../docs log --output=a.md",
        "cd tmp && git log --output=x.txt", "cd docs && git archive -o ../tmp/x.zip HEAD",
        "git -c user.name=x log -1",
        "bash -c 'git log --output=tmp/x.txt'", "git status && git log --output=tmp/x.txt",
    )

    def setUp(self):
        self.proj = enforcement.make_project(self)
        self.cwd = str(self.proj)
        (self.proj / "tmp").mkdir(exist_ok=True)

    def test_every_dev_repro_that_writes_protected_state_or_the_harness_is_refused(self):
        for command in self.DENIED:
            with self.subTest(command=command):
                code, err = enforcement.bash("dev", command, self.cwd)
                self.assertEqual(2, code, err)

    def test_the_four_reported_targets_are_refused_by_name(self):
        for target in (".agentry/state/approvals", ".agentry/state/trunk_push", ".agentry/pipeline.json",
                       "plugin/tools/pipeline/pretool_gate.py"):
            with self.subTest(target=target):
                command = f"git log -1 --pretty=format:auto --output={target}"
                code, err = enforcement.bash("dev", command, self.cwd)
                self.assertEqual(2, code)
                self.assertIn(f"git --output '{target}'", err)
                self.assertIn("like a redirect", err)

    def test_working_files_and_non_target_options_still_run_for_dev(self):
        for command in self.ALLOWED:
            with self.subTest(command=command):
                code, err = enforcement.bash("dev", command, self.cwd)
                self.assertEqual(0, code, err)

    def test_the_gate_does_not_run_the_command_it_refuses(self):
        target = self.proj / ".agentry" / "state" / "approvals"
        enforcement.bash("dev", FORGE_APPROVALS, self.cwd)
        self.assertEqual("manual", target.read_text(encoding="utf-8"))

    def test_readonly_and_docs_stay_refused_by_the_default_deny_not_by_this_check(self):
        for profile in ("readonly", "docs"):
            code, err = enforcement.bash(profile, "git format-patch -o tmp/p -1", self.cwd)
            self.assertEqual(2, code, err)
            self.assertIn(agent_gate.ALLOWLIST_PHRASE, err)

    def test_the_check_fails_closed(self):
        with unittest.mock.patch.object(agent_gate, "git_target_denied", side_effect=RuntimeError("x")):
            code, err = enforcement.bash("dev", "git format-patch -o tmp/p -1", self.cwd)
        self.assertEqual(2, code, err)
        self.assertIn("target check failed", err)

    def test_the_check_is_total_over_odd_text(self):
        for text in ("git", "git -", "git --", "git -c", "git -C", "git log --output", "git bundle",
                     "git bundle create -", "git archive -o -", "git log --output=\x00", "git ; git",
                     "git log | git", "git >", "git log >", "git 2>&1", "git -C docs", "git -p"):
            with self.subTest(text=text):
                self.assertIn(enforcement.bash("dev", text, self.cwd)[0], (0, 2))

    def test_options_are_read_the_way_git_reads_them(self):
        options = agent_gate.git_target_options
        self.assertEqual([("--output", "a")], options("log", ["--output=a"]))
        self.assertEqual([("--out", "a")], options("log", ["--out", "a"]))
        self.assertEqual([("--output", "")], options("log", ["--output"]))
        self.assertEqual([], options("log", ["--output-indicator-new=+", "--", "--output=a"]))
        self.assertEqual([("-o", "a")], options("archive", ["-o", "a", "HEAD"]))
        self.assertEqual([("-o", "a")], options("format-patch", ["-1", "-oa"]))
        self.assertEqual([("-o", "a")], options("format-patch", ["-no", "a"]))
        self.assertEqual([], options("ls-files", ["-o"]))
        self.assertEqual([("--output-directory", "d")], options("format-patch", ["--output-directory=d"]))
        self.assertEqual([("bundle create", "f"), ("bundle create", "HEAD")],
                         options("bundle", ["create", "f", "HEAD"]))
        self.assertEqual([], options("bundle", ["verify", "f"]))
        self.assertEqual([("--file", "f")], options("config", ["--file", "f", "k", "v"]))
        self.assertEqual([], options("log", ["--file", "f"]))


class MemoryTargetGateTest(unittest.TestCase):
    """Every profile: memory.py writes only under .agentry/memory/ and tmp/."""

    EXPORT = f"python {MEMORY_PY} --export"
    FORGERIES = (
        f"{EXPORT} --out .agentry/pipeline.json",
        f"{EXPORT} --out=.agentry/pipeline.json",
        f"{EXPORT} --out .agentry/state/approvals",
        f"{EXPORT} --out=.agentry/state/trunk_push",
        f"{EXPORT} --out plugin/tools/pipeline/pretool_gate.py",
        f"{EXPORT} --out .claude/settings.json",
        f"{EXPORT} --o .agentry/pipeline.json", f"{EXPORT} --ou=.agentry/pipeline.json",
        f"{EXPORT} --out docs/a.md", f"{EXPORT} --out ../outside.md", f"{EXPORT} --out /outside.md",
        f"{EXPORT} --out tmp/../.agentry/pipeline.json",
        f"{EXPORT} --out .agentry/memory/../pipeline.json",
        f"{EXPORT} --out .agentry/pipeline.json::$DATA",
        f"{EXPORT} --out $TARGET", f"{EXPORT} --out \"${{TARGET}}\"", f"{EXPORT} --out ~/x.md",
        f"{EXPORT} --out $(echo .agentry/pipeline.json)", f"{EXPORT} --out `echo x`",
        f"python {MEMORY_PY} --db .agentry/state/x.db --stats",
        f"python {MEMORY_PY} --db=plugin/tools/pipeline/pretool_gate.py --stats",
        f"python {MEMORY_PY} --db .agentry/pipeline.json --stats",
        f"python {MEMORY_PY} --db docs/a.md --stats",
        f"python {MEMORY_PY} --db tmp/ok.db --export --out docs/a.md",
        f"cd .agentry && python ../{MEMORY_PY} --export --out pipeline.json",
        "cd plugin/tools/memory && python memory.py --export --out ../../../.agentry/pipeline.json",
        "cd plugin/tools/memory && python -m memory --export --out ../pipeline/pretool_gate.py",
        f"python \"$CLAUDE_PROJECT_DIR/{MEMORY_PY}\" --export --out .agentry/pipeline.json",
        f"python -B -X utf8 {MEMORY_PY} --export --out .agentry/pipeline.json",
        f"x=$(python {MEMORY_PY} --export --out .agentry/pipeline.json); echo $x",
        f"echo hi && python {MEMORY_PY} --export --out .agentry/pipeline.json",
    )
    ALLOWED = (
        f"{EXPORT} --out tmp/x.md", f"{EXPORT} --out=tmp/x.md", f"{EXPORT} --out tmp/sub/x.md",
        f"{EXPORT} --out ./tmp/x.md", f"{EXPORT} --out .agentry/memory/export.md",
        f"{EXPORT} --o tmp/x.md", EXPORT,
        f"python {MEMORY_PY} --query lesson", f"python {MEMORY_PY} --stats",
        f"python {MEMORY_PY} --db .agentry/memory/memory.db --stats",
        f"python {MEMORY_PY} --db tmp/t.db --stats",
        f"python {MEMORY_PY} --db=tmp/t.db --export --out tmp/e.md",
        f"cd tmp && python ../{MEMORY_PY} --export --out e.md",
        f"python {MEMORY_PY} --query \"--out is a flag\"",
    )

    def setUp(self):
        self.proj = enforcement.make_project(self)
        self.cwd = str(self.proj)

    def test_an_out_or_db_target_outside_the_two_directories_is_refused_for_every_profile(self):
        for profile in ("dev", "readonly", "docs"):
            for command in self.FORGERIES:
                with self.subTest(profile=profile, command=command):
                    code, err = enforcement.bash(profile, command, self.cwd)
                    self.assertEqual(2, code, err)

    def test_the_memory_refusal_names_the_option_and_the_two_directories(self):
        code, err = enforcement.bash("readonly", f"{self.EXPORT} --out .agentry/pipeline.json", self.cwd)
        self.assertEqual(2, code)
        self.assertIn("--out", err)
        self.assertIn(".agentry/pipeline.json", err)
        self.assertIn(".agentry/memory/", err)
        self.assertIn("tmp/", err)

    def test_an_export_under_tmp_or_the_memory_directory_and_plain_reads_still_run(self):
        for profile in ("dev", "readonly", "docs"):
            for command in self.ALLOWED:
                with self.subTest(profile=profile, command=command):
                    code, err = enforcement.bash(profile, command, self.cwd)
                    self.assertEqual(0, code, err)

    def test_a_dev_agents_own_script_named_memory_py_is_not_the_harness_tool(self):
        (self.proj / "app" / "memory.py").write_text("pass\n", encoding="utf-8")
        code, err = enforcement.bash("dev", "python app/memory.py --out docs/a.md", self.cwd)
        self.assertEqual(0, code, err)

    def test_a_nested_shell_cannot_hide_the_target_from_a_dev_agent(self):
        for command in (f"bash -c '{self.EXPORT} --out .agentry/pipeline.json'",
                        f"sh -c \"{self.EXPORT} --out=.agentry/state/approvals\"",
                        f"eval '{self.EXPORT} --out .agentry/pipeline.json'"):
            with self.subTest(command=command):
                self.assertEqual(2, enforcement.bash("dev", command, self.cwd)[0])

    def test_the_check_fails_closed(self):
        with unittest.mock.patch.object(agent_gate, "memory_target_denied", side_effect=RuntimeError("x")):
            for profile in ("dev", "readonly", "docs"):
                with self.subTest(profile=profile):
                    code, err = enforcement.bash(profile, f"{self.EXPORT} --out tmp/x.md", self.cwd)
                    self.assertEqual(2, code, err)
                    self.assertIn("target check failed", err)

    def test_options_are_read_the_way_argparse_reads_them(self):
        self.assertEqual([("--out", "a"), ("--db", "b")],
                         agent_gate.memory_options(["--out", "a", "--db=b"]))
        self.assertEqual([("--out", "a")], agent_gate.memory_options(["--o=a"]))
        self.assertEqual([("--out", "")], agent_gate.memory_options(["--out"]))
        self.assertEqual([], agent_gate.memory_options(["--d", "x", "--dry-run", "--", "--out", "y"]))


class MemoryScriptTargetGuardTest(unittest.TestCase):
    """memory.py itself (second layer): the state tree, pipeline.json and .claude/ are never
    written by --out or --db, whatever called it."""

    def setUp(self):
        self.proj = enforcement.make_project(self)
        patcher = unittest.mock.patch.object(memory, "ROOT", self.proj)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = self.proj / "tmp" / "guard.db"

    def run_memory(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = memory.main(list(argv) if "--db" in argv else ["--db", str(self.db), *argv])
        return code, out.getvalue()

    def test_an_export_into_the_state_tree_pipeline_json_or_claude_is_refused_and_leaves_the_file(self):
        targets = {
            self.proj / ".agentry" / "state" / "approvals": "manual",
            self.proj / ".agentry" / "pipeline.json": "{}",
            self.proj / "plugin" / "tools" / "pipeline" / "pretool_gate.py": "pass\n",
            self.proj / ".claude" / "settings.json": "{}",
        }
        spellings = [lambda p: str(p), lambda p: str(p.parent / ".." / p.parent.name / p.name),
                     lambda p: str(p) + "::$DATA"]
        if os.name == "nt":  # a case-insensitive volume: the upper-case spelling is the same file
            spellings.append(lambda p: p.as_posix().upper())
        for path, text in targets.items():
            for spell in spellings:
                with self.subTest(path=path.name, spelling=spell(path)):
                    code, out = self.run_memory("--export", "--out", spell(path))
                    self.assertEqual(1, code)
                    self.assertIn("refused", out)
                    self.assertEqual(text, path.read_text(encoding="utf-8"))

    def test_an_export_to_a_new_file_under_claude_or_state_is_refused_and_creates_nothing(self):
        for rel in (".claude/planted.md", "plugin/tools/memory/planted.py", ".agentry/state/planted.md"):
            with self.subTest(rel=rel):
                code, _ = self.run_memory("--export", "--out", str(self.proj / rel))
                self.assertEqual(1, code)
                self.assertFalse((self.proj / rel).exists())

    def test_a_store_path_in_a_protected_place_is_refused(self):
        for rel in (".agentry/state/x.db", ".agentry/pipeline.json", ".claude/x.db"):
            for flag in ("--stats", "--export"):
                with self.subTest(rel=rel, flag=flag):
                    code, out = self.run_memory("--db", str(self.proj / rel), flag)
                    self.assertEqual(1, code)
                    self.assertIn("--db", out)
        self.assertEqual("{}", (self.proj / ".agentry" / "pipeline.json").read_text(encoding="utf-8"))
        self.assertFalse((self.proj / ".claude" / "x.db").exists())

    def test_a_hard_link_to_pipeline_json_is_refused(self):
        link = self.proj / "docs" / "link.json"
        try:
            os.link(self.proj / ".agentry" / "pipeline.json", link)
        except OSError:
            self.skipTest("hard links are not available here")
        code, _ = self.run_memory("--export", "--out", str(link))
        self.assertEqual(1, code)
        self.assertEqual("{}", link.read_text(encoding="utf-8"))

    def test_an_export_under_tmp_or_the_memory_directory_still_writes(self):
        for rel in ("tmp/x.md", ".agentry/memory/export.md", "docs/export.md"):
            with self.subTest(rel=rel):
                target = self.proj / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                code, out = self.run_memory("--export", "--out", str(target))
                self.assertEqual(0, code, out)
                self.assertTrue(target.is_file())

    def test_an_unresolvable_target_is_refused(self):
        code, out = self.run_memory("--export", "--out", "bad\x00path.md")
        self.assertEqual(1, code)
        self.assertIn("refused", out)


class RealHookRepro(unittest.TestCase):
    """The QA repros through the process Claude Code starts. The gate refuses them before
    anything runs, so no file of the real tree is at risk."""

    @staticmethod
    def hook(profile: str, command: str) -> subprocess.CompletedProcess:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(state.ROOT)}
        return subprocess.run(
            [sys.executable, str(PIPELINE_DIR / "agent_gate.py"), "--profile", profile],
            input=json.dumps(payload).encode("utf-8"), capture_output=True, timeout=60)

    def test_the_approvals_and_trunk_marker_forgeries_and_the_memory_overwrite_exit_2(self):
        for profile in ("readonly", "docs"):
            for command in (FORGE_APPROVALS, FORGE_TRUNK_MARKER,
                            f"python {MEMORY_PY} --export --out .agentry/pipeline.json",
                            f"python {MEMORY_PY} --export --out=plugin/tools/pipeline/pretool_gate.py"):
                with self.subTest(profile=profile, command=command):
                    proc = self.hook(profile, command)
                    self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_a_dev_agent_is_refused_the_memory_overwrite_too(self):
        proc = self.hook("dev", f"python {MEMORY_PY} --export --out .agentry/pipeline.json")
        self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_a_dev_agent_is_refused_the_git_output_forgeries_and_may_format_patch_into_tmp(self):
        for command in (FORGE_APPROVALS, FORGE_TRUNK_MARKER, "git log --output=.agentry/pipeline.json",
                        "git log --output=plugin/tools/pipeline/pretool_gate.py"):
            with self.subTest(command=command):
                proc = self.hook("dev", command)
                self.assertEqual(2, proc.returncode, proc.stderr.decode("utf-8", "replace"))
        proc = self.hook("dev", "git format-patch -o tmp/p -1")
        self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", "replace"))

    def test_a_git_read_and_an_export_under_tmp_exit_0(self):
        for profile in ("readonly", "docs"):
            for command in ("git log -1 --oneline", f"python {MEMORY_PY} --export --out tmp/x.md"):
                with self.subTest(profile=profile, command=command):
                    proc = self.hook(profile, command)
                    self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
