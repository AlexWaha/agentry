import { beforeEach, describe, expect, it } from "vitest";

import { bootToken, getToken, isTokenExpired, markTokenExpired, resetToken } from "@/lib/token";

beforeEach(() => {
  resetToken();
});

describe("bootToken", () => {
  it("stores the token from the fragment and removes the fragment", () => {
    window.history.replaceState(null, "", "/board?epic=epic-0005#token=abc123");

    bootToken();

    expect(getToken()).toBe("abc123");
    expect(localStorage.getItem("agentry-dashboard-token")).toBe("abc123");
    expect(window.location.hash).toBe("");
    expect(window.location.pathname + window.location.search).toBe("/board?epic=epic-0005");
  });

  it("uses the stored token on a later load without a fragment", () => {
    localStorage.setItem("agentry-dashboard-token", "stored-token");

    bootToken();

    expect(getToken()).toBe("stored-token");
  });

  it("replaces an old token with the one in the new launch URL and clears the expired flag", () => {
    localStorage.setItem("agentry-dashboard-token", "old");
    markTokenExpired();

    window.history.replaceState(null, "", "/#token=fresh");
    bootToken();

    expect(getToken()).toBe("fresh");
    expect(isTokenExpired()).toBe(false);
  });

  it("returns an empty token when there is none", () => {
    expect(getToken()).toBe("");
  });

  it("leaves unrelated fragments alone", () => {
    window.history.replaceState(null, "", "/#section");

    bootToken();

    expect(window.location.hash).toBe("#section");
    expect(getToken()).toBe("");
  });
});
