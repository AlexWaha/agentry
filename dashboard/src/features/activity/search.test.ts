import { describe, expect, it } from "vitest";

import { validateActivitySearch } from "./search";

describe("validateActivitySearch", () => {
  it("keeps valid filters and the page", () => {
    expect(validateActivitySearch({ task: "0136", kind: "gate", result: "fail", page: 3 })).toEqual({
      task: "0136",
      kind: "gate",
      result: "fail",
      page: 3,
    });
  });

  it("reads a number-looking task as text", () => {
    expect(validateActivitySearch({ task: 136 }).task).toBe("136");
  });

  it("drops a task the API would refuse", () => {
    expect(validateActivitySearch({ task: "no spaces" }).task).toBeUndefined();
    expect(validateActivitySearch({ task: "x".repeat(33) }).task).toBeUndefined();
  });

  it("drops an unknown kind and an unknown result", () => {
    const search = validateActivitySearch({ kind: "bogus", result: "maybe" });

    expect(search.kind).toBeUndefined();
    expect(search.result).toBeUndefined();
  });

  it("omits page 1 and an invalid page", () => {
    expect(validateActivitySearch({ page: 1 }).page).toBeUndefined();
    expect(validateActivitySearch({ page: "abc" }).page).toBeUndefined();
    expect(validateActivitySearch({ page: -4 }).page).toBeUndefined();
  });
});
