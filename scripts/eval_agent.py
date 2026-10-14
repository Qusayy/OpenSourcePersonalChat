#!/usr/bin/env python3
"""Run the agent's tasks and report how it did.

    python scripts/eval_agent.py                       # the scripted engine: no model needed
    python scripts/eval_agent.py --engine real --out report.json
    python scripts/eval_agent.py --engine real --compare previous.json
    python scripts/eval_agent.py --engine real --probe-routing
    python scripts/eval_agent.py --engine real --probe-cache
    python scripts/eval_agent.py --engine real --rules evals/rules/default-none.txt

With ``--engine scripted`` each task brings what the model says, so the run
measures the code around the model. It needs no weights and takes a second.

With ``--engine real`` the model is loaded exactly as the site loads it, and
every pass is given the seed. This is the run whose numbers mean something
about the agent. Stop the web service first: a second process would load a
second copy of the model and share the cores with the first. With the model in
a llama-server (AURORA_LLM_URL), stop the app and leave the server running: the
run uses that server, and two apps sharing its one slot would each keep a queue
the other cannot see.

Nothing is written to the database either way. Tool results come from
``evals/fixtures``, so no task touches the network.

Exit status is 1 if any task failed, or passed while marked as a known gap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import aclosing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.context import Turn  # noqa: E402
from app.personas import get_persona  # noqa: E402
from app.router import decision_grammar, decision_limit  # noqa: E402
from app.tools.base import available  # noqa: E402
from app.transcript import Transcript, system_for  # noqa: E402
from evals import fixtures  # noqa: E402
from evals.runner import BAD, compare, run_suite  # noqa: E402
from evals.tasks import TASKS, load_tasks  # noqa: E402
from evals.wording import applied, load as load_wording  # noqa: E402


def load_engine():
    """The site's own engine, loaded here. Returns it, or exits saying why not."""
    from app.llm import engine

    if settings.mock:
        raise SystemExit("AURORA_MOCK is set: there is no model to evaluate.")
    print(f"loading {settings.model_label} …", file=sys.stderr, flush=True)
    engine.load_blocking()
    if not engine.ready:
        raise SystemExit(engine.state.error or "the model did not load")
    return engine


async def probe_routing(engine, seed: int | None) -> dict:
    """The same first choice twice, back to back.

    The first reads its whole prompt. The second finds that prompt already in
    the model's cache, so the difference in time to the first token is what
    the cache is worth on this machine.
    """
    with fixtures.recorded({}):
        tools = available()
        system = system_for(get_persona("agent"), tools)
        messages = Transcript(system, [], "Do I need a coat in Paris today?").messages()
        grammar, limit = decision_grammar(tools), decision_limit(tools)
    out = {}
    for name in ("cold", "warm"):
        said = await engine.complete_json(messages, grammar, limit, seed=seed)
        out[name] = {
            "prompt_tokens": said.prompt_tokens,
            "output_tokens": said.completion_tokens,
            "first_token_s": None if said.ttft_ms is None else round(said.ttft_ms / 1000, 2),
            "total_s": None if said.total_ms is None else round(said.total_ms / 1000, 2),
            "said": said.raw,
            "error": said.error,
        }
    return out


# A conversation of three turns that no pattern answers, so every turn is the
# model's, played once with a persona that never looks anything up and once
# with the one that can.
PROBE_TURNS = (
    "Give me one fact about octopuses, in a sentence.",
    "And one about their eyes?",
    "Which of those two surprised scientists more?",
)
PROBE_PERSONAS = ("explainer", "agent")


async def probe_cache(engine, seed: int | None) -> list[dict]:
    """Three turns of one conversation, and what each pass of each turn read.

    Each pass is a row: the prompt as this app counts it, as the server
    counted it (what it read and what it took from its cache, together), what
    it read, and the time to its first token. The first figure against the
    second is how far the app's prompt renderer is from the model's own
    template. The third, on the second and third turns, is whether the
    server's cache is used at all: a turn that extends the one before should
    read little more than the new message.
    """
    rows: list[dict] = []
    current: dict = {}
    original = engine.stream_chat

    async def recording(messages, sampling, *args, **kwargs):
        async with aclosing(original(messages, sampling, *args, **kwargs)) as stream:
            async for event, data in stream:
                if event == "done":
                    read, cached = data.get("read_tokens"), data.get("cached_tokens")
                    ttft = data.get("ttft_ms")
                    rows.append({
                        **current,
                        "pass": "choice" if kwargs.get("grammar") else "answer",
                        "counted": data.get("prompt_tokens"),
                        "server": None if read is None or cached is None else read + cached,
                        "read": read,
                        "cached": cached,
                        "first_token_s": None if ttft is None else round(ttft / 1000, 2),
                    })
                yield event, data

    engine.stream_chat = recording
    try:
        with fixtures.recorded({}):
            for persona in PROBE_PERSONAS:
                history: list[dict] = []
                sent: list[Turn] = []
                for turn, text in enumerate(PROBE_TURNS, start=1):
                    current.update(persona=persona, turn=turn)
                    trace = agent.Trace()
                    async for _ in agent.run(
                        text, persona=get_persona(persona), history=list(history), trace=trace,
                        engine=engine, tools_enabled=True, seed=seed, context=list(sent),
                    ):
                        pass
                    # Each turn is read with the ones before it, as the site
                    # reads a conversation: as it was sent.
                    history.append({"role": "user", "content": text})
                    if trace.answer and trace.answer.strip():
                        history.append({"role": "assistant", "content": trace.answer})
                        sent.append(trace.turn(text))
                    else:
                        sent.append(Turn(text, ""))
    finally:
        del engine.stream_chat
    return rows


