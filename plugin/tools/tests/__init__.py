"""Test package for the harness tools.

The suite must measure the code, not the environment of whoever ran it. One
inherited variable broke that: a session the supervisor spawns is marked with
AGENTRY_UNATTENDED=1, and pretool_gate.handle_bash() refuses a merge, a push and
approve.py for as long as that mark is set. Run the suite from such a session
and 26 tests in test_pretool_gate_git.py got the unattended refusal in place of
the one they assert on - red for a reason that has nothing to do with the code
under test (task-0090). The pipeline's `test` exit gate is this very suite and
it inherits the session environment, so a supervisor-relaunched task would have
burned its retry budget and parked blocked on those phantom failures.

The mark is therefore cleared here, at the single import root every test module
under `unittest discover` passes through, rather than in one module's fixture:
the next module to drive handle_bash() would have inherited the same bug. The
tests that need the mark SET it themselves with patch.dict(os.environ), which
this does not disturb.

The same root explains CLAUDE_PROJECT_DIR. The tools read the project root from that
variable and from nothing else (tools/paths.py: no walk up from the cwd, which would
find the tracked `.agentry/` template of src/ and of every src-wt/<task>/ tree). A
session Bash tool has it exported by session_start.py, pointing at the LIVE
workspace, so a suite that inherited it would read and write the live run state. It is
therefore SET here, unconditionally, to the checkout that holds these tests
(plugin/tools/tests -> the repository root, the directory the old `__file__`-derived
root named), before any tool module is imported. A test that needs another root
patches state.ROOT or hands a child process its own CLAUDE_PROJECT_DIR.
"""

import os
from pathlib import Path

# Keep this name equal to pretool_gate.UNATTENDED_ENV / supervisor.UNATTENDED_ENV.
# Spelled as a literal rather than imported, so the test package's import root
# stays free of sys.path surgery; EnvironmentHermeticityTest in
# test_pretool_gate_git.py is what proves the clear still works.
os.environ.pop("AGENTRY_UNATTENDED", None)
os.environ["CLAUDE_PROJECT_DIR"] = str(Path(__file__).resolve().parents[3])
