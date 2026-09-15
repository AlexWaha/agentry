"""The handoff chain gets three size contracts, and none of them may brick the pipeline.

FR-32 (a byte cap on a handoff doc), FR-33 (inject the newest in full and the
rest as index lines), FR-34 (an epic thread doc capped at 4096 bytes), contract
C-5 for the config keys.

Three things these tests exist to pin, in order of how much a mistake costs:

1. **Fail open.** `handoff.py` is read by `advance.py`, `stop_gate.py` AND
   `pretool_gate.py`. A cap that rejects on a config typo would refuse every
   registration and freeze every edit at once, and the conveyor is what you
   would need to fix it with. Every unusable config value is asserted to
   DISABLE the cap, not to reject the document - including the ones nothing
   else reaches, because an untested fail-open branch is a branch that gets
   deleted by a refactor without anyone noticing.
2. **The exemption really exempts.** The CEO's decision of 2026-09-15 was that
   the cap binds new documents only; the 18 already over it are grandfathered.
   `test_an_exempt_task_passes_while_an_identical_one_fails` is that decision
   encoded: same bytes, same cap, opposite verdicts, the exempt list the only
   difference.
3. **Sizes are real bytes on disk.** Every document in these tests is padded to
   an exact `os.path.getsize`, never approximated, because the boundary is what
   the acceptance criteria name (6200 rejected, 6100 passed).
"""

from __future__ import annotations

import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from typing import ClassVar

TESTS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = TESTS_DIR.parent / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(TESTS_DIR))

import handoff  # noqa: E402 - path set above
import tmproot  # noqa: E402 - arms the temp-file policy, see its docstring

# A valid doc: every required section, past both thin-checks, no scaffold
# marker. The {pad} slot is what makes the file an exact size.
VALID = """---
task: {task}
title: {title}
date: 2026-09-01
---

# Handoff: {task}

## What was done
The work landed and the behaviour it changed is described here at length. {pad}

## Key decisions
The alternatives were weighed and the chosen one is recorded with its reason.

## Files touched
- one file, with a sentence saying what it is for and why it moved.

## Gotchas and lessons
The surprise that cost a review round, stated so it is not rediscovered.

## Impact on next tasks
What the next assignee can build on instead of reinventing it from scratch.

## Context loaded
- .agentry/project/project-context.md - the harness is its own first adopter.
- spec for my upcoming task (spec-0001) - the cap is raw file bytes.
- .agentry/tasks/done/{task}.md and its diff on main - it shipped as described.
"""

THREAD = """---
epic: {epic}
---

## Where the chain stands
Phase 1 is in flight and the tasks around it are listed here. {pad}

## What is decided
The decisions taken so far, each with the date it was taken on.

## What remains
The phases still to land, named so the next agent can pick one up.
"""


def write_sized(path: Path, template: str, size: int, **fields) -> Path:
    """Write `template` padded to EXACTLY `size` bytes on disk.

    Exact rather than approximate: these tests assert on both sides of a
    boundary, and "about 6200 bytes" cannot tell a working cap from an
    off-by-one. ASCII padding, so one character is one byte.

    `newline="\\n"` is load-bearing on Windows and was found by running this:
    the default translates every \\n to \\r\\n on write, so the first version of
    these tests asked for 4200 bytes and put 4212 on disk. The cap counts bytes
    from `os.path.getsize`, so the file has to be written with the byte count
    the test intends."""
    body = template.format(pad="", **fields)
    pad = size - len(body.encode("utf-8"))
    if pad < 0:
        raise AssertionError(f"template already exceeds {size} bytes")
    path.write_text(template.format(pad="x" * pad, **fields),
                    encoding="utf-8", newline="\n")
    actual = os.path.getsize(path)
    if actual != size:
        raise AssertionError(f"wrote {actual} bytes, wanted {size}")
    return path


