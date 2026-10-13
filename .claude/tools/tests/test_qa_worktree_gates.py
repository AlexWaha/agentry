"""epic-0004 / task-0130 (QA): bypass probes for the worktree hook gates.

The developer's own file (test_worktree_gates) pins what the gates are meant to do. This
one attacks them. Every test builds a real workspace (a `src` repo on main, real `git
worktree add` trees under `src-wt/`, a run.db and a pipeline.json of its own) through the
developer's WorkspaceCase and runs the REAL gates against it; nothing about worktree.py is
faked.

Sections:

- the review freeze and the trunk freeze under every spelling of a path an agent can use
  (case, separators, msys drive, `..`, stream suffix, trailing dot or space, junction, 8.3
  short name, extended-length prefix);
- the trunk freeze for shell writes: every redirect form, tee, sed -i, cp, mv, rm, a `cd`
  into the trunk in all its spellings;
- merge condition 4 (freshness) under every merge and pull spelling, the order of the four
  conditions, and a tree whose directory is gone;
- placement of commit and branch checks by `-C`, `cd` and the payload cwd with two live
  trees;
- the dev layout denies in every spelling of git and of worktree.py;
- flag off: a corpus that must stay allowed, and a static guard that the new read-only
  checks hold no subprocess call;
- KNOWN GAPS: `expectedFailure` tests that state the behaviour the gate should have and
  does not yet. Each names its finding in qa-gates.md. When a gap is fixed its test turns
  into an "unexpected success": delete the decorator then.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import ast
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
import worktree
from test_worktree_gates import (
    SUBAGENT,
    TASK_A,
    TASK_B,
    TASK_C,
    WorkspaceCase,
    bash_payload,
    edit_payload,
    hook,
)

BACKSLASH = chr(92)
QUOTE = chr(34)
WORKTREE_PY = "python .claude/tools/pipeline/worktree.py"


class QaCase(WorkspaceCase):
    """WorkspaceCase plus the two verdicts a dev subagent's call passes through."""

    def subagent_edit(self, path, tool: str = "Write", cwd=None) -> tuple:
        code, _, err = hook(pretool_gate, edit_payload(str(path), str(cwd or self.root), tool,
                                                       **SUBAGENT))
        return code, err

    def subagent_bash(self, command: str, cwd=None, profile: str = "dev") -> tuple:
        """(worst exit code, stderr) of pretool_gate and agent_gate for one Bash call."""
        where = str(cwd or self.root)
        pre = hook(pretool_gate, bash_payload(command, where, **SUBAGENT))
        agent = hook(agent_gate, bash_payload(command, where, **SUBAGENT),
                     ["agent_gate", "--profile", profile])
        return max(pre[0], agent[0]), pre[2] + agent[2]

    def junction(self, name: str, target: Path) -> Path:
        link = self.root / name
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            self.skipTest(f"cannot create a junction here: {done.stderr.strip()}")
        return link

    def set_recorded_tree(self, task: str, value: str) -> None:
        conn = state.connect()
        try:
            conn.execute("UPDATE runs SET worktree = ? WHERE task = ?", (value, task))
            conn.commit()
        finally:
            conn.close()


# --- the review freeze under every spelling of a path ------------------------------------

