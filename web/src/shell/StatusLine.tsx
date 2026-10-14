import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { coreNoun, cpuText, fmtInt, gb, pct } from "@/lib/format";
import { EASE, reduced } from "@/lib/frame";
import type { SystemSnapshot } from "@/lib/types";
import { useInfo, useSystem } from "@/state/queries";
import { useShell } from "@/state/shell";
import { bindFigures } from "./figures";
import type { PageName } from "./page";
import { SysRow } from "./sys";

/* The status line. On the left, the machine: its state, the model, and what
   its CPUs and memory are doing right now, measured on the server and
   repainted about once a second. On the right, on the chat page, the
   conversation: what a request is costing while it is in flight, and how full
   the context is. When an answer ends its figures are printed under it and
   leave this line.

   The elements and their class names are the ones styles/status.css is
   written against; see the note at the top of that file. */

// What the server's own states are called on screen.
const STATE_WORDS: Record<string, string> = {
  cold: "starting",
  loading: "loading",
  ready: "ready",
  error: "model unavailable",
};

const MAX_CORE_BARS = 8; // past this, columns would be a comb; one bar for all

interface Load {
  /** The sample these came from, so the same one is not taken twice. */
  at: number;
  cores: number[];
  total: number;
}

const loadOf = (s: SystemSnapshot): Load | null =>
  s.cores_pct && s.cpu_pct !== null ? { at: s.at, cores: s.cores_pct, total: s.cpu_pct } : null;

