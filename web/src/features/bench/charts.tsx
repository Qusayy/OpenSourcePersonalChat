/* The charts. Each is plain HTML or SVG, styled by bench.css, and each draws
   itself when it is scrolled to (shell/reveal.ts): on screen or printed, a
   chart is simply there; it is only the arrival that is animated.

   A piece of text is written as one string wherever the page it replaces had
   one: a figure and its unit split across two text nodes are shaped apart
   and can land a pixel differently. */

import {
  type CSSProperties,
  Fragment,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { fmt, fmtInt, fmtMs } from "@/lib/format";
import type { BenchCaseSummary, BenchReference, BenchRun, BudgetPart } from "@/lib/types";
import { reveal, unreveal, useReveal } from "@/shell/reveal";
import {
  type BarRow,
  barShare,
  barTop,
  budgetShare,
  HISTORY_HEIGHT,
  historyGeometry,
  historyPoints,
  rangeChart,
} from "./geometry";
import { Empty } from "./parts";

/* ----------------------------------------------------------- bar chart -- */

export function BarChart({
  id,
  rows,
  unit,
  digits = 1,
  max,
}: {
  id: string;
  rows: readonly BarRow[];
  unit: string;
  digits?: number;
  max?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  // New bars grow from the baseline when the chart is on screen.
  useReveal(host, rows);
  const top = rows.length ? barTop(rows, max) : 1;

  return (
    <div className="chart" id={id} ref={host}>
      {rows.length ? (
        rows.map((row, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: rows are positions on a chart; a p95 row has no label of its own
          <div className="bar-row" key={i}>
            <span className="lab">
              {row.label}
              {row.sub ? <small>{row.sub}</small> : null}
            </span>
            <span className="track" title={`${row.label}: ${fmt(row.value, digits)} ${unit}`}>
              <i
                className={row.alt ? "alt" : undefined}
                style={{ "--v": barShare(row.value, top) } as CSSProperties}
              />
            </span>
            <span className="val">
              {fmt(row.value, digits)}
              <small>{` ${unit}`}</small>
              {row.extra ? <small>{` · ${row.extra}`}</small> : null}
            </span>
          </div>
        ))
      ) : (
        <Empty>Run the suite to fill this in.</Empty>
      )}
    </div>
  );
}

/* --------------------------------------------------------- range strip -- */

/**
 * Where this machine sits against reference hardware. Reference rows are
 * published ranges. This server is a row of its own, holding only what the
 * suite measured here: which class of machine it belongs to is not something
 * the page can know, so it does not say.
 */
export function RangeStrip({
  reference,
  measured,
  threads,
}: {
  reference: readonly BenchReference[];
  measured?: number | null;
  threads?: number;
}) {
  const chart = useMemo(() => rangeChart(reference, measured), [reference, measured]);
  return (
    <>
      <div className="chart" id="chart-ref">
        <div className="bar-row">
          <span className="lab">
            This server
            <small>
              {chart.here !== null && measured
                ? `${threads ? `${threads} threads · ` : ""}measured`
                : "not measured yet"}
            </small>
          </span>
          <span className="strip self">
            {chart.here !== null && measured ? (
              <b
                style={{ left: `${chart.here}%` }}
                title={`measured here: ${fmt(measured, 1)} tok/s`}
              />
            ) : null}
          </span>
          <span className="val">{measured ? fmt(measured, 1) : ""}</span>
        </div>
        {chart.rows.map((row) => (
          <div className="bar-row" key={row.hw}>
            <span className="lab">
              {row.hw}
              <small>{`${row.threads} threads`}</small>
            </span>
            <span className="strip">
              <i
                style={{ left: `${row.left}%`, width: `${row.width}%` }}
                title={`${row.gen} tok/s`}
              />
            </span>
            <span className="val">{row.gen}</span>
          </div>
        ))}
      </div>
      <div className="flex justify-between mt-2 text-ink-2 text-1">
        <span className="num">0</span>
        <span>tokens per second</span>
        <span className="num" id="ref-max">
          {Math.round(chart.max)}
        </span>
      </div>
    </>
  );
}

/* ------------------------------------------------------- memory budget -- */

/**
 * The memory estimate as one stacked bar and its legend. The one chart with
 * categories: four colours in a fixed order, and every segment also named
 * with its value beneath, so nothing rests on telling the colours apart.
 */
export function BudgetChart({
  budget,
  notes,
}: {
  budget: readonly BudgetPart[];
  /** Say where each figure comes from, after its value. */
  notes?: boolean;
}) {
  const stack = useRef<HTMLDivElement>(null);
  useReveal(stack);
  const total = budget.reduce((sum, part) => sum + part.mb, 0);

  return (
    <>
      <div
        className="stack"
        role="img"
        aria-label={`Estimated memory use: ${budget
          .map((part) => `${part.name} ${part.mb} megabytes`)
          .join(", ")}`}
        ref={stack}
      >
        {budget.map((part, i) => (
          <span
            key={part.name}
            style={{ width: `${budgetShare(part.mb, total)}%`, background: `var(--c${i + 1})` }}
            title={`${part.name} — ${part.mb} MB`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-y-2 gap-x-5">
        {budget.map((part, i) => (
          <span className="flex items-baseline gap-2 text-2" key={part.name}>
            <span className="size-[10px] flex-none" style={{ background: `var(--c${i + 1})` }} />
            <span>
              {`${part.name} `}
              <b className="num">{`${part.mb} MB`}</b>
              {notes ? (
                <>
                  {" "}
                  <span className="text-ink-2 text-1">{`· ${part.note}`}</span>
                </>
              ) : null}
            </span>
          </span>
        ))}
      </div>
    </>
  );
}

/** The estimate's total, in megabytes. */
export const budgetTotal = (budget: readonly BudgetPart[]): number =>
  budget.reduce((sum, part) => sum + part.mb, 0);

/* ------------------------------------------------------- history chart -- */

/**
 * Generation speed across every stored run, oldest first. Drawn at the pixel
 * width it is given and redrawn when that changes, so its labels stay the
 * size the stylesheet says. Hidden while there is nothing to join into a line.
 */
export function HistoryChart({ runs }: { runs: readonly BenchRun[] }) {
  const svg = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(0);
  const pts = useMemo(() => historyPoints(runs), [runs]);
  const none = pts.length < 2;

  // The chart has no width while it is hidden, so it is measured once it is
  // shown, before that is painted.
  // biome-ignore lint/correctness/useExhaustiveDependencies: measured again when the chart is shown or hidden
  useLayoutEffect(() => {
    const w = Math.round(svg.current?.clientWidth ?? 0);
    if (w) setWidth(w);
  }, [none]);

  useEffect(() => {
    const el = svg.current;
    const box = el?.parentElement;
    if (!el || !box || !("ResizeObserver" in window)) return;
    const observer = new ResizeObserver(() => {
      const w = Math.round(el.clientWidth);
      if (w) setWidth((was) => (was === w ? was : w));
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  const drawn = !none && width > 0;
  // Drawn in the first time it is seen. A redraw at a new width must not
  // replay that, so the mark of having arrived is left on.
  useEffect(() => {
    const el = svg.current as unknown as HTMLElement | null;
    if (!drawn || !el) return;
    if (!el.classList.contains("in-view")) reveal(el);
    return () => unreveal(el);
  }, [drawn]);

  const g = drawn ? historyGeometry(pts, width) : null;

  return (
    <>
      <svg
        className="linechart"
        id="history-chart"
        viewBox={`0 0 ${g ? g.width : 720} ${HISTORY_HEIGHT}`}
        role="img"
        aria-label="Generation speed across past benchmark runs"
        // `hidden` is as good on an SVG element as on any other; the types
        // list it for HTML only.
        {...({ hidden: none } as object)}
        ref={svg}
      >
        {g ? (
          <>
            {g.grid.map((tick) => (
              <Fragment key={tick.y}>
                <line className="grid" x1={g.left} x2={g.right} y1={tick.y} y2={tick.y} />
                <text className="lbl" x={g.labelX} y={tick.labelY} textAnchor="end">
                  {tick.value}
                </text>
              </Fragment>
            ))}
            <path className="area" d={g.area} />
            {/* pathLength 1 lets the stylesheet draw the line in with a dash
                offset without knowing how long it is. */}
            <path className="series" pathLength="1" d={g.line} />
            {g.points.map((p) => (
              <circle className="pt" key={p.run.id} cx={p.x} cy={p.y} r="4">
                <title>{`${p.run.label || "run"} — ${fmt(p.run.gen_tps, 2)} tok/s · ${new Date(
                  p.run.created_at * 1000,
                ).toLocaleString()}`}</title>
              </circle>
            ))}
            <text className="lbl strong" x={g.last.x} y={g.last.y} textAnchor="end">
              {`${fmt(g.last.value, 1)} tok/s`}
            </text>
            <text className="lbl" x={g.left} y={g.captionY}>
              oldest
            </text>
            <text className="lbl" x={g.right} y={g.captionY} textAnchor="end">
              newest
            </text>
          </>
        ) : null}
      </svg>
      <Empty id="history-empty" hidden={!none}>
        No benchmark runs recorded yet.
      </Empty>
    </>
  );
}

/* --------------------------------------------------------------- table -- */

/** The same data as the charts, for reading and copying. */
export function NumbersTable({ cases }: { cases: readonly BenchCaseSummary[] }) {
  return (
    <table className="tbl" id="case-table">
      <thead>
        <tr>
          <th>Case</th>
          <th>Prompt tok</th>
          <th>Gen tok</th>
          <th>Runs</th>
          <th>Prefill tok/s</th>
          <th>Gen tok/s</th>
          <th>TTFT p50</th>
          <th>TTFT p95</th>
        </tr>
      </thead>
      <tbody>
        {cases.length ? (
          cases.map((c) => (
            <tr key={c.id}>
              <td data-label="Case">{c.label}</td>
              <td data-label="Prompt tokens">{fmtInt(c.prompt_tokens)}</td>
              <td data-label="Generated tokens">{fmtInt(c.gen_tokens)}</td>
              <td data-label="Runs">{c.reps}</td>
              <td data-label="Prefill tok/s">{fmt(c.prefill_tps, 1)}</td>
              <td data-label="Gen tok/s">{fmt(c.gen_tps, 2)}</td>
              <td data-label="TTFT p50">{fmtMs(c.ttft_p50)}</td>
              <td data-label="TTFT p95">{fmtMs(c.ttft_p95)}</td>
            </tr>
          ))
        ) : (
          <tr>
            <td colSpan={8} className="tbl-empty">
              No run recorded yet.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