class CapTest(unittest.TestCase):
    """FR-32: the byte cap, both sides of the boundary the AC names."""

    CAP: ClassVar[dict] = {"enabled": True, "min_section_chars": 40,
                           "min_total_chars": 500, "max_total_bytes": 6144}

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "handoff-cap")

    def doc(self, size: int, task: str = "task-0100") -> Path:
        return write_sized(self.tmp / f"{task}.md", VALID, size,
                           task=task, title="A handoff document")

    def check(self, path: Path, task: str = "task-0100", **overrides) -> list[str]:
        config = dict(self.CAP, **overrides)
        with unittest.mock.patch.object(handoff, "cfg", return_value=config):
            return handoff.validate_handoff(path, task)

    def test_a_6200_byte_document_is_rejected(self):
        problems = self.check(self.doc(6200))
        self.assertEqual(1, len(problems), problems)
        self.assertIn("6200", problems[0])
        self.assertIn("6144", problems[0])

    def test_a_6100_byte_document_passes(self):
        self.assertEqual([], self.check(self.doc(6100)))

    def test_the_boundary_itself_passes(self):
        # "larger than 6144" in FR-32, so 6144 exactly is not larger.
        self.assertEqual([], self.check(self.doc(6144)))

    def test_one_byte_over_the_boundary_is_rejected(self):
        self.assertNotEqual([], self.check(self.doc(6145)))

    def test_an_exempt_task_passes_while_an_identical_one_fails(self):
        # The CEO decision of 2026-09-15, encoded: the cap applies to new
        # documents only. Same byte count, same cap; the exempt list is the
        # only difference between the two verdicts.
        old = self.doc(9000, task="task-0081")
        new = self.doc(9000, task="task-0100")
        self.assertEqual([], self.check(old, task="task-0081",
                                        max_bytes_exempt=["task-0081"]))
        problems = self.check(new, task="task-0100", max_bytes_exempt=["task-0081"])
        self.assertEqual(1, len(problems), problems)
        # The size, not just "some problem": an unrelated future failure would
        # otherwise keep this green while the cap silently stopped working.
        self.assertIn("9000", problems[0])
        self.assertIn("6144", problems[0])

    def test_the_rejection_names_the_way_out(self):
        problems = self.check(self.doc(6200))
        self.assertIn("max_bytes_exempt", problems[0])

    def test_the_cap_does_not_mask_the_other_checks(self):
        thin = self.tmp / "task-0100.md"
        thin.write_text("---\ntask: task-0100\n---\n\n## What was done\nx\n",
                        encoding="utf-8")
        problems = self.check(thin)
        self.assertTrue(any("missing section" in p for p in problems), problems)


