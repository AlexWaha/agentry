"""A rule reaches an agent because the agent declared it, or not at all.

FR-28 of spec-0001, contract C-7. The hook under test is the reader half: it
resolves the `rules:` key of the dispatched agent's frontmatter and delivers
those files. The writer half (agent files gaining the key) is task-0014, so the
corpus test at the bottom pins the no-op: all 31 shipped agents declare nothing
today, and the hook runs on every dispatch in between.

Two disciplines carried from task-0011. The fail-open handler is tested on the
paths nothing else reaches - a handler no test can reach is a handler that can
be deleted unnoticed, which is the mutation that survived last time. And the
happy path is compared against the REAL .claude/rules/testing.md rather than a
fixture, because a fixture proves the hook copies bytes, not that it reaches
the rule an agent will actually name.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
# The hook lives in tools/hooks/ and this file in tools/tests/; both inserts are
# harmless in either layout, so the module resolves whether it is installed or
# still staged beside this file.
sys.path.insert(0, str(TOOLS_DIR / "hooks"))
sys.path.insert(0, str(TESTS_DIR))

import inject_rules  # noqa: E402 - path set above
import tmproot  # noqa: E402 - same

AGENTS_DIR = inject_rules.AGENTS_DIR
RULES_DIR = inject_rules.RULES_DIR
AGENT_COUNT = 31

# The smallest shipped rule, used where a test needs a real one and its size is
# beside the point.
SMALL_RULE = "code-retrieval.md"


class InjectRulesTestCase(unittest.TestCase):
    """A temp agents dir and a temp rules dir per test, passed through the two
    flags that exist to be this seam. Nothing here writes into .claude/."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "inject_rules_")
        self.agents = self.tmp / "agents"
        self.rules = self.tmp / "rules"
        self.agents.mkdir()
        self.rules.mkdir()

    def dirs(self, rules=None) -> list:
        return ["--agents-dir", str(self.agents),
                "--rules-dir", str(rules or self.rules)]

    def write_agent(self, name: str, *frontmatter: str, body: str = "# Body\n") -> Path:
        path = self.agents / f"{name}.md"
        block = "\n".join(("---", f"name: {name}", *frontmatter, "---", "", body))
        path.write_text(block, encoding="utf-8")
        return path

    def write_rule(self, name: str, text: str) -> Path:
        path = self.rules / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_hook(self, payload, argv=None):
        """main() with stdin swapped, the shape InjectTestCase.run_inject uses."""
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        real_stdin, sys.stdin = sys.stdin, io.StringIO(raw)
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = inject_rules.main(self.dirs() if argv is None else argv)
        finally:
            sys.stdin = real_stdin
        return code, out.getvalue(), err.getvalue()

    def context(self, out: str) -> str:
        """The additionalContext of the envelope, or the assertion that says why
        there is none. Criterion 1 is checked after parsing, never on raw
        stdout: the envelope is a transport detail, the block is the payload."""
        envelope = json.loads(out)
        self.assertEqual(envelope["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        return envelope["hookSpecificOutput"]["additionalContext"]


class AcceptanceCriteriaTest(InjectRulesTestCase):
    """The five criteria of task-0013, each against real stdout."""

    def test_a_declared_rule_arrives_with_the_real_file_contents(self):
        # Criterion 1, against the shipped .claude/rules/testing.md.
        self.write_agent("declarer", "rules: [testing.md]")
        code, out, _ = self.run_hook({"agent_type": "declarer"},
                                     self.dirs(rules=RULES_DIR))
        self.assertEqual(code, 0)
        block = self.context(out)
        expected = (RULES_DIR / "testing.md").read_text(encoding="utf-8").rstrip()
        self.assertIn("## Agent rules: 1 of 1 declared file(s)", block)
        self.assertIn("Contents of .claude/rules/testing.md:", block)
        self.assertIn(expected, block)

    def test_an_agent_without_a_rules_key_injects_nothing(self):
        # Criterion 2, the path all 31 shipped agents take today.
        self.write_agent("quiet", "model: opus")
        code, out, err = self.run_hook({"agent_type": "quiet"})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_a_rule_that_does_not_exist_is_a_diagnostic_not_a_crash(self):
        # Criterion 3. NFR-4: the dispatch continues, the gap is visible.
        self.write_agent("misdeclarer", "rules: [nope.md]")
        code, out, _ = self.run_hook({"agent_type": "misdeclarer"})
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 0 of 1 declared file(s)", block)
        self.assertIn("- not injected: nope.md", block)

    def test_several_rules_arrive_in_the_declared_order(self):
        # Criterion 4. Declared order, not directory order, not sorted order.
        for name in ("alpha.md", "beta.md", "gamma.md"):
            self.write_rule(name, f"# {name}\n\nbody of {name}\n")
        self.write_agent("ordered", "rules: [gamma.md, alpha.md, beta.md]")
        code, out, _ = self.run_hook({"agent_type": "ordered"})
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 3 of 3 declared file(s)", block)
        positions = [block.index(f"Contents of {self.rel(name)}:")
                     for name in ("gamma.md", "alpha.md", "beta.md")]
        self.assertEqual(positions, sorted(positions), block[:400])
        for name in ("gamma.md", "alpha.md", "beta.md"):
            self.assertIn(f"body of {name}", block)

    def test_a_mixed_declaration_puts_the_diagnostic_before_the_contents(self):
        # Some resolve, some do not. render() joins [head, *failures,
        # *sections], so the order is part of the contract: a refactor that
        # flips it moves a diagnostic below the rule text it warns about.
        self.write_agent("mixed", "rules: [testing.md, nope.md]")
        code, out, _ = self.run_hook({"agent_type": "mixed"},
                                     self.dirs(rules=RULES_DIR))
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 1 of 2 declared file(s)", block)
        self.assertLess(block.index("- not injected: nope.md"),
                        block.index("Contents of .claude/rules/testing.md:"),
                        block[:400])

    def rel(self, name: str) -> str:
        return inject_rules.rel(self.rules / name)


class ValueFormTest(InjectRulesTestCase):
    """YAML allows three shapes for a list value and task-0014 may write any of
    them; the block form is what `skills:` already uses in every agent file."""

    def test_the_block_form_yields_the_same_list_as_the_flow_form(self):
        text = ("---\nname: a\nrules:\n  - first.md\n  - second.md\n"
                "tools: Read\n---\n\nbody\n")
        self.assertEqual(inject_rules.declared_rules(text), ["first.md", "second.md"])

    def test_a_single_scalar_and_a_comma_list_both_parse(self):
        one = "---\nname: a\nrules: only.md\n---\n\nbody\n"
        two = "---\nname: a\nrules: one.md, two.md\n---\n\nbody\n"
        self.assertEqual(inject_rules.declared_rules(one), ["only.md"])
        self.assertEqual(inject_rules.declared_rules(two), ["one.md", "two.md"])

    def test_quotes_are_stripped_and_empty_entries_dropped(self):
        text = "---\nname: a\nrules: ['one.md', \"two.md\", ]\n---\n\nbody\n"
        self.assertEqual(inject_rules.declared_rules(text), ["one.md", "two.md"])

    def test_duplicates_are_kept_as_declared(self):
        text = "---\nname: a\nrules: [one.md, one.md]\n---\n\nbody\n"
        self.assertEqual(inject_rules.declared_rules(text), ["one.md", "one.md"])

    def test_a_rules_line_in_the_body_is_not_a_declaration(self):
        text = ("---\nname: a\nmodel: opus\n---\n\n"
                "# Body\n\nrules: [testing.md]\n")
        self.assertEqual(inject_rules.declared_rules(text), [])

    def test_the_block_list_ends_at_the_next_key(self):
        text = ("---\nname: a\nrules:\n  - first.md\ntools: Read, Write\n"
                "  - notarule.md\n---\n\nbody\n")
        self.assertEqual(inject_rules.declared_rules(text), ["first.md"])


class SilentPathTest(InjectRulesTestCase):
    """Rows 1 to 8 of the fail-open table: nothing to say, said silently."""

    def test_empty_stdin_injects_nothing(self):
        code, out, _ = self.run_hook("")
        self.assertEqual((code, out), (0, ""))

    def test_stdin_that_is_not_json_injects_nothing(self):
        code, out, _ = self.run_hook("nope")
        self.assertEqual((code, out), (0, ""))

    def test_json_that_is_not_an_object_injects_nothing(self):
        for raw in ("[]", '"x"', "42", "null"):
            with self.subTest(payload=raw):
                code, out, _ = self.run_hook(raw)
                self.assertEqual((code, out), (0, ""))

    def test_a_payload_with_no_identity_key_injects_nothing(self):
        code, out, _ = self.run_hook({"session_id": "abc", "agent_id": "agent-7"})
        self.assertEqual((code, out), (0, ""))

    def test_an_agent_with_no_project_definition_injects_nothing(self):
        code, out, _ = self.run_hook({"agent_type": "general-purpose"})
        self.assertEqual((code, out), (0, ""))

    def test_an_agent_file_with_no_frontmatter_injects_nothing(self):
        (self.agents / "bare.md").write_text("# Just a body\n", encoding="utf-8")
        code, out, _ = self.run_hook({"agent_type": "bare"})
        self.assertEqual((code, out), (0, ""))

    def test_an_unclosed_frontmatter_block_injects_nothing(self):
        # Definition of Done: malformed frontmatter fails open.
        (self.agents / "torn.md").write_text(
            "---\nname: torn\nrules: [testing.md]\n\n# Body\n", encoding="utf-8")
        code, out, _ = self.run_hook({"agent_type": "torn"})
        self.assertEqual((code, out), (0, ""))

    def test_a_bare_or_empty_rules_key_injects_nothing(self):
        for value in ("rules:", "rules: []", "rules: [ ]", "rules: ''"):
            with self.subTest(value=value):
                self.write_agent("empty", value, "tools: Read")
                code, out, _ = self.run_hook({"agent_type": "empty"})
                self.assertEqual((code, out), (0, ""))

    def test_an_agent_file_that_is_a_directory_injects_nothing(self):
        (self.agents / "dirlike.md").mkdir()
        code, out, _ = self.run_hook({"agent_type": "dirlike"})
        self.assertEqual((code, out), (0, ""))


class IdentityTest(InjectRulesTestCase):
    """Which key names the dispatched agent, and what is refused as a name."""

    def test_agent_type_wins_and_agent_id_is_never_an_identity(self):
        payload = {"agent_id": "agent-1", "agent_name": "third",
                   "subagent_type": "second", "agent_type": "first"}
        self.assertEqual(inject_rules.agent_type(payload), "first")
        self.assertEqual(inject_rules.agent_type({"agent_id": "agent-1"}), "")

    def test_the_known_spellings_are_tried_in_order(self):
        self.assertEqual(inject_rules.agent_type({"subagent_type": "second"}), "second")
        self.assertEqual(inject_rules.agent_type({"agent_name": "third"}), "third")

    def test_a_non_string_identity_is_ignored(self):
        for value in (None, 42, ["reviewer"], {"name": "reviewer"}, "   "):
            with self.subTest(value=value):
                self.assertEqual(inject_rules.agent_type({"agent_type": value}), "")

    def test_an_unusable_name_is_refused_before_any_filesystem_access(self):
        self.write_agent("architect", "rules: [testing.md]")
        with unittest.mock.patch.object(
                Path, "is_file", side_effect=AssertionError("touched the filesystem")):
            code, out, err = self.run_hook({"agent_type": "../architect"})
        self.assertEqual((code, out), (0, ""))
        self.assertIn("ignoring unusable agent name", err)


class ContainmentTest(InjectRulesTestCase):
    """A declared string never reaches the filesystem unchecked. Each refusal
    still emits the envelope, because the diagnostic is the deliverable."""

    def refuse(self, declared: str) -> str:
        self.write_agent("hostile", f"rules: [{declared}]")
        code, out, _ = self.run_hook({"agent_type": "hostile"})
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 0 of 1 declared file(s)", block)
        self.assertIn("- not injected:", block)
        return block

    def test_traversal_and_absolute_paths_are_refused(self):
        for declared in ("../../etc/passwd", "/etc/passwd", "C:\\windows\\x.md",
                         "sub/../../outside.md"):
            with self.subTest(declared=declared):
                self.refuse(declared)

    def test_a_hidden_file_and_a_non_markdown_file_are_refused(self):
        self.write_rule("notes.txt", "not a rule")
        for declared in (".hidden.md", "notes.txt", "no-suffix"):
            with self.subTest(declared=declared):
                self.refuse(declared)

    def test_an_unclosed_flow_list_is_one_diagnostic_and_no_partial_list(self):
        self.write_rule("real.md", "# real\n")
        self.write_agent("truncated", "rules: [real.md, other.md")
        code, out, _ = self.run_hook({"agent_type": "truncated"})
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 0 of 1 declared file(s)", block)
        self.assertNotIn("Contents of", block)

    def test_a_directory_named_like_a_rule_is_refused(self):
        (self.rules / "dir.md").mkdir()
        self.refuse("dir.md")

    def test_a_subdirectory_rule_is_allowed(self):
        # C-7 says paths, so a nested rule file resolves.
        self.write_rule("stack/php.md", "# php rules\n\nuse strict types\n")
        self.write_agent("nested", "rules: [stack/php.md]")
        code, out, _ = self.run_hook({"agent_type": "nested"})
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 1 of 1 declared file(s)", block)
        self.assertIn("use strict types", block)

    def test_a_link_out_of_the_rules_directory_is_refused(self):
        """The containment half of resolve_rule, on a real escaping path.

        Nothing else in this suite reaches it: every traversal, absolute and
        drive-relative string above is refused by the path regex before any
        stat, and the directory case fails on is_file instead. A link is the
        only input that passes the regex, resolves outside and still names a
        real file - so this is the one test that would notice is_relative_to
        being dropped.

        This host grants no symlink privilege ([WinError 1314]), so fall back
        to a directory junction, which Windows creates without one. Skipping
        instead would leave the check unexecuted on the machine the suite
        actually runs on.
        """
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "payload.md").write_text("# classified\n", encoding="utf-8")
        link = self.rules / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if sys.platform != "win32":
                self.skipTest(f"no link mechanism on this host: {exc}")
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                           capture_output=True, text=True, check=True)
        block = self.refuse("escape/payload.md")
        self.assertNotIn("classified", block)


