import { describe, expect, it } from "vitest";

import { tabPatch, validateMemorySearch } from "./search";

describe("validateMemorySearch", () => {
  it("keeps every lesson filter and omits the default tab", () => {
    expect(validateMemorySearch({ tab: "lesson", q: "hook", agent: "qa", area: "hooks", task: "task-0013", retired: "only", page: 2 })).toEqual({
      q: "hook",
      agent: "qa",
      area: "hooks",
      task: "task-0013",
      retired: "only",
      page: 2,
    });
  });

  it("keeps only the filters of the pattern and module tabs", () => {
    expect(validateMemorySearch({ tab: "pattern", q: "x", agent: "qa", area: "hooks", task: "task-1", retired: "only" })).toEqual({
      tab: "pattern",
      q: "x",
      task: "task-1",
    });
    expect(validateMemorySearch({ tab: "module", q: "x", task: "task-1" })).toEqual({ tab: "module", q: "x" });
  });

  it("keeps nothing but the tab on the Stats tab", () => {
    expect(validateMemorySearch({ tab: "stats", q: "x", page: 3 })).toEqual({ tab: "stats" });
  });

  it("drops invalid values and treats a numeric search text as text", () => {
    expect(validateMemorySearch({ tab: "nope", retired: "exclude", page: "abc", q: 123 })).toEqual({ q: "123" });
    expect(validateMemorySearch({ page: 0 })).toEqual({});
  });
});

describe("tabPatch", () => {
  it("clears the filters that do not apply to the new tab and sets the tab", () => {
    expect(tabPatch("pattern")).toEqual({ tab: "pattern", agent: undefined, area: undefined, retired: undefined });
    expect(tabPatch("module")).toEqual({ tab: "module", agent: undefined, area: undefined, task: undefined, retired: undefined });
    expect(tabPatch("stats")).toMatchObject({ tab: "stats", q: undefined, task: undefined });
  });

  it("leaves every filter in place on the default tab", () => {
    expect(tabPatch("lesson")).toEqual({ tab: undefined });
  });
});
