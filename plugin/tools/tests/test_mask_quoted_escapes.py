"""mask_quoted follows the shell's quoting, not a regex (task-0108).

The regex `'[^']*'|"[^"]*"` ended a double-quoted span at an ESCAPED quote, so the
tail of `ssh h "psql -e \\"select 1 where price>0\\""` was scanned as live shell
and its `>` read as a redirect. The task-0102 corpus diff (49,252 real commands)
found 35 of those denials once the glued form was caught too. The scanner now
treats a backslash-escaped quote as text, pairs the quotes of a `$( ... )` inside
double quotes among themselves, and masks comments.

Every case here is judged against what bash does, in both directions: text that
bash reads as quoted must stop looking like a redirect, and a redirect bash
would execute must still be found - the old mask HID `echo \\" > f \\"`.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "pipeline"))
sys.path.insert(0, str(TESTS_DIR))

import agent_gate  # noqa: E402 - path set above
import pretool_gate  # noqa: E402

# Commands whose `>` is quoted or commented: no redirect, so no write.
ESCAPED_QUOTE_TEXT = (
    'ssh h "psql -e \\"select 1 where price>0\\""',
    'echo "say \\"a > b\\" now"',
    'python -c "print(\\"x\\" if 1>0 else 0)"',
    'grep -oE "policyName\\": \\"[A-Z_]+\\"|skip[^|]*" f',
    'echo "$t => $r"',
    "echo \"it's > fine\"",
    'git log --grep "fix \\"a->b\\" parsing"',
)

# A quote nested in $( ... ) inside double quotes belongs to the substitution.
NESTED_TEXT = (
    'echo "$(echo "a>b")"',
    'echo "x $(grep -c "n>0" f) y"',
    'echo "$(echo "$(echo "a>b")")"',
    'echo "$(grep -c "it(" f)"; grep "b->c" f',
)

# Real redirects bash executes. The first three were invisible to the old mask.
STILL_WRITES = {
    'echo \\" > f \\"': "> f",
    "echo \\' > f \\'": "> f",
    'echo "a" \\" > f': "> f",
    'echo "a \\" b" > f': "> f",
    'echo "$(echo "a>b")" x>y': ">y",
    "echo 'a>b' > c": "> c",
    "ls # note\necho z>f": ">f",
    "echo a#b>f": ">f",
    "echo $#>f": ">f",
    "echo ${x#p} > f": "> f",
    "echo ${x/ #/y} > f": "> f",
    "echo [[ > f ]]": "> f",
}

# Not masked, on purpose: bash reads no redirect in these, this gate still does.
# Masking them needs a command parser (`echo [[ > f` above is a real write, and
# `$((` is ambiguous with `$( (`), so the false positive is documented, not fixed.
KNOWN_FALSE_POSITIVES = {
    "(( n>0 ))": ">0",
    "[[ a>b ]]": ">b",
    "echo $((a>b))": ">b))",
    "echo ${x:-a>b}": ">b}",
}


class EscapedQuotesAreTextTest(unittest.TestCase):

    def test_no_redirect_is_read_in_a_span_with_an_escaped_quote(self):
        for command in ESCAPED_QUOTE_TEXT:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))
                self.assertEqual("", agent_gate.bash_mutates(command))

    def test_the_whole_span_is_blanked(self):
        masked = pretool_gate.mask_quoted('ssh h "psql -e \\"x>0\\""')
        self.assertEqual("ssh h " + "Q" * 17, masked)

    def test_an_escaped_quote_outside_quotes_opens_nothing(self):
        command = 'echo \\" > f \\"'
        self.assertEqual(command, pretool_gate.mask_quoted(command))
        self.assertEqual("> f", pretool_gate.redirect_write_target(command))


class NestedQuotesInASubstitutionTest(unittest.TestCase):

    def test_no_redirect_is_read_in_the_nested_span(self):
        for command in NESTED_TEXT:
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_a_substitution_body_cut_at_a_quoted_paren_is_the_old_defect(self):
        # The parenthesis count read the `(` in "it(" as structure and cut the body
        # in the wrong place, so a later quoted arrow was scanned as live text.
        body = pretool_gate.command_substitutions('echo "$(grep -c "it(" f)"; grep "b->c" f')
        self.assertEqual(['grep -c "it(" f'], body)

    def test_quotes_that_do_not_pair_up_fall_back_to_counting_parentheses(self):
        self.assertEqual(["echo it's"], pretool_gate.command_substitutions("echo $(echo it's)"))

    def test_a_failed_nested_pairing_falls_back_to_flat_spans(self):
        # Bash calls this unterminated. The mask still pairs the quotes the way
        # the flat scan always did, so the quoted `>q` is blanked. (The gate as
        # a whole still refuses the text: the unclosed `$(` yields the rest of
        # the command as a body of its own, fail-closed.)
        self.assertEqual("echo " + "Q" * 7 + " " + "Q" * 4,
                         pretool_gate.mask_quoted('echo "x $(y" ">q"'))

    def test_a_write_inside_the_substitution_is_still_found(self):
        self.assertEqual("> docs/x", pretool_gate.redirect_write_target('echo "$(cat a > docs/x)"'))


class StillWritesTest(unittest.TestCase):

    def test_each_real_redirect_is_reported(self):
        for command, expected in STILL_WRITES.items():
            with self.subTest(command=command):
                self.assertEqual(expected, pretool_gate.redirect_write_target(command))

    def test_readonly_is_denied_each_real_redirect(self):
        for command in STILL_WRITES:
            with self.subTest(command=command):
                self.assertNotEqual("", agent_gate.bash_mutates(command))


class CommentsAreNotCodeTest(unittest.TestCase):

    def test_a_comment_hides_its_redirect_up_to_the_end_of_the_line(self):
        for command in ("ls # a>b", "ls ;# a>b", "ls &&# a>b", "(# a>b\nls)", "# a>b\nls"):
            with self.subTest(command=command):
                self.assertEqual("", pretool_gate.redirect_write_target(command))

    def test_the_next_line_is_code_again(self):
        self.assertEqual(">f", pretool_gate.redirect_write_target("ls # a>b\necho z>f"))

    def test_a_hash_inside_a_word_starts_no_comment(self):
        for command in ("echo a#b>f", "echo $#>f", "echo x$#>f", "echo ${x#p}>f"):
            with self.subTest(command=command):
                self.assertTrue(pretool_gate.redirect_write_target(command))

    def test_a_hash_inside_braces_hides_nothing(self):
        # Bash does not read `#` there as a comment, and masking from it would
        # hide the redirect that follows on the same line.
        self.assertEqual("> f", pretool_gate.redirect_write_target("echo ${x/ #/y} > f"))
        self.assertEqual("> f", pretool_gate.redirect_write_target("echo ${x:- #} > f"))

    def test_braces_that_close_end_the_protection(self):
        self.assertEqual("", pretool_gate.redirect_write_target("echo ${x} # a>b"))
        self.assertEqual("", pretool_gate.redirect_write_target("echo ${x:-${y}} # a>b"))

    def test_a_heredoc_opener_inside_a_comment_strips_nothing(self):
        # `<<EOF` in a comment is not an opener; reading it as one deleted the
        # next lines, among them a real push.
        command = "echo ok # <<EOF\ngit push origin main\nEOF"
        self.assertIn("git push origin main", pretool_gate.strip_heredocs(command))

    def test_an_apostrophe_in_a_comment_no_longer_swallows_the_line_after_it(self):
        command = "ls # don't\necho z>f"
        self.assertEqual(">f", pretool_gate.redirect_write_target(command))


class OtherContextsStayUnmaskedTest(unittest.TestCase):
    """The remaining behaviour, pinned so a change to it is a decision."""

    def test_each_is_still_read_as_a_redirect(self):
        for command, expected in KNOWN_FALSE_POSITIVES.items():
            with self.subTest(command=command):
                self.assertEqual(expected, pretool_gate.redirect_write_target(command))

    def test_a_heredoc_body_a_shell_could_run_is_scanned(self):
        # runs_a_shell() cannot tell `cat <<EOF` from `bash <<EOF` once the body
        # has an unbalanced quote and a shebang, so the body is kept and its
        # redirects count. A false positive, not a hole; the fix is in runs_a_shell.
        command = "cat > s.sh <<'EOF'\n#!/bin/bash\n# don't\n> \"$OUT\"\nEOF"
        self.assertEqual(['> s.sh', '> "$OUT"'], list(pretool_gate.redirect_write_fragments(command)))

    def test_the_same_body_without_a_shell_word_is_stripped(self):
        command = "cat > s.sh <<'EOF'\n# don't\n> \"$OUT\"\nEOF"
        self.assertEqual(["> s.sh"], list(pretool_gate.redirect_write_fragments(command)))


class MaskIsTotalAndLinearTest(unittest.TestCase):

    NOISE = ('"', "'", '\\', '"$(', "$(", "${", "#", "\n", ")", "}", " ", "a", ">", "`")

    def test_length_and_line_count_survive_any_mix(self):
        import itertools
        for combo in itertools.product(self.NOISE, repeat=4):
            text = "".join(combo)
            masked = pretool_gate.mask_quoted(text)
            self.assertEqual(len(text), len(masked), repr(text))
            self.assertEqual(text.count("\n"), masked.count("\n"), repr(text))

    def test_an_unterminated_span_is_left_alone(self):
        for command in ("echo 'a > f", 'echo "a > f', 'echo "$(a > f'):
            with self.subTest(command=command):
                self.assertEqual("> f", pretool_gate.redirect_write_target(command))

    def test_input_built_to_defeat_the_pairing_stays_fast(self):
        for text in ('"$(' * 20000, '"$(echo ' * 20000, "'" * 50001, "# a" * 20000,
                     "${" * 20000 + " #x", "echo $(" * 20000,
                     "echo $(echo it's) " * 4000):
            started = time.perf_counter()
            pretool_gate.mask_quoted(text)
            pretool_gate.command_substitutions(text)
            self.assertLess(time.perf_counter() - started, 2.0, text[:12])

    def test_deep_nesting_does_not_raise(self):
        text = '"$(' * 600 + "x" + ')"' * 600
        self.assertEqual(len(text), len(pretool_gate.mask_quoted(text)))


class RedirectPatternsAreLinearOnDigitRunsTest(unittest.TestCase):
    """task-0108 (task-0102 review L5). `\\d*` ahead of the operator retried from
    every digit of a run, so n digits cost n^2 steps: 40,000 of them took 10.6 s
    in REDIR_RE and 10.2 s in NUL_REDIR_RE, after which the gate timed out."""

    def test_a_long_digit_run_costs_milliseconds(self):
        text = "1" * 40000
        for regex in (pretool_gate.REDIR_RE, pretool_gate.NUL_REDIR_RE):
            started = time.perf_counter()
            self.assertEqual([], list(regex.finditer(text)))
            self.assertLess(time.perf_counter() - started, 2.0, regex.pattern[:30])

    def test_a_descriptor_still_belongs_to_its_operator(self):
        self.assertEqual("2>f", pretool_gate.redirect_write_target("ls 2>f"))
        self.assertEqual("12>f", pretool_gate.redirect_write_target("ls 12>f"))
        self.assertEqual("2>NUL", pretool_gate.redirect_write_target("ls 2>NUL"))
        self.assertTrue(pretool_gate.redirects_to_bare_nul("ls 12>NUL:"))


if __name__ == "__main__":
    unittest.main()
