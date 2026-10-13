/* What the server sends, typed. One file, so a change to the API is a change
   in one place and the compiler finds every reader. Field names are the
   server's own (snake_case); nothing is renamed on the way in. */

/* ------------------------------------------------------------- the engine -- */

export type EngineStatus = "cold" | "loading" | "ready" | "error";

/** `engine.info()` in app/llm.py. */
export interface EngineInfo {
  status: EngineStatus;
  /** A sentence for the visitor when status is "error"; never a path. */
  error: string | null;
  model: string;
  quant: string;
  params: string;
  n_ctx: number;
  n_threads: number;
  n_batch: number;
  /** A phrase describing the machine, measured unless the operator set one. */
  hardware: string;
  mock: boolean;
  busy: boolean;
  queued: number;
  completed: number;
  load_ms: number | null;
  uptime_s: number;
  rss_mb: number | null;
  platform: string;
  /** CPUs this process can use (affinity and cgroup quota). */
  cpu_count: number | null;
}

/** `telemetry.snapshot()`: the machine, measured on the server. Any figure the
 *  platform could not give is null, and the page shows nothing for it. */
export interface SystemSnapshot {
  at: number;
  source: "procfs" | "psutil" | "none";
  cpus: number | null;
  cpu_pct: number | null;
  cores_pct: number[] | null;
  mem_total_mb: number | null;
  mem_used_mb: number | null;
  rss_mb: number | null;
  limit_mb: number | null;
  limit_high_mb: number | null;
  cgroup_mb: number | null;
  load: number[] | null;
}

/** GET /api/telemetry */
export interface Telemetry {
  system: SystemSnapshot;
  engine: Pick<EngineInfo, "status" | "busy" | "queued">;
}

/** GET /api/health */
export interface Health {
  engine: EngineInfo;
  system: SystemSnapshot;
  chat: {
    n: number;
    avg_tps: number | null;
    avg_ttft: number | null;
    tokens: number | null;
  };
}

/* ------------------------------------------- personas, skills and tools -- */

export interface Persona {
  id: string;
  name: string;
  blurb: string;
}

export interface Skill {
  id: string;
  name: string;
  glyph: string;
  blurb: string;
  /** Tool names, in the order the skill runs them. */
  pipeline: string[];
  persona: string;
  placeholder: string;
}

