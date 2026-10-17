import { useQuery } from "@tanstack/react-query";

import { t } from "@/i18n";
import { NetworkError } from "@/lib/api-client";
import { statusQuery } from "@/lib/queries";
import { useSocketState, type ConnectionState } from "@/lib/ws-client";
import { cn } from "@/lib/utils";

const LABEL_KEYS: Record<ConnectionState, string> = {
  live: "conn.live",
  reconnecting: "conn.reconnecting",
  offline: "conn.offline",
};

const DOT_STYLES: Record<ConnectionState, string> = {
  live: "bg-status-done-edge ring-[3px] ring-status-done-edge/20",
  reconnecting: "bg-muted-foreground",
  offline: "border border-muted-foreground bg-transparent",
};

/**
 * Connection state in the top bar (FR-77). Live only after the WebSocket `hello`; Reconnecting while it
 * retries; Offline when /status fails with a network error (the server is gone).
 */
export function useConnectionState(): ConnectionState {
  const socket = useSocketState();
  const status = useQuery(statusQuery());
  return status.isError && status.error instanceof NetworkError ? "offline" : socket;
}

export function ConnectionIndicator() {
  const state = useConnectionState();
  return (
    <span role="status" data-state={state} className="inline-flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
      <i aria-hidden="true" className={cn("size-[7px] rounded-full", DOT_STYLES[state])} />
      {t(LABEL_KEYS[state])}
    </span>
  );
}
