import { describe, expect, it } from "vitest";

import { ratio } from "@/test/qa/contrast";

describe.each(["light", "dark"] as const)("%s control border (WCAG 1.4.11)", (mode) => {
  it.each(["--background", "--card", "--muted"])("--border-control against %s reaches 3:1", (surface) => {
    expect(ratio(mode, "--border-control", surface)).toBeGreaterThanOrEqual(3);
  });
});