export interface ToolArg {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface Tool {
  name: string;
  label: string;
  glyph: string;
  description: string;
  card: CardKind | null;
  network: boolean;
  example: string;
  args: ToolArg[];
}

/* ------------------------------------------------------------------ cards -- */

/** The figure a tool's result is drawn as. "error" is a tool that failed. */
export type CardKind =
  | "calculator"
  | "weather"
  | "wikipedia"
  | "url"
  | "currency"
  | "convert"
  | "clock"
  | "textstats"
  | "error";

/** A card's payload. Each card type reads its own fields from it and treats
 *  every one as possibly missing: the data comes from the network. */
export type CardData = Record<string, unknown>;

/* ---------------------------------------------------------- conversations -- */

/** A row of GET /api/conversations. */
export interface ConversationSummary {
  id: string;
  title: string;
  persona: string;
  created_at: number;
  updated_at: number;
  /** How many messages it holds. */
  n: number;
}

export interface StoredToolCall {
  id: string;
  conversation_id: string;
  message_id: string | null;
  tool: string;
  via: string | null;
  args: Record<string, unknown>;
  card: CardKind | null;
  data: CardData;
  summary: string | null;
  ms: number | null;
  ok: boolean;
  created_at: number;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: number;
  ttft_ms: number | null;
  gen_tps: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  tool_calls: StoredToolCall[];
}

/** GET /api/conversations/{id} */
export interface Conversation {
  id: string;
  title: string;
  persona: string;
  created_at: number;
  updated_at: number;
  messages: StoredMessage[];
  /** Calls whose answer was cancelled before it could be stored. */
  orphan_tool_calls: StoredToolCall[];
}

/* ------------------------------------------------------- the chat stream -- */

/** POST /api/chat */
export interface ChatRequest {
  message: string;
  conversation_id?: string | null;
  persona?: string | null;
  skill?: string | null;
}

export interface StepEvent {
  /** "route", "tool-0", "tool-1", …, "answer". */
  id: string;
  kind: "route" | "tool" | "model";
  status: "running" | "ok" | "failed";
  label: string;
  glyph?: string;
  /** For a route: how it was decided ("skill", "slash", "pattern", "model", "direct"). For a failed tool: why. */
  detail?: string | null;
  tool?: string | null;
  args?: Record<string, unknown>;
  ms?: number;
}

/** What the answer cost, sent when it ends. */
export interface DoneEvent {
  cancelled: boolean;
  ttft_ms: number;
  queue_wait_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  prefill_tps: number;
  gen_tps: number;
  total_ms: number;
  rss_mb: number | null;
  tool_calls: number;
}

/** Every event of the chat stream, by name. A type, not an interface, so it
 *  can be the event map `sseFetch` is generic over. */
export type ChatEvents = {
  conversation: {
    id: string;
    title: string;
    created: boolean;
    skill: string | null;
    persona: string;
  };
  step: StepEvent;
  card: { step_id: string; card: CardKind; tool: string; data: CardData };
  trimmed: { dropped: number };
  /** Waiting for the one engine. `position` is how many are ahead. */
  queue: { position: number; first: boolean };
  /** Generation has the engine. Only now is there a request to cancel. */
  start: { request_id: string; queue_wait_ms: number; prompt_tokens: number; n_ctx: number };
  /** One piece of text. The first of a response also carries `ttft_ms`. */
  token: { t: string; ttft_ms?: number };
  done: DoneEvent;
  error: { message: string };
};

/* -------------------------------------------------------------- benchmark -- */

export interface BenchCase {
  id: string;
  label: string;
  /** Prompt size aimed for, in tokens. */
  target: number;
  gen: number;
  reps: number;
}

export interface BenchCaseSummary {
  id: string;
  label: string;
  prompt_tokens: number;
  gen_tokens: number;
  reps: number;
  ttft_p50: number;
  ttft_p95: number;
  prefill_tps: number;
  gen_tps: number;
  gen_tps_min: number;
  gen_tps_max: number;
  rss_mb: number | null;
}

/** The newest stored suite, rebuilt from its runs. */
export interface BenchLatest {
  cases: BenchCaseSummary[];
  gen_tps: number;
  prefill_tps: number;
  rss_mb: number | null;
  at: number;
}

/** A row of the `runs` table. */
export interface BenchRun {
  id: string;
  kind: string;
  label: string | null;
  prompt_tokens: number | null;
  gen_tokens: number | null;
  ttft_ms: number | null;
  prefill_tps: number | null;
  gen_tps: number | null;
  rss_mb: number | null;
  created_at: number;
}

/** A reference machine. None of these rows is this server. */
export interface BenchReference {
  hw: string;
  threads: number | string;
  prefill: string;
  gen: string;
}

/** One part of the memory estimate. */
export interface BudgetPart {
  name: string;
  mb: number;
  note: string;
  /** Only on the weights: whether the figure is the size of a real file. */
  on_disk?: boolean;
}

/** Where a 7B model would stand on this machine. `fits` is null when the
 *  machine's memory could not be read; the page then makes no claim. */
export interface LargerModel {
  need_mb: number;
  room_mb: number | null;
  by_limit: boolean;
  fits: boolean | null;
  share: number | null;
}

/** What the benchmark page shows before any run. */
export interface BenchStatic {
  cases: BenchCase[];
  reference: BenchReference[];
  budget: BudgetPart[];
  larger: LargerModel;
  latest: BenchLatest | null;
}

/** GET /api/bench/results */
export interface BenchResults extends BenchStatic {
  history: BenchRun[];
  info: EngineInfo;
}

/** Every event of the benchmark stream, by name. */
export type BenchEvents = {
  stage: { text: string; pct: number };
  rep: { case: string; rep: number; gen_tps: number; ttft_ms: number; pct: number };
  case: BenchCaseSummary;
  summary: {
    cases: BenchCaseSummary[];
    elapsed_s: number;
    gen_tps: number | null;
    prefill_tps: number | null;
    rss_mb: number | null;
    info: EngineInfo;
    at: number;
    /** False in mock mode: the figures measured a sleep and were not kept. */
    stored: boolean;
  };
  error: { message: string };
};

/* -------------------------------------------------------------- bootstrap -- */

/** The first values a page shows, written into its HTML by the server (or
 *  fetched from /api/bootstrap under the Vite dev server). */
export interface Bootstrap {
  version: string;
  info: EngineInfo;
  system: SystemSnapshot;
  personas: Persona[];
  defaultPersona: string;
  skills: Skill[];
  tools: Tool[];
  conversations: ConversationSummary[];
  bench: BenchStatic;
}
