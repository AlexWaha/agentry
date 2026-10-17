"""docs/technical/dashboard-api.yaml against the server (spec-0002 FR-16).

There is no YAML or JSON-schema library in the standard library, so this module carries a reader for
the subset of YAML the file uses and a validator for the subset of JSON Schema it uses. Both are
exercised on their own first, so a green result means the live responses really matched the schemas.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
REPO_ROOT = TOOLS_DIR.parents[1]
for _dir in (TOOLS_DIR / "memory", TOOLS_DIR / "pipeline", TOOLS_DIR / "dashboard", TESTS_DIR):
    sys.path.insert(0, str(_dir))

import dashboard_sandbox as sb
import server
import stores
import tmproot
import views_harness

SPEC_PATH = REPO_ROOT / "docs" / "technical" / "dashboard-api.yaml"


# --------------------------------------------------------------------------- YAML subset reader

class YamlError(ValueError):
    pass


def _split_key(text: str) -> tuple[str, str] | None:
    """('key', 'rest') for a `key: rest` line (the colon followed by a space or the end of line,
    outside quotes), else None."""
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == ":" and (i + 1 == len(text) or text[i + 1] == " "):
            return text[:i].strip(), text[i + 1:].strip()
    return None


def _scalar(text: str):
    text = text.strip()
    if text.startswith(("{", "[")):
        value, end = _flow(text, 0)
        if text[end:].strip():
            raise YamlError(f"trailing text after a flow value: {text!r}")
        return value
    return _plain(text)


def _plain(text: str):
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _flow(text: str, pos: int):
    """(value, end) of the flow collection or scalar starting at text[pos]."""
    while text[pos] == " ":
        pos += 1
    if text[pos] == "{":
        out, pos = {}, pos + 1
        while True:
            while text[pos] in " ,":
                pos += 1
            if text[pos] == "}":
                return out, pos + 1
            key, pos = _flow_token(text, pos, ":")
            pos += 1  # the colon
            out[key], pos = _flow(text, pos)
    if text[pos] == "[":
        out, pos = [], pos + 1
        while True:
            while text[pos] in " ,":
                pos += 1
            if text[pos] == "]":
                return out, pos + 1
            item, pos = _flow(text, pos)
            out.append(item)
    raw, pos = _flow_token(text, pos, ",}]")
    return _plain(raw), pos


def _flow_token(text: str, pos: int, stops: str):
    """Text up to the first stop character outside quotes, and the position of that character."""
    start, quote = pos, ""
    while pos < len(text):
        ch = text[pos]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and pos == start:
            quote = ch
        elif ch in stops:
            break
        pos += 1
    return text[start:pos].strip(), pos


def load_yaml(text: str):
    lines = []
    for raw in text.splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            lines.append([len(raw) - len(raw.lstrip(" ")), raw.strip()])

    def block(i: int, indent: int):
        if lines[i][1].startswith("- "):
            return sequence(i, indent)
        return mapping(i, indent)

    def sequence(i: int, indent: int):
        out = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            rest = lines[i][1][2:].strip()
            if not rest:
                value, i = block(i + 1, lines[i + 1][0])
            elif rest[0] not in "{[\"'" and _split_key(rest):
                lines[i] = [indent + 2, rest]  # `- key: value` opens a mapping at the key's column
                value, i = mapping(i, indent + 2)
            else:
                value, i = _scalar(rest), i + 1
            out.append(value)
        return out, i

    def mapping(i: int, indent: int):
        out = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            split = _split_key(lines[i][1])
            if split is None:
                raise YamlError(f"not a mapping line: {lines[i][1]!r}")
            key, rest = split
            key = _plain(key) if key[:1] in "\"'" else key
            i += 1
            if rest in (">", ">-", "|", "|-"):
                folded = []
                while i < len(lines) and lines[i][0] > indent:
                    folded.append(lines[i][1])
                    i += 1
                out[key] = " ".join(folded)
            elif rest:
                out[key] = _scalar(rest)
            elif i < len(lines) and lines[i][0] > indent:
                out[key], i = block(i, lines[i][0])
            else:
                out[key] = None
        return out, i

    value, end = block(0, lines[0][0])
    if end != len(lines):
        raise YamlError(f"stopped at line {lines[end][1]!r}")
    return value


# --------------------------------------------------------------------------- JSON Schema subset

def resolve(root: dict, ref: str):
    node = root
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node


def type_ok(value, name: str) -> bool:
    return {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
        "null": value is None,
    }[name]


def validate(value, schema: dict, root: dict, path: str = "$") -> list[str]:
    """Errors of `value` against `schema`; [] when it conforms."""
    if "$ref" in schema:
        return validate(value, resolve(root, schema["$ref"]), root, path)
    errors: list[str] = []
    if "oneOf" in schema:
        matching = sum(not validate(value, option, root, path) for option in schema["oneOf"])
        if matching != 1:
            errors.append(f"{path}: matches {matching} of the oneOf options")
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        errors.append(f"{path}: {value!r} is not the constant {schema['const']!r}")
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(type_ok(value, n) for n in names):
            return [*errors, f"{path}: {value!r} is not of type {names}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema \
            and value < schema["minimum"]:
        errors.append(f"{path}: {value} is below {schema['minimum']}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing {name}")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in schema.get("properties", {}):
                errors += validate(item, schema["properties"][name], root, f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}: unexpected key {name}")
            elif isinstance(extra, dict):
                errors += validate(item, extra, root, f"{path}.{name}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], root, f"{path}[{i}]")
    return errors


# --------------------------------------------------------------------------- the readers themselves

class YamlReaderTest(unittest.TestCase):

    def test_maps_lists_scalars_and_comments(self):
        data = load_yaml("# top\na: 1\nb:\n  - x\n  - y: 2\n    z: [1, 'q', \"r\"]\n  - {k: v, n: null}\n"
                         "c:\n  d: true\n  e: 1.5\n  f: '^a{1,3}$'\n\"200\": {$ref: \"#/x\"}\n")
        self.assertEqual({"a": 1, "b": ["x", {"y": 2, "z": [1, "q", "r"]}, {"k": "v", "n": None}],
                          "c": {"d": True, "e": 1.5, "f": "^a{1,3}$"}, "200": {"$ref": "#/x"}}, data)

    def test_folded_scalars_nested_flow_and_awkward_keys(self):
        data = load_yaml("a: >-\n  one\n  two\nb: {x: [a, \"null\"], y: {z: 1}}\n"
                         "/api/v1/tasks/{id}:\n  get: {}\nversion: \"1\"\nopenapi: 3.1.0\n")
        self.assertEqual({"a": "one two", "b": {"x": ["a", "null"], "y": {"z": 1}},
                          "/api/v1/tasks/{id}": {"get": {}}, "version": "1", "openapi": "3.1.0"}, data)

    def test_a_dash_item_opening_a_mapping_continues_on_deeper_lines(self):
        data = load_yaml("p:\n  - {name: a}\n  - name: limit\n    in: query\n    schema: {type: integer}\n")
        self.assertEqual({"p": [{"name": "a"}, {"name": "limit", "in": "query",
                                                 "schema": {"type": "integer"}}]}, data)

    def test_the_shipped_file_parses(self):
        spec = load_yaml(SPEC_PATH.read_text(encoding="utf-8"))
        self.assertEqual("3.1.0", spec["openapi"])
        self.assertTrue({"info", "paths", "components"} <= set(spec))


class SchemaReaderTest(unittest.TestCase):

    def errors(self, value, schema) -> list[str]:
        root = {"components": {"schemas": {"S": {"type": "object", "required": ["a"],
                                                  "additionalProperties": False,
                                                  "properties": {"a": {"type": "integer", "minimum": 1}}}}}}
        return validate(value, schema, root)

    def test_types_are_strict_about_booleans_and_numbers(self):
        self.assertEqual([], self.errors(1, {"type": "integer"}))
        self.assertTrue(self.errors(True, {"type": "integer"}))
        self.assertTrue(self.errors(1.5, {"type": "integer"}))
        self.assertEqual([], self.errors(1.5, {"type": "number"}))
        self.assertEqual([], self.errors(None, {"type": ["string", "null"]}))
        self.assertTrue(self.errors(None, {"type": "string"}))

    def test_objects_check_required_extra_and_nested_properties(self):
        ref = {"$ref": "#/components/schemas/S"}
        self.assertEqual([], self.errors({"a": 1}, ref))
        self.assertTrue(self.errors({}, ref))
        self.assertTrue(self.errors({"a": 1, "b": 2}, ref))
        self.assertTrue(self.errors({"a": 0}, ref))
        self.assertTrue(self.errors({"a": "x"}, ref))

    def test_enum_pattern_const_items_and_one_of(self):
        self.assertTrue(self.errors("c", {"enum": ["a", "b"]}))
        self.assertTrue(self.errors("abc", {"type": "string", "pattern": "^x"}))
        self.assertTrue(self.errors(1, {"const": True}))
        self.assertTrue(self.errors([1, "x"], {"type": "array", "items": {"type": "integer"}}))
        both = {"oneOf": [{"type": "integer"}, {"type": "number"}]}
        self.assertTrue(self.errors(1, both))      # matches both: not exactly one
        self.assertEqual([], self.errors(1.5, both))


# --------------------------------------------------------------------------- the file against the server

class SpecCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spec = load_yaml(SPEC_PATH.read_text(encoding="utf-8"))


class SpecShapeTest(SpecCase):

    def test_the_documented_paths_are_exactly_the_served_routes(self):
        self.assertEqual(sorted(server.ROUTE_TEMPLATES), sorted(self.spec["paths"]))

    def test_every_route_is_get_only_with_a_unique_operation_id(self):
        ids = []
        for path, item in self.spec["paths"].items():
            with self.subTest(path=path):
                self.assertEqual(["get"], list(item))
                ids.append(item["get"]["operationId"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_reference_resolves(self):
        refs = []

        def walk(node):
            if isinstance(node, dict):
                refs.extend(v for k, v in node.items() if k == "$ref")
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.spec)
        self.assertGreater(len(refs), 30)
        for ref in refs:
            with self.subTest(ref=ref):
                resolve(self.spec, ref)

    def test_every_route_documents_the_token_error_and_a_json_success(self):
        for path, item in self.spec["paths"].items():
            responses = item["get"]["responses"]
            with self.subTest(path=path):
                self.assertIn("200", responses)
                self.assertIn("application/json", responses["200"]["content"])
                self.assertIn("401", responses)
                self.assertEqual("bearerAuth", next(iter(self.spec["security"][0])))


class LiveResponseTest(SpecCase):
    """A real server on a populated sandbox; every documented 200 schema must accept what it sends."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = tmproot.mkdtemp("dash_oas_")
        cls.addClassCleanup(tmproot.rmtree, cls.root)
        sb.build_project(cls.root)
        files = ["a/x.py", "a/y.py", "b/z.py"]
        sb.make_codegraph(cls.root, files, [("a/x.py", "b/z.py", 2), ("a/y.py", "b/z.py", 1)])
        sb.seed(cls.root, {"supervisor": {"task-0106": {"last_label": "DEAD", "seen": 1790000000.0}}})
        cls.server = sb.start_server(cls.root)
        cls.addClassCleanup(cls.server.stop)

    def url(self, template: str, query: str = "") -> str:
        return template.replace("{id}", "task-0106").replace("{key}", "root") + query

    def schema(self, template: str, status: str = "200") -> dict:
        response = self.spec["paths"][template]["get"]["responses"][status]
        return response["content"]["application/json"]["schema"]

    def assertConforms(self, template: str, query: str = "") -> dict:
        status, _, body = self.server.get(self.url(template, query))
        self.assertEqual(200, status, body)
        payload = json.loads(body)
        problems = validate(payload, self.schema(template), self.spec)
        self.assertEqual([], problems[:5], f"{template}{query}")
        return payload

    def test_each_route_validates_against_its_documented_schema(self):
        for template in sorted(self.spec["paths"]):
            with self.subTest(route=template):
                self.assertConforms(template)

    def test_variants_with_other_data_validate_too(self):
        for template, query in (("/api/v1/tasks/{id}", ""), ("/api/v1/memory", "?kind=pattern"),
                                ("/api/v1/memory", "?kind=module"), ("/api/v1/memory", "?retired=include"),
                                ("/api/v1/tasks", "?include_superseded=true&limit=500"),
                                ("/api/v1/events", "?kind=gate&result=fail"),
                                ("/api/v1/metrics", "?since=2026-10-01"), ("/api/v1/runs", "?lane=fix"),
                                ("/api/v1/codegraph/{key}/graph", "?level=file&limit=10")):
            with self.subTest(route=template, query=query):
                self.assertConforms(template, query)

    def test_tasks_without_a_run_or_handoff_validate(self):
        for task in ("task-0101", "task-0102", "task-0104", "task-0107"):
            with self.subTest(task=task):
                status, _, body = self.server.get(f"/api/v1/tasks/{task}")
                self.assertEqual(200, status)
                self.assertEqual([], validate(json.loads(body), self.schema("/api/v1/tasks/{id}"), self.spec))

    def test_the_documented_error_bodies_match_what_the_server_sends(self):
        error = {"$ref": "#/components/schemas/Error"}
        for path in ("/api/v1/nope", "/api/v1/tasks?limit=0", "/api/v1/tasks/task-9999", "/api/v1/tasks/x"):
            with self.subTest(path=path):
                status, _, body = self.server.get(path)
                self.assertGreaterEqual(status, 400)
                self.assertEqual([], validate(json.loads(body), error, self.spec))
        status, _, body = self.server.get("/api/v1/status", {"Authorization": None})
        self.assertEqual((401, []), (status, validate(json.loads(body), error, self.spec)))
        status, _, body = self.server.request("POST", "/api/v1/status")
        self.assertEqual((405, []), (status, validate(json.loads(body), error, self.spec)))

    def test_the_validator_rejects_a_perturbed_live_response(self):
        data = json.loads(self.server.get("/api/v1/status")[2])
        schema = self.schema("/api/v1/status")
        for mutate in (lambda d: d["data"].pop("counts"), lambda d: d["data"].update(extra=1),
                       lambda d: d["data"]["counts"].update(done="4"), lambda d: d.update(data=[]),
                       lambda d: d["data"].update(protocol=2),
                       lambda d: d["data"]["lanes"][0].update(approvals="whenever")):
            broken = json.loads(json.dumps(data))
            mutate(broken)
            self.assertTrue(validate(broken, schema, self.spec))

    def test_documented_parameter_ranges_are_the_ones_the_server_enforces(self):
        for template, item in self.spec["paths"].items():
            for param in item["get"].get("parameters", []):
                param = resolve(self.spec, param["$ref"]) if "$ref" in param else param
                if param["in"] != "query":
                    continue
                schema = param["schema"]
                name = param["name"]
                with self.subTest(route=template, param=name):
                    def status_of(value) -> int:
                        return self.server.get(self.url(template, f"?{name}={value}"))[0]

                    if "maximum" in schema:
                        self.assertEqual(200, status_of(schema["maximum"]))
                        self.assertEqual(422, status_of(schema["maximum"] + 1))
                    if "minimum" in schema:
                        self.assertEqual(200, status_of(schema["minimum"]))
                        self.assertEqual(422, status_of(schema["minimum"] - 1))
                    for allowed in schema.get("enum", []):
                        self.assertEqual(200, status_of(allowed))
                    if "enum" in schema:
                        self.assertEqual(422, status_of("not-a-value"))


class HarnessSchemaTest(SpecCase):
    """The real repository tree (agents, rules, skills, hooks) against the Harness schema."""

    def test_the_repository_harness_validates(self):
        ctx = stores.Ctx(REPO_ROOT, REPO_ROOT / "plugin", 1, "http://127.0.0.1:1", "t", REPO_ROOT)
        data, _ = views_harness.harness(ctx, stores.Params({}))
        schema = {"$ref": "#/components/schemas/Harness"}
        self.assertEqual([], validate(json.loads(json.dumps(data)), schema, self.spec))
        self.assertGreater(len(data["agents"]), 20)


if __name__ == "__main__":
    unittest.main()
