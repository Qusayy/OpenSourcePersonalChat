#!/usr/bin/env python3
"""Run the agent's tasks and report how it did.

    python scripts/eval_agent.py                       # the scripted engine: no model needed
    python scripts/eval_agent.py --engine real --out report.json
    python scripts/eval_agent.py --engine real --compare previous.json
    python scripts/eval_agent.py --engine real --probe-cache
    python scripts/eval_agent.py --engine real --rules evals/rules/default-none.txt
    python scripts/eval_agent.py --engine real --tasks evals/reasoning.jsonl \
        --category reasoning --thinking on --think-budget 2048 --rows

With ``--engine scripted`` each task brings what the model says, so the run
measures the code around the model. It needs no weights and takes a second.

With ``--engine real`` the model is loaded exactly as the site loads it, and
every request is given the seed. This is the run whose numbers mean something
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
import tempfile
from contextlib import aclosing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import agent  # noqa: E402
from app.agent.context import Turn  # noqa: E402
from app.agent.reply import CODE_END  # noqa: E402
from app.chatml import THINK_CLOSE  # noqa: E402
from app.config import settings  # noqa: E402
from app.store import db, projects  # noqa: E402
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


# A conversation of four turns, played once with the tools off and once with
# them on. The last asks for a file, so that with the tools on a reply goes on
# from where it stopped more than once: out of its thinking, into the file's
# content, and out of it again.
PROBE_TURNS = (
    "Give me one fact about octopuses, in a sentence.",
    "And one about their eyes?",
    "Which of those two surprised scientists more?",
    "Write that fact to a project file called facts.md, in one line.",
)
PROBE_RUNS = (("off", False), ("on", True))


def _phase(stop: list[str] | None) -> str:
    """Which part of a reply a request wrote, from the strings it stopped at."""
    stop = stop or []
    if stop == [THINK_CLOSE]:
        return "think"
    if CODE_END in stop:
        return "code"
    return "prose"


async def probe_cache(engine, seed: int | None) -> list[dict]:
    """A conversation of a few turns, and what each request of each reply read.

    Each request is a row: the prompt as this app counts it, as the server
    counted it (what it read and what it took from its cache, together), what
    it read, and the time to its first token. The first figure against the
    second is how far the app's prompt renderer is from the server's
    tokenizer. The third is whether the server's cache is used: a turn that
    extends the one before should read little more than the new message, and
    a request that goes on with a reply ("continued") little more than the
    tokens the last one stopped at. A continued request that reads the whole
    prompt means the server could not reuse its cache, which on a model with
    recurrent layers costs the whole conversation every time.
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
                        "pass": _phase(kwargs.get("stop")),
                        "continued": bool(kwargs.get("prefix")),
                        "counted": data.get("prompt_tokens"),
                        "server": None if read is None or cached is None else read + cached,
                        "read": read,
                        "cached": cached,
                        "wrote": data.get("completion_tokens"),
                        "ended": data.get("finish"),
                        "first_token_s": None if ttft is None else round(ttft / 1000, 2),
                    })
                yield event, data

    engine.stream_chat = recording
    try:
        with fixtures.recorded({}), tempfile.TemporaryDirectory() as scratch, \
                db.elsewhere(Path(scratch) / "probe.db"):
            for label, tools_on in PROBE_RUNS:
                history: list[dict] = []
                sent: list[Turn] = []
                scope = f"probe:{label}"
                for turn, text in enumerate(PROBE_TURNS, start=1):
                    current.update(tools=label, turn=turn)
                    trace = agent.Trace()
                    async for _ in agent.run(
                        text, history=list(history), trace=trace,
                        engine=engine, tools_enabled=tools_on, seed=seed, context=list(sent),
                        scope=scope, project=projects.manifest(scope),
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

    print(f"\n{'tools':<6} {'turn':>4} {'pass':<6} {'cont':<5} {'counted':>8} {'server':>7} "
          f"{'read':>6} {'cached':>7} {'wrote':>6} {'ended':<6} {'first token s':>14}", file=file)
    for r in rows:
        print(f"{r['tools']:<6} {r['turn']:>4} {r['pass']:<6} {'yes' if r['continued'] else '':<5} "
              f"{cell(r['counted']):>8} {cell(r['server']):>7} {cell(r['read']):>6} "
              f"{cell(r['cached']):>7} {cell(r['wrote']):>6} {cell(r['ended']):<6} "
              f"{cell(r['first_token_s']):>14}", file=file)
    if all(r["read"] is None for r in rows):
        print("\n  the server reported nothing of what it read", file=file)


def show(report: dict, file=None) -> None:
    file = file or sys.stderr  # looked up when called: a caller may have redirected it
    s = report["summary"]

    def pct(value) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    loop = "" if report.get("loop", True) else ", one call where a task does not ask for more"
    thinking = report.get("thinking") or {}
    thought = ""
    if thinking.get("mode") == "on":
        thought = f", thinking on (budget {thinking['budget']} a reply)"
    elif thinking.get("mode"):
        thought = f", thinking {thinking['mode']}"
    print(f"\n{report['engine']} engine, seed {report['seed']}{loop}{thought}", file=file)
    if report.get("rules"):
        print(f"  worded by {report['rules']['file']} ({report['rules']['sha256']})", file=file)
    print(
        f"  {s['passed']} passed, {s['failed']} failed, {s['pending']} known gaps, "
        f"{s['unexpected_passes']} passed that were marked as gaps, {s['skipped']} not run",
        file=file,
    )
    print(
        f"  task success {pct(s['task_success'])}   routing {pct(s['routing_accuracy'])}   "
        f"arguments {pct(s['argument_match'])}   "
        f"unreadable calls {pct(s.get('invalid_call_rate'))}",
        file=file,
    )
    if s.get("correction_rate") is not None:
        print(f"  corrections {pct(s['correction_rate'])} of checked answers "
              f"(a figure nothing returned: {pct(s['unsupported_rate'])})", file=file)
    if s.get("think_tokens"):
        t = s["think_tokens"]
        print(f"  thinking tokens per task: median {t['median']}, 95% within {t['p95']}; "
              f"cut at the budget in {pct(s['budget_hit_rate'])} of thoughts, "
              f"{pct(s['budget_task_rate'])} of tasks; ended inside a thought "
              f"{pct(s['think_eos_rate'])}", file=file)
        if s.get("success_when_cut") is not None:
            print(f"  success where a thought was cut {pct(s['success_when_cut'])}, "
                  f"where none was {pct(s['success_when_uncut'])}", file=file)
    if s.get("no_answer_rate"):
        print(f"  no answer written: {pct(s['no_answer_rate'])} of tasks", file=file)
    if s.get("seconds") and s["seconds"]["total"]:
        t = s["seconds"]["total"]
        print(f"  seconds per task: median {t['median']}, 95% within {t['p95']}", file=file)
    if s.get("write_tps"):
        print(f"  writing speed: median {s['write_tps']['median']} tokens a second", file=file)
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


def show_rows(report: dict, file=None) -> None:
    """One line a task: how it ended, its replies, its thinking, the tools it
    called and its time."""
    file = file or sys.stderr
    print(f"\n  {'task':<28} {'status':<8} {'passes':>6} {'think':>6} {'cut':>3} {'eos':>3} "
          f"{'seconds':>8}  called", file=file)
    for row in report["tasks"]:
        if row["status"] == "skipped":
            continue
        ms = (row.get("ms") or {}).get("total")
        seconds = "-" if ms is None else f"{ms / 1000:.1f}"
        print(f"  {row['id'][:28]:<28} {row['status'][:8]:<8} {row.get('passes', 0):>6} "
              f"{row.get('think_tokens', 0):>6} {row.get('think_cut', 0):>3} "
              f"{row.get('think_eos', 0):>3} {seconds:>8}  {','.join(row.get('called', [])) or '-'}",
              file=file)


def show_change(change: dict, file=None) -> None:
    file = file or sys.stderr
    print(f"\nagainst the report of {change['previous'].get('created')}", file=file)
    for key, f in change["figures"].items():
        # A spread is shown by its median, and the time by its p95 as well;
        # the rest is in the report.
        shown = not key.endswith((".mean", ".max", ".p95")) or key == "seconds.total.p95"
        if f["change"] and shown:
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
        help="let the model call one tool in every task that does not ask for the loop, "
             "whatever the setting says (scripts are written for the loop on)",
    )
    parser.add_argument(
        "--rules", type=Path,
        help="a wording file: other texts for the model to read, for this run only",
    )
    parser.add_argument(
        "--probe-cache", action="store_true",
        help="play a short conversation and show what each request read (real engine only)",
    )
    parser.add_argument(
        "--thinking", choices=("on", "off", "none"),
        help="how the model thinks for this run, whatever the settings say",
    )
    parser.add_argument(
        "--think-budget", type=int, metavar="N",
        help="the most tokens a reply may think for this run (with thinking on)",
    )
    parser.add_argument("--rows", action="store_true", help="show one line a task")
    args = parser.parse_args(argv)
    try:
        wording = load_wording(args.rules) if args.rules else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.think_budget is not None and args.think_budget < 1:
        parser.error("--think-budget must be at least 1")
    config = {}
    if args.thinking:
        config["thinking"] = args.thinking
    if args.think_budget is not None:
        config["think_budget"] = args.think_budget

    if args.probe_cache:
        if args.engine != "real":
            parser.error("--probe-cache reads what the model server reports, so it needs --engine real")
        with applied(wording):
            rows = asyncio.run(probe_cache(load_engine(), args.seed))
        show_probe(rows)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    tasks = load_tasks(args.tasks)
    engine = load_engine() if args.engine == "real" else None
    report = asyncio.run(run_suite(
        tasks, engine_kind=args.engine, engine=engine, seed=args.seed, categories=args.category,
        loop=False if args.single else None, wording=wording, config=config or None,
    ))
    report["task_file"] = args.tasks.as_posix()
    if args.compare:
        report["compared"] = compare(report, json.loads(args.compare.read_text(encoding="utf-8")))

    show(report)
    if args.rows:
        show_rows(report)
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