class CapFailsOpenTest(unittest.TestCase):
    """NFR-4 and the Definition of Done: an unusable cap disables the cap.

    Each value here is a separate test rather than a loop, so a failure names
    the value that broke rather than "subTest 3". The document is 9000 bytes in
    every one of them: well over any plausible cap, so a passing result can
    only mean the cap was disabled."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "handoff-open")
        self.path = write_sized(self.tmp / "task-0100.md", VALID, 9000,
                                task="task-0100", title="Oversized on purpose")

    def check(self, **config) -> list[str]:
        base = {"enabled": True, "min_section_chars": 40, "min_total_chars": 500}
        with unittest.mock.patch.object(handoff, "cfg", return_value=dict(base, **config)):
            return handoff.validate_handoff(self.path, "task-0100")

    def test_a_missing_key_disables_the_cap(self):
        self.assertEqual([], self.check())

    def test_a_null_cap_disables_the_cap(self):
        self.assertEqual([], self.check(max_total_bytes=None))

    def test_a_string_cap_disables_the_cap(self):
        # int("6144") would have worked and int("6 KB") would have raised out
        # of validate_handoff into uncovered_done_tasks, which reports "no
        # debt" - switching the whole handoff gate off on a typo.
        self.assertEqual([], self.check(max_total_bytes="6 KB"))
        self.assertEqual([], self.check(max_total_bytes="6144"))

    def test_a_zero_or_negative_cap_disables_the_cap(self):
        self.assertEqual([], self.check(max_total_bytes=0))
        self.assertEqual([], self.check(max_total_bytes=-1))

    def test_a_boolean_cap_disables_the_cap(self):
        # True is an int to Python and would otherwise mean a one-byte cap.
        self.assertEqual([], self.check(max_total_bytes=True))

    def test_a_malformed_exempt_list_exempts_rather_than_rejects(self):
        self.assertEqual([], self.check(max_total_bytes=6144,
                                        max_bytes_exempt="task-0100"))

    def test_a_cfg_that_raises_leaves_the_debt_check_answering_no_debt(self):
        # The end-to-end shape of the fail-open contract: whatever goes wrong in
        # config, the pipeline keeps moving.
        with unittest.mock.patch.object(handoff, "cfg", side_effect=RuntimeError("boom")):
            self.assertIsNone(handoff.cap("max_total_bytes"))
            self.assertEqual([], handoff.uncovered_done_tasks())


class InjectionTest(unittest.TestCase):
    """FR-33: the newest document in full, every older one as one index line."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "handoff-inject")
        # Forced on rather than inherited from the shipped pipeline.json: a test
        # that passes because of live config proves nothing about the code.
        patch = unittest.mock.patch.object(handoff, "cfg",
                                           return_value={"enabled": True})
        patch.start()
        self.addCleanup(patch.stop)
        for i, (task, title) in enumerate((
                ("task-0013", "Rule injection hook"),
                ("task-0014", "Split the rules out of the core import"),
                ("task-0015", "Per-repo nested CLAUDE.md"))):
            path = write_sized(self.tmp / f"{task}.md", VALID, 1200 + i,
                               task=task, title=title)
            # Explicit mtimes: the order is the thing under test, so it is set
            # rather than inherited from how fast the loop ran.
            os.utime(path, (1_000_000 + i, 1_000_000 + i))

    def test_three_documents_inject_as_one_full_and_two_index_lines(self):
        block = handoff.render_injection(self.tmp)
        self.assertIn("## What was done", block)          # the newest, in full
        # One body, not three: the section headers appear once each.
        self.assertEqual(1, block.count("## Key decisions"))
        index = [ln for ln in block.splitlines() if ln.startswith("- task-")]
        self.assertEqual(2, len(index), block)
        self.assertEqual(["task-0014", "task-0013"],
                         [ln.split(" - ")[0].lstrip("- ") for ln in index])

    def test_the_newest_document_is_the_one_rendered_in_full(self):
        block = handoff.render_injection(self.tmp)
        self.assertIn("# Handoff: task-0015", block)
        self.assertNotIn("# Handoff: task-0014", block)

    def test_each_index_line_carries_id_title_and_path(self):
        block = handoff.render_injection(self.tmp)
        line = [ln for ln in block.splitlines() if ln.startswith("- task-0014")]
        self.assertEqual(1, len(line), block)
        self.assertIn("Split the rules out of the core import", line[0])
        self.assertIn("task-0014.md", line[0])

    def test_a_single_document_injects_with_no_index_section(self):
        for stale in ("task-0013", "task-0014"):
            (self.tmp / f"{stale}.md").unlink()
        block = handoff.render_injection(self.tmp)
        self.assertIn("# Handoff: task-0015", block)
        self.assertNotIn("Older handoff documents", block)

    def test_an_empty_directory_injects_nothing(self):
        empty = tmproot.sandbox(self, "handoff-empty")
        self.assertEqual("", handoff.render_injection(empty))