class ReviewFreezeSpellingTest(QaCase):
    def setUp(self):
        super().setUp()
        self.tree_a = self.add_tree(TASK_A)
        self.tree_b = self.add_tree(TASK_B)
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")
        self.target = self.tree_a / "app" / "main.py"

    def assert_frozen(self, spelling: str, cwd=None) -> None:
        code, err = self.subagent_edit(spelling, cwd=cwd)
        self.assertEqual(2, code, f"{spelling!r} was not frozen")
        self.assertIn(f"{TASK_A} is in the read-only 'review' stage", err)

    def test_the_path_spellings_of_the_reviewed_tree_stay_frozen(self):
        plain = str(self.target)
        spellings = {
            "upper case": plain.upper(),
            "lower case": plain.lower(),
            "forward slashes": plain.replace(BACKSLASH, "/"),
            "msys drive": "/" + plain[0].lower() + plain[2:].replace(BACKSLASH, "/"),
            "dot dot": str(self.tree_a / "app" / ".." / "app" / "main.py"),
            "through the sibling tree": str(self.tree_b / ".." / TASK_A / "app" / "main.py"),
            "stream suffix": plain + "::$DATA",
            "directory stream": str(self.tree_a) + "::$INDEX_ALLOCATION" + BACKSLASH
                                + "app" + BACKSLASH + "main.py",
            "trailing dot on the tree": str(self.tree_a) + "." + BACKSLASH + "app" + BACKSLASH
                                        + "main.py",
            "trailing space on the tree": str(self.tree_a) + " " + BACKSLASH + "app" + BACKSLASH
                                          + "main.py",
            "doubled separators": plain.replace(TASK_A, TASK_A + BACKSLASH * 2),
            "quoted": QUOTE + plain + QUOTE,
        }
        for label, spelling in spellings.items():
            with self.subTest(spelling=label):
                self.assert_frozen(spelling)

    def test_an_extended_length_or_device_prefix_is_refused_to_a_subagent(self):
        plain = str(self.target)
        for label, spelling in (("extended-length", BACKSLASH * 2 + "?" + BACKSLASH + plain),
                                ("device", BACKSLASH * 2 + "." + BACKSLASH + plain),
                                ("extended-length, forward slashes",
                                 "//?/" + plain.replace(BACKSLASH, "/"))):
            with self.subTest(spelling=label):
                code, _ = self.subagent_edit(spelling)
                self.assertEqual(2, code)

    def test_a_junction_onto_the_reviewed_tree_is_frozen_even_for_a_new_file(self):
        link = self.junction("junction-to-a", self.tree_a)

        self.assert_frozen(str(link / "app" / "main.py"))
        self.assert_frozen(str(link / "app" / "brand_new.py"))

    def test_a_junction_onto_the_other_tree_stays_editable(self):
        link = self.junction("junction-to-b", self.tree_b)

        self.assertEqual(0, self.subagent_edit(link / "app" / "main.py")[0])

    def test_the_8_3_short_name_of_the_reviewed_file_is_frozen(self):
        if os.name != "nt":
            self.skipTest("8.3 short names exist on Windows only")
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(self.target), buf, 520):
            self.skipTest("this volume reports no short name")

        self.assert_frozen(buf.value)

    def test_a_relative_path_is_placed_by_the_payload_cwd(self):
        self.assert_frozen("app/main.py", cwd=self.tree_a)
        self.assertEqual(0, self.subagent_edit("app/main.py", cwd=self.tree_b)[0])

    def test_a_relative_path_climbing_into_the_reviewed_tree_is_frozen(self):
        self.assert_frozen(f"../{TASK_A}/app/main.py", cwd=self.tree_b)

    def test_every_editing_tool_is_frozen_inside_the_reviewed_tree(self):
        payloads = {
            "Edit": {"file_path": str(self.target), "new_string": "x"},
            "Write": {"file_path": str(self.target), "content": "x"},
            "MultiEdit": {"file_path": str(self.target), "edits": [{"new_string": "x"}]},
            "NotebookEdit": {"notebook_path": str(self.tree_a / "n.ipynb"), "new_source": "x"},
        }
        for tool, tool_input in payloads.items():
            with self.subTest(tool=tool):
                code, _, err = hook(pretool_gate, {"tool_name": tool, "tool_input": tool_input,
                                                   "cwd": str(self.root), **SUBAGENT})
                self.assertEqual(2, code, err)
                self.assertIn("read-only 'review' stage", err)

    def test_a_sibling_directory_whose_name_starts_like_the_tree_is_not_the_tree(self):
        lookalike = self.root / "src-wt" / (TASK_A + "0") / "app" / "main.py"
        lookalike.parent.mkdir(parents=True)

        self.assertEqual(0, self.subagent_edit(lookalike)[0])

    def test_two_trees_in_review_freeze_only_their_own_edits(self):
        tree_c = self.add_tree(TASK_C)
        self.register(TASK_C, "implement")
        conn = state.connect()
        try:
            state.set_fields(conn, TASK_B, stage="review")
        finally:
            conn.close()

        self.assertEqual(2, self.subagent_edit(self.tree_a / "app" / "main.py")[0])
        self.assertEqual(2, self.subagent_edit(self.tree_b / "app" / "main.py")[0])
        self.assertEqual(0, self.subagent_edit(tree_c / "app" / "main.py")[0])

    def test_a_legacy_review_run_beside_a_tree_review_run_keeps_the_global_freeze(self):
        self.register(TASK_C, "review", tree=False)
        tree_c = self.add_tree(TASK_C)

        for target in (tree_c / "app" / "main.py", self.tree_b / "app" / "main.py"):
            with self.subTest(target=str(target)):
                code, err = self.subagent_edit(target)
                self.assertEqual(2, code)
                self.assertIn("is in the read-only 'review' stage", err)

    def test_a_paused_review_run_freezes_nothing(self):
        conn = state.connect()
        try:
            state.set_fields(conn, TASK_A, stage_status=state.ST_PAUSED)
        finally:
            conn.close()

        self.assertEqual(0, self.subagent_edit(self.target)[0])

    def test_bookkeeping_inside_a_reviewed_tree_stays_editable(self):
        # The same lexical exemption the gate always had: docs/ and .claude/ are not code.
        self.assertEqual(0, self.subagent_edit(self.tree_a / "docs" / "note.md")[0])

    def test_a_run_that_left_review_unfreezes_its_tree(self):
        conn = state.connect()
        try:
            state.set_fields(conn, TASK_A, stage="ready")
        finally:
            conn.close()

        self.assertEqual(0, self.subagent_edit(self.target)[0])

    def test_a_review_run_whose_recorded_tree_is_unreadable_freezes_globally(self):
        # Fail closed (plan 2.1 rule 3): when the recorded tree of the run in review cannot
        # be located (a damaged row), the freeze must not vanish. Today owner_of answers
        # None for every path, so active_review_run skips the run and NOTHING is frozen.
        self.set_recorded_tree(TASK_A, "../escaped")

        code, _ = self.subagent_edit(self.target)

        self.assertEqual(2, code)


# --- the trunk freeze for Edit and Write under every spelling -----------------------------

