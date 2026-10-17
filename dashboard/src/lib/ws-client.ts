import type { QueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import { INVALIDATE_COALESCE_MS, RECONNECT_DELAYS_MS, WS_PATH } from "@/config/constants";
import { isTopic, TOPIC_RESOURCES } from "@/config/topics";
import { getToken } from "@/lib/token";
import type { Event, Topic, WsMessage } from "@/types/api";

/*
 * The single WebSocket of the app (FR-82, FR-83). Receive-only in v1: nothing is ever sent. The connection
 * counts as live only after the server's `hello`. Every (re)connect and every `resync` invalidates all
 * queries; `invalidate` messages are coalesced per topic; `events.appended` rows go to registered handlers
 * (the Activity page patches its cached list) without a network call.
 */

export type SocketState = "live" | "reconnecting";
export type ConnectionState = SocketState | "offline";

type EventsHandler = (events: Event[]) => void;

let socketState: SocketState = "reconnecting";
const stateListeners = new Set<() => void>();
const eventsHandlers = new Set<EventsHandler>();

function setSocketState(next: SocketState): void {
  if (socketState === next) return;
  socketState = next;
  for (const listener of stateListeners) listener();
}

export function getSocketState(): SocketState {
  return socketState;
}

export function subscribeSocketState(listener: () => void): () => void {
  stateListeners.add(listener);
  return () => stateListeners.delete(listener);
}

export function useSocketState(): SocketState {
  return useSyncExternalStore(subscribeSocketState, getSocketState, () => "reconnecting");
}

/** Registers a handler for `events.appended` rows (ascending id). Returns the unsubscribe function. */
export function onEventsAppended(handler: EventsHandler): () => void {
  eventsHandlers.add(handler);
  return () => eventsHandlers.delete(handler);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Parses a text frame into a known server message, or null for anything unknown or malformed. */
export function parseWsMessage(raw: unknown): WsMessage | null {
  if (typeof raw !== "string") return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value) || value.v !== 1 || !isRecord(value.payload)) return null;
  const payload = value.payload;
  switch (value.type) {
    case "hello":
      return value as unknown as WsMessage;
    case "events.appended":
      return Array.isArray(payload.events) ? (value as unknown as WsMessage) : null;
    case "invalidate":
      return Array.isArray(payload.topics) ? (value as unknown as WsMessage) : null;
    case "resync":
    case "command.refused":
    case "error":
      return value as unknown as WsMessage;
    default:
      return null;
  }
}

function socketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${WS_PATH}?token=${encodeURIComponent(getToken())}`;
}

/** Connects and keeps reconnecting until the returned function is called. Start it once, outside React. */
export function startWsClient(queryClient: QueryClient): () => void {
  let socket: WebSocket | null = null;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let flushTimer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  const pending = new Set<Topic>();

  const invalidateAll = (): void => {
    void queryClient.invalidateQueries();
  };

  const flush = (): void => {
    flushTimer = undefined;
    const resources = new Set<string>();
    for (const topic of pending) for (const resource of TOPIC_RESOURCES[topic]) resources.add(resource);
    pending.clear();
    void queryClient.invalidateQueries({ predicate: (query) => resources.has(String(query.queryKey[0])) });
  };

  const queueTopics = (topics: readonly unknown[]): void => {
    for (const topic of topics) if (isTopic(topic)) pending.add(topic);
    if (pending.size > 0 && flushTimer === undefined) flushTimer = setTimeout(flush, INVALIDATE_COALESCE_MS);
  };

  const handle = (message: WsMessage): void => {
    switch (message.type) {
      case "hello":
        attempt = 0;
        setSocketState("live");
        invalidateAll();
        break;
      case "invalidate":
        queueTopics(message.payload.topics);
        break;
      case "resync":
        pending.clear();
        clearTimeout(flushTimer);
        flushTimer = undefined;
        invalidateAll();
        break;
      case "events.appended":
        for (const handler of eventsHandlers) handler(message.payload.events);
        break;
      default:
        break;
    }
  };

  const scheduleReconnect = (): void => {
    if (stopped) return;
    const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
    attempt += 1;
    reconnectTimer = setTimeout(connect, delay);
  };

  function connect(): void {
    if (stopped) return;
    const ws = new WebSocket(socketUrl());
    socket = ws;
    ws.onmessage = (event: MessageEvent) => {
      const message = parseWsMessage(event.data);
      if (message) handle(message);
    };
    ws.onclose = () => {
      if (socket !== ws) return;
      setSocketState("reconnecting");
      scheduleReconnect();
    };
  }

  connect();

  return () => {
    stopped = true;
    clearTimeout(reconnectTimer);
    clearTimeout(flushTimer);
    const ws = socket;
    socket = null;
    ws?.close();
    setSocketState("reconnecting");
  };
}