class InjectionBudgetTest(unittest.TestCase):
    """The block has a ceiling of its own, and FR-32 does not provide one.

    Grandfathered documents are exempt from the per-document cap but not from
    being injected, and the index grows by one line per completed task forever,
    so without this the context-budget epic leaks through its own handoff
    mechanism."""

    ENABLED: ClassVar[dict] = {"enabled": True}

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "handoff-budget")
        # One oversized document plus five older ones, which is the shape the
        # cap has to handle: a grandfathered doc in the full-size slot.
        write_sized(self.tmp / "task-0200.md", VALID, 9000,
                    task="task-0200", title="Grandfathered and large")
        os.utime(self.tmp / "task-0200.md", (2_000_000, 2_000_000))
        for i in range(5):
            task = f"task-01{i:02d}"
            p = write_sized(self.tmp / f"{task}.md", VALID, 1000,
                            task=task, title=f"Older document {i}")
            os.utime(p, (1_000_000 + i, 1_000_000 + i))

    def render(self, **config) -> str:
        with unittest.mock.patch.object(handoff, "cfg",
                                        return_value=dict(self.ENABLED, **config)):
            return handoff.render_injection(self.tmp)

    def size(self, text: str) -> int:
        return len(text.encode("utf-8"))

    def test_without_a_budget_the_whole_newest_document_travels(self):
        block = self.render()
        self.assertGreater(self.size(block), 9000)
        self.assertNotIn("truncated to fit", block)

    def test_the_block_is_held_under_the_budget(self):
        for budget in (8000, 6000, 4000, 2000):
            with self.subTest(budget=budget):
                self.assertLessEqual(self.size(self.render(
                    inject_budget_bytes=budget)), budget)

    def index_of(self, block: str) -> list[str]:
        return [ln.split(" - ")[0].lstrip("- ") for ln in block.splitlines()
                if ln.startswith("- task-")]

    def test_the_oldest_index_lines_go_before_the_document_is_touched(self):
        # The order the CEO chose on 2026-09-15, after the reverse order was
        # built and measured: this block's contract is the newest document in
        # full, so pointers are spent before content is.
        block = self.render(inject_budget_bytes=9300)
        self.assertLessEqual(self.size(block), 9300)
        self.assertNotIn("truncated to fit", block)
        kept = self.index_of(block)
        self.assertLess(len(kept), 5)
        self.assertNotIn("task-0100", kept)   # the oldest went first
        self.assertEqual("task-0104", kept[0])  # the newest older one stayed

    def test_the_document_is_truncated_only_once_the_index_is_gone(self):
        block = self.render(inject_budget_bytes=4000)
        self.assertLessEqual(self.size(block), 4000)
        self.assertEqual([], self.index_of(block))
        self.assertIn("truncated to fit", block)
        self.assertIn("task-0200.md", block)  # the path survives truncation

    def test_a_document_larger_than_the_whole_budget_still_renders(self):
        # The degenerate end, and the shape of a grandfathered 11985-byte
        # document reaching the full-size slot: one document, no index left to
        # give, and no trimming order that can deliver it whole.
        solo = tmproot.sandbox(self, "handoff-solo")
        write_sized(solo / "task-0300.md", VALID, 12000,
                    task="task-0300", title="Grandfathered, over any budget")
        with unittest.mock.patch.object(
                handoff, "cfg",
                return_value={"enabled": True, "inject_budget_bytes": 2000}):
            block = handoff.render_injection(solo)
        self.assertLessEqual(self.size(block), 2000)
        self.assertIn("truncated to fit", block)
        self.assertIn("task-0300.md", block)
        self.assertIn("## What was done", block)  # a usable head, not a stub

    def test_a_budget_below_the_floor_still_names_the_path(self):
        # Under MIN_BODY_BYTES a fragment teaches nothing, so the body goes and
        # the note stays. The head plus the path line plus the note is the
        # floor: below that the block cannot shrink further, and reporting the
        # path beats reporting nothing.
        block = self.render(inject_budget_bytes=300)
        self.assertEqual([], self.index_of(block))
        self.assertIn("truncated to fit", block)
        self.assertIn("task-0200.md", block)

    def test_an_unusable_budget_disables_the_budget(self):
        for bad in (None, "7 KB", "7168", 0, -1, True):
            with self.subTest(value=bad):
                self.assertNotIn("truncated to fit",
                                 self.render(inject_budget_bytes=bad))

    def test_a_disabled_chain_injects_nothing(self):
        # The module docstring says "Missing block = feature OFF", and
        # covered_required() honours it. The injection must too, or an adopter
        # who switched the chain off still pays for it on every dispatch.
        with unittest.mock.patch.object(handoff, "cfg", return_value={"enabled": False}):
            self.assertEqual("", handoff.render_injection(self.tmp))
        with unittest.mock.patch.object(handoff, "cfg", return_value={}):
            self.assertEqual("", handoff.render_injection(self.tmp))


class ChainOrderTest(unittest.TestCase):
    """The full-size slot is decided by the author's date, not by mtime."""

    ENABLED: ClassVar[dict] = {"enabled": True}

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "handoff-order")

    def write(self, task: str, date: str, mtime: int) -> Path:
        path = self.tmp / f"{task}.md"
        path.write_text(VALID.replace("date: 2026-09-01", f"date: {date}").format(
            pad="", task=task, title=f"Document {task}"),
            encoding="utf-8", newline="\n")
        os.utime(path, (mtime, mtime))
        return path

    def test_a_touched_old_document_does_not_take_the_full_size_slot(self):
        # The measured scenario: a rescaffold or a path sweep rewrites an old
        # document's mtime. Under mtime ordering that document would have been
        # injected in full and the real newest one demoted to an index line.
        self.write("task-0090", "2026-01-01", 9_000_000)   # old, touched today
        self.write("task-0091", "2026-09-15", 1_000_000)   # newest, untouched
        with unittest.mock.patch.object(handoff, "cfg", return_value=self.ENABLED):
            self.assertEqual("task-0091", handoff.chain_docs(self.tmp)[0].stem)
            block = handoff.render_injection(self.tmp)
        self.assertIn("# Handoff: task-0091", block)
        self.assertIn("- task-0090", block)

    def test_a_dateless_document_sorts_last_rather_than_winning(self):
        dated = self.write("task-0092", "2026-02-02", 1_000_000)
        undated = self.tmp / "task-0093.md"
        undated.write_text(VALID.replace("date: 2026-09-01\n", "").format(
            pad="", task="task-0093", title="No date"),
            encoding="utf-8", newline="\n")
        os.utime(undated, (9_000_000, 9_000_000))
        with unittest.mock.patch.object(handoff, "cfg", return_value=self.ENABLED):
            self.assertEqual(dated, handoff.chain_docs(self.tmp)[0])