class TrunkFreezeSpellingTest(QaCase):
    def assert_frozen(self, spelling: str, cwd=None, tool: str = "Write") -> None:
        code, err = self.subagent_edit(spelling, tool=tool, cwd=cwd)
        self.assertEqual(2, code, f"{spelling!r} was not frozen")
        self.assertIn("trunk", err)

    def test_the_path_spellings_of_the_trunk_checkout_are_frozen(self):
        plain = str(self.src / "app" / "main.py")
        spellings = {
            "upper case": plain.upper(),
            "forward slashes": plain.replace(BACKSLASH, "/"),
            "msys drive": "/" + plain[0].lower() + plain[2:].replace(BACKSLASH, "/"),
            "dot dot": str(self.src / "app" / ".." / "app" / "main.py"),
            "stream suffix": plain + "::$DATA",
            "trailing dot on the repo": str(self.src) + "." + BACKSLASH + "app" + BACKSLASH
                                        + "main.py",
            "new file": str(self.src / "app" / "brand_new.py"),
            "repo root itself": str(self.src),
            "quoted": QUOTE + plain + QUOTE,
        }
        for label, spelling in spellings.items():
            with self.subTest(spelling=label):
                self.assert_frozen(spelling)

    def test_a_junction_onto_the_trunk_checkout_is_frozen(self):
        link = self.junction("junction-to-src", self.src)

        self.assert_frozen(str(link / "app" / "main.py"))
        self.assert_frozen(str(link / "app" / "brand_new.py"))

    def test_the_extended_length_and_device_prefixes_are_refused_to_a_subagent(self):
        plain = str(self.src / "app" / "main.py")
        for label, spelling in (("extended-length", BACKSLASH * 2 + "?" + BACKSLASH + plain),
                                ("device", BACKSLASH * 2 + "." + BACKSLASH + plain)):
            with self.subTest(spelling=label):
                self.assertEqual(2, self.subagent_edit(spelling)[0])

    @unittest.expectedFailure
    def test_FINDING_the_main_thread_is_frozen_out_of_the_trunk_in_extended_length_spelling(self):
        # in_trunk_checkout answers False for any UNC-shaped path, and the main thread has no
        # live-harness UNC deny (subagents do), so `\\?\E:\...\src\.claude\x.py` is editable.
        plain = str(self.src / ".claude" / "tools" / "x.py")
        for spelling in (BACKSLASH * 2 + "?" + BACKSLASH + plain,
                         "//?/" + plain.replace(BACKSLASH, "/")):
            with self.subTest(spelling=spelling):
                self.assertEqual(2, self.edit(spelling, orch=True)[0])

    def test_a_relative_path_climbing_out_of_a_worktree_into_the_trunk_is_frozen(self):
        tree = self.add_tree(TASK_A)

        self.assert_frozen("../../src/app/main.py", cwd=tree)

    def test_a_relative_path_with_no_payload_cwd_resolves_against_the_workspace_root(self):
        code, _, err = hook(pretool_gate, {"tool_name": "Write",
                                           "tool_input": {"file_path": "src/app/main.py",
                                                          "content": "x"}, **SUBAGENT})
        self.assertEqual(2, code, err)
        self.assertIn("trunk checkout", err)

    def test_agentry_bookkeeping_inside_the_trunk_is_exempt_in_every_spelling(self):
        plain = str(self.src / ".agentry" / "note.md")
        spellings = {
            "plain": plain,
            "upper case": plain.upper(),
            "forward slashes": plain.replace(BACKSLASH, "/"),
            "dot dot": str(self.src / "app" / ".." / ".agentry" / "note.md"),
        }
        for label, spelling in spellings.items():
            with self.subTest(spelling=label):
                self.assertEqual(0, self.subagent_edit(spelling)[0])

    def test_a_directory_that_only_ends_in_agentry_is_not_the_exemption(self):
        self.assert_frozen(str(self.src / "app" / "x.agentry" / "note.py"))

    def test_the_git_directory_of_the_trunk_is_refused(self):
        self.assertEqual(2, self.subagent_edit(self.src / ".git" / "config")[0])

    def test_a_worktree_linked_to_the_trunk_repo_is_not_the_trunk(self):
        tree = self.add_tree(TASK_A)

        self.assertEqual(0, self.subagent_edit(tree / "app" / "main.py")[0])
        self.assertEqual(0, self.subagent_edit(tree / "app" / "brand_new.py")[0])

    def test_a_tree_checked_out_detached_at_main_is_not_the_trunk(self):
        tree = self.root / "src-wt" / "detached"
        self.git(self.src, "worktree", "add", "-q", "--detach", str(tree), "main")

        self.assertEqual(0, self.subagent_edit(tree / "app" / "main.py")[0])

    def test_turning_the_flag_off_lifts_the_freeze(self):
        self.assertEqual(2, self.subagent_edit(self.src / "app" / "main.py")[0])

        self.set_config(False)

        self.assertEqual(0, self.subagent_edit(self.src / "app" / "main.py")[0])

    def test_the_freeze_follows_main_branch_of_the_config(self):
        # A repository whose HEAD is the configured trunk is frozen; the same repository
        # under another configured trunk name is just a feature checkout.
        self.config.write_text(
            '{"main_branch": "release", "workflow": {"mode": "solo"}, '
            '"worktrees": {"enabled": true, "max_parallel": 2}}', encoding="utf-8")

        self.assertEqual(0, self.subagent_edit(self.src / "app" / "main.py")[0])

    def test_a_repo_with_no_git_directory_is_not_a_trunk_checkout(self):
        plain = self.root / "scratch-repo" / "a.py"
        plain.parent.mkdir()

        self.assertEqual(0, self.subagent_edit(plain)[0])

    def test_a_subagent_cannot_lift_the_freeze_by_moving_head_off_the_trunk(self):
        # The trunk is recognised by HEAD being on main. A dev agent may run
        # `git checkout -b feature/task-N` (a legal branch name, cut from main) in the
        # trunk checkout, after which every edit there is allowed. With the flag on a
        # subagent has no reason to move the trunk checkout's HEAD.
        for command in (f"git -C {self.src.as_posix()} checkout -b feature/task-9400",
                        f"git -C {self.src.as_posix()} switch -c feature/task-9400",
                        f"git -C {self.src.as_posix()} checkout --detach"):
            with self.subTest(command=command):
                self.assertEqual(2, self.subagent_bash(command)[0])


# --- the trunk freeze for shell writes ------------------------------------------------------