class EnvelopeTest(InjectRulesTestCase):
    """SubagentStart drops raw stdout on this build, so the shape is the
    contract and ASCII output is not a stylistic choice."""

    def test_the_envelope_is_one_json_object_with_the_event_name(self):
        self.write_rule("one.md", "# one\n")
        self.write_agent("enveloped", "rules: [one.md]")
        code, out, _ = self.run_hook({"agent_type": "enveloped"})
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1, "envelope must be one line")
        envelope = json.loads(out)
        self.assertEqual(list(envelope), ["hookSpecificOutput"])
        self.assertEqual(set(envelope["hookSpecificOutput"]),
                         {"hookEventName", "additionalContext"})

    def test_the_envelope_is_pure_ascii_even_for_a_non_ascii_rule(self):
        # A cp1252 stdout would raise on these bytes, and that error would die
        # in the blanket handler as a permanent silent no-op.
        self.write_rule("wide.md", "# wide\n\n" + chr(0x2192) + " arrow, \u0434\u0430\n")
        self.write_agent("wide", "rules: [wide.md]")
        code, out, _ = self.run_hook({"agent_type": "wide"})
        self.assertEqual(code, 0)
        out.encode("ascii")  # raises if json.dumps ever gains ensure_ascii=False
        self.assertIn(chr(0x2192), self.context(out))


