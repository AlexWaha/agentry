import { describe, expect, it } from "vitest";

import { kindClass, kindLabel, kindPaint, PAINT_TOKENS } from "./kinds";

/*
 * Two tables colour a node kind: Tailwind classes for the legend swatches (kindClass) and CSS custom property names
 * for the canvas and the 3D scene (kindPaint). They must say the same thing, or the legend lies about the drawing.
 */

const KINDS = ["lesson", "pattern", "task", "agent", "area", "module", "file"];

/** `fill-status-active-fg` becomes `--status-active-fg`; a stroke width such as `stroke-1` is not a colour. */
function tokenOf(classes: string, utility: "fill" | "stroke"): string | undefined {
  const match = classes.split(/\s+/).find((name) => name.startsWith(`${utility}-`) && !/^stroke-\d/.test(name));
  return match ? `--${match.slice(utility.length + 1)}` : undefined;
}

describe("kind colours", () => {
  it.each(KINDS)("the legend swatch and the drawing agree on the fill and the ring of a %s", (kind) => {
    const classes = kindClass(kind);
    const paint = kindPaint(kind);

    expect(tokenOf(classes, "fill")).toBe(paint.fill);
    expect(tokenOf(classes, "stroke")).toBe(paint.stroke);
  });

  it("agree on the width of the ring too", () => {
    for (const kind of KINDS) {
      const width = /stroke-(\d)/.exec(kindClass(kind))?.[1];
      expect(Number(width)).toBe(kindPaint(kind).strokeWidth);
    }
  });

  it("gives each kind the colour the product promises: lessons and modules blue, patterns green, an area a ring", () => {
    expect(kindPaint("lesson").fill).toBe("--status-active-fg");
    expect(kindPaint("module").fill).toBe("--status-active-fg");
    expect(kindPaint("pattern").fill).toBe("--status-done-fg");
    expect(kindPaint("task").fill).toBe("--muted-foreground");
    expect(kindPaint("agent").fill).toBe("--foreground");
    expect(kindPaint("area")).toEqual({ fill: "--card", stroke: "--foreground", strokeWidth: 2 });
  });

  it("tells the kinds that share a colour apart by fill versus ring, never by colour alone", () => {
    const looks = KINDS.map((kind) => JSON.stringify(kindPaint(kind)));

    expect(new Set(looks).size).toBeGreaterThanOrEqual(5);
    expect(kindPaint("area").fill).not.toBe(kindPaint("agent").fill);
  });

  it("falls back to the plain grey for a kind it does not know, in both tables", () => {
    expect(kindClass("mystery")).toBe(kindClass("task"));
    expect(kindPaint("mystery")).toEqual(kindPaint("task"));
  });

  it("lists every token the drawing reads, so the palette resolves them all", () => {
    for (const kind of KINDS) {
      expect(PAINT_TOKENS).toContain(kindPaint(kind).fill);
      expect(PAINT_TOKENS).toContain(kindPaint(kind).stroke);
    }
    for (const token of ["--ring", "--foreground", "--background", "--muted"]) expect(PAINT_TOKENS).toContain(token);
  });
});

describe("kindLabel", () => {
  it("names a known kind and shows an unknown one as written", () => {
    expect(kindLabel("lesson")).toBe("Lesson");
    expect(kindLabel("mystery")).toBe("mystery");
  });
});