class TrunkFreezeShellTest(QaCase):
    def assert_denied(self, command: str, cwd=None) -> None:
        code, err = self.subagent_bash(command, cwd=cwd)
        self.assertEqual(2, code, f"{command!r} was not denied")
        self.assertIn("trunk checkout", err)

    def test_every_redirect_form_into_the_trunk_is_denied(self):
        src = self.src.as_posix()
        for command in (f"echo x > {src}/app/n.py",
                        f"echo x >{src}/app/n.py",
                        f"echo x 1>{src}/app/n.py",
                        f"echo x >> {src}/app/main.py",
                        f"echo x &> {src}/app/n.py",
                        f"echo x >| {src}/app/n.py",
                        f"cat > {src}/app/n.py <<EOF{chr(10)}hello{chr(10)}EOF",
                        f"exec 3> {src}/app/n.py"):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_every_tee_sed_cp_mv_rm_form_into_the_trunk_is_denied(self):
        src = self.src.as_posix()
        root = self.root.as_posix()
        for command in (f"printf x | tee -a {src}/app/n.py",
                        f"tee {root}/tmp/a.txt {src}/app/n.py",
                        f"sed -i s/1/2/ {src}/app/main.py",
                        f"sed --in-place s/1/2/ {src}/app/main.py",
                        f"sed -i.bak s/1/2/ {src}/app/main.py",
                        f"sed -ni s/1/2/p {src}/app/main.py",
                        f"sed -e s/1/2/ -i {src}/app/main.py",
                        f"cp -t {src}/app a b",
                        f"cp -r a {src}/",
                        f"cp a {src}/app/",
                        f"mv {src}/app/main.py {root}/tmp/",
                        f"mv a {src}/app/b",
                        f"rm -rf {src}/app",
                        f"rm -rf {src}/*",
                        f"ln -s a {src}/app/lnk",
                        f"truncate -s 0 {src}/app/main.py",
                        f"chmod +x {src}/app/main.py",
                        f"mkdir {src}/app/sub",
                        f"xargs -I{{}} cp {{}} {src}/app/ < list"):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_a_cd_into_the_trunk_in_every_spelling_places_a_relative_target(self):
        src = self.src.as_posix()
        root = self.root.as_posix()
        for command in (f"cd {src} && echo x > app/n.py",
                        f"cd {src}; echo x > app/n.py",
                        f"(cd {src} && echo x > app/n.py)",
                        f"pushd {src} && echo x > app/n.py",
                        f"cd {root}/tmp && cd ../src && echo x > app/n.py",
                        f"bash -c 'cd {src} && echo x > app/n.py'",
                        f"cd {src} && cp ../a.txt ."):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_a_relative_target_is_placed_by_the_payload_cwd(self):
        for command in ("echo x > app/n.py", "echo x > ./app/n.py", "echo x > ../src/app/n.py",
                        "cp ../a.txt ."):
            with self.subTest(command=command):
                self.assert_denied(command, cwd=self.src)

    def test_a_dev_subagent_and_the_main_thread_are_both_frozen_out(self):
        command = f"echo x > {self.src.as_posix()}/app/n.py"

        self.assertEqual(2, self.bash(command, orch=True)[0])
        self.assertEqual(2, self.subagent_bash(command)[0])

    def test_the_session_cleanup_and_scratch_writes_are_not_frozen(self):
        root = self.root.as_posix()
        for command in ("rm -f nul NUL", f"echo x > {root}/tmp/out.txt",
                        f"echo x > {root}/.agentry/state/note.txt",
                        f"cp a {root}/tmp/b.txt"):
            with self.subTest(command=command):
                self.assertEqual(0, self.bash(command, cwd=self.src)[0])

    def test_reads_of_the_trunk_are_not_writes(self):
        src = self.src.as_posix()
        for command in (f"cat {src}/app/main.py", f"ls {src}/app", f"git -C {src} log --oneline",
                        f"git -C {src} diff", f"grep -r x {src}/app",
                        f"cp {src}/app/main.py {self.root.as_posix()}/tmp/copy.py"):
            with self.subTest(command=command):
                code, err = self.subagent_bash(command)
                self.assertEqual(0, code, err)

    def test_the_merge_runs_from_the_trunk_checkout_unhindered_by_the_freeze(self):
        tree = self.add_tree(TASK_A)
        self.commit(tree, "work")
        self.register(TASK_A, "ready", commit_approved=1)

        code, err = self.bash(f"git -C {self.src.as_posix()} merge --no-ff feature/{TASK_A}")

        self.assertEqual(0, code, err)

    def test_a_write_into_a_task_worktree_by_every_route_is_allowed(self):
        tree = self.add_tree(TASK_A)
        self.register(TASK_A, "implement")
        t = tree.as_posix()

        for command in (f"echo x > {t}/app/n.py", f"sed -i s/1/2/ {t}/app/main.py",
                        f"cd {t} && cp app/main.py app/copy.py",
                        f"cd {t}/app && echo x > ../n.py"):
            with self.subTest(command=command):
                self.assertEqual(0, self.subagent_bash(command)[0], command)

    def test_a_variable_at_the_end_of_a_trunk_path_does_not_hide_the_write(self):
        # The literal prefix already sits in the trunk checkout; only the last word is a
        # shell expansion. Any `$` makes the freeze skip the whole operand today.
        src = self.src.as_posix()
        undenied = [c for c in (f"for f in a b; do echo x > {src}/app/$f; done",
                                f"echo x > {src}/app/$(echo n).py",
                                f"cp a {src}/app/$NAME")
                    if self.subagent_bash(c)[0] != 2]
        self.assertEqual([], undenied)

    def test_the_common_file_writers_beyond_the_shared_list_are_frozen_too(self):
        # The freeze reuses the writer list of the plan gate (rm mv cp tee truncate ln
        # chmod mkdir, sed -i, redirects). These write trunk files as well.
        src = self.src.as_posix()
        undenied = [c for c in (f"touch {src}/app/x",
                                f"install -m 644 a {src}/app/b.py",
                                f"dd if=a of={src}/app/main.py",
                                f"rmdir {src}/app",
                                f"unlink {src}/app/main.py",
                                f"find {src} -name main.py -delete",
                                f"patch -p1 -d {src} < x.diff",
                                f"git -C {src} checkout -- app/main.py",
                                f"git -C {src} apply x.patch")
                    if self.subagent_bash(c)[0] != 2]
        self.assertEqual([], undenied)


# --- merge condition 4 -----------------------------------------------------------------------

