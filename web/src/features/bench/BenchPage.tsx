import { useEffect, useMemo, useRef, useState } from "react";
import { BENCH_RUN_URL } from "@/lib/api";
import { bootstrap } from "@/lib/bootstrap";
import { cx } from "@/lib/cx";
import { fmt, fmtInt, fmtMs } from "@/lib/format";
import { sseFetch } from "@/lib/sse";
import type { BenchCaseSummary, BenchEvents } from "@/lib/types";
import { Button } from "@/shell/Button";
import { useBenchResults, useInfo } from "@/state/queries";
import { toast } from "@/state/shell";
import { BarChart, HistoryChart, NumbersTable, RangeStrip } from "./charts";
import type { BarRow } from "./geometry";
import { MemoryEstimate } from "./MemoryEstimate";
import {
  Caveat,
  Code,
  Empty,
  FACT_ROW,
  FACT_TERM,
  Fact,
  Facts,
  LongPage,
  P,
  PageHead,
  Panel,
  Section,
} from "./parts";

/* Benchmark: run the suite over a stream, then draw what came back.

   The page opens on what the server already knows (the suite, the reference
   machines, the memory estimate and the newest stored run are in the page),
   asks for the run history, and after a run draws that run. */

/** A suite's headline figures: the newest stored one, or the one just run. */
interface Summary {
  cases: BenchCaseSummary[];
  gen_tps: number | null;
  prefill_tps: number | null;
  rss_mb: number | null;
}

const CONTENTS = [
  ["measured", "Measured on this box"],
  ["throughput", "Throughput by prompt size"],
  ["latency", "Time to first token"],
  ["context", "Against other hardware"],
  ["memory", "Memory budget"],
  ["history", "History"],
  ["why", "Why it is this fast"],
] as const;

const has = (v: number | null | undefined): v is number =>
  v !== null && v !== undefined && !Number.isNaN(v);