class FailOpenHandlerTest(InjectRulesTestCase):
    """Reads that raise, and the one path left that reaches main()'s handler.

    These exist because of the mutation that survived task-0011: a handler
    whose only reachable path is one no test writes can be deleted with the
    suite still green. Replace the `pass` in main() with `raise` and
    test_a_stdout_that_raises_is_swallowed must go red - it is now the only
    row that gets there, because both reads in render() catch OSError
    themselves rather than letting one failed entry discard the whole block.
    """

    def test_a_stdout_that_raises_is_swallowed(self):
        self.write_rule("one.md", "# one\n")
        self.write_agent("printer", "rules: [one.md]")

        class Exploding(io.StringIO):
            def write(self, _s):
                raise OSError("stdout is gone")

        real_stdin, sys.stdin = sys.stdin, io.StringIO(
            json.dumps({"agent_type": "printer"}))
        try:
            with redirect_stdout(Exploding()):
                code = inject_rules.main(self.dirs())
        finally:
            sys.stdin = real_stdin
        self.assertEqual(code, 0)

    def test_an_agent_file_that_raises_on_read_is_swallowed(self):
        # A global read_text patch fails on the AGENT file first, so this row
        # never reached the rule read - which is how the unguarded read at
        # render() survived review. It pins the agent-file half only.
        #
        # Asserted on render(), not through main(): without the guard the
        # OSError falls into main()'s blanket handler, which produces the same
        # (0, "") a caught error does, so a main()-level assertion cannot tell
        # the guard from its absence. render() returns "" guarded and raises
        # unguarded, which is the difference the row exists to pin.
        self.write_rule("one.md", "# one\n")
        self.write_agent("unreadable", "rules: [one.md]")
        with unittest.mock.patch.object(Path, "read_text", side_effect=OSError("EIO")):
            self.assertEqual(inject_rules.render("unreadable", self.agents, self.rules), "")
            code, out, _ = self.run_hook({"agent_type": "unreadable"})
        self.assertEqual((code, out), (0, ""))

    def test_a_rule_that_raises_on_read_is_one_diagnostic_and_the_rest_arrive(self):
        # The row the table omitted: a file that passes is_file() and raises on
        # read. Unguarded, the OSError leaves render() and the blanket handler
        # discards the whole block - both sections and every diagnostic - at
        # exit 0. Patched per path, never globally, so the read under test is
        # the RULE read and nothing else.
        #
        # The failure sits in the MIDDLE on purpose: declared last it would
        # prove only that earlier sections survive, declared first only that
        # later ones arrive. Between two good rules, a `sections.clear()` on
        # this path - the whole-block discard the guard exists to prevent -
        # loses first.md and fails the count.
        self.write_rule("first.md", "# first\n\nbody of first.md\n")
        self.write_rule("broken.md", "# broken\n")
        self.write_rule("last.md", "# last\n\nbody of last.md\n")
        self.write_agent("halfread", "rules: [first.md, broken.md, last.md]")

        real_read_text = Path.read_text

        def only_the_broken_rule(self, *args, **kwargs):
            if self.name == "broken.md":
                raise OSError("EIO")
            return real_read_text(self, *args, **kwargs)

        with unittest.mock.patch.object(Path, "read_text", only_the_broken_rule):
            code, out, _ = self.run_hook({"agent_type": "halfread"})
        self.assertEqual(code, 0)
        block = self.context(out)
        self.assertIn("## Agent rules: 2 of 3 declared file(s)", block)
        self.assertIn("- not injected: broken.md (declared, but unreadable)", block)
        self.assertIn("body of first.md", block)
        self.assertIn("body of last.md", block)