class MergeFreshnessSpellingTest(QaCase):
    def setUp(self):
        super().setUp()
        self.tree = self.add_tree(TASK_A)
        self.commit(self.tree, "task work")
        self.branch = f"feature/{TASK_A}"
        self.src_path = self.src.as_posix()
        self.register(TASK_A, "ready", commit_approved=1)

    def verdict(self, command: str, cwd=None, subagent: bool = False) -> tuple:
        extra = SUBAGENT if subagent else {}
        out = hook(pretool_gate, bash_payload(command, str(cwd or self.root), **extra))
        return out[0], out[2]

    def stale_trunk(self, commits: int = 1) -> None:
        for n in range(commits):
            self.commit(self.src, f"sibling {n}")

    def test_every_merge_and_pull_spelling_is_refused_while_the_trunk_has_moved(self):
        self.stale_trunk()
        for tail in ("merge --no-ff {b}", "merge {b}", "merge --ff-only {b}", "merge --squash {b}",
                     "merge --no-ff --no-edit {b}", "merge -m msg {b}", "merge -s ours {b}",
                     "merge -X theirs {b}", "merge --no-ff -- {b}", "merge {b} {b}",
                     "pull . {b}", "pull --no-ff . {b}", "pull --rebase . {b}"):
            command = f"git -C {self.src_path} " + tail.format(b=self.branch)
            with self.subTest(command=tail):
                code, err = self.verdict(command)
                self.assertEqual(2, code, command)
                self.assertIn("condition 4 failed (freshness)", err)

    def test_every_merge_spelling_is_allowed_when_the_branch_holds_the_trunk(self):
        for tail in ("merge --no-ff {b}", "merge {b}", "merge --ff-only {b}", "merge --squash {b}",
                     "merge -m msg {b}", "merge --no-ff -- {b}", "pull --no-ff . {b}"):
            command = f"git -C {self.src_path} " + tail.format(b=self.branch)
            with self.subTest(command=tail):
                code, err = self.verdict(command)
                self.assertEqual(0, code, err)

    def test_one_commit_behind_is_enough_to_refuse(self):
        self.stale_trunk(1)

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")

        self.assertEqual(2, code)
        self.assertIn(worktree.stale_message(1), err)

    def test_the_count_names_every_trunk_commit_the_branch_lacks(self):
        self.stale_trunk(3)

        _, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")

        self.assertIn(worktree.stale_message(3), err)

    def test_after_a_real_sibling_merge_the_second_task_is_stale_by_that_merge(self):
        tree_b = self.add_tree(TASK_B)
        self.commit(tree_b, "second task work")
        self.register(TASK_B, "ready", commit_approved=1)
        self.git(self.src, "merge", "-q", "--no-ff", "-m", "merge a", self.branch)

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff feature/{TASK_B}")

        self.assertEqual(2, code)
        self.assertIn(worktree.stale_message(2), err)

    def test_a_rebase_onto_main_in_the_worktree_also_satisfies_the_condition(self):
        self.stale_trunk()
        self.assertEqual(2, self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")[0])

        self.git(self.tree, "rebase", "-q", "main")

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")
        self.assertEqual(0, code, err)

    def test_two_sources_are_each_held_to_their_own_conditions(self):
        tree_b = self.add_tree(TASK_B)
        self.commit(tree_b, "second task work")
        self.register(TASK_B, "ready", commit_approved=0)

        code, err = self.verdict(
            f"git -C {self.src_path} merge {self.branch} feature/{TASK_B}")

        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)

    def test_the_order_of_the_conditions_is_one_two_three_then_four(self):
        self.stale_trunk()
        conn = state.connect()
        try:
            state.set_fields(conn, TASK_A, commit_approved=0)
        finally:
            conn.close()

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")

        self.assertEqual(2, code)
        self.assertIn("condition 3 of 3 failed", err)
        self.assertNotIn("condition 4", err)

    def test_the_verdict_does_not_depend_on_the_tree_directory_still_existing(self):
        self.stale_trunk()
        self.git(self.src, "worktree", "remove", "--force", str(self.tree))
        self.assertFalse(self.tree.exists())

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")

        self.assertEqual(2, code)
        self.assertIn("condition 4 failed (freshness)", err)

    def test_a_freshness_that_cannot_be_read_refuses_instead_of_guessing(self):
        self.git(self.src, "worktree", "remove", "--force", str(self.tree))
        self.git(self.src, "branch", "-D", self.branch)

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")

        self.assertEqual(2, code)
        self.assertIn("could not be read", err)

    def test_a_subagent_is_held_to_the_same_condition(self):
        self.stale_trunk()

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}",
                                 subagent=True)

        self.assertEqual(2, code)
        self.assertIn("condition 4", err)

    def test_the_repository_is_placed_by_cd_and_by_cwd_not_only_by_dash_c(self):
        self.stale_trunk()
        for command, cwd in ((f"cd {self.src_path} && git merge {self.branch}", None),
                             (f"git merge {self.branch}", self.src),
                             (f"git merge --no-ff {self.branch}", self.src)):
            with self.subTest(command=command):
                code, err = self.verdict(command, cwd=cwd)
                self.assertEqual(2, code)
                self.assertIn(worktree.stale_message(1), err)

    def test_a_fresh_branch_merges_from_the_cwd_and_after_a_cd_alike(self):
        for command, cwd in ((f"cd {self.src_path} && git merge --no-ff {self.branch}", None),
                             (f"git merge --no-ff {self.branch}", self.src)):
            with self.subTest(command=command):
                code, err = self.verdict(command, cwd=cwd)
                self.assertEqual(0, code, err)

    def test_a_merge_of_main_into_the_task_branch_is_the_sync_and_is_not_blocked(self):
        self.stale_trunk()

        code, err = self.verdict(f"git -C {self.tree.as_posix()} merge main")

        self.assertEqual(0, code, err)

    def test_the_message_names_the_sync_command_exactly_once_and_no_other_text_is_lost(self):
        self.stale_trunk()

        _, err = self.verdict(f"git -C {self.src_path} merge --no-ff {self.branch}")

        self.assertEqual(1, err.count("`git merge main` in the worktree"))
        self.assertIn("Merge into protected branch 'main' refused", err)

    def test_a_legacy_run_with_a_stale_branch_is_not_asked_for_freshness(self):
        self.git(self.src, "branch", f"feature/{TASK_C}", "main")
        self.stale_trunk()
        self.register(TASK_C, "ready", tree=False, commit_approved=1)

        code, err = self.verdict(f"git -C {self.src_path} merge --no-ff feature/{TASK_C}")

        self.assertEqual(0, code, err)


# --- placement of commit and branch checks with two live trees ---------------------------------