def show_probe(rows: list[dict], file=None) -> None:
    file = file or sys.stderr

    def cell(value) -> str:
        return "-" if value is None else str(value)

    print(f"\n{'persona':<10} {'turn':>4} {'pass':<7} {'counted':>8} {'server':>7} "
          f"{'read':>6} {'cached':>7} {'first token s':>14}", file=file)
    for r in rows:
        print(f"{r['persona']:<10} {r['turn']:>4} {r['pass']:<7} {cell(r['counted']):>8} "
              f"{cell(r['server']):>7} {cell(r['read']):>6} {cell(r['cached']):>7} "
              f"{cell(r['first_token_s']):>14}", file=file)
    if all(r["read"] is None for r in rows):
        print("\n  the server reported nothing of what it read", file=file)


def show(report: dict, file=None) -> None:
    file = file or sys.stderr  # looked up when called: a caller may have redirected it
    s = report["summary"]

    def pct(value) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    loop = "" if report.get("loop", True) else ", one choice where a task does not ask for more"
    print(f"\n{report['engine']} engine, seed {report['seed']}{loop}", file=file)
    if report.get("rules"):
        print(f"  worded by {report['rules']['file']} ({report['rules']['sha256']})", file=file)
    print(
        f"  {s['passed']} passed, {s['failed']} failed, {s['pending']} known gaps, "
        f"{s['unexpected_passes']} passed that were marked as gaps, {s['skipped']} not run",
        file=file,
    )
    print(
        f"  task success {pct(s['task_success'])}   routing {pct(s['routing_accuracy'])}   "
        f"skills {pct(s.get('skill_selection'))}   arguments {pct(s['argument_match'])}   "
        f"invalid decisions {pct(s['invalid_output_rate'])}",
        file=file,
    )
    if s.get("seconds") and s["seconds"]["total"]:
        t = s["seconds"]["total"]
        print(f"  seconds per task: median {t['median']}, 95% within {t['p95']}", file=file)
    if s.get("read_tokens"):
        r = s["read_tokens"]
        print(f"  prompt tokens the model read per task: median {r['median']}, most {r['max']}", file=file)
    if report["tokens_are_estimates"]:
        print("  token counts are estimates (four characters a token); no timings", file=file)
    for row in report["tasks"]:
        if row["status"] in BAD:
            print(f"\n  {row['status'].upper()}  {row['id']}", file=file)
            for failure in row["failures"]:
                print(f"      {failure}", file=file)
            if row["status"] == "unexpected pass":
                print(f"      it is marked as a known gap: {row.get('pending')}", file=file)


def show_change(change: dict, file=None) -> None:
    file = file or sys.stderr
    print(f"\nagainst the report of {change['previous'].get('created')}", file=file)
    for key, f in change["figures"].items():
        if f["change"]:
            print(f"  {key}: {f['was']} -> {f['now']}", file=file)
    for row in change["changed"]:
        print(f"  {row['id']}: {row['was']} -> {row['now']}", file=file)
    if not change["changed"] and not any(f["change"] for f in change["figures"].values()):
        print("  nothing changed", file=file)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent's tasks and report how it did.")
    parser.add_argument("--engine", choices=("scripted", "real"), default="scripted")
    parser.add_argument("--category", action="append", help="only this category; may be repeated")
    parser.add_argument("--seed", type=int, default=0, help="given to every pass (default 0)")
    parser.add_argument("--tasks", type=Path, default=TASKS, help="the task file")
    parser.add_argument("--out", type=Path, help="write the report here as JSON")
    parser.add_argument("--compare", type=Path, help="an earlier report to set this one beside")
    parser.add_argument(
        "--single", action="store_true",
        help="let the model choose once in every task that does not ask for the loop, "
             "whatever the setting says (scripts are written for the loop on)",
    )
    parser.add_argument(
        "--probe-routing", action="store_true",
        help="time one routing pass cold and warm instead of running the tasks (real engine only)",
    )
    parser.add_argument(
        "--rules", type=Path,
        help="a wording file: other texts for the model to choose by, for this run only",
    )
    parser.add_argument(
        "--probe-cache", action="store_true",
        help="play a three-turn conversation and show what each pass read (real engine only)",
    )
    args = parser.parse_args(argv)
    try:
        wording = load_wording(args.rules) if args.rules else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if args.probe_cache:
        if args.engine != "real":
            parser.error("--probe-cache reads what the model server reports, so it needs --engine real")
        with applied(wording):
            rows = asyncio.run(probe_cache(load_engine(), args.seed))
        show_probe(rows)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if args.probe_routing:
        if args.engine != "real":
            parser.error("--probe-routing times the model, so it needs --engine real")
        with applied(wording):
            probe = asyncio.run(probe_routing(load_engine(), args.seed))
        print(json.dumps(probe, indent=2, ensure_ascii=False))
        return 0

    tasks = load_tasks(args.tasks)
    engine = load_engine() if args.engine == "real" else None
    report = asyncio.run(run_suite(
        tasks, engine_kind=args.engine, engine=engine, seed=args.seed, categories=args.category,
        loop=False if args.single else None, wording=wording,
    ))
    if args.compare:
        report["compared"] = compare(report, json.loads(args.compare.read_text(encoding="utf-8")))

    show(report)
    if args.compare:
        show_change(report["compared"])
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"\nreport written to {args.out}", file=sys.stderr)
    else:
        print(text)

    s = report["summary"]
    return 1 if s["failed"] or s["unexpected_passes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
