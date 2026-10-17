import type { Topic } from "@/types/api";

/**
 * Which cached resources an `invalidate` topic refreshes. A resource is the first element of a query key
 * (see query-keys.ts). `events` is deliberately in no topic: the journal list is patched in place from
 * `events.appended` rows (FR-100) and refetched only on reconnect and resync, which invalidate everything.
 */
export const TOPIC_RESOURCES: Record<Topic, readonly string[]> = {
  runs: ["runs", "task", "metrics"],
  status: ["status"],
  tasks: ["tasks", "task", "epics"],
  memory: ["memory", "memoryStats", "memoryGraph"],
  handoffs: ["handoffs", "handoff"],
  harness: ["harness"],
  codegraph: ["codegraph", "codegraphGraph"],
};

export const TOPICS = Object.keys(TOPIC_RESOURCES) as Topic[];

export function isTopic(value: unknown): value is Topic {
  return typeof value === "string" && Object.hasOwn(TOPIC_RESOURCES, value);
}
