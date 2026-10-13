#!/usr/bin/env python3
"""Run the agent's tasks and report how it did.

    python scripts/eval_agent.py                       # the scripted engine: no model needed
    python scripts/eval_agent.py --engine real --out report.json
    python scripts/eval_agent.py --engine real --compare previous.json
    python scripts/eval_agent.py --engine real --probe-routing

With ``--engine scripted`` each task brings what the model says, so the run
measures the code around the model. It needs no weights and takes a second.

With ``--engine real`` the model is loaded exactly as the site loads it, and
every pass is given the seed. This is the run whose numbers mean something
about the agent. Stop the web service first: a second process would load a
second copy of the model and share the cores with the first.

Nothing is written to the database either way. Tool results come from
``evals/fixtures``, so no task touches the network.

Exit status is 1 if any task failed, or passed while marked as a known gap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.router import decision_grammar, decision_limit, decision_messages  # noqa: E402
from app.tools.base import available  # noqa: E402
from evals import fixtures  # noqa: E402
from evals.runner import BAD, compare, run_suite  # noqa: E402
from evals.tasks import TASKS, load_tasks  # noqa: E402


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
    """The same routing pass twice, back to back.

    The first reads its whole prompt. The second finds that prompt already in
    the model's cache, so the difference in time to the first token is what
    the cache is worth on this machine.
    """
    with fixtures.recorded({}):
        tools = available()
        messages = decision_messages("Do I need a coat in Paris today?", tools)
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


def show(report: dict, file=None) -> None:
    file = file or sys.stderr  # looked up when called: a caller may have redirected it
    s = report["summary"]

    def pct(value) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    print(f"\n{report['engine']} engine, seed {report['seed']}", file=file)
    print(
        f"  {s['passed']} passed, {s['failed']} failed, {s['pending']} known gaps, "
        f"{s['unexpected_passes']} passed that were marked as gaps, {s['skipped']} not run",
        file=file,
    )
    print(
        f"  task success {pct(s['task_success'])}   routing {pct(s['routing_accuracy'])}   "
        f"arguments {pct(s['argument_match'])}   invalid decisions {pct(s['invalid_output_rate'])}",
        file=file,
    )
    if s.get("seconds") and s["seconds"]["total"]:
        t = s["seconds"]["total"]
        print(f"  seconds per task: median {t['median']}, 95% within {t['p95']}", file=file)
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
        "--probe-routing", action="store_true",
        help="time one routing pass cold and warm instead of running the tasks (real engine only)",
    )
    args = parser.parse_args(argv)

    if args.probe_routing:
        if args.engine != "real":
            parser.error("--probe-routing times the model, so it needs --engine real")
        probe = asyncio.run(probe_routing(load_engine(), args.seed))
        print(json.dumps(probe, indent=2, ensure_ascii=False))
        return 0

    tasks = load_tasks(args.tasks)
    engine = load_engine() if args.engine == "real" else None
    report = asyncio.run(run_suite(
        tasks, engine_kind=args.engine, engine=engine, seed=args.seed, categories=args.category,
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
