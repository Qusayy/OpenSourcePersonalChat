"""What a chat run leaves behind: its tool calls, its steps, its answer."""

from __future__ import annotations

from .. import agent
from ..store import conversations, runs


class Keeper:
    """Stores what a run has done, before the page is told it happened.

    The run records each thing it finishes and then says so. ``keep`` is called
    as each event arrives and before it is sent on, so a tool call is in the
    database before its step closes, and the answer before ``done``, which the
    page answers by asking for the conversation list.
    """

    def __init__(
        self, cid: str, trace: agent.Trace, question_id: str, *, label: str | None = None
    ) -> None:
        self.cid = cid
        # What the answer's figures are filed under: the style it was written in.
        self.label = label
        self.trace = trace
        self.question_id = question_id
        self.call_ids: list[str] = []
        self.steps_kept = 0
        self.answered = False

    def keep(self) -> None:
        for call, result in self.trace.calls[len(self.call_ids):]:
            self.call_ids.append(conversations.add_tool_call(
                self.cid, call.name, via=call.via, args=call.args,
                card=result.card if result.ok else None,
                data=result.data, summary=result.summary or result.error or "",
                ms=result.ms, ok=result.ok,
            ))

        for step in self.trace.steps[self.steps_kept:]:
            runs.add_step(
                self.trace.run_id, self.cid, self.steps_kept, step.kind,
                name=step.name, via=step.via, input=step.input, output=step.output,
                prompt_tokens=step.prompt_tokens, new_tokens=step.new_tokens,
                output_tokens=step.output_tokens, queue_ms=step.queue_ms,
                first_token_ms=step.first_token_ms, total_ms=step.total_ms,
                ok=step.ok, error=step.error, created_at=step.at,
            )
            self.steps_kept += 1

        if self.trace.answer is None or self.answered:
            return
        self.answered = True
        # An answer with nothing in it is not a message. Its tool calls stay
        # with the conversation and are replayed from there.
        if not self.trace.answer.strip():
            return
        usage = self.trace.usage
        mid = conversations.add_message(
            self.cid, "assistant", self.trace.answer,
            ttft_ms=usage.get("ttft_ms"),
            gen_tps=usage.get("gen_tps"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            correction=self.trace.correction,
        )
        conversations.attach_tool_calls(mid, self.call_ids)
        runs.attach_steps(mid, self.trace.run_id)
        conversations.touch(self.cid)
        # What the answer was sent, for the next message to be read with.
        sent = self.trace.turn("").parts
        if sent and self.trace.system_key:
            conversations.add_context_turn(
                self.cid, self.trace.run_id, question_id=self.question_id, answer_id=mid,
                system_key=self.trace.system_key, parts=[p.as_dict() for p in sent],
            )
        if usage.get("asked"):
            # A question put back to the visitor was written by a routing
            # pass. It is a message, and it is not a sample of how fast this
            # machine writes answers.
            return
        runs.add_run(
            "chat",
            label=self.label,
            prompt_tokens=usage.get("prompt_tokens"),
            gen_tokens=usage.get("completion_tokens"),
            ttft_ms=usage.get("ttft_ms"),
            prefill_tps=usage.get("prefill_tps"),
            gen_tps=usage.get("gen_tps"),
            rss_mb=usage.get("rss_mb"),
        )
