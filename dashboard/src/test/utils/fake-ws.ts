import { vi } from "vitest";

import type { WsMessage } from "@/types/api";

/*
 * A scriptable stand-in for the browser WebSocket. installFakeWebSocket() replaces the global, so the
 * ws-client connects to it. The test drives the server side: open(), receive(message), serverClose().
 * Everything the client sends is recorded in `sent` (the v1 client sends nothing).
 */

const CONNECTING = 0;
const OPEN = 1;
const CLOSED = 3;

export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  static reset(): void {
    FakeWebSocket.instances = [];
  }

  static get last(): FakeWebSocket {
    const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    if (!socket) throw new Error("No FakeWebSocket has been created");
    return socket;
  }

  readonly url: string;
  readyState = CONNECTING;
  sent: unknown[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: unknown): void {
    this.sent.push(data);
  }

  /** Client-initiated close. */
  close(): void {
    this.readyState = CLOSED;
    this.onclose?.(new Event("close") as CloseEvent);
  }

  /** The TCP/WebSocket handshake completes (before any `hello`). */
  open(): void {
    this.readyState = OPEN;
    this.onopen?.(new Event("open"));
  }

  /** The server sends a text frame: an object is serialised as JSON, a string is sent as is. */
  receive(message: WsMessage | Record<string, unknown> | string): void {
    const data = typeof message === "string" ? message : JSON.stringify(message);
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  /** The server (or the network) drops the connection. */
  serverClose(): void {
    this.readyState = CLOSED;
    this.onclose?.(new Event("close") as CloseEvent);
  }
}

export function installFakeWebSocket(): typeof FakeWebSocket {
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  return FakeWebSocket;
}

/** The first message the server sends after the 101. */
export const helloMessage: WsMessage = {
  type: "hello",
  v: 1,
  payload: { protocol: 1, read_only: true, latest_event_id: 0 },
};

export function invalidateMessage(...topics: Extract<WsMessage, { type: "invalidate" }>["payload"]["topics"]): WsMessage {
  return { type: "invalidate", v: 1, payload: { topics } };
}