class PlacementTwoTreesTest(QaCase):
    def setUp(self):
        super().setUp()
        self.tree_a = self.add_tree(TASK_A)
        self.tree_b = self.add_tree(TASK_B)
        self.register(TASK_A, "ready", commit_approved=1)
        self.register(TASK_B, "implement", commit_approved=0)
        self.a = self.tree_a.as_posix()
        self.b = self.tree_b.as_posix()

    def verdict(self, command: str, cwd=None) -> tuple:
        out = hook(pretool_gate, bash_payload(command, str(cwd or self.root)))
        return out[0], out[2]

    def test_dash_c_wins_over_the_payload_cwd_in_both_directions(self):
        self.assertEqual(0, self.verdict(f"git -C {self.a} commit -m x", cwd=self.tree_b)[0])
        code, err = self.verdict(f"git -C {self.b} commit -m x", cwd=self.tree_a)
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_dash_c_wins_over_an_earlier_cd(self):
        self.assertEqual(0, self.verdict(f"cd {self.b} && git -C {self.a} commit -m x")[0])
        code, err = self.verdict(f"cd {self.a} && git -C {self.b} commit -m x")
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_a_relative_dash_c_and_a_relative_cd_are_placed_from_the_payload_cwd(self):
        for command, cwd in ((f"git -C ../{TASK_B} commit -m x", self.tree_a),
                             (f"cd ../{TASK_B} && git commit -m x", self.tree_a),
                             (f"cd src-wt/{TASK_B} && git commit -m x", self.root)):
            with self.subTest(command=command):
                code, err = self.verdict(command, cwd=cwd)
                self.assertEqual(2, code)
                self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_the_last_cd_before_git_wins_when_two_cds_precede_it(self):
        self.assertEqual(0, self.verdict(f"cd {self.b} && cd {self.a} && git commit -m x")[0])
        code, err = self.verdict(f"cd {self.a} && cd {self.b} && git commit -m x")
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)

    def test_a_semicolon_and_a_quoted_cd_place_the_commit_too(self):
        code, err = self.verdict(f"cd {self.b}; git commit -m x")
        self.assertEqual(2, code)
        self.assertIn(f"Commit for {TASK_B} is not approved", err)
        code, err = self.verdict(f"cd {QUOTE}{self.b}{QUOTE} && git commit -m x")
        self.assertEqual(2, code)

    def test_a_commit_the_gate_cannot_place_is_refused_not_guessed(self):
        # A subshell, pushd, bash -c or --git-dir/--work-tree hides the tree: the commit
        # then falls back to the workspace root and is refused as "not on a feature branch".
        for command in (f"(cd {self.b} && git commit -m x)",
                        f"pushd {self.b} && git commit -m x",
                        f"bash -c {chr(39)}cd {self.b} && git commit -m x{chr(39)}",
                        f"git --work-tree={self.b} commit -m x",
                        f"GIT_DIR={self.b}/.git git commit -m x"):
            with self.subTest(command=command):
                self.assertEqual(2, self.verdict(command)[0])

    def test_the_commit_tag_of_the_message_overrides_the_tree_of_the_cwd(self):
        # Tree A is approved, tree B is not: a message tagged for B from A's cwd is B's commit.
        code, err = self.verdict(f'git commit -m "[{TASK_B}] feat: x"', cwd=self.tree_a)
        self.assertEqual(2, code)
        self.assertIn(f"This commit is tagged [{TASK_B}] but the checked-out branch", err)

    def test_a_second_commit_in_the_same_command_is_judged_by_its_own_tree(self):
        # The placement is read once, from the first -C or the last cd before the first
        # `git`. A command that commits in the approved tree and then in the unapproved one
        # (untagged message) is judged entirely as the approved task.
        undenied = [c for c in (
            f"cd {self.a} && git commit -m a && cd {self.b} && git commit -m b",
            f"git -C {self.a} commit -m a && git -C {self.b} commit -m b",
            f"cd {self.a}; git commit -m a; cd {self.b}; git commit -m b")
            if self.verdict(c)[0] != 2]
        self.assertEqual([], undenied)

    def test_the_unapproved_tree_first_is_refused_whatever_follows(self):
        for command in (f"cd {self.b} && git commit -m b && cd {self.a} && git commit -m a",
                        f"git -C {self.b} commit -m b && git -C {self.a} commit -m a"):
            with self.subTest(command=command):
                self.assertEqual(2, self.verdict(command)[0])


class WorktreeAddNamingTest(QaCase):
    def add_command(self, flags: str, name: str, start: str = "main") -> str:
        return (f"git -C {self.src.as_posix()} worktree add {flags} {name} "
                f"{self.root.as_posix()}/src-wt/task-9310 {start}").replace("  ", " ")

    def test_a_bad_name_is_refused_in_every_separate_option_spelling(self):
        for command in (self.add_command("-b", "badname"),
                        self.add_command("-B", "badname"),
                        self.add_command("-b", "hotfix/oops"),
                        self.add_command("--lock -b", "badname"),
                        self.add_command("--force -B", "badname"),
                        self.add_command("-f -b", "badname"),
                        (f"git -C {self.src.as_posix()} worktree add --orphan -b badname "
                         f"{self.root.as_posix()}/src-wt/x")):
            with self.subTest(command=command):
                code, err = self.bash(command)
                self.assertEqual(2, code)
                self.assertIn("breaks the naming rule", err)

    def test_a_good_name_from_the_trunk_is_allowed_in_every_start_point_spelling(self):
        for start in ("main", "origin/main", "refs/heads/main", ""):
            with self.subTest(start=start):
                command = self.add_command("-b", "feature/task-9310", start)
                self.assertEqual(0, self.bash(command)[0], command)

    def test_a_start_point_that_is_not_the_trunk_is_refused(self):
        for start in ("develop", "HEAD", "main~1", f"feature/{TASK_A}"):
            with self.subTest(start=start):
                code, err = self.bash(self.add_command("-b", "feature/task-9310", start))
                self.assertEqual(2, code)
                self.assertIn("must be cut from up-to-date 'main'", err)

    def test_checkout_of_an_existing_branch_creates_no_name_to_judge(self):
        command = f"git -C {self.src.as_posix()} worktree add {self.root.as_posix()}/src-wt/x main"
        self.assertEqual(0, self.bash(command)[0])

    def test_bundled_short_options_do_not_hide_the_branch_name(self):
        # git bundles short options: `-fb NAME` and `-qb NAME` create NAME (measured).
        # new_branch_name looks for the exact tokens -b and -B only, so the naming and base
        # rules never see the name. The same gap exists for `git checkout -fb`.
        undenied = [c for c in (self.add_command("-fb", "badname"),
                                self.add_command("-qb", "badname"),
                                self.add_command("-fB", "badname"))
                    if self.bash(c)[0] != 2]
        self.assertEqual([], undenied)


# --- the dev layout denies in every spelling ----------------------------------------------------

