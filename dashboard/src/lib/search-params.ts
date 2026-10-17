import { parseSearchWith, stringifySearchWith, useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";

import { MAX_PAGE, SEARCH_DEBOUNCE_MS } from "@/config/constants";

/*
 * URL state of the list pages (FR-124). Every filter, search text, tab and the page number of a list live in
 * the URL search params, so a reload or a shared link restores the same view. Pages are 1-based and `page` is
 * omitted when it is 1. The router keeps every value as typed (no JSON parsing, so `?q=1.50` stays "1.50"), but
 * its query decoder still turns plain numbers and true/false into numbers and booleans: the parse* helpers
 * below accept both and drop what is invalid.
 */

/**
 * Only values that look like a JSON array, object or quoted string are JSON-parsed; everything else stays text.
 * The router's default parses numbers too, which would rewrite a search text (`?q=1.50` became 1.5, `?q=1e3` 1000).
 */
function parseStructured(value: string): unknown {
  if (!/^[[{"]/.test(value)) throw new Error("plain text");
  return JSON.parse(value);
}

/** Router `parseSearch`: a search text reaches the API exactly as typed. */
export const parseSearch = parseSearchWith(parseStructured);

/** Router `stringifySearch`: the inverse of parseSearch, so a text that looks like JSON survives a reload. */
export const stringifySearch = stringifySearchWith(JSON.stringify, parseStructured);

export interface PageSearch {
  page?: number;
}

/** A page number from a raw search value: an integer from 1 to MAX_PAGE, anything else is page 1. */
export function parsePage(raw: unknown): number {
  const n = typeof raw === "string" ? Number(raw) : raw;
  return typeof n === "number" && Number.isInteger(n) && n >= 1 && n <= MAX_PAGE ? n : 1;
}

/** The value a route's validateSearch keeps for `page`: undefined for page 1 and for invalid input. */
export function pageParam(raw: unknown): number | undefined {
  const page = parsePage(raw);
  return page > 1 ? page : undefined;
}

/** A non-empty string; numbers and booleans from JSON-parsed params become strings. */
export function textParam(raw: unknown): string | undefined {
  if (typeof raw === "number" || typeof raw === "boolean") return String(raw);
  return typeof raw === "string" && raw.trim() !== "" ? raw : undefined;
}

/** One of the allowed values, or undefined (so the default is omitted from the URL and unknown values vanish). */
export function enumParam<T extends string>(raw: unknown, allowed: readonly T[]): T | undefined {
  const text = textParam(raw);
  return allowed.find((value) => value === text);
}

/** A flag written as `1` (or `true`); absent otherwise. */
export function flagParam(raw: unknown): true | undefined {
  return raw === 1 || raw === "1" || raw === true || raw === "true" ? true : undefined;
}

export function lastPage(total: number, size: number): number {
  return Math.max(1, Math.ceil(total / size));
}

export function pageOffset(page: number, size: number): number {
  return (page - 1) * size;
}

/** The page to show: a page beyond the last one becomes the last page. */
export function clampPage(page: number, total: number, size: number): number {
  return Math.min(page, lastPage(total, size));
}

function dropEmpty<S extends object>(search: S): S {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) out[key] = value;
  }
  return out as S;
}

/** `search` with a new page; page 1 is omitted. */
export function withPage<S extends PageSearch>(search: S, page: number): S {
  return dropEmpty({ ...search, page: page > 1 ? page : undefined });
}

/** `search` with a filter patch applied and the page reset to 1 (a filter change always starts at page 1). */
export function withFilters<S extends PageSearch>(search: S, patch: Partial<S>): S {
  return dropEmpty({ ...search, ...patch, page: undefined });
}

/** True when any of the named filter params is set. */
export function hasFilters(search: object, keys: readonly string[]): boolean {
  const record: Record<string, unknown> = { ...search };
  return keys.some((key) => record[key] !== undefined && record[key] !== "");
}

export interface NavigateOptions {
  /** Replace the current history entry instead of adding one (typing in a search box). */
  replace?: boolean;
}

export interface ListSearch<S extends PageSearch> {
  search: S;
  /** The page from the URL, at least 1. */
  page: number;
  setPage: (page: number, options?: NavigateOptions) => void;
  /** Applies a filter patch and resets the page to 1. */
  setFilters: (patch: Partial<S>, options?: NavigateOptions) => void;
  /** Removes the named params and resets the page to 1. */
  clearFilters: (keys: readonly (keyof S & string)[]) => void;
}

/** Search params of the current route plus the setters that follow the FR-124 rules. */
export function useListSearch<S extends PageSearch>(): ListSearch<S> {
  const search = useSearch({ strict: false }) as S;
  const navigate = useNavigate();

  const setPage = useCallback(
    (page: number, options?: NavigateOptions) => {
      void navigate({ to: ".", search: (prev: S) => withPage(prev, page), replace: options?.replace });
    },
    [navigate],
  );

  const setFilters = useCallback(
    (patch: Partial<S>, options?: NavigateOptions) => {
      void navigate({ to: ".", search: (prev: S) => withFilters(prev, patch), replace: options?.replace });
    },
    [navigate],
  );

  const clearFilters = useCallback(
    (keys: readonly (keyof S & string)[]) => {
      const cleared = Object.fromEntries(keys.map((key) => [key, undefined])) as Partial<S>;
      searchTextResets.forEach((reset) => reset());
      void navigate({ to: ".", search: (prev: S) => withFilters(prev, cleared) });
    },
    [navigate],
  );

  return { search, page: parsePage(search.page), setPage, setFilters, clearFilters };
}

/**
 * Once the total is known, a `page` beyond the last page is replaced by the last page in the URL, so the
 * list renders the last page (FR-124). Call it in every paged list page.
 */
export function useClampPage(
  page: number,
  total: number | undefined,
  size: number,
  setPage: (page: number, options?: NavigateOptions) => void,
): void {
  useEffect(() => {
    if (total === undefined || total === 0) return;
    const last = lastPage(total, size);
    if (page > last) setPage(last, { replace: true });
  }, [page, total, size, setPage]);
}

/** The reset callbacks of the mounted search boxes; "Clear filters" calls them so pending text goes too. */
const searchTextResets = new Set<() => void>();

/**
 * Text of a search box that is committed to the URL after SEARCH_DEBOUNCE_MS of quiet. `committed` is the URL
 * value; `commit` writes it (use setFilters with `{ replace: true }` so typing adds no history entries). A change
 * of the URL value from elsewhere, for example "Clear filters", replaces the text.
 */
export function useSearchText(
  committed: string,
  commit: (value: string) => void,
  delay: number = SEARCH_DEBOUNCE_MS,
): [string, (value: string) => void] {
  const [text, setText] = useState(committed);
  const sent = useRef(committed);
  const latestCommit = useRef(commit);

  useEffect(() => {
    latestCommit.current = commit;
  });

  useEffect(() => {
    if (committed !== sent.current) {
      sent.current = committed;
      setText(committed);
    }
  }, [committed]);

  useEffect(() => {
    const reset = () => {
      sent.current = "";
      setText("");
    };
    searchTextResets.add(reset);
    return () => {
      searchTextResets.delete(reset);
    };
  }, []);

  useEffect(() => {
    if (text === sent.current) return undefined;
    const timer = setTimeout(() => {
      sent.current = text;
      latestCommit.current(text);
    }, delay);
    return () => clearTimeout(timer);
  }, [text, delay]);

  return [text, setText];
}
