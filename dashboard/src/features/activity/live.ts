import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { queryKeys, type EventsParams } from "@/config/query-keys";
import { applyAppended, type CachedEvents } from "@/lib/live-events";
import { onEventsAppended } from "@/lib/ws-client";

const EVENTS_RESOURCE = queryKeys.events()[0];

/** Keeps every cached events list current while the page is mounted. */
export function useLiveEvents(): void {
  const queryClient = useQueryClient();
  useEffect(
    () =>
      onEventsAppended((rows) => {
        for (const query of queryClient.getQueryCache().findAll({ queryKey: [EVENTS_RESOURCE] })) {
          const filters = query.queryKey[1] as EventsParams;
          queryClient.setQueryData<CachedEvents>(query.queryKey, (cached) =>
            cached ? applyAppended(cached, filters, rows) : cached,
          );
        }
      }),
    [queryClient],
  );
}
