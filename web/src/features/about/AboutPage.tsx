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
  ["limits", "What it's good for"],
  ["agent", "When it looks things up"],
  ["stack", "The stack"],
  ["queue", "One answer at a time"],
  ["template", "The chat template"],
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
      <PageHead title="What answers you, and how" contents={CONTENTS}>
        {`${info.model} at ${info.quant} on a ${info.hardware}, with no GPU. Every word of an answer is written here; only a lookup (Wikipedia, the weather, a rate, a page you paste) goes out to the web.`}
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

      <Section title="What it's good for, and what it isn't" id="limits">
        <Panel>
          <P>
            {"It is a "}
            <b>{`${info.params}-parameter`}</b>
            {` model at ${info.quant}. Use it for short explanations, quick drafts, summarising something you paste in, and simple code. On its own it gets arithmetic, citations and long chains of reasoning wrong, and knows nothing recent, so for sums, rates, the weather, the news and anything current it looks the answer up instead of guessing. How long each answer took is printed under it.`}
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

      <Section title="When it looks things up" id="agent">
        <Panel>
          <P>
            {
              "There is nothing to switch on. Every message can use the tools: a calculator, a clock, a unit converter, text statistics, Wikipedia, the weather, exchange rates, a web search, the headlines, a page reader and a notebook. A command such as "
            }
            <Code>/weather Lisbon</Code>
            {
              ", or a pattern it knows (a sum, a pasted link), picks the tool before the model is asked anything, and a greeting is answered without asking it to choose. Otherwise the model chooses, under a grammar that only lets it write a call the tool can take. It may look something up, read the result and look up something else: at most three lookups, within ninety seconds, never the same one twice. How the answer is written, code first, a list, a few sentences or an explanation, is read from the message itself."
            }
          </P>
          <P>
            {
              "What it reads from the web is material, never instructions. It only opens an address you wrote yourself, and keeps a note only when you ask; by default a note belongs to the conversation it was made in and goes when the conversation does. Its answer is held against what it looked up, and a figure no lookup returned is named in a line under the answer. Under each answer, How it ran lists every step with what the model read, what it wrote and how long it took."
            }
          </P>
        </Panel>
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

      <Section title="One answer at a time" id="queue">
        <Panel>
          <P>
            {"Two answers written side by side finish "}
            <em>later</em>
            {
              " than the same two written back to back: they share the same memory bandwidth and the same "
            }
            <span className="num">{info.n_threads}</span>
            {
              " model threads. So the server writes one at a time, and if anything is ahead of you, the status line shows your place."
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

      <Section title="The chat template" id="template">
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
              " format the model was fine-tuned on, so it stops on its own token, and sampling can sit at each kind of answer's own temperature ("
            }
            <Code>0.6</Code>
            {" for an explanation) with a light "}
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
    </LongPage>
  );
}
