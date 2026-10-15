import { Link } from "react-router";
import { MemoryEstimate } from "@/features/bench/MemoryEstimate";
import {
  Code,
  FACT_ROW,
  FACT_TERM,
  FACT_VALUE,
  Fact,
  Facts,
  LongPage,
  P,
  PageHead,
  Panel,
  Section,
} from "@/features/bench/parts";
import { type SysFactName, SysRow } from "@/shell/sys";
import { useInfo } from "@/state/queries";

/* About: what runs here, how, and what it cannot do. Long-form, set from the
   same pieces as the benchmark page. */

const CONTENTS = [
  ["stack", "The stack"],
  ["queue", "One request at a time"],
  ["template", "Getting the prompt right"],
  ["agent", "The Agent"],
  ["limits", "What it cannot do"],
] as const;

/** A fact read from the machine as you look at it; absent when the machine
 *  could not give it. */
function Measured({ fact, term }: { fact: SysFactName; term: string }) {
  return (
    <SysRow fact={fact} as="div" className={FACT_ROW}>
      {(value) => (
        <>
          <dt className={FACT_TERM}>{term}</dt>
          <dd className={`${FACT_VALUE} num`}>{value}</dd>
        </>
      )}
    </SysRow>
  );
}

export function AboutPage() {
  const info = useInfo();
  return (
    <LongPage>
      <PageHead title="Everything here runs on one small server" contents={CONTENTS}>
        {`No API key, no upstream provider, no GPU. ${info.model} at ${info.quant} on a ${info.hardware}, answering in the open with its own speed printed under every reply.`}
      </PageHead>

      <Section
        title="Server"
        first
        // Measured on the machine and repainted while the page is open. A row
        // with nothing measured behind it is left out.
        sub="Read from the machine as you look at it. The CPU and memory figures are the whole machine's, which this site shares with others."
      >
        <Facts>
          <Fact term="Params" figure>
            {info.params}
          </Fact>
          <Fact term="Context" figure>
            {info.n_ctx}
          </Fact>
          <Fact term="Threads for the model" figure>
            {`${info.n_threads} / ${info.cpu_count || "?"}`}
          </Fact>
          <Measured fact="cpus" term="CPUs this app can use" />
          <Measured fact="cpu" term="CPU load now" />
          <Measured fact="ram" term="Memory in use, whole machine" />
          <Measured fact="rss" term="Memory held by the app and its model" />
          <Measured fact="limit" term="Memory limit on the service" />
          <Measured fact="load" term="Load average, 1 / 5 / 15 min" />
          {info.load_ms ? (
            <Fact term="Load time" figure>
              {`${info.load_ms} ms`}
            </Fact>
          ) : null}
        </Facts>
      </Section>

      <Section title="The stack" id="stack">
        <Panel>
          <P>
            <b>llama.cpp</b>
            {info.runtime === "llama-server" ? (
              <>
                {" as a "}
                <Code>llama-server</Code>
                {
                  " holds the model in a service of its own beside the app — loaded once at startup, kept warm, and sent each pass over the machine's own loopback. "
                }
              </>
            ) : (
              <>
                {" via "}
                <Code>llama-cpp-python</Code>
                {" holds the model — one instance, loaded once at startup and kept warm. "}
              </>
            )}
            <b>FastAPI</b>
            {" on a single "}
            <Code>uvicorn</Code>
            {" worker serves the pages and streams tokens over Server-Sent Events. "}
            <b>SQLite</b>
            {
              " in WAL mode stores conversations and every recorded run. The front-end is React and TypeScript, built with Vite into static files that this server only has to send. It ships "
            }
            <b>five runtime packages and nothing from a CDN</b>
            {
              ": every script, style and font is served from this machine, and nothing tracks you. The markdown renderer is the site's own. The typeface, Recursive, is here under the SIL Open Font License."
            }
          </P>
          <P>
            Generation happens on a worker thread and tokens cross back to the event loop through a
            queue, so health checks, cancellation and other visitors' queue positions all keep
            responding while the model is busy.
          </P>
        </Panel>
      </Section>

      <Section title="One request at a time — on purpose" id="queue">
        <Panel>
          <P>
            {"Two generations running side by side finish "}
            <em>later</em>
            {
              " than the same two run back to back: they fight over the same memory bandwidth and the same "
            }
            <span className="num">{info.n_threads}</span>
            {
              " model threads. So this server takes one at a time and shows you your place in the line. Honest queueing beats fake concurrency."
            }
          </P>
          <P>
            {"The lock is released by the generation thread itself, in its own "}
            <Code>finally</Code>
            {
              ". Releasing it from the request handler would let a disconnecting client hand the slot to the next visitor while llama.cpp was still mid-token."
            }
          </P>
        </Panel>
      </Section>

      <Section title="Getting the prompt right matters more than the sampler" id="template">
        <Panel>
          <P>
            An instruct model fed raw text behaves like a base model: it rambles, it repeats, it
            never stops cleanly. The usual reaction is to clamp the sampler down to near-greedy and
            bolt on a loop that detects repeated sentences — which makes the repetition worse,
            because greedy decoding is exactly what produces loops.
          </P>
          <P>
            {"The fix is the chat template. Every turn goes through the "}
            <Code>{"<|im_start|>"}</Code>
            {" / "}
            <Code>{"<|im_end|>"}</Code>
            {
              " format the model was fine-tuned on, so it stops on its own token, and sampling can sit at a normal "
            }
            <Code>temperature 0.7</Code>
            {" with a light "}
            <Code>repeat_penalty</Code>
            {". No dedup hack required."}
          </P>
        </Panel>
      </Section>

      <Section
        title="Memory"
        sub="An estimate of what a loaded model takes, not a measurement. The measured figure is beneath it."
      >
        <Panel>
          <MemoryEstimate>
            <Link to="/bench#memory" viewTransition>
              How that compares with a larger model
            </Link>
          </MemoryEstimate>
        </Panel>
      </Section>

      <Section title="The Agent looks things up, within limits" id="agent">
        <Panel>
          <P>
            {
              "The Agent persona has tools: a calculator, a clock, a unit converter, text statistics, Wikipedia, the weather, exchange rates, a page reader and a notebook. A command such as "
            }
            <Code>/weather Lisbon</Code>
            {
              ", or a pattern it knows (a sum, a pasted link), picks the tool before the model is asked anything. Otherwise the model chooses, under a grammar that only lets it write a call the tool can take. It may look something up, read the result and look up something else: at most three lookups, within ninety seconds, never the same one twice."
            }
          </P>
          <P>
            {
              "What it reads from the web is material, never instructions. It only opens an address you wrote yourself, and keeps a note only when you ask; by default a note belongs to the conversation it was made in and goes when the conversation does. Its answer is held against what it looked up, and a figure no lookup returned is named in a line under the answer. Under each answer, How it ran lists every step with what the model read, what it wrote and how long it took."
            }
          </P>
        </Panel>
      </Section>

      <Section title="What it cannot do" id="limits">
        <Panel>
          <P>
            {"It is a "}
            <b>1.5-billion-parameter</b>
            {
              " model quantised to four bits. It will get arithmetic wrong, invent citations, lose the thread on long reasoning chains, and know nothing about anything recent. It is good at short explanations, quick drafts, summarising what you paste in, and simple code — and it answers in a couple of seconds without sending a byte off this machine."
            }
          </P>
          <P>
            {"The context window is "}
            <b className="num">{info.n_ctx}</b>
            {
              " tokens. Older turns are trimmed automatically when a conversation outgrows it; the system prompt is always kept."
            }
          </P>
          <P>
            The check on an answer looks at its figures, not its sentences: it can tell that a
            number came from nowhere, not that what is said around it is true.
          </P>
        </Panel>
      </Section>
    </LongPage>
  );
}
