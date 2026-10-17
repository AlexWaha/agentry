"""Contract test (FR-120): the Vitest fixtures are validated against what the server really answers.

Every file under dashboard/src/test/fixtures/<group>/ is `{"route": "/api/v1/...", "body": {"data", "meta"}}`.
The fixture is requested from a sandbox server and the two bodies must have the same keys and the same JSON
types, so a renamed or retyped field fails here instead of in front of the User. Page tasks add their own
fixture folders; this module discovers every group, so adding a fixture needs no edit here.

Rules of the comparison, all derived from the spec shapes:
  * objects must have the same key set, except the open maps (a map from names to numbers or to an optional
    detail object), whose values are compared by type only;
  * lists are compared through their first element, and a list that is empty on either side is skipped
    (a sandbox cannot be forced to hold every kind of row);
  * a field the spec marks nullable may be null in the fixture or in the answer, and is then not
    descended into.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import tmproot

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
import dashboard_sandbox as sandbox  # noqa: E402

REPO = TESTS_DIR.parents[2]
FIXTURES = REPO / "dashboard" / "src" / "test" / "fixtures"
DIST = TESTS_DIR.parent / "dashboard" / "dist"

# Keys whose value is a map with free keys: only the value types are compared.
OPEN_MAPS = frozenset({"kinds", "by_agent", "by_area", "detail"})

# Fields the spec types as nullable (`X | null`), by key name anywhere in a body.
NULLABLE = frozenset({
    "needs_you", "supervisor", "run", "handoff", "memory_stamped_at", "lead_time_s", "timeout",
})

REQUIRED_BODY_KEYS = {"data"}
GROUP_NAMES = ("shell", "pipeline", "activity", "memory", "harness")


def kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def diff(fixture, live, path: str = "$", key: str = "") -> list[str]:
    """Differences between a fixture value and the live value at the same place."""
    f_kind, l_kind = kind(fixture), kind(live)
    if f_kind == "null" or l_kind == "null":
        if f_kind == l_kind or key in NULLABLE:
            return []
        return [f"{path}: fixture is {f_kind}, live is {l_kind}"]
    if f_kind != l_kind:
        return [f"{path}: fixture is {f_kind}, live is {l_kind}"]
    if f_kind == "array":
        if not fixture or not live:
            return []
        return diff(fixture[0], live[0], f"{path}[0]", key)
    if f_kind == "object":
        if key in OPEN_MAPS:
            return _open_map_diff(fixture, live, path)
        problems = [f"{path}.{name}: missing in the live answer" for name in sorted(set(fixture) - set(live))]
        problems += [f"{path}.{name}: missing in the fixture" for name in sorted(set(live) - set(fixture))]
        for name in sorted(set(fixture) & set(live)):
            problems += diff(fixture[name], live[name], f"{path}.{name}", name)
        return problems
    return []


def _open_map_diff(fixture: dict, live: dict, path: str) -> list[str]:
    types = {kind(value) for value in fixture.values()} | {kind(value) for value in live.values()}
    return [] if len(types) <= 1 else [f"{path}: values of mixed types {sorted(types)}"]


def discover() -> list[Path]:
    return sorted(path for group in FIXTURES.iterdir() if group.is_dir() for path in group.glob("*.json"))


class ContractHelpersTest(unittest.TestCase):
    """The comparison itself, on small hand-made values (no server)."""

    def test_equal_shapes_have_no_difference(self):
        self.assertEqual(diff({"a": 1, "b": ["x"]}, {"a": 7.5, "b": ["y", "z"]}), [])

    def test_a_renamed_key_fails_in_both_directions(self):
        problems = diff({"id": "x", "title": "t"}, {"id": "x", "name": "t"})

        self.assertEqual(problems, ["$.title: missing in the live answer", "$.name: missing in the fixture"])

    def test_a_retyped_field_fails(self):
        self.assertEqual(diff({"total": 1}, {"total": "1"}), ["$.total: fixture is number, live is string"])

    def test_a_nullable_field_may_be_null_on_either_side(self):
        self.assertEqual(diff({"run": None}, {"run": {"task": "x"}}), [])
        self.assertEqual(diff({"run": {"task": "x"}}, {"run": None}), [])
        self.assertEqual(diff({"title": None}, {"title": "x"}), ["$.title: fixture is null, live is string"])

    def test_empty_lists_are_skipped_and_non_empty_lists_compare_their_first_element(self):
        self.assertEqual(diff({"rows": []}, {"rows": [{"a": 1}]}), [])
        self.assertEqual(diff({"rows": [{"a": 1}]}, {"rows": []}), [])
        self.assertEqual(diff({"rows": [{"a": 1}]}, {"rows": [{"b": 1}]}), [
            "$.rows[0].a: missing in the live answer", "$.rows[0].b: missing in the fixture"])

    def test_open_maps_compare_value_types_only(self):
        self.assertEqual(diff({"kinds": {"gate": 3}}, {"kinds": {"merged": 1, "approved": 2}}), [])
        self.assertEqual(diff({"kinds": {"gate": 3}}, {"kinds": {"merged": "1"}}),
                         ["$.kinds: values of mixed types ['number', 'string']"])


class FixtureFilesTest(unittest.TestCase):
    def test_fixtures_exist_and_are_well_formed(self):
        files = discover()
        self.assertTrue(files, "no fixture found")
        for path in files:
            with self.subTest(fixture=path.relative_to(FIXTURES).as_posix()):
                self.assertIn(path.parent.name, GROUP_NAMES)
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(sorted(document), ["body", "route"])
                self.assertRegex(document["route"], r"^/api/v1/[\w/%.-]+(\?[\w=&%.,:-]*)?$")
                self.assertTrue(REQUIRED_BODY_KEYS <= set(document["body"]))

    def test_the_shell_group_covers_the_routes_the_shell_reads(self):
        files = (FIXTURES / "shell").glob("*.json")
        routes = {json.loads(path.read_text(encoding="utf-8"))["route"] for path in files}

        self.assertEqual(routes, {"/api/v1/status", "/api/v1/runs"})


class LiveContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = tmproot.mkdtemp("dash_contract_")
        cls.addClassCleanup(tmproot.rmtree, root)
        project = sandbox.build_project(root / "project", "small")
        # A code index, so the codegraph routes answer with data and their fixtures are compared (FR-120).
        sandbox.make_codegraph(
            project,
            ["plugin/tools/hooks/a.py", "plugin/tools/pipeline/b.py", "plugin/tools/memory/c.py"],
            [("plugin/tools/hooks/a.py", "plugin/tools/pipeline/b.py", 3),
             ("plugin/tools/hooks/a.py", "plugin/tools/memory/c.py", 1)],
        )
        cls.server = sandbox.start_server(project, dist=DIST)
        cls.addClassCleanup(cls.server.stop)

    def live(self, route: str) -> dict:
        status, _head, body = self.server.get_json(route)
        self.assertEqual(status, 200, f"{route} answered {status}: {body}")
        return body

    def test_every_fixture_has_the_shape_the_server_answers_with(self):
        for path in discover():
            name = path.relative_to(FIXTURES).as_posix()
            with self.subTest(fixture=name):
                document = json.loads(path.read_text(encoding="utf-8"))
                problems = diff(document["body"], self.live(document["route"]))
                self.assertEqual(problems, [], f"{name} differs from {document['route']}")

    def test_a_renamed_fixture_key_is_detected_against_the_live_answer(self):
        document = json.loads((FIXTURES / "shell" / "status.json").read_text(encoding="utf-8"))
        document["body"]["data"]["plugin_ver"] = document["body"]["data"].pop("plugin_version")

        problems = diff(document["body"], self.live(document["route"]))

        self.assertIn("$.data.plugin_ver: missing in the live answer", problems)
        self.assertIn("$.data.plugin_version: missing in the fixture", problems)

    def test_a_retyped_fixture_value_is_detected(self):
        document = json.loads((FIXTURES / "shell" / "status.json").read_text(encoding="utf-8"))
        document["body"]["data"]["read_only"] = "yes"

        problems = diff(document["body"], self.live(document["route"]))

        self.assertIn("$.data.read_only: fixture is string, live is boolean", problems)


if __name__ == "__main__":
    unittest.main()
