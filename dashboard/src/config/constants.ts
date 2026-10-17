/** One page size per paged list; the request and the Pager both read it, so they cannot drift (FR-124). */
export const PAGE_SIZE = {
  events: 25,
  tasks: 25,
  memory: 20,
  handoffs: 25,
} as const;

export type PagedList = keyof typeof PAGE_SIZE;

/** A Board column shows at most this many cards; above it the column offers "View all" (FR-94, FR-125). */
export const BOARD_COLUMN_CAP = 12;

/** The GitHub documentation shown by the sidebar Docs link (FR-129). */
export const DOCS_URL = "https://github.com/AlexWaha/agentry/tree/main/docs";

/** Base of the "Learn more" links, completed by docs-links.json (FR-112). */
export const DOCS_BLOB_URL = "https://github.com/AlexWaha/agentry/blob/main";

export const API_BASE = "/api/v1";
export const WS_PATH = "/ws";

/** Reconnect delays in milliseconds; the last value repeats (FR-82). */
export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000] as const;

/** Invalidations arriving within this window become one invalidation per topic (FR-83). */
export const INVALIDATE_COALESCE_MS = 500;

/** The largest page number a list accepts from the URL; a larger one is page 1 (a request offset stays an integer). */
export const MAX_PAGE = 1_000_000;

/** Search boxes send their text after this much quiet (FR-100, FR-103, FR-126). */
export const SEARCH_DEBOUNCE_MS = 300;

/** A 503 is retried this many times, waiting `Retry-After` between attempts (FR-80). */
export const MAX_503_RETRIES = 3;
export const DEFAULT_RETRY_AFTER_S = 1;

/** The Pager shows every page up to this count and collapses runs of pages with ellipses beyond it (FR-124). */
export const PAGER_FULL_LIMIT = 7;

/** How long an unused query stays cached. */
export const QUERY_GC_MS = 300_000;
