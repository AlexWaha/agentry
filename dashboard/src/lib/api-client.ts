import { API_BASE, DEFAULT_RETRY_AFTER_S, MAX_503_RETRIES } from "@/config/constants";
import { cleanParams, type QueryParams } from "@/config/query-keys";
import { getToken, markTokenExpired } from "@/lib/token";
import type { ErrorBody } from "@/types/api";

const HTTP_UNAUTHORIZED = 401;
const HTTP_UNPROCESSABLE = 422;
const HTTP_UNAVAILABLE = 503;
const MS_PER_S = 1000;

/** A non-2xx answer: `status`, the server's `code` and, for 422, the per-field `errors` (FR-12). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly errors: Record<string, string[]> | undefined;

  constructor(status: number, body: Partial<ErrorBody>) {
    super(body.message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code ?? "http_error";
    this.errors = body.errors;
  }
}

/** A 422 `validation_failed`; `errors` maps each offending field to its messages. */
export class ValidationError extends ApiError {
  constructor(body: Partial<ErrorBody>) {
    super(HTTP_UNPROCESSABLE, body);
    this.name = "ValidationError";
  }
}

/** The request never produced an HTTP answer (server stopped, connection refused). */
export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

export interface ApiResult<T, M = undefined> {
  data: T;
  meta: M;
}

export interface ApiGetOptions {
  signal?: AbortSignal;
}

export function buildUrl(path: string, params?: QueryParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(cleanParams(params))) search.set(key, String(value));
  const query = search.toString();
  return `${API_BASE}${path}${query ? `?${query}` : ""}`;
}

function retryDelayMs(response: Response): number {
  const seconds = Number(response.headers.get("Retry-After"));
  return (Number.isFinite(seconds) && seconds >= 0 ? seconds : DEFAULT_RETRY_AFTER_S) * MS_PER_S;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

async function send(url: string, signal: AbortSignal | undefined): Promise<Response> {
  try {
    return await fetch(url, {
      headers: { Authorization: `Bearer ${getToken()}`, Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new NetworkError(error instanceof Error ? error.message : "Network error");
  }
}

/**
 * GET `/api/v1<path>` with the bearer token. Returns the unwrapped `{data, meta}`. A 503 is retried up to
 * three times, waiting `Retry-After` seconds each time (FR-80). A 401 marks the token expired (the shell then
 * shows the expired-link page) and throws.
 */
export async function apiGet<T, M = undefined>(
  path: string,
  params?: QueryParams,
  options: ApiGetOptions = {},
): Promise<ApiResult<T, M>> {
  const url = buildUrl(path, params);
  let response = await send(url, options.signal);
  for (let retry = 0; response.status === HTTP_UNAVAILABLE && retry < MAX_503_RETRIES; retry += 1) {
    await wait(retryDelayMs(response));
    response = await send(url, options.signal);
  }

  const body = await readJson(response);
  if (!response.ok) {
    const error: Partial<ErrorBody> = typeof body === "object" && body !== null ? (body as Partial<ErrorBody>) : {};
    if (response.status === HTTP_UNAUTHORIZED) markTokenExpired();
    throw response.status === HTTP_UNPROCESSABLE ? new ValidationError(error) : new ApiError(response.status, error);
  }
  if (typeof body !== "object" || body === null || !("data" in body)) {
    throw new ApiError(response.status, { message: "Unexpected response body", code: "bad_response" });
  }
  const envelope = body as { data: T; meta?: M };
  return { data: envelope.data, meta: envelope.meta as M };
}
