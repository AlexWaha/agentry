import { describe, expect, it } from "vitest";

import { DEFAULT_LOCALE, interpolate, mergeMessages, t } from "@/i18n";

describe("t", () => {
  it("returns the English text of a known key", () => {
    expect(t("conn.live")).toBe("Live");
  });

  it("fills placeholders", () => {
    expect(t("pager.range", { from: 26, to: 50, total: 120, noun: "tasks" })).toBe("26-50 of 120 tasks");
  });

  it("renders the key name for a missing key so the gap is visible", () => {
    expect(t("nope.missing.key")).toBe("nope.missing.key");
  });

  it("keeps a placeholder without a value as written", () => {
    expect(interpolate("Hello {name}, {missing}", { name: "Ada" })).toBe("Hello Ada, {missing}");
  });

  it("has one DEFAULT_LOCALE constant", () => {
    expect(DEFAULT_LOCALE).toBe("en");
  });
});

describe("mergeMessages", () => {
  it("merges groups into one catalog", () => {
    expect(mergeMessages({ "a.ts": { "a.one": "1" }, "b.ts": { "b.one": "2" } })).toEqual({ "a.one": "1", "b.one": "2" });
  });

  it("throws on a duplicate key and names both files", () => {
    expect(() => mergeMessages({ "a.ts": { "x.key": "1" }, "b.ts": { "x.key": "2" } })).toThrow(/x\.key.*b\.ts.*a\.ts/);
  });
});
