import { describe, expect, it } from "vitest";

import memoryGraph from "@/test/fixtures/memory/graph-memory.json";

import { isLitLink, litNodes, mixColours, parseColour } from "./highlight";
import { buildScene } from "./layout";

const scene = buildScene(memoryGraph.body.data.nodes, memoryGraph.body.data.edges);

describe("litNodes", () => {
  it("is the active node and its neighbours and nothing else", () => {
    const lit = litNodes(scene, "lesson:1");

    expect([...(lit ?? [])].sort()).toEqual(["agent:senior-backend-dev", "area:hooks", "lesson:1", "task:task-0106"]);
  });

  it("includes the neighbours of a node that several others point at", () => {
    expect([...(litNodes(scene, "area:hooks") ?? [])].sort()).toEqual(["area:hooks", "lesson:1", "lesson:2"]);
  });

  it("is just the node itself when it has no links", () => {
    expect([...(litNodes(scene, "pattern:2") ?? [])]).toEqual(["pattern:2"]);
  });

  it("is null, so nothing dims, without an active node or for an id the graph does not hold", () => {
    expect(litNodes(scene, null)).toBeNull();
    expect(litNodes(scene, undefined)).toBeNull();
    expect(litNodes(scene, "ghost")).toBeNull();
  });
});

describe("isLitLink", () => {
  it("is true for a link that touches the active node at either end", () => {
    expect(isLitLink("a", "a", "b")).toBe(true);
    expect(isLitLink("b", "a", "b")).toBe(true);
  });

  it("is false for any other link and when nothing is active", () => {
    expect(isLitLink("c", "a", "b")).toBe(false);
    expect(isLitLink(null, "a", "b")).toBe(false);
    expect(isLitLink(undefined, "a", "b")).toBe(false);
  });
});

describe("parseColour", () => {
  it("reads long and short hex and the rgb forms the theme tokens resolve to", () => {
    expect(parseColour("#1d4ed8")).toEqual([29, 78, 216]);
    expect(parseColour("#fff")).toEqual([255, 255, 255]);
    expect(parseColour("rgb(10, 20, 30)")).toEqual([10, 20, 30]);
    expect(parseColour("rgba(10 20 30 / 0.5)")).toEqual([10, 20, 30]);
    expect(parseColour("  #0A0A0B ")).toEqual([10, 10, 11]);
  });

  it("falls back to a mid grey for a colour it cannot read", () => {
    expect(parseColour("currentColor")).toEqual([128, 128, 128]);
    expect(parseColour("")).toEqual([128, 128, 128]);
  });
});

describe("mixColours", () => {
  it("moves a colour the given share of the way to the background", () => {
    expect(mixColours("#000000", "#ffffff", 0)).toBe("rgb(0, 0, 0)");
    expect(mixColours("#000000", "#ffffff", 1)).toBe("rgb(255, 255, 255)");
    expect(mixColours("#000000", "#ffffff", 0.5)).toBe("rgb(128, 128, 128)");
  });

  it("makes a strongly dimmed colour close to the background", () => {
    const dimmed = parseColour(mixColours("#ff0000", "#101010", 0.88));

    expect(Math.abs(dimmed[0] - 16)).toBeLessThan(35);
    expect(Math.abs(dimmed[1] - 16)).toBeLessThan(3);
  });
});