export function StatusLine({ page }: { page: PageName }) {
  const info = useInfo();
  const system = useSystem();
  const phase = useShell((s) => s.phase);
  const otherBusy = useShell((s) => s.otherBusy);
  const [open, setOpen] = useState(false);
  const bar = useRef<HTMLElement>(null);

  let key: string = phase?.key || info.status || "cold";
  let word = phase?.text || STATE_WORDS[info.status] || info.status || "";
  if (!phase && info.status === "ready" && otherBusy) {
    key = "busy";
    word = "busy";
  }

  // A load figure is the difference between two readings, and a server nobody
  // has asked for a while answers once with none. The bars then keep what
  // they last showed instead of dropping to nothing for a second.
  const [load, setLoad] = useState<Load | null>(() => loadOf(system));
  const fresh = loadOf(system);
  if (fresh && fresh.at !== load?.at) setLoad(fresh);

  const coreCount = load
    ? load.cores.length
    : (system.cores_pct?.length ?? (system.cpus === null ? 0 : Math.ceil(system.cpus)));
  // One bar per core up to eight; past that, one bar for all of them.
  const aggregate = coreCount > MAX_CORE_BARS;
  const bars = aggregate
    ? [load ? load.total : null]
    : Array.from({ length: coreCount }, (_, i) => (load ? (load.cores[i] ?? 0) : null));

  const ramKnown = system.mem_total_mb !== null && system.mem_used_mb !== null;
  const ramShare = ramKnown ? (system.mem_used_mb as number) / (system.mem_total_mb as number) : 0;

  // Toasts and the aurora sit just above the line, which is one row or two
  // depending on width.
  useEffect(() => {
    const el = bar.current;
    if (!el || !("ResizeObserver" in window)) return;
    const observer = new ResizeObserver(() => {
      document.documentElement.style.setProperty("--status-h", `${el.offsetHeight}px`);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    // biome-ignore lint/a11y/useAriaPropsSupportedByRole: a page-level footer is the contentinfo landmark, and this names it
    <footer
      className="status"
      id="status"
      ref={bar}
      data-phase={key}
      data-open={open ? "true" : undefined}
      data-ram={ramKnown ? (ramShare > 0.9 ? "high" : "ok") : undefined}
      aria-label="Server status"
    >
      <div className="status-id">
        <span className="st-state">
          <span className="dot" aria-hidden="true" />
          <StateWord text={word} />
        </span>
        <span className="st-seg st-model" title={`${info.model} ${info.quant}`}>
          {info.model} <span className="num">{info.quant}</span>
        </span>
        <span
          className="st-seg st-cpu"
          id="st-cpu"
          hidden={system.cpus === null}
          title={`The whole machine, measured now${
            system.load ? ` · load average ${system.load.join(" ")}` : ""
          }`}
        >
          <span className="cpu-n">
            <span className="num" id="cpu-count">
              {system.cpus === null ? "" : cpuText(system.cpus)}
            </span>{" "}
            <span id="cpu-noun">{system.cpus === null ? "cores" : coreNoun(system.cpus)}</span>
          </span>
          <span className="st-short">CPU</span>
          <span
            className={aggregate ? "cpu-bars agg" : "cpu-bars"}
            id="cpu-bars"
            aria-hidden="true"
          >
            {bars.map((value, i) => (
              <i
                // biome-ignore lint/suspicious/noArrayIndexKey: a bar is its core's position
                key={i}
                style={
                  value === null
                    ? undefined
                    : ({ "--v": (value / 100).toFixed(3) } as React.CSSProperties)
                }
              />
            ))}
          </span>
          <b className="num" id="cpu-pct">
            {load ? pct(load.total) : ""}
          </b>
        </span>
        <span
          className="st-seg st-ram"
          id="st-ram"
          hidden={!ramKnown}
          title={
            ramKnown
              ? `The whole machine's memory, measured now${
                  system.rss_mb !== null
                    ? ` · the app and its model hold ${gb(system.rss_mb)} GB`
                    : ""
                }`
              : undefined
          }
        >
          <span className="st-lab">RAM</span>{" "}
          <b className="num" id="ram-used">
            {system.mem_used_mb !== null ? gb(system.mem_used_mb) : ""}
          </b>
          {" / "}
          <span className="num" id="ram-total">
            {system.mem_total_mb !== null ? `${gb(system.mem_total_mb)} GB` : ""}
          </span>
          <span className="meter" aria-hidden="true">
            <i
              id="ram-fill"
              style={ramKnown ? ({ "--v": ramShare.toFixed(3) } as React.CSSProperties) : undefined}
            />
          </span>
        </span>
        {/* Says the answers are canned, so it is never hidden with the rest. */}
        <span className="st-tag" id="b-mock" hidden={!info.mock}>
          mock
        </span>
      </div>
      <div className="status-figs" id="status-figs">
        {page === "chat" && <ConversationFigures contextSize={info.n_ctx} />}
      </div>
      {/* Shown only when the line is opened on a narrow screen: the figures
          that are worth having but not worth a place on the line itself. */}
      <div className="status-extra" id="status-extra">
        <span className="st-seg">
          <span className="num">{info.n_threads}</span> threads for the model
        </span>
        <SysRow fact="rss" className="st-seg">
          {(value) => (
            <>
              the app and its model hold <b className="num">{value}</b>
            </>
          )}
        </SysRow>
        <SysRow fact="limit" className="st-seg">
          {(value) => (
            <>
              service limit <b className="num">{value}</b>
            </>
          )}
        </SysRow>
        <SysRow fact="load" className="st-seg">
          {(value) => (
            <>
              load average <b className="num">{value}</b>
            </>
          )}
        </SysRow>
      </div>
      {/* On a narrow screen the line shows one figure and keeps the rest behind this. */}
      <button
        className="status-more"
        id="status-more"
        type="button"
        aria-expanded={open}
        aria-controls="status-figs status-extra"
        onClick={() => setOpen((was) => !was)}
      >
        {open ? "Hide" : "Details"}
      </button>
    </footer>
  );
}

/**
 * The state word. When it changes, the outgoing word leaves upward as the new
 * one comes up from below, so a change of state is seen to happen rather than
 * found to have happened.
 *
 * The element's children are the two words while one replaces the other, so
 * they are made here and not rendered: React is given an empty element and
 * never looks inside it.
 */
function StateWord({ text }: { text: string }) {
  const host = useRef<HTMLSpanElement>(null);
  // What the line currently says. Compared against rather than read back from
  // the element, which briefly holds two words.
  const shown = useRef<string | null>(null);

  useLayoutEffect(() => {
    const el = host.current;
    if (!el || shown.current === text) return;
    const first = shown.current === null;
    shown.current = text;

    const next = document.createElement("span");
    next.className = "st-word-in";
    next.textContent = text;
    const old = el.querySelector<HTMLElement>(".st-word-in:not(.out)");
    if (first || reduced() || !old || !old.animate || document.hidden) {
      el.replaceChildren(next);
      return;
    }
    old.classList.add("out");
    el.appendChild(next);
    old.animate(
      [
        { opacity: 1, translate: "0 0" },
        { opacity: 0, translate: "0 -70%" },
      ],
      { duration: 140, easing: "cubic-bezier(0.7, 0, 0.84, 0)", fill: "forwards" },
    ).onfinish = () => old.remove();
    next.animate(
      [
        { opacity: 0, translate: "0 70%" },
        { opacity: 1, translate: "0 0" },
      ],
      { duration: 220, easing: EASE },
    );
  }, [text]);

  return <span className="st-word" id="b-state" ref={host} />;
}

/**
 * The right-hand figures, on the chat page only. Their text and visibility
 * are written by shell/figures.ts, many times a second while an answer is
 * being produced; this component draws the empty slots once and hands them
 * over. It takes nothing that changes, so it never renders again and never
 * undoes what was written.
 */
const ConversationFigures = memo(function ConversationFigures({
  contextSize,
}: {
  contextSize: number;
}) {
  const wait = useRef<HTMLSpanElement>(null);
  const tps = useRef<HTMLSpanElement>(null);
  const ttft = useRef<HTMLSpanElement>(null);
  const ctx = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const bar = document.getElementById("status");
    const find = <T extends HTMLElement>(root: HTMLElement | null, selector: string) =>
      root?.querySelector<T>(selector) ?? null;
    const els = {
      bar,
      wait: wait.current,
      waitLead: find(wait.current, "#st-wait-lead"),
      waitS: find(wait.current, "#st-wait-s"),
      tpsFig: tps.current,
      tps: find(tps.current, "#tps-value"),
      spark: find<HTMLCanvasElement>(tps.current, "#spark"),
      ttftFig: ttft.current,
      ttft: find(ttft.current, "#ttft-value"),
      ctxValue: find(ctx.current, "#ctx-value"),
      ctxFill: find(ctx.current, "#ctx-fill"),
    };
    if (Object.values(els).some((el) => el === null)) return;
    bindFigures(els as { [K in keyof typeof els]: NonNullable<(typeof els)[K]> });
    return () => bindFigures(null);
  }, []);

  return (
    <>
      <span className="st-fig st-wait" id="st-wait" ref={wait} hidden>
        <span id="st-wait-lead" />
        <b className="num" id="st-wait-s" />
      </span>
      <span className="st-fig st-tps" ref={tps} hidden>
        <b className="num" id="tps-value" /> tok/s
        <span className="spark-wrap" aria-hidden="true">
          <canvas className="spark" id="spark" />
        </span>
      </span>
      <span className="st-fig st-ttft" ref={ttft} hidden>
        first token <b className="num" id="ttft-value" />
      </span>
      <span className="st-fig st-ctx" ref={ctx}>
        <b className="num" id="ctx-value" /> / <span className="num">{fmtInt(contextSize)}</span>{" "}
        ctx
        <span className="meter" aria-hidden="true">
          <i id="ctx-fill" />
        </span>
      </span>
    </>
  );
});
