import { describe, expect, it } from "vitest";

import { orderedSections } from "./TaskBody";

describe("orderedSections", () => {
  it("drops the parenthetical of a heading", () => {
    const sections = orderedSections("## Acceptance Criteria (spec trace)\n\n- [ ] one\n");

    expect(sections.map((section) => section.title)).toEqual(["Acceptance Criteria"]);
  });

  it("splits a heading of 100000 spaces in linear time", () => {
    const body = `## Notes${" ".repeat(100_000)}x\n\ntext\n`;
    const start = performance.now();

    const sections = orderedSections(body);

    expect(performance.now() - start).toBeLessThan(500);
    expect(sections).toHaveLength(1);
  });
});
