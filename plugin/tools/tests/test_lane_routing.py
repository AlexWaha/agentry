"""task-0030, FR-45: `lanes{}` binds each lane to one repo, and the Stop hook
offers a lane only the backlog tasks bound to that repo.

Three layers. `state.lanes()` / `lane_repo()` read the config; `read_backlog()`
filters by it; and TwoLanesDrivenForRealTest runs the real hook script, once per
lane, against a throwaway workspace holding two repos, two lanes and a backlog -
so the acceptance scenario is driven, not simulated."""

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

import state
import stop_gate
import tmproot
from test_conveyor_gaps import decide_output_with, run

REPOS = {
    "web": {"path": "web", "stack": "node", "stages": {}},
    "api": {"path": "api", "stack": "python", "stages": {}},
}
LANES = {"default": {"repo": "web"}, "backend": {"repo": "api"}}


def config(lanes=LANES, repos=REPOS) -> dict:
    cfg: dict = {}
    if lanes is not None:
        cfg["lanes"] = lanes
    if repos is not None:
        cfg["repos"] = repos
    return cfg


@contextlib.contextmanager
def stderr_captured():
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        yield buf


class LaneConfigTest(unittest.TestCase):
    """state.lane_name(), state.lanes(), state.lane_repo()."""

    def pipeline(self, cfg):
        p = unittest.mock.patch.object(state, "load_pipeline", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)

    def in_lane(self, name):
        p = unittest.mock.patch.object(state, "LANE", name)
        p.start()
        self.addCleanup(p.stop)

    def test_an_unset_lane_is_named_default(self):
        self.in_lane("")
        self.assertEqual("default", state.lane_name())

    def test_a_set_lane_is_named_as_it_was_set(self):
        self.in_lane("backend")
        self.assertEqual("backend", state.lane_name())

    def test_a_missing_lanes_object_is_empty_and_silent(self):
        self.pipeline({})
        with stderr_captured() as err:
            self.assertEqual({}, state.lanes())
        self.assertEqual("", err.getvalue())

    def test_lanes_that_is_not_an_object_is_empty_and_reported(self):
        self.pipeline({"lanes": ["default"]})
        with stderr_captured() as err:
            self.assertEqual({}, state.lanes())
        self.assertIn("lanes must be an object", err.getvalue())

    def test_each_lane_maps_to_its_repo_and_comment_keys_are_skipped(self):
        self.pipeline({"lanes": {"_comment": "text", **LANES}})
        with stderr_captured() as err:
            self.assertEqual({"default": "web", "backend": "api"}, state.lanes())
        self.assertEqual("", err.getvalue())

    def test_an_entry_without_exactly_one_repo_string_is_dropped_alone(self):
        bad = {"two": {"repo": ["web", "api"]}, "none": {}, "blank": {"repo": " "},
               "text": "web"}
        self.pipeline({"lanes": {**bad, "backend": {"repo": "api"}}})
        with stderr_captured() as err:
            self.assertEqual({"backend": "api"}, state.lanes())
        for name in bad:
            self.assertIn(repr(name), err.getvalue())

    def test_lane_repo_defaults_to_this_sessions_lane(self):
        self.pipeline(config())
        self.in_lane("backend")
        self.assertEqual("api", state.lane_repo())
        self.in_lane("")
        self.assertEqual("web", state.lane_repo())

    def test_lane_repo_of_a_named_lane_ignores_the_session_lane(self):
        self.pipeline(config())
        self.in_lane("backend")
        self.assertEqual("web", state.lane_repo("default"))

    def test_lane_repo_of_an_undeclared_lane_is_none(self):
        self.pipeline(config())
        self.assertIsNone(state.lane_repo("plan"))

    def test_lane_repo_without_lanes_is_none(self):
        self.pipeline({})
        self.assertIsNone(state.lane_repo())


def write_task(folder: Path, number: str, repo: str | None, body: str = "",
               newline: str = "\n") -> None:
    head = ["---", f"id: {number}"]
    if repo is not None:
        head.append(f"repo: {repo}")
    head += ["depends_on: []", "---"]
    text = newline.join(head) + newline + body
    # newline="" keeps a CRLF test file CRLF instead of translating it.
    with open(folder / f"task-{number}.md", "w", encoding="utf-8", newline="") as f:
        f.write(text)