class RealTreeTest(unittest.TestCase):
    """Against the shipped .claude/, in a real interpreter with real stdout."""

    def test_a_real_dispatch_payload_injects_nothing_today(self):
        # Criterion 2 on real data: no shipped agent declares a rule until
        # task-0014, and the hook fires on every dispatch in between.
        hook = Path(inject_rules.__file__)
        result = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({"hook_event_name": "SubagentStart",
                              "agent_id": "agent-1", "agent_type": "architect"}),
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", result.stdout[:500])

    @unittest.skipUnless(AGENTS_DIR.is_dir(), "shipped agents directory not present")
    def test_no_shipped_agent_declares_a_rule_yet(self):
        # task-0014 replaces this with "every declared entry resolves under
        # .claude/rules/ and appears once".
        agents = sorted(AGENTS_DIR.glob("*.md"))
        self.assertEqual(len(agents), AGENT_COUNT, [p.stem for p in agents])
        for path in agents:
            with self.subTest(agent=path.stem):
                self.assertEqual(
                    inject_rules.declared_rules(path.read_text(encoding="utf-8")), [])

    @unittest.skipUnless(RULES_DIR.is_dir(), "shipped rules directory not present")
    def test_every_shipped_rule_resolves_when_declared(self):
        for path in sorted(RULES_DIR.glob("*.md")):
            with self.subTest(rule=path.name):
                self.assertEqual(inject_rules.resolve_rule(path.name, RULES_DIR),
                                 path.resolve())
        self.assertIsNotNone(inject_rules.resolve_rule(SMALL_RULE, RULES_DIR))


if __name__ == "__main__":
    unittest.main()
