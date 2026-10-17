import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError, ValidationError, apiGet, buildUrl } from "@/lib/api-client";
import { getToken, isTokenExpired } from "@/lib/token";
import { fail, mockApi, networkFailure, ok } from "@/test/utils/api-mock";

beforeEach(() => {
  localStorage.setItem("agentry-dashboard-token", "tok-123");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("apiGet", () => {
  it("sends the bearer token and unwraps data and meta", async () => {
    const api = mockApi({ "/api/v1/tasks": ok([{ id: "task-0001" }], { total: 1, limit: 25, offset: 0 }) });

    const result = await apiGet<{ id: string }[], { total: number }>("/tasks");

    expect(result.data).toEqual([{ id: "task-0001" }]);
    expect(result.meta.total).toBe(1);
    expect(getToken()).toBe("tok-123");
    expect(api.requests[0].headers.get("Authorization")).toBe("Bearer tok-123");
  });

  it("returns meta undefined for a route without meta", async () => {
    mockApi({ "/api/v1/status": ok({ project: "demo" }) });

    const result = await apiGet<{ project: string }>("/status");

    expect(result).toEqual({ data: { project: "demo" }, meta: undefined });
  });

  it("builds a sorted query string and leaves out empty values", () => {
    const url = buildUrl("/tasks", { q: "", offset: 25, folder: "done", epic: undefined, include_superseded: false, limit: 25 });

    expect(url).toBe("/api/v1/tasks?folder=done&include_superseded=false&limit=25&offset=25");
  });

  it("throws a typed ValidationError with per-field messages for a 422", async () => {
    mockApi({ "/api/v1/tasks": fail(422, "validation_failed", "Invalid", { limit: ["must be 1 to 500"] }) });

    const error = await apiGet("/tasks", { limit: 9999 }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ValidationError);
    expect(error).toMatchObject({ status: 422, code: "validation_failed", errors: { limit: ["must be 1 to 500"] } });
  });

  it("throws an ApiError carrying status and code for other failures", async () => {
    mockApi({ "/api/v1/tasks/task-9999": fail(404, "not_found", "No such task") });

    const error = await apiGet("/tasks/task-9999").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(ValidationError);
    expect(error).toMatchObject({ status: 404, code: "not_found", message: "No such task" });
  });

  it("marks the token expired on a 401", async () => {
    mockApi({ "/api/v1/status": fail(401, "unauthorized") });

    await expect(apiGet("/status")).rejects.toMatchObject({ status: 401, code: "unauthorized" });

    expect(isTokenExpired()).toBe(true);
  });

  it("throws a NetworkError when the server cannot be reached", async () => {
    mockApi({ "/api/v1/status": networkFailure() });

    await expect(apiGet("/status")).rejects.toBeInstanceOf(NetworkError);
  });

  it("throws bad_response for a 200 without a data envelope", async () => {
    mockApi({ "/api/v1/status": { status: 200, body: { nope: true } } });

    await expect(apiGet("/status")).rejects.toMatchObject({ code: "bad_response" });
  });
});

describe("503 retry", () => {
  it("retries after Retry-After seconds and returns the later success", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const api = mockApi({
      "/api/v1/runs": () => {
        calls += 1;
        return calls < 3 ? { status: 503, body: { message: "busy", code: "unavailable" }, headers: { "Retry-After": "2" } } : ok([]);
      },
    });

    const pending = apiGet("/runs");
    await vi.advanceTimersByTimeAsync(1999);
    expect(api.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.requests).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(2000);

    await expect(pending).resolves.toEqual({ data: [], meta: undefined });
    expect(api.requests).toHaveLength(3);
  });

  it("gives up after three retries and throws the 503", async () => {
    vi.useFakeTimers();
    const api = mockApi({
      "/api/v1/runs": { status: 503, body: { message: "busy", code: "unavailable" }, headers: { "Retry-After": "1" } },
    });

    const pending = apiGet("/runs").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(3000);

    expect(await pending).toMatchObject({ status: 503, code: "unavailable" });
    expect(api.requests).toHaveLength(4);
  });
});
