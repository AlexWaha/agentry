import { vi, type Mock } from "vitest";

/*
 * Replaces global fetch with a route table. A route key is a path (`/api/v1/tasks`); a key that also holds a
 * query string (`/api/v1/tasks?folder=done&limit=12`) wins over the bare path for that exact request. The value
 * is a MockResponse or a function of the request URL returning one (use it for sequences, such as one 503
 * followed by a 200). A request to an unknown route answers 404 `not_found`, like the real server.
 */

export interface MockResponse {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
  /** Reject the fetch itself, as when the server is down. */
  networkError?: boolean;
}

export type MockHandler = MockResponse | ((url: URL) => MockResponse);

export interface RecordedRequest {
  url: URL;
  headers: Headers;
}

export interface ApiMock {
  fetch: Mock<typeof fetch>;
  requests: RecordedRequest[];
  /** Adds or replaces a route. */
  set: (route: string, handler: MockHandler) => void;
  /** Requests whose path (without the query) equals `path`. */
  callsTo: (path: string) => RecordedRequest[];
}

export const ok = <T, M = undefined>(data: T, meta?: M): MockResponse => ({
  status: 200,
  body: meta === undefined ? { data } : { data, meta },
});

export const fail = (status: number, code: string, message = code, errors?: Record<string, string[]>): MockResponse => ({
  status,
  body: errors ? { message, code, errors } : { message, code },
});

export const networkFailure = (): MockResponse => ({ status: 0, networkError: true });

export function mockApi(routes: Record<string, MockHandler> = {}): ApiMock {
  const table = new Map<string, MockHandler>(Object.entries(routes));
  const requests: RecordedRequest[] = [];

  const resolve = (url: URL): MockResponse => {
    const handler = table.get(`${url.pathname}${url.search}`) ?? table.get(url.pathname);
    if (!handler) return fail(404, "not_found", "Not found");
    return typeof handler === "function" ? handler(url) : handler;
  };

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input instanceof Request ? input.url : input), window.location.origin);
    requests.push({ url, headers: new Headers(init?.headers) });
    const response = resolve(url);
    if (response.networkError) throw new TypeError("Failed to fetch");
    return new Response(response.body === undefined ? null : JSON.stringify(response.body), {
      status: response.status,
      headers: { "Content-Type": "application/json", ...response.headers },
    });
  });
  vi.stubGlobal("fetch", fetchMock);

  return {
    fetch: fetchMock,
    requests,
    set: (route, handler) => {
      table.set(route, handler);
    },
    callsTo: (path) => requests.filter((request) => request.url.pathname === path),
  };
}