class ReadBacklogLaneTest(unittest.TestCase):
    """read_backlog() over a real folder of task files."""

    def setUp(self):
        self.backlog = tmproot.sandbox(self, "lane_backlog_")

    def offered(self, lane: str, cfg: dict) -> list[dict]:
        with unittest.mock.patch.object(stop_gate, "BACKLOG_DIR", self.backlog), \
                unittest.mock.patch.object(state, "load_pipeline", return_value=cfg), \
                unittest.mock.patch.object(state, "LANE", lane), \
                stderr_captured():
            return stop_gate.read_backlog()

    def ids(self, lane: str, cfg: dict) -> list[str]:
        return [t["id"] for t in self.offered(lane, cfg)]

    def test_each_lane_is_offered_only_the_task_of_its_own_repo(self):
        write_task(self.backlog, "0001", "web")
        write_task(self.backlog, "0002", "api")
        self.assertEqual(["task-0001"], self.ids("", config()))
        self.assertEqual(["task-0002"], self.ids("backend", config()))

    def test_a_task_with_an_empty_repo_is_offered_to_the_default_lane_only(self):
        write_task(self.backlog, "0003", "")
        write_task(self.backlog, "0004", None)
        self.assertEqual(["task-0003", "task-0004"], self.ids("", config()))
        self.assertEqual([], self.ids("backend", config()))

    def test_a_quoted_repo_and_a_crlf_file_resolve_to_the_same_repo(self):
        write_task(self.backlog, "0001", '"web"')
        write_task(self.backlog, "0002", "web", newline="\r\n")
        self.assertEqual(["task-0001", "task-0002"], self.ids("", config()))

    def test_a_repo_named_only_in_the_body_is_not_the_tasks_repo(self):
        write_task(self.backlog, "0001", "api", body="repo: web\n")
        self.assertEqual([], self.ids("", config()))
        self.assertEqual(["task-0001"], self.ids("backend", config()))

    def test_a_repo_naming_a_path_inside_a_repo_routes_to_that_repos_lane(self):
        write_task(self.backlog, "0001", "api/src")
        self.assertEqual(["task-0001"], self.ids("backend", config()))
        self.assertEqual([], self.ids("", config()))
        self.assertFalse(self.offered("backend", config())[0].get("unknown_repo"))

    def test_entries_carry_their_repo(self):
        write_task(self.backlog, "0001", "web")
        self.assertEqual("web", self.offered("", config())[0]["repo"])

    def test_a_repo_that_is_no_key_of_repos_is_flagged_and_offered_to_no_lane_as_work(self):
        write_task(self.backlog, "0001", "web")
        write_task(self.backlog, "0005", "nope")
        for lane in ("", "backend"):
            with self.subTest(lane=lane):
                flagged = [t["id"] for t in self.offered(lane, config()) if t.get("unknown_repo")]
                self.assertEqual(["task-0005"], flagged)
        plain = [t["id"] for t in self.offered("", config()) if not t.get("unknown_repo")]
        self.assertEqual(["task-0001"], plain)

    def test_a_lane_that_lanes_does_not_declare_is_offered_nothing_and_told_why(self):
        write_task(self.backlog, "0001", "web")
        write_task(self.backlog, "0003", "")
        with unittest.mock.patch.object(stop_gate, "BACKLOG_DIR", self.backlog), \
                unittest.mock.patch.object(state, "load_pipeline", return_value=config()), \
                unittest.mock.patch.object(state, "LANE", "plan"), \
                stderr_captured() as err:
            self.assertEqual([], stop_gate.read_backlog())
        self.assertIn("'plan' is not declared", err.getvalue())

    def test_without_lanes_the_whole_backlog_is_offered_and_nothing_is_flagged(self):
        write_task(self.backlog, "0001", "web")
        write_task(self.backlog, "0002", "api")
        write_task(self.backlog, "0003", "")
        write_task(self.backlog, "0005", "nope")
        for lane in ("", "backend"):
            with self.subTest(lane=lane):
                got = self.offered(lane, config(lanes=None))
                self.assertEqual(["task-0001", "task-0002", "task-0003", "task-0005"],
                                 [t["id"] for t in got])
                self.assertFalse(any(t.get("unknown_repo") for t in got))

    def test_a_malformed_lanes_object_offers_the_whole_backlog(self):
        write_task(self.backlog, "0001", "web")
        write_task(self.backlog, "0002", "api")
        self.assertEqual(["task-0001", "task-0002"], self.ids("", config(lanes="oops")))

    def test_without_repos_a_lane_still_matches_its_repo_and_nothing_is_flagged(self):
        write_task(self.backlog, "0001", "web")
        write_task(self.backlog, "0002", "api")
        write_task(self.backlog, "0005", "nope")
        got = self.offered("", config(repos=None))
        self.assertEqual(["task-0001"], [t["id"] for t in got])

    def test_superseded_and_blocked_tasks_stay_out_whatever_their_repo(self):
        write_task(self.backlog, "0001", "web", body="")
        text = (self.backlog / "task-0001.md").read_text(encoding="utf-8")
        (self.backlog / "task-0001.md").write_text(
            text.replace("depends_on: []", "depends_on: []\nblocked_on: vendor"),
            encoding="utf-8")
        self.assertEqual([], self.ids("", config()))


