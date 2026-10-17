import { QueryClient } from "@tanstack/react-query";

import { QUERY_GC_MS } from "@/config/constants";

/**
 * Query defaults (FR-81). Data never goes stale on its own: it refreshes through WebSocket invalidation, a
 * manual reload, or a reconnect. There is no polling, and no refetch on window focus or network change.
 * apiGet retries a 503 itself, so the query layer does not retry again.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Infinity,
        gcTime: QUERY_GC_MS,
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
}