export function BenchPage() {
  const info = useInfo();
  const boot = bootstrap().bench;
  const results = useBenchResults();

  // A mock run is not kept on the server, so there is nothing to read back:
  // what just arrived is drawn, over whatever history was there.
  const [unstored, setUnstored] = useState<Summary | null>(null);
  const stored = results.data ? results.data.latest : boot.latest;
  const summary: Summary | null = unstored ?? stored;
  const runs = results.data?.history ?? NO_RUNS;

  useEffect(() => {
    if (results.isError) toast("Could not load benchmark results", "err");
  }, [results.isError]);

  /* ---------------------------------------------------------- running -- */

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [status, setStatus] = useState(() =>
    boot.latest
      ? "Last run stored on this server."
      : info.mock
        ? "Nothing is stored in mock mode. A run takes about two minutes."
        : "No run recorded yet — takes about two minutes.",
  );
  const busy = useRef(false);
  const request = useRef<AbortController | null>(null);
  // Leaving the page ends the run, as closing it always did.
  useEffect(() => () => request.current?.abort(), []);

  // Started from the click, never from an effect: a development build runs
  // effects twice, and the suite takes two minutes of the one engine.
  async function run() {
    if (busy.current) return;
    busy.current = true;
    setRunning(true);
    setProgress(0);
    setStatus("Starting…");
    const controller = new AbortController();
    request.current = controller;

    try {
      await sseFetch<BenchEvents>(BENCH_RUN_URL, {
        signal: controller.signal,
        onEvent: (event, data) => {
          if (event === "stage") {
            setStatus(data.text);
            if (data.pct != null) setProgress(data.pct / 100);
          } else if (event === "rep") {
            setProgress(data.pct / 100);
            setStatus(
              `${data.case} run ${data.rep}: ${fmt(data.gen_tps, 2)} tok/s · ${fmtMs(
                data.ttft_ms,
              )} to first token`,
            );
          } else if (event === "case") {
            setStatus(`${data.label} done — median ${fmt(data.gen_tps, 2)} tok/s`);
          } else if (event === "summary") {
            setProgress(1);
            setStatus(
              `Finished in ${fmt(data.elapsed_s, 0)}s — median ${fmt(data.gen_tps, 2)} tok/s${
                data.stored === false ? ". Mock mode: not stored." : ""
              }`,
            );
            if (data.stored === false) {
              setUnstored(data);
            } else {
              setUnstored(null);
              void results.refetch();
            }
          } else if (event === "error") {
            setStatus(data.message);
            toast(data.message, "err");
          }
        },
      });
    } catch (err) {
      if (!controller.signal.aborted) {
        const message = err instanceof Error ? err.message : String(err);
        setStatus(message);
        toast(message, "err");
      }
    } finally {
      busy.current = false;
      request.current = null;
      setRunning(false);
    }
  }

  /* --------------------------------------------------------- headline -- */

  // A figure that was not measured is left out, not printed as a dash: an
  // empty row under "Headline" reads as a result of nothing.
  const short = summary?.cases?.find((c) => c.id === "short") || summary?.cases?.[0];
  const tiles = {
    gen: has(summary?.gen_tps) ? fmt(summary.gen_tps, 1) : null,
    prefill: has(summary?.prefill_tps) ? fmt(summary.prefill_tps, 0) : null,
    ttft: has(short?.ttft_p50) ? fmtMs(short.ttft_p50) : null,
    rss: summary?.rss_mb ? `${fmtInt(summary.rss_mb)} MB` : null,
  };
  const anyTile = Object.values(tiles).some((v) => v !== null);

  /* ----------------------------------------------------------- charts -- */

  // Kept the same from one render to the next unless the suite changes: a
  // chart given new rows draws itself again, and the status line under the
  // button changes several times a second during a run.
  const cases = summary?.cases;
  const throughput = useMemo<BarRow[]>(
    () =>
      (cases ?? []).map((c) => ({
        label: c.label,
        sub: `${fmtInt(c.prompt_tokens)} prompt tokens`,
        value: c.gen_tps,
        extra: `${fmt(c.gen_tps_min, 1)}–${fmt(c.gen_tps_max, 1)}`,
      })),
    [cases],
  );
  // The p95 rows read as the lighter of each pair.
  const latency = useMemo<BarRow[]>(
    () =>
      (cases ?? []).flatMap((c) => [
        { label: c.label, sub: "p50", value: c.ttft_p50 },
        { label: "", sub: "p95", value: c.ttft_p95, alt: true },
      ]),
    [cases],
  );

  return (
    <LongPage>
      <PageHead title="How fast this server writes" contents={CONTENTS}>
        {`The suite runs ${info.model} through the same path the chat uses (same lock, same sampling, same context window) and records what came back.`}
      </PageHead>

      {info.mock ? (
        // Said before anything is read as a result.
        <Caveat className="mt-7" role="note">
          This server is in mock mode: no model is loaded, and the suite times a fake token stream
          paced by a timer. The figures it produces are not measurements of this machine, and they
          are not stored.
        </Caveat>
      ) : null}

      <section
        id="measured"
        className={cx(
          "flex flex-wrap items-center gap-y-4 gap-x-5 py-4 border-y border-rule",
          "intro:arrive-3",
          // After the mock note, whose own rule this bar's top rule closes.
          info.mock ? "mt-0" : "mt-7",
        )}
      >
        <Button variant="primary" id="run-btn" disabled={running} onClick={() => void run()}>
          Run the benchmark
        </Button>
        <div className="flex-1 min-w-[14rem]">
          {/* A run in progress is the live thing on this page. scaleX, not
              width: the bar advances on every rep, and animating width
              relayouts the row each frame. */}
          <div className="h-[4px] bg-rule overflow-hidden">
            <i
              id="progress"
              className="block h-full bg-live origin-left transition-[transform] duration-(--dur-2) ease-arrive"
              style={{ transform: `scaleX(${progress ?? 0})` }}
            />
          </div>
          <div className="mt-2 text-ink-2 text-1 tabular-nums" id="run-status">
            {status}
          </div>
        </div>
      </section>

      <Section title="Headline" sub="Median across every case in the most recent suite.">
        <Facts id="tiles" hidden={!anyTile}>
          <div className={FACT_ROW} id="row-gen" hidden={tiles.gen === null}>
            <dt className={FACT_TERM}>Generation</dt>
            <dd className={LEAD_VALUE}>
              <span className={LEAD_FIGURE} id="t-gen">
                {tiles.gen}
              </span>
              {" tokens per second"}
            </dd>
          </div>
          <div className={FACT_ROW} id="row-prefill" hidden={tiles.prefill === null}>
            <dt className={FACT_TERM}>Prefill</dt>
            <dd className={LEAD_VALUE}>
              <span className={LEAD_FIGURE} id="t-prefill">
                {tiles.prefill}
              </span>
              {" prompt tokens per second"}
            </dd>
          </div>
          <div className={FACT_ROW} id="row-ttft" hidden={tiles.ttft === null}>
            <dt className={FACT_TERM}>First token</dt>
            <dd className={LEAD_VALUE}>
              <span className={LEAD_FIGURE} id="t-ttft">
                {tiles.ttft}
              </span>
              {" short prompt, p50"}
            </dd>
          </div>
          <div className={FACT_ROW} id="row-rss" hidden={tiles.rss === null}>
            <dt className={FACT_TERM}>Peak memory</dt>
            <dd className={LEAD_VALUE}>
              <span className={LEAD_FIGURE} id="t-rss">
                {tiles.rss}
              </span>
              {" resident, the app and its model"}
            </dd>
          </div>
        </Facts>
        <Empty id="tiles-empty" hidden={anyTile}>
          Run the suite to fill this in.
        </Empty>
      </Section>

      <Section title="Suite" id="suite">
        <Facts>
          {boot.cases.map((c) => (
            <Fact key={c.id} term={c.label} figure>
              {`~${c.target} tok × ${c.reps}`}
            </Fact>
          ))}
          <Fact term="Generated">
            <span className="num">128 tok</span>
            {" each"}
          </Fact>
          <Fact term="Warm-up">discarded</Fact>
          <Fact term="Context" figure>
            {info.n_ctx}
          </Fact>
          <Fact term="Threads" figure>
            {`${info.n_threads} / ${info.cpu_count || "?"}`}
          </Fact>
          <Fact term="n_batch" figure>
            {info.n_batch}
          </Fact>
        </Facts>
      </Section>

      <Section
        title="Throughput by prompt size"
        id="throughput"
        sub="Generation speed barely moves as the prompt grows — the weights read per token are the same either way. Prompt length shows up in the wait before the first token, not in the speed after it."
      >
        <Panel>
          <BarChart id="chart-gen" rows={throughput} unit="tok/s" digits={2} />
        </Panel>
      </Section>

      <Section
        title="Time to first token"
        id="latency"
        sub="Prefill is compute-bound and batches, so a 1024-token prompt costs far less than 32× a 32-token one. The fainter bar is the p95."
      >
        <Panel>
          <BarChart id="chart-ttft" rows={latency} unit="ms" digits={0} />
        </Panel>
      </Section>

      <Section
        title="Against other hardware"
        id="context"
        sub="Other machines, from published figures for Qwen2.5-1.5B at Q4_K_M, a model of about this size."
      >
        <Caveat>
          Every row except this server is an order-of-magnitude range collected from published
          llama.cpp figures, not a measurement taken here. Real numbers swing widely with memory
          bandwidth, AVX support and — on shared cloud — whoever else is on the host.
        </Caveat>
        <Panel>
          <RangeStrip
            reference={boot.reference}
            measured={summary?.gen_tps}
            threads={info.n_threads}
          />
        </Panel>
      </Section>

      <Section
        title="Memory budget"
        id="memory"
        // An estimate, and labelled as one. The claim about a larger model is
        // worked out against this machine's measured memory, so it is only
        // made where it is true.
        sub={`An estimate of what a loaded ${info.params} model at ${info.quant} takes, worked out from ${
          boot.budget[0]?.on_disk ? "the size of its file" : "a typical size for its file"
        } and the context length. It is not a measurement; the measured figure is beneath it.`}
      >
        <Panel>
          <MemoryEstimate notes larger={boot.larger} />
        </Panel>
      </Section>

      <Section
        title="History"
        id="history"
        sub="Every benchmark run this server has recorded, oldest first."
      >
        <Panel className="breakout">
          <HistoryChart runs={runs} />
        </Panel>
      </Section>

      <Section
        title="The numbers"
        sub="The same data as the charts above, for reading and copying."
      >
        <Panel
          className="overflow-x-auto breakout"
          // A scrolling region must be reachable by keyboard to be scrolled by it.
          tabIndex={0}
          role="region"
          aria-label="Benchmark results, scrollable"
        >
          <NumbersTable cases={cases ?? NO_CASES} />
        </Panel>
      </Section>

      <Section title="Why it is this fast" id="why">
        <Panel>
          <P>
            {"Writing a token means reading "}
            <b>every weight in the model once</b>
            {". At "}
            <Code>{info.quant}</Code>
            {
              " that is the whole model file, read again for every token, so throughput tracks memory bandwidth far more closely than clock speed: "
            }
            <b>tokens per second ≈ effective bandwidth ÷ model size</b>
            {", which is why a desktop's faster memory puts its row above this server's."}
          </P>
          <P>
            {`That formula is also the whole argument for 4-bit. Dropping from 16-bit to 4-bit weights cuts the bytes moved per token by roughly four, and the speed rises by roughly the same factor, which is what makes a ${info.params} model usable on shared CPU cores, where a 7B model would write several times more slowly.`}
          </P>
          <P>
            {
              "Prefill behaves differently. Reading the prompt is a batched matrix multiply, so it is "
            }
            <b>compute-bound</b>
            {
              " and processes many tokens per pass. That is why a long prompt costs seconds rather than minutes, and why follow-up turns are quicker still: llama.cpp keeps the KV cache from the previous turn and only prefills what is new."
            }
          </P>
          <P>
            {
              "The last piece is the queue. Two generations running together fight over the same memory bandwidth and the same "
            }
            <span className="num">{info.n_threads}</span>
            {
              " model threads, and finish later than the same two run back to back, so this server writes them one at a time and shows your place in the line."
            }
          </P>
        </Panel>
      </Section>
    </LongPage>
  );
}

const NO_RUNS: never[] = [];
const NO_CASES: never[] = [];

/* The headline figures are the same list with the number given weight. */
const LEAD_VALUE = "text-right text-ink-2";
const LEAD_FIGURE = "text-ink font-semibold num";