BAD_TASK = {"id": "task-0005", "deps": [], "repo": "nope", "unknown_repo": True}
GOOD_TASK = {"id": "task-0002", "deps": [], "repo": "web"}


class UnknownRepoDecideTest(unittest.TestCase):
    """decide() names a task whose repo is no key of repos{}: through the
    run-less FR-37 counter, only when the lane has nothing else to start."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "lane_unknown_")
        p = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        q = unittest.mock.patch.object(
            stop_gate.git_state, "repo_entries", return_value=REPOS)
        q.start()
        self.addCleanup(q.stop)

    BAD = BAD_TASK
    GOOD = GOOD_TASK

    def stop(self, queue, **kw):
        return decide_output_with([], queue=queue, count_nags=True, **kw)

    def nag_count(self):
        return json.loads((self.tmp / stop_gate.DEBT_NAG_FILE).read_text(encoding="utf-8"))

    def test_the_task_is_named_with_its_repo_and_the_valid_keys(self):
        reason = self.stop([self.BAD])["reason"]
        for text in ("task-0005", "`repo: nope`", "repos{}", "api, web"):
            self.assertIn(text, reason)
        self.assertNotIn("Start the next ready task", reason)

    def test_the_branch_spends_the_fr37_counter_and_does_not_block_for_ever(self):
        first = self.stop([self.BAD])
        self.assertEqual({"key": "repo:task-0005", "count": 1}, self.nag_count())
        self.stop([self.BAD])
        self.stop([self.BAD])
        self.assertEqual(3, self.nag_count()["count"])
        escalated = self.stop([self.BAD])
        self.assertIn("survived", escalated["reason"])
        self.assertIn("AskUserQuestion", escalated["reason"])
        self.assertIsNone(self.stop([self.BAD]))
        self.assertNotEqual(first["reason"], escalated["reason"])

    def test_the_escalation_offers_the_repo_fix_and_not_the_debt_options(self):
        for _ in range(3):
            self.stop([self.BAD])
        out = self.stop([self.BAD])
        for text in (out["reason"], out["systemMessage"]):
            self.assertIn("repo:", text)
            self.assertNotIn("baseline", text)
            self.assertNotIn("waive", text)

    def test_a_ready_task_is_started_and_the_unroutable_one_waits(self):
        reason = self.stop([self.BAD, self.GOOD])["reason"]
        self.assertIn("Start the next ready task task-0002", reason)
        self.assertNotIn("task-0005", reason)

    def test_a_run_in_flight_is_driven_and_the_unroutable_task_is_not_mentioned(self):
        out = decide_output_with([run(task="task-0001", stage="implement")],
                                 queue=[self.BAD], count_nags=True)
        self.assertIn("task-0001", out["reason"])
        self.assertNotIn("task-0005", out["reason"])

    def test_below_the_auto_level_the_hook_offers_and_says_nothing(self):
        self.assertIsNone(self.stop([self.BAD], level="assisted"))

    def test_the_block_site_counts_pinned_by_the_conveyor_suite_are_unchanged(self):
        import inspect
        src = inspect.getsource(stop_gate.decide)
        self.assertEqual(7, src.count("return block("))
        self.assertEqual(5, src.count("return bounded_block("))
        self.assertIn("return bounded_block(", inspect.getsource(stop_gate.unknown_repo_block))


class DebtEscalationTextTest(unittest.TestCase):
    """bounded_block() grew an `options` parameter; its default must leave the
    documentation-debt escalation byte-for-byte what it was."""

    def setUp(self):
        self.tmp = tmproot.sandbox(self, "lane_debt_")
        p = unittest.mock.patch.object(state, "STATE_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def test_the_debt_escalation_keeps_its_wording(self):
        debt = ({"task": "task-0067", "reason": "no handoff doc"},)
        for _ in range(3):
            decide_output_with([], debt=debt, count_nags=True, queue=[])
        out = decide_output_with([], debt=debt, count_nags=True, queue=[])
        self.assertIn("pay it, waive it (handoff.py --waive / update.py --stamp --none), "
                      "or change the baseline in pipeline.json - and your recommendation",
                      out["reason"])
        self.assertTrue(out["systemMessage"].endswith(
            "Decision owed by you: pay it, waive it, or change the baseline in pipeline.json."))


class TwoLanesDrivenForRealTest(unittest.TestCase):
    """The acceptance scenario, driven: the real stop_gate.py script runs once per
    lane (PIPELINE_LANE in the environment) over a throwaway workspace that holds
    two repos, two lanes and a backlog. Nothing is patched."""

    def workspace(self, tasks: dict[str, str | None], cfg: dict | None = None) -> Path:
        root = tmproot.sandbox(self, "lane_real_")
        agentry = root / ".agentry"
        backlog = agentry / "tasks" / "backlog"
        for sub in ("active", "done"):
            (agentry / "tasks" / sub).mkdir(parents=True)
        backlog.mkdir(parents=True)
        (agentry / "state").mkdir()
        # One approval level per lane (approvals.py), so each lane is set to auto.
        for suffix in ("", ".backend"):
            (agentry / "state" / f"approvals{suffix}").write_text("auto\n", encoding="utf-8")
        flow = {"build": {"stages": [
            {"name": "implement"}, {"name": "test"}, {"name": "review"},
            {"name": "ready", "checkpoints": ["commit"]}, {"name": "done"}]}}
        (agentry / "pipeline.json").write_text(
            json.dumps({"pipelines": flow, **(cfg if cfg is not None else config())}),
            encoding="utf-8")
        for number, repo in tasks.items():
            write_task(backlog, number, repo)
        return root

    def drive(self, root: Path, lane: str) -> dict | None:
        env = {k: v for k, v in os.environ.items() if k != "PIPELINE_LANE"}
        env["CLAUDE_PROJECT_DIR"] = str(root)  # the throwaway workspace is the project root
        if lane:
            env["PIPELINE_LANE"] = lane
        script = PIPELINE_DIR / "stop_gate.py"
        done = subprocess.run([sys.executable, str(script)], input="{}", env=env,
                              capture_output=True, text=True, timeout=120, cwd=root)
        out = done.stdout.strip()
        return json.loads(out) if out else None

    def test_two_lanes_two_repos_each_lane_is_offered_only_its_own_task(self):
        root = self.workspace({"0001": "web", "0002": "api"})
        web = self.drive(root, "")
        api = self.drive(root, "backend")
        self.assertIn("Start the next ready task task-0001", web["reason"])
        self.assertNotIn("task-0002", web["reason"])
        self.assertIn("Start the next ready task task-0002", api["reason"])
        self.assertNotIn("task-0001", api["reason"])

    def test_a_task_with_an_empty_repo_goes_to_the_default_lane_only(self):
        root = self.workspace({"0003": ""})
        self.assertIn("Start the next ready task task-0003", self.drive(root, "")["reason"])
        self.assertIsNone(self.drive(root, "backend"))

    def test_a_task_naming_a_repo_that_is_not_in_repos_is_named_to_the_default_lane(self):
        root = self.workspace({"0005": "nope"})
        out = self.drive(root, "")
        self.assertIn("task-0005", out["reason"])
        self.assertIn("not a key of repos{}", out["reason"])
        self.assertNotIn("Start the next ready task", out["reason"])
        counter = json.loads((root / ".agentry" / "state" / stop_gate.DEBT_NAG_FILE)
                             .read_text(encoding="utf-8"))
        self.assertEqual({"key": "repo:task-0005", "count": 1}, counter)

    def test_without_lanes_both_lanes_see_the_whole_backlog_as_before(self):
        root = self.workspace({"0001": "web", "0002": "api"}, cfg=config(lanes=None))
        for lane in ("", "backend"):
            with self.subTest(lane=lane):
                self.assertIn("Start the next ready task task-0001",
                              self.drive(root, lane)["reason"])


if __name__ == "__main__":
    unittest.main()
