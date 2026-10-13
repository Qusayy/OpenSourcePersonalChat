import { useEffect, useRef, useState } from "react";
import { CHAT_URL } from "@/lib/api";
import { aurora } from "@/lib/aurora";
import { fmt, fmtInt } from "@/lib/format";
import { Meter } from "@/lib/meter";
import { type ProseRenderer, proseRenderer } from "@/lib/prose-stream";
import { sseFetch } from "@/lib/sse";
import type { ChatEvents } from "@/lib/types";
import { Button } from "@/shell/Button";
import { livePhase, resetFigures, setContext, showFigures, traceSample } from "@/shell/figures";
import { Main } from "@/shell/Main";
import { useInfo } from "@/state/queries";
import { announce, holdPhase } from "@/state/shell";

/* Placeholder for the conversation page, which is built in its own stream.

   It is not the design: one field, one answer. What it does is real, and is
   the proof that the pieces under it work together inside React: the stream
   reader, the meter, the streaming renderer writing into a node React leaves
   alone, the status line's figures, and the aurora. The real page replaces
   this file and keeps the same calls. */

const meter = new Meter({
  onSample: ({ rate, total, at }) => {
    if (total) traceSample(rate, at);
  },
});

export function ChatPage() {
  const info = useInfo();
  const [streaming, setStreaming] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const answer = useRef<HTMLDivElement>(null);
  const prose = useRef<ProseRenderer | null>(null);

  useEffect(() => {
    input.current?.focus();
    return () => prose.current?.dispose();
  }, []);

  // Started from the handler, never from an effect: a development build runs
  // effects twice, and a second POST would store the question twice.
  async function send() {
    const text = input.current?.value.trim();
    const host = answer.current;
    if (!text || !host || streaming) return;
    if (input.current) input.current.value = "";

    prose.current?.dispose();
    host.replaceChildren();
    const out = proseRenderer(host);
    prose.current = out;

    let raw = "";
    let dirty = false;
    let caretOn = true;
    let finishedUnseen = false;
    const painter = setInterval(() => {
      if (!dirty || document.hidden) return;
      dirty = false;
      out.update(raw, caretOn);
    }, 100);

    setStreaming(true);
    livePhase("working");
    meter.start();
    resetFigures();

    try {
      await sseFetch<ChatEvents>(CHAT_URL, {
        body: { message: text, conversation_id: null, persona: null, skill: null },
        onEvent: (event, data) => {
          if (event === "queue") {
            livePhase("queued", data.position > 0 ? `${data.position} ahead` : "next in line");
          } else if (event === "start") {
            livePhase("reading");
            setContext(data.prompt_tokens);
          } else if (event === "token") {
            if (!raw) {
              livePhase("writing");
              showFigures(data.ttft_ms);
            }
            raw += data.t;
            dirty = true;
            meter.token(1);
            aurora.pulse();
          } else if (event === "done") {
            caretOn = false;
            dirty = false;
            out.finish(raw);
            setContext((data.prompt_tokens || 0) + (data.completion_tokens || 0));
            finishedUnseen = document.hidden;
            announce(
              `Answer complete — ${fmtInt(data.completion_tokens)} tokens at ${fmt(data.gen_tps, 1)} per second.`,
            );
          } else if (event === "error") {
            caretOn = false;
            out.finish(raw || data.message);
          }
        },
      });
    } catch (err) {
      out.finish(raw || (err instanceof Error ? err.message : "The answer stopped."));
    } finally {
      clearInterval(painter);
      if (raw) out.finish(raw);
      meter.stop();
      livePhase(null);
      resetFigures();
      if (finishedUnseen) holdPhase("answer ready");
      setStreaming(false);
      input.current?.focus();
    }
  }

  return (
    <Main data-view="thread" className="overflow-y-auto">
      <div className="mx-auto w-full max-w-col px-edge py-6 flex flex-col gap-4">
        <div className="flex items-end gap-3 border-b-2 border-ink pb-2">
          <textarea
            id="input"
            ref={input}
            rows={1}
            aria-label="Message"
            placeholder={info.status === "ready" ? "Ask anything" : "The model is not ready"}
            className="flex-1 resize-none bg-transparent outline-none text-3"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <Button
            variant="primary"
            id="send"
            disabled={info.status !== "ready" || streaming}
            onClick={() => void send()}
          >
            Send
          </Button>
        </div>
        {/* React renders this element empty and never looks inside it: the
            streaming renderer owns its children. */}
        <div className="prose" id="answer" dir="auto" ref={answer} />
      </div>
    </Main>
  );
}
