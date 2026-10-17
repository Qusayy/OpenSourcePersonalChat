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
  /** What holds the model: this app's own process, or a llama-server beside it. */
  runtime: "llama-cpp-python" | "llama-server";
  busy: boolean;
  queued: number;
  completed: number;
  load_ms: number | null;
  uptime_s: number;
  /** The app and its model, resident: one process, or the app and its llama-server. */
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
  /** The app and its model, resident, as in `EngineInfo.rss_mb`. */
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

/* ------------------------------------------------------------------ tools -- */

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
  | "note"
  | "search"
  | "news"
  | "error";

/** A card's payload. Each card type reads its own fields from it and treats
 *  every one as possibly missing: the data comes from the network. */
export type CardData = Record<string, unknown>;

/* ---------------------------------------------------------- conversations -- */

/** A row of GET /api/conversations. */
export interface ConversationSummary {
  id: string;
  title: string;
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
  /** What the answer said that nothing it looked up returned, in a line,
   *  when it said anything of the kind. */
  correction?: string | null;
  /** How the answer was arrived at, while its steps are kept. */
  run?: StoredRun | null;
  tool_calls: StoredToolCall[];
}

/** GET /api/conversations/{id} */
export interface Conversation {
  id: string;
  title: string;
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
}

export interface StepEvent {
  /** "route", "tool-0", "tool-1", …, "answer". */
  id: string;
  kind: "route" | "tool" | "model";
  /** "stopped": the run was stopped while this step was under way. */
  status: "running" | "ok" | "failed" | "stopped";
  label: string;
  glyph?: string;
  /** For a route: how it was decided ("skill", "slash", "heuristic", "model", "direct"). For a failed tool: why. */
  detail?: string | null;
  tool?: string | null;
  args?: Record<string, unknown>;
  ms?: number;
  /** On a step that was a pass through the model, once it has ended: the
   *  tokens it read and the tokens it wrote. Null if the pass never ran. */
  tokens_in?: number | null;
  tokens_out?: number | null;
  /** Which choice of the run this is, counting from one, when a run may make
   *  several: "route" is the first, "route-2" the second, and so on. */
  pass?: number;
}

/** The whole run in a line, sent last, after `done` or `error`. */
export interface RunEvent {
  /** The run's name: the `request_id` it was announced under. */
  id: string;
  /** "asked": the reply was a question put back to the user. */
  outcome: "answered" | "asked" | "stopped" | "failed";
  /** Everything it did: routing, each tool, the answer. */
  steps: number;
  /** How many of those were passes through the model. */
  passes: number;
  /** What the steps cost, added up. */
  total_ms: number;
  /** Where that went. The four add up to `total_ms`: waiting for the engine,
   *  the model reading, the model writing, and tools. */
  waiting_ms: number;
  reading_ms: number;
  writing_ms: number;
  tools_ms: number;
  /** First step to last by the clock; a little more than `total_ms`. */
  wall_ms: number | null;
  /** Each step, in order: what "How it ran" lists. */
  ledger: LedgerStep[];
}

/** One step of a run, as its ledger lists it. */
export interface LedgerStep {
  /** route | fill | decide | repair | tool | answer: one row per pass. */
  kind: string;
  /** The tool or skill; "ask" for a question put back; null for no tool. */
  name: string | null;
  /** How it was chosen: slash | heuristic | skill | model. */
  via: string | null;
  ok: boolean;
  error: string | null;
  /** On a pass: the prompt, what of it the model actually read (the rest
   *  was in its cache; null where that was not reported), and what it wrote. */
  tokens_in: number | null;
  tokens_read: number | null;
  tokens_out: number | null;
  ms: number;
}

/** A run as a stored answer reads it back: the run event, without what
 *  only the live run knew. */
export type StoredRun = Omit<RunEvent, "outcome"> & { outcome: RunEvent["outcome"] | null };

/** What the answer cost, sent when it ends. A run that was stopped before its
 *  answer was begun still ends in this, with `cancelled` set and nothing in the
 *  figures: no prompt was read and no token written. */
export interface DoneEvent {
  cancelled: boolean;
  ttft_ms: number | null;
  queue_wait_ms: number | null;
  prompt_tokens: number | null;
  /** Of the prompt, what the model read and what it had in its cache, as a
   *  model server reports them. Null where nothing reported them. */
  read_tokens?: number | null;
  cached_tokens?: number | null;
  completion_tokens: number;
  prefill_tps: number | null;
  gen_tps: number | null;
  total_ms: number | null;
  rss_mb: number | null;
  tool_calls: number;
  /** Set when the reply is a question the model put back to the user in place
   *  of an answer. The figures are then those of the pass that chose to ask. */
  asked?: boolean;
  /** The figures in the answer that nothing it looked up returned, and the
   *  line said about them under the answer. Absent where it was not checked. */
  unsupported?: string[];
  correction?: string | null;
}

/** Every event of the chat stream, by name. A type, not an interface, so it
 *  can be the event map `sseFetch` is generic over. */
export type ChatEvents = {
  conversation: {
    id: string;
    title: string;
    created: boolean;
    /** The run's name, known from the first event: what Stop asks the server
     *  to stop, at whatever stage the run has reached. */
    request_id?: string;
  };
  step: StepEvent;
  card: { step_id: string; card: CardKind; tool: string; data: CardData };
  /** Something had to give for the prompt to fit the window: the oldest
   *  messages, given to the model only in short, or the end of this one. */
  /** The answer so far is taken back: it said it could not look something
   *  up, and the run is looking it up and writing it again. */
  retract: { reason: string };
  trimmed: {
    dropped: number;
    /** Earlier turns read whole, and earlier turns given only in short. */
    turns?: number;
    summarised?: number;
    /** This message was too long, and the model read its first part. */
    cut?: boolean;
  };
  /** Waiting for the one engine. `position` is how many are ahead. */
  queue: { position: number; first: boolean };
  /** The pass that writes the answer has the engine. `request_id` is the
   *  run's, the same one `conversation` carried. */
  start: { request_id: string; queue_wait_ms: number; prompt_tokens: number; n_ctx: number };
  /** One piece of text. The first of a response also carries `ttft_ms`. */
  token: { t: string; ttft_ms?: number };
  done: DoneEvent;
  error: { message: string };
  run: RunEvent;
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
  tools: Tool[];
  conversations: ConversationSummary[];
  bench: BenchStatic;
}
