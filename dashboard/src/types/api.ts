/*
 * Transcription of "Data and API Contracts" of spec-0002 (the contract) plus the supervisor's correction of the
 * memory row shapes (they follow the memory.db columns). An untyped field in the spec is a string; an absent
 * text is "" and never null, except the fields marked nullable here.
 */

export type Folder = "backlog" | "active" | "done";

export interface PageMeta {
  total: number;
  limit: number;
  offset: number;
}

export interface TasksMeta extends PageMeta {
  hidden_superseded: number;
  facets: {
    folders: { backlog: number; active: number; done: number };
    types: string[];
  };
}

export interface EventsMeta extends PageMeta {
  latest_id: number;
  kinds: string[];
}

export interface GraphMeta {
  truncated: boolean;
  elapsed_ms: number;
}

/** `GET /codegraph/{key}/graph` meta; the memory graph route returns no meta. */
export type EmptyMeta = undefined;

export interface TaskRef {
  id: string;
  title: string;
  folder: Folder | "";
}

export interface Stage {
  name: string;
  human: boolean;
  state: "done" | "now" | "next" | "skipped";
  pass: number;
  fail: number;
}

export interface NeedsYou {
  kind: string;
  since: string;
  reason: string;
}

export interface Run {
  task: string;
  title: string;
  lane: string;
  pipeline: "build" | "plan";
  type: string;
  stage: string;
  stage_status: string;
  awaiting_human: string;
  retries: number;
  epic: string;
  worktree: string;
  umbrella: boolean;
  subtasks: TaskRef[];
  updated: string;
  stages: Stage[];
  needs_you: NeedsYou | null;
  supervisor: { label: string; seen: string } | null;
}

export interface TaskSummary {
  id: string;
  title: string;
  type: string;
  priority: string;
  epic: string;
  spec: string;
  assignee: string;
  folder: Folder;
  created: string;
  completed: string;
  blocked_on: string;
  superseded_by: string;
  depends_on: TaskRef[];
}

export interface EventDetail {
  reason?: string;
  duration_ms?: number;
  exit?: number;
  cmd?: string;
}

/** A journal row. Named Event as in the contract; import it as `PipelineEvent` where the DOM Event is also in use. */
export interface Event {
  id: number;
  at: string;
  lane: string;
  task: string;
  pipeline: string;
  kind: string;
  stage: string;
  from_stage: string;
  outcome: string;
  via: string;
  detail: EventDetail;
}

export interface TaskDetail extends TaskSummary {
  phase: string;
  repo: string;
  umbrella: boolean;
  subtasks: TaskRef[];
  dependents: TaskRef[];
  body: string;
  body_truncated: boolean;
  run: Run | null;
  stages: Stage[];
  events: Event[];
  events_truncated: boolean;
  gate_runs: number;
  gate_failures: number;
  rejections: number;
  handoff: { valid: boolean; problems: string[] } | null;
  memory_stamped_at: string | null;
}

export type PipelineEvent = Event;

export interface Epic {
  id: string;
  title: string;
  status: string;
  tasks: TaskRef[];
}

export interface GateMetric {
  stage: string;
  runs: number;
  failures: number;
  median_ms: number;
  p90_ms: number;
  max_ms: number;
}

export interface SlowGate {
  task: string;
  stage: string;
  ms: number;
  passed: boolean;
  at: string;
}

export interface DwellMetric {
  stage: string;
  n: number;
  median_s: number;
  p90_s: number;
  max_s: number;
}

export interface PerDayMetric {
  day: string;
  gates: number;
  gate_failures: number;
  merged: number;
  rejected: number;
}

export interface RejectionRow {
  task: string;
  at: string;
  from_stage: string;
  stage: string;
  reason: string;
}

export interface Metrics {
  span: string;
  events_total: number;
  kinds: Record<string, number>;
  gates: GateMetric[];
  slowest: SlowGate[];
  dwell: DwellMetric[];
  per_day: PerDayMetric[];
  rejections: { total: number; latest: RejectionRow[] };
  merges_total: number;
  lead_time_s: number | null;
}

export type MemoryKind = "lesson" | "pattern" | "module";

export interface Lesson {
  kind: "lesson";
  id: number;
  signature: string;
  trigger: string;
  what: string;
  why: string;
  fix: string;
  area: string;
  task: string;
  agent: string;
  retired: boolean;
  created: string;
}

