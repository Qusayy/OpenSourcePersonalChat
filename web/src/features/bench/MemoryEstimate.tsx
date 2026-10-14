import type { ReactNode } from "react";
import { bootstrap } from "@/lib/bootstrap";
import { gb, gbExact } from "@/lib/format";
import type { LargerModel } from "@/lib/types";
import { SysRow } from "@/shell/sys";
import { useInfo } from "@/state/queries";
import { BudgetChart, budgetTotal } from "./charts";
import { AFTER_CHART } from "./parts";

/**
 * What a loaded model takes, part by part: the chart, then the total in a
 * sentence, then what the app and its model actually hold right now.
 *
 * The estimate is arithmetic done on the server when the page was sent (the
 * size of the model's file, the context length, two round figures), and the
 * machine's total memory is from that same moment; neither changes while the
 * page is open. The measured figure beside them does, and is live.
 */
export function MemoryEstimate({
  notes,
  larger,
  children,
}: {
  /** Say where each part's figure comes from. */
  notes?: boolean;
  /** Also say where a 7B model would stand on this machine. */
  larger?: LargerModel;
  /** Ends the first paragraph: a link onward, say. */
  children?: ReactNode;
}) {
  const info = useInfo();
  const { bench, system } = bootstrap();
  const total = budgetTotal(bench.budget);
  const machine = system.mem_total_mb;

  return (
    <>
      <BudgetChart budget={bench.budget} notes={notes} />
      <p className={AFTER_CHART}>
        {"Estimated with the model loaded: "}
        <b className="num">{`${gbExact(total)} GB`}</b>
        {machine !== null ? (
          <>
            {" of the "}
            <b className="num">{`${gb(machine)} GB`}</b>
            {" on this machine. "}
          </>
        ) : (
          ". "
        )}
        <SysRow fact="rss-exact">
          {(value) => (
            <>
              {"Measured now: the app and its model hold "}
              <b className="num">{value}</b>
              {" resident."}
            </>
          )}
        </SysRow>
        {info.mock
          ? " This is mock mode: the weights are not loaded, so the two are not expected to agree. "
          : " "}
        {children}
      </p>
      {larger ? <LargerSentence larger={larger} /> : null}
    </>
  );
}

/** The comparison with a larger model, made only where it can be made truthfully. */
function LargerSentence({ larger }: { larger: LargerModel }) {
  const room = larger.room_mb;
  let figure: string | null = null;
  let before = " with the rest of this budget.";
  let after = "";
  if (larger.fits === false && room !== null) {
    before += ` It would not load here: ${
      larger.by_limit ? "this service is limited to" : "this machine has"
    } `;
    figure = `${gb(room)} GB`;
    after = ". If it did load, every token would come off disk.";
  } else if (larger.fits) {
    before += " It would fit here, taking about ";
    figure = `${larger.share}%`;
    after = ` of ${
      larger.by_limit ? "the memory this service is allowed" : "this machine's memory"
    }, and would write several times more slowly.`;
  }
  return (
    <p className={AFTER_CHART}>
      {"A 7B model at the same quantisation is roughly 4.4 GB of weights alone, about "}
      <b className="num">{`${gb(larger.need_mb)} GB`}</b>
      {before}
      {figure !== null ? <b className="num">{figure}</b> : null}
      {after}
    </p>
  );
}
