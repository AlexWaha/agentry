import { describe, expect, it } from "vitest";

import { ago, bytes, capitalize, clock, dur, ms, stamp } from "@/lib/format";

describe("dur", () => {
  it.each([
    [0.2, "<1s"],
    [45, "45s"],
    [185, "3m 05s"],
    [7800, "2h 10m"],
    [100800, "1d 4h"],
  ])("formats %s seconds as %s", (seconds, expected) => {
    expect(dur(seconds)).toBe(expected);
  });

  it("shows a dash for a missing value", () => {
    expect(dur(null)).toBe("-");
    expect(dur(undefined)).toBe("-");
  });
});

describe("ms", () => {
  it("keeps milliseconds under a second and converts above it", () => {
    expect(ms(420)).toBe("420ms");
    expect(ms(90_000)).toBe("1m 30s");
    expect(ms(null)).toBe("-");
  });
});

describe("stamp and clock", () => {
  it("cuts an ISO UTC timestamp to month-day and time", () => {
    expect(stamp("2026-10-16T09:05:07Z")).toBe("10-16 09:05:07Z");
    expect(clock("2026-10-16T09:05:07Z")).toBe("09:05:07");
  });

  it("shows a dash for an empty timestamp", () => {
    expect(stamp("")).toBe("-");
    expect(clock("")).toBe("-");
  });
});

describe("ago", () => {
  it("measures from the injected clock", () => {
    const now = Date.parse("2026-10-16T09:10:00Z");

    expect(ago("2026-10-16T09:05:00Z", now)).toBe("5m 00s ago");
  });

  it("never reports a negative duration for a timestamp slightly in the future", () => {
    const now = Date.parse("2026-10-16T09:00:00Z");

    expect(ago("2026-10-16T09:00:05Z", now)).toBe("<1s ago");
  });
});

describe("bytes", () => {
  it("picks B, KB or MB", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(2048)).toBe("2.0 KB");
    expect(bytes(3 * 1024 * 1024)).toBe("3.00 MB");
    expect(bytes(null)).toBe("-");
  });
});

describe("capitalize", () => {
  it("upper-cases the first letter only", () => {
    expect(capitalize("needs you")).toBe("Needs you");
  });
});