export interface Pattern {
  kind: "pattern";
  id: number;
  name: string;
  use_when: string;
  body: string;
  task: string;
  created: string;
}

export interface Module {
  kind: "module";
  id: number;
  path: string;
  responsibility: string;
  symbols: string;
  notes: string;
  updated: string;
}

export type MemoryRow = Lesson | Pattern | Module;

export interface InjectionProfile {
  matcher: string;
  kinds: string[];
  limit: number;
  matched: number;
  shown: number;
  bytes: number;
}

export interface MemoryStats {
  db_bytes: number;
  wal_bytes: number;
  counts: Record<MemoryKind, number>;
  growth: { day: string; rows: number }[];
  by_agent: Record<string, number>;
  by_area: Record<string, number>;
  stamps: { task: string; at: string }[];
  injection: {
    budget_bytes: number;
    active_tasks: string[];
    profiles: InjectionProfile[];
  };
}

export interface CodeIndex {
  key: string;
  path: string;
  db_bytes: number;
  last_sync: string;
  supported: boolean;
  files: number;
  symbols: number;
  edges: number;
}

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  weight: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface HarnessAgent {
  name: string;
  description: string;
  department: string;
  profile: string;
  model: string;
  tools: string[];
  rules: string[];
  skills: string[];
}

export interface HarnessRule {
  name: string;
  bytes: number;
  delivery: "core" | "main-thread" | "targeted" | "unused";
  agents: string[];
}

export interface HarnessSkill {
  name: string;
  description: string;
  source: string;
  /** The slash command that runs it, for example "/agentry:pipeline-show" or "/verify". */
  command: string;
  user_only: boolean;
  preloaded_by: string[];
}

export interface HarnessHook {
  source: string;
  event: string;
  matcher: string;
  command: string;
  script: string;
  script_exists: boolean;
  timeout: number | null;
}

export interface Harness {
  plugin: { name: string; version: string; root: string; install: "directory" | "cache" };
  agents: HarnessAgent[];
  rules: HarnessRule[];
  skills: HarnessSkill[];
  hooks: HarnessHook[];
}

export interface Handoff {
  task: string;
  title: string;
  author: string;
  date: string;
  bytes: number;
  valid: boolean;
}

export interface HandoffDoc extends Handoff {
  body: string;
  problems: string[];
}

export interface DebtItem {
  task: string;
  reason: string;
}

export interface Lane {
  name: string;
  repo: string;
  mode: "build" | "plan" | "talk";
  approvals: "manual" | "assisted" | "auto";
  swarm: boolean;
}

export interface Status {
  project: string;
  plugin_version: string;
  protocol: 1;
  read_only: true;
  workflow: { mode: "pr" | "solo"; push_needs_approval: boolean };
  lanes: Lane[];
  worktrees: { enabled: boolean; max_parallel: number };
  supervisor: { state: string; runs: number };
  debt: { handoffs: DebtItem[]; memory: DebtItem[] };
  counts: { backlog: number; active: number; done: number; hidden_superseded: number };
}

/** Success body of every `/api/v1` route; `meta` is present on paged and graph routes. */
export interface Envelope<T, M = undefined> {
  data: T;
  meta: M;
}

/** Error body of every `/api/v1` route. */
export interface ErrorBody {
  message: string;
  code: string;
  errors?: Record<string, string[]>;
}

/** A name in an `invalidate` message. */
export type Topic = "runs" | "status" | "tasks" | "memory" | "handoffs" | "harness" | "codegraph";

export interface HelloMessage {
  type: "hello";
  v: 1;
  payload: { protocol: 1; read_only: true; latest_event_id: number };
}

export interface EventsAppendedMessage {
  type: "events.appended";
  v: 1;
  payload: { events: Event[] };
}

export interface InvalidateMessage {
  type: "invalidate";
  v: 1;
  payload: { topics: Topic[] };
}

export interface ResyncMessage {
  type: "resync";
  v: 1;
  payload: { reason: "overflow" | "burst" | "cursor_regression" };
}

export interface CommandRefusedMessage {
  type: "command.refused";
  v: 1;
  payload: { code: "read_only"; message: string };
}

export interface ErrorMessage {
  type: "error";
  v: 1;
  payload: { code: "bad_message"; message: string };
}

/** Server to client. The client sends nothing in v1. */
export type WsMessage =
  | HelloMessage
  | EventsAppendedMessage
  | InvalidateMessage
  | ResyncMessage
  | CommandRefusedMessage
  | ErrorMessage;