class LayoutDenySpellingTest(QaCase):
    def assert_denied(self, command: str, profile: str = "dev") -> None:
        code, err = self.subagent_bash(command, profile=profile)
        self.assertEqual(2, code, f"{command!r} was not denied")
        self.assertIn("worktree layout", err)

    def test_git_is_found_under_every_wrapper_and_spelling(self):
        for command in ("git.exe worktree remove x",
                        "GIT_TRACE=1 git worktree remove x",
                        "/usr/bin/git worktree remove x",
                        QUOTE + "C:/Program Files/Git/cmd/git.exe" + QUOTE + " worktree remove x",
                        "env git worktree remove x",
                        "command git worktree remove x",
                        "exec git worktree remove x",
                        "time git worktree remove x",
                        "nice git worktree remove x",
                        "echo x | xargs git worktree remove",
                        "git -c core.x=1 worktree remove x",
                        "git --no-pager worktree remove x",
                        "git --git-dir=src/.git worktree remove x",
                        "git -C src -c advice.x=1 worktree remove x",
                        "git  worktree   remove x",
                        "git worktree 'remove' x",
                        "git " + QUOTE + "worktree" + QUOTE + " remove x",
                        "git worktree REMOVE x",
                        "git WORKTREE remove x"):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_git_inside_a_compound_or_nested_command_is_found(self):
        for command in ("eval 'git worktree remove x'",
                        "eval git worktree remove x",
                        "for t in a b; do git worktree remove $t; done",
                        "if true; then git worktree remove x; fi",
                        "git worktree remove x || true",
                        "(git worktree remove x)",
                        "{ git worktree remove x; }",
                        "bash <<EOF" + chr(10) + "git worktree remove x" + chr(10) + "EOF",
                        "echo $(git worktree remove x)",
                        "bash -c " + chr(39) + "sh -c " + QUOTE + "git worktree prune" + QUOTE
                        + chr(39)):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_every_writer_subcommand_is_denied_for_all_three_profiles(self):
        for profile in ("dev", "readonly", "docs"):
            for sub in ("add", "remove", "move", "prune", "repair", "lock", "unlock"):
                with self.subTest(profile=profile, sub=sub):
                    self.assert_denied(f"git worktree {sub} a b", profile=profile)

    def test_worktree_py_is_found_in_every_interpreter_and_path_spelling(self):
        for command in (f"{WORKTREE_PY} --remove --task t",
                        f"{WORKTREE_PY} --remove=task-9301",
                        f"{WORKTREE_PY} --repair --task t",
                        f"{WORKTREE_PY} --task t --remove",
                        f"{WORKTREE_PY} --sweep --repair --task t",
                        "python3 .claude/tools/pipeline/worktree.py --remove --task t",
                        "py -3 .claude/tools/pipeline/worktree.py --remove --task t",
                        "python E:/Personal/Agenty/.claude/tools/pipeline/worktree.py --remove --task t",
                        "python ./worktree.py --remove --task t",
                        "python -m worktree --remove --task t",
                        "python -m pipeline.worktree --remove --task t",
                        "PYTHONPATH=.claude/tools/pipeline python -m worktree --remove --task t",
                        "cd .claude/tools/pipeline && python worktree.py --remove --task t",
                        f"({WORKTREE_PY} --remove --task t)",
                        f"bash -c {chr(39)}{WORKTREE_PY} --remove --task t{chr(39)}",
                        f"echo t | xargs {WORKTREE_PY} --remove --task"):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_every_argparse_abbreviation_of_the_two_writing_options_is_denied(self):
        for option in ("--re", "--rem", "--remo", "--remov", "--rep", "--repa", "--repai"):
            with self.subTest(option=option):
                self.assert_denied(f"{WORKTREE_PY} {option} --task t")

    def test_the_reading_options_and_unrelated_text_stay_open(self):
        for command in (f"{WORKTREE_PY} --list", f"{WORKTREE_PY} --sweep",
                        f"{WORKTREE_PY} --sweep --task t", f"{WORKTREE_PY} --lis",
                        "git worktree list", "git worktree list --porcelain",
                        "git -C src worktree list",
                        "cat .claude/tools/pipeline/worktree.py",
                        "grep -n remove .claude/tools/pipeline/worktree.py",
                        "echo " + QUOTE + "worktree.py --remove is the orchestrator's" + QUOTE,
                        "git log --oneline -- .claude/tools/pipeline/worktree.py",
                        "git status"):
            with self.subTest(command=command):
                self.assertEqual(0, self.subagent_bash(command)[0], command)

    def test_the_orchestrator_main_thread_is_not_bound_by_the_agent_profile(self):
        for command in (f"{WORKTREE_PY} --remove --task t", "git worktree prune"):
            with self.subTest(command=command):
                out = hook(pretool_gate, bash_payload(command, str(self.root)))
                self.assertEqual(0, out[0], out[2])

    def test_a_deny_does_not_depend_on_the_working_directory(self):
        tree = self.add_tree(TASK_A)

        for cwd in (self.root, self.src, tree):
            with self.subTest(cwd=str(cwd)):
                code, _ = self.subagent_bash("git worktree remove x", cwd=cwd)
                self.assertEqual(2, code)

    def test_a_reader_profile_cannot_read_the_worktree_listing_either(self):
        # Pre-existing default-deny of the readonly and docs profiles; pinned because the
        # layout deny must not be the only thing between those profiles and `worktree`.
        for profile in ("readonly", "docs"):
            with self.subTest(profile=profile):
                self.assertEqual(2, self.subagent_bash("git worktree list", profile=profile)[0])


# --- flag off ------------------------------------------------------------------------------------