class ThreadTest(unittest.TestCase):
    """FR-34 and contract C-10: one thread doc per epic, capped at 4096."""

    CFG: ClassVar[dict] = {"enabled": True, "thread_max_bytes": 4096}

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "handoff-thread")

    def problems(self, **overrides) -> list[dict]:
        config = dict(self.CFG, **overrides)
        with unittest.mock.patch.object(handoff, "cfg", return_value=config):
            return handoff.thread_problems(self.tmp)

    def thread(self, size: int, epic: str = "epic-0001") -> Path:
        return write_sized(self.tmp / f"{epic}-thread.md", THREAD, size, epic=epic)

    def test_a_4200_byte_thread_document_is_rejected(self):
        self.thread(4200)
        found = self.problems()
        self.assertEqual(1, len(found), found)
        self.assertIn("4200", found[0]["reason"])
        self.assertIn("4096", found[0]["reason"])
        self.assertEqual("epic-0001", found[0]["epic"])

    def test_a_4000_byte_thread_document_passes(self):
        self.thread(4000)
        self.assertEqual([], self.problems())

    def test_the_three_c10_sections_are_required(self):
        (self.tmp / "epic-0001-thread.md").write_text(
            "---\nepic: epic-0001\n---\n\n## Where the chain stands\nx\n",
            encoding="utf-8")
        reason = self.problems()[0]["reason"]
        self.assertIn("What is decided", reason)
        self.assertIn("What remains", reason)

    def test_a_missing_thread_document_is_not_a_problem(self):
        # An adopter with an epic and no thread doc must not inherit permanent
        # debt from a check they never opted into.
        (self.tmp / "epic-0002-harness.md").write_text("---\nid: 0002\n---\n",
                                                       encoding="utf-8")
        self.assertEqual([], self.problems())

    def test_an_unusable_thread_cap_disables_that_cap(self):
        self.thread(4200)
        self.assertEqual([], self.problems(thread_max_bytes="4 KB"))
        self.assertEqual([], self.problems(thread_max_bytes=None))

    def test_a_missing_epics_directory_is_not_a_problem(self):
        with unittest.mock.patch.object(handoff, "cfg", return_value=self.CFG):
            self.assertEqual([], handoff.thread_problems(self.tmp / "nope"))


class ShippedConfigTest(unittest.TestCase):
    """The keys C-5 names, in the file that ships, with the values it names.

    Read from .agentry/pipeline.json rather than from a fixture: a test that
    only proves the code can read a cap it was handed is how a config key goes
    missing while every test stays green."""

    def setUp(self):
        self.cfg = handoff.cfg()

    def test_the_two_caps_are_configured_with_the_c5_values(self):
        self.assertEqual(6144, self.cfg.get("max_total_bytes"))
        self.assertEqual(4096, self.cfg.get("thread_max_bytes"))

    def test_every_exempt_id_is_a_real_over_cap_document(self):
        # The list is grandfathering, not a mute button: an id in it that is
        # not actually over the cap is an exemption nobody needs, and one that
        # no longer exists is a line to delete.
        exempt = self.cfg.get("max_bytes_exempt", [])
        self.assertIsInstance(exempt, list)
        for task in exempt:
            path = handoff.handoff_path(task)
            self.assertTrue(path.is_file(), f"{task} is exempt but has no doc")
            self.assertGreater(os.path.getsize(path), 6144,
                               f"{task} is exempt but is under the cap")

    def test_no_shipped_document_is_over_the_cap_without_being_exempt(self):
        exempt = set(self.cfg.get("max_bytes_exempt", []))
        over = [p.stem for p in handoff.chain_docs()
                if os.path.getsize(p) > 6144 and p.stem not in exempt]
        self.assertEqual([], over)


if __name__ == "__main__":
    unittest.main()