class FlagOffCorpusTest(QaCase):
    """Nothing registered, flag off (false, absent, not a boolean): none of the new checks
    may change a single verdict. The corpus holds only calls the gates allowed before
    epic-0004 (checked against the pre-change gates by a differential run, see qa-gates.md)."""

    ENABLED = False

    def edits(self) -> list:
        return [self.src / "app" / "main.py", self.src / "app" / "brand_new.py",
                self.src / ".claude" / "tools" / "x.py", self.src / ".agentry" / "n.md",
                self.root / "elsewhere.py", self.root / ".agentry" / "tasks" / "x.md",
                self.src / "docs" / "a.md", self.src / "README.md"]

    def commands(self) -> list:
        src, root = self.src.as_posix(), self.root.as_posix()
        return ["ls", "echo hi", f"echo x > {src}/app/n.py", f"cp a {src}/app/",
                f"sed -i s/1/2/ {src}/app/main.py", f"rm {src}/app/main.py",
                f"cd {src} && echo x > app/n.py", f"git -C {src} status",
                f"git -C {src} worktree list", f"git -C {src} checkout -b feature/task-9310",
                f"git -C {src} worktree add {root}/src-wt/task-9310 main",
                f"git -C {src} worktree add -b feature/task-9310 {root}/src-wt/task-9310 main",
                "git worktree remove x", "git worktree prune", "git worktree lock x",
                "git worktree move a b", "git worktree repair",
                f"{WORKTREE_PY} --remove --task t", f"{WORKTREE_PY} --repair --task t",
                f"{WORKTREE_PY} --list", "python -m worktree --sweep",
                f"echo x > {root}/tmp/a.txt", "rm -f nul NUL"]

    def check_dark(self):
        for path in self.edits():
            for tool in ("Write", "Edit"):
                with self.subTest(path=str(path), tool=tool):
                    self.assertEqual(0, self.subagent_edit(path, tool=tool)[0])
        for command in self.commands():
            with self.subTest(command=command):
                self.assertEqual(0, self.subagent_bash(command)[0], command)

    def test_with_the_flag_false(self):
        self.check_dark()

    def test_with_no_worktrees_block(self):
        self.set_config(None)
        self.check_dark()

    def test_with_a_non_boolean_flag(self):
        for value in ('"true"', "1", "null", '"yes"', "[]"):
            with self.subTest(value=value):
                self.config.write_text(
                    '{"main_branch": "main", "workflow": {"mode": "solo"}, "worktrees": '
                    '{"enabled": ' + value + "}}", encoding="utf-8")
                self.check_dark()

    def test_with_an_unreadable_pipeline_config(self):
        self.config.write_text("{ not json", encoding="utf-8")
        self.assertFalse(pretool_gate.worktrees_on())
        self.assertEqual(0, self.subagent_edit(self.src / "app" / "main.py")[0])

    def test_with_the_module_missing_and_the_flag_off_the_gates_behave_as_before(self):
        with unittest.mock.patch.object(pretool_gate, "worktree_mod", return_value=None):
            self.assertEqual(0, self.subagent_edit(self.src / "app" / "main.py")[0])
            self.assertEqual(0, self.subagent_bash(f"echo x > {self.src.as_posix()}/app/n.py")[0])
            self.assertEqual(0, self.subagent_bash("git worktree remove x")[0])

    def test_with_the_module_missing_and_the_flag_on_a_subagent_is_refused_not_waved_through(self):
        # The config says on, the module that would answer is gone: that is doubt, and doubt
        # reads against the subagent. The main thread is the only caller that can repair
        # the module, so it is never bricked by it.
        self.set_config(True)
        with unittest.mock.patch.object(pretool_gate, "worktree_mod", return_value=None):
            self.assertEqual(2, self.subagent_edit(self.src / "app" / "main.py")[0])
            self.assertEqual(2, self.subagent_bash(f"echo x > {self.src.as_posix()}/app/n.py")[0])
            self.assertEqual(2, self.subagent_bash("git worktree remove x")[0])
            self.assertEqual(0, self.edit(self.src / "app" / "main.py", orch=True)[0])
            self.assertEqual(0, self.bash(f"echo x > {self.src.as_posix()}/app/n.py")[0])

    def test_a_recorded_tree_with_the_flag_off_keeps_the_per_task_freeze_only(self):
        # Rollback semantics (board, C1): the recorded tree drives the review freeze, the
        # freshness condition and the layout denies (while its run is not done); the trunk
        # freeze follows the flag.
        tree = self.add_tree(TASK_A)
        other = self.add_tree(TASK_B)
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")

        self.assertEqual(2, self.subagent_edit(tree / "app" / "main.py")[0])
        self.assertEqual(0, self.subagent_edit(other / "app" / "main.py")[0])
        self.assertEqual(0, self.subagent_edit(self.src / "app" / "main.py")[0])
        self.assertEqual(2, self.subagent_bash("git worktree remove x")[0])


# --- R4: the read-only checks hold no subprocess ---------------------------------------------------

class NoSubprocessTest(QaCase):
    NEW_PRETOOL = ("worktree_mod", "worktrees_on", "owns_path", "in_trunk_checkout",
                   "trunk_bash_write", "trunk_freeze_deny", "active_review_run",
                   "worktree_add_branch")
    NEW_AGENT = ("worktree_layout_change",)

    def functions(self, module: str, names: tuple) -> list:
        tree = ast.parse((PIPELINE_DIR / module).read_text(encoding="utf-8"))
        found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(sorted(names), sorted(n.name for n in found))
        return found

    def test_the_new_functions_never_call_a_process_api(self):
        banned = {"run", "Popen", "call", "check_output", "check_call", "system", "popen",
                  "git_run", "spawnl", "spawnv", "startfile"}
        for module, names in (("pretool_gate.py", self.NEW_PRETOOL),
                              ("agent_gate.py", self.NEW_AGENT)):
            for func in self.functions(module, names):
                for node in ast.walk(func):
                    if isinstance(node, ast.Call):
                        callee = node.func
                        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(
                            callee, "id", "")
                        with self.subTest(function=func.name, call=name):
                            self.assertNotIn(name, banned)

    def test_the_whole_flag_on_edit_and_bash_matrix_starts_no_process(self):
        tree = self.add_tree(TASK_A)
        other = self.add_tree(TASK_B)
        self.register(TASK_A, "review")
        self.register(TASK_B, "implement")
        boom = AssertionError("a gate check started a process")
        targets = [self.src / "app" / "main.py", tree / "app" / "main.py",
                   other / "app" / "main.py", self.root / "elsewhere" / "a.py"]
        with unittest.mock.patch.object(subprocess, "Popen", side_effect=boom), \
                unittest.mock.patch.object(subprocess, "run", side_effect=boom), \
                unittest.mock.patch.object(os, "system", side_effect=boom), \
                unittest.mock.patch.object(pretool_gate, "git_run", side_effect=boom):
            for path in targets:
                self.subagent_edit(path)
            for command in (f"echo x > {self.src.as_posix()}/a.py", "echo x > a.py",
                            f"cd {tree.as_posix()} && sed -i s/1/2/ app/main.py",
                            "git worktree remove x", f"{WORKTREE_PY} --remove --task t"):
                hook(agent_gate, bash_payload(command, str(self.root), **SUBAGENT),
                     ["agent_gate", "--profile", "dev"])
                self.bash(command)

    def test_the_freshness_check_is_the_one_git_call_and_it_is_not_made_for_a_legacy_task(self):
        self.git(self.src, "branch", f"feature/{TASK_C}", "main")
        self.register(TASK_C, "ready", tree=False, commit_approved=1)
        calls = []
        real = pretool_gate.git_run

        def spy(argv, repo):
            calls.append(argv)
            return real(argv, repo)

        with unittest.mock.patch.object(pretool_gate, "git_run", spy):
            self.bash(f"git -C {self.src.as_posix()} merge --no-ff feature/{TASK_C}")

        self.assertEqual([], [a for a in calls if "rev-list" in a])

    def test_a_stalled_ownership_lookup_is_the_watchdog_s_problem_not_a_silent_allow(self):
        # owner_of failing is read as "frozen" (global freeze), never as "free".
        self.register(TASK_A, "review")
        tree = self.add_tree(TASK_A)
        with unittest.mock.patch.object(worktree, "owner_of", side_effect=OSError("disk")):
            self.assertEqual(2, self.subagent_edit(tree / "app" / "main.py")[0])


if __name__ == "__main__":
    unittest.main()
