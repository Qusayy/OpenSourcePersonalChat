"""The task suite itself: that it loads, scores what it claims to score, and
runs with no model and no network.

Needs the app's own requirements; without them the whole module is skipped.
A suite that passed every task whatever happened would be worse than none, so
most of this is about making it fail on purpose.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from app import agent
    from app.config import settings
    from app.tools import base
    from app.tools.base import call_tool
    from evals import fixtures
    from evals.runner import FAILED, PASSED, PENDING, SKIPPED, UNEXPECTED, compare, run_suite
    from evals.score import route_match, score
    from evals.tasks import Task, Turn, load_tasks
    from scripts import eval_agent
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

NONE = '{"tool": "none"}'


class TaskFileTests(unittest.TestCase):
    def test_every_task_in_the_file_loads(self) -> None:
        tasks = load_tasks()
        self.assertGreater(len(tasks), 30)
        self.assertEqual(len({t.id for t in tasks}), len(tasks))

    def test_every_recording_a_task_names_is_there(self) -> None:
        for task in load_tasks():
            for files in task.tools.values():
                for name in [files] if isinstance(files, str) else files:
                    with self.subTest(task=task.id, file=name):
                        self.assertEqual(fixtures.load(name)["result"].keys() >= {"ok"}, True)

    def test_every_recording_says_where_its_content_came_from(self) -> None:
        for path in sorted(fixtures.FIXTURES.glob("*.json")):
            with self.subTest(file=path.name):
                fixture = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn(fixture["tool"], base.REGISTRY)
                self.assertTrue(fixture["recorded"] or fixture["source"])

    def test_a_task_with_no_script_can_only_be_run_with_the_model(self) -> None:
        tasks = load_tasks()
        needs_model = [t for t in tasks if t.script is None]
        self.assertGreater(len(needs_model), 5)
        for task in needs_model:
            with self.subTest(task=task.id):
                self.assertFalse(task.runs_with("scripted"))
                self.assertTrue(task.runs_with("real"))

    def test_a_task_about_a_scripted_reply_is_not_run_with_the_model(self) -> None:
        scripted_only = [t for t in load_tasks() if t.scripted_only]
        self.assertTrue(scripted_only)
        for task in scripted_only:
            self.assertTrue(task.runs_with("scripted"))
            self.assertFalse(task.runs_with("real"))

    def test_a_line_that_is_not_a_task_stops_the_load_and_says_where(self) -> None:
        good = '{"id": "a", "category": "c", "turns": ["hi"]}'
        for lines, complaint in (
            ([good, "{not json"], "tasks.jsonl:2: not JSON"),
            ([good, '{"id": "b", "category": "c", "turns": ["hi"], "colour": 1}'],
             "tasks.jsonl:2: unknown field 'colour'"),
            ([good, '{"id": "b", "category": "c", "turns": ["hi"], "expect": {"rout": {}}}'],
             "tasks.jsonl:2: unknown expectation 'rout'"),
            (['{"category": "c", "turns": ["hi"]}'], "tasks.jsonl:1: 'id' is missing"),
            (['{"id": "a", "category": "c", "turns": []}'], "tasks.jsonl:1: 'turns' is missing"),
            ([good, "", good], "tasks.jsonl:3: the id 'a' is used twice"),
        ):
            with self.subTest(complaint=complaint), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "tasks.jsonl"
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    load_tasks(path)
                self.assertIn(complaint, str(caught.exception))


class FixtureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        support.no_network(self)

    async def test_a_named_tool_answers_from_its_file_every_time(self) -> None:
        with fixtures.recorded({"weather": "weather_lisbon.json"}) as seen:
            first = await call_tool("weather", {"location": "Lisbon"})
            second = await call_tool("weather", {"location": "anywhere"})
        self.assertIs(first.ok, True)
        self.assertTrue(first.summary.startswith("Lisbon, Portugal: Light rain"))
        self.assertEqual(first.card, "weather")
        self.assertEqual(second.summary, first.summary)
        self.assertEqual(seen["weather"], [{"location": "Lisbon"}, {"location": "anywhere"}])

    async def test_a_list_of_files_is_used_in_call_order(self) -> None:
        with fixtures.recorded({"wikipedia": ["wikipedia_python.json", "wikipedia_rust.json"]}):
            titles = [
                (await call_tool("wikipedia", {"query": q})).data["title"]
                for q in ("Python", "Rust", "again")
            ]
        self.assertEqual(titles[0], "Python (programming language)")
        self.assertEqual(titles[1], "Rust (programming language)")
        self.assertEqual(titles[2], titles[1])  # the last one goes on answering

    async def test_a_recorded_failure_is_a_failed_call(self) -> None:
        with fixtures.recorded({"weather": "weather_no_such_place.json"}):
            result = await call_tool("weather", {"location": "Atlantis"})
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, "no such place")

    async def test_a_tool_that_goes_out_with_no_recording_fails_and_reaches_nothing(self) -> None:
        with fixtures.recorded({}) as seen:
            result = await call_tool("wikipedia", {"query": "Ada Lovelace"})
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, "no recording")
        self.assertEqual(seen["wikipedia"], [{"query": "Ada Lovelace"}])

    async def test_a_tool_that_needs_no_network_runs_for_real(self) -> None:
        with fixtures.recorded({}):
            result = await call_tool("calculator", {"expression": "6*7"})
        self.assertEqual(result.summary, "6*7 = 42")

    async def test_the_real_dispatcher_is_still_what_calls_a_recorded_tool(self) -> None:
        with fixtures.recorded({"weather": "weather_lisbon.json"}) as seen:
            result = await call_tool("weather", {})
        self.assertEqual(result.error, "missing input")
        self.assertEqual(seen["weather"], [])

    async def test_recordings_stand_in_for_the_network_even_where_it_is_switched_off(self) -> None:
        support.with_settings(self, allow_outbound=False, tools_enabled=False)
        with fixtures.recorded({"weather": "weather_lisbon.json"}):
            self.assertIs((await call_tool("weather", {"location": "Lisbon"})).ok, True)
            self.assertIn("weather", [t.name for t in base.available()])
        self.assertIs(settings.allow_outbound, False)
        self.assertIs(settings.tools_enabled, False)

    async def test_the_tools_are_put_back_as_they_were(self) -> None:
        before = dict(base.REGISTRY)
        with fixtures.recorded({"weather": "weather_lisbon.json"}):
            self.assertIsNot(base.REGISTRY["weather"], before["weather"])
        self.assertEqual(base.REGISTRY, before)


def turn(*steps: agent.Step, outcome: str = "answered", prompt: list[dict] | None = None,
         answer: str = "") -> Turn:
    trace = agent.Trace(steps=list(steps), outcome=outcome, prompt=prompt, answer=answer)
    return Turn(user="", events=[], trace=trace)


def task(**expect) -> Task:
    return Task(id="t", category="c", turns=("hi",), expect=expect)


ROUTE = agent.Step("route", model=True, name="weather", via="model")
CALL = agent.Step("tool", name="weather", via="model", input={"location": "Lisbon"})
ANSWER = agent.Step("answer", model=True)


class ScoreTests(unittest.TestCase):
    def test_a_run_that_did_what_was_expected_has_no_failures(self) -> None:
        wanted = task(route={"tool": "weather", "via": "model", "args": {"location": "Lisbon"}},
                      calls=["weather"], max_steps=1, max_model_passes=2)
        self.assertEqual(score(wanted, [turn(ROUTE, CALL, ANSWER)]), [])

    def test_the_wrong_tool_is_a_failure_that_names_both(self) -> None:
        failures = score(task(route={"tool": "clock"}), [turn(ROUTE, CALL, ANSWER)])
        self.assertEqual(failures, ["route: chose 'weather', expected 'clock'"])

    def test_no_tool_where_one_was_expected_and_one_where_none_was(self) -> None:
        none = agent.Step("route", model=True, name=None, via="model")
        self.assertEqual(
            score(task(route={"tool": "weather"}), [turn(none, ANSWER)]),
            ["route: chose None, expected 'weather'"],
        )
        self.assertEqual(
            score(task(route={"tool": None}), [turn(ROUTE, CALL, ANSWER)]),
            ["route: chose 'weather', expected None"],
        )
        # A message that was never routed chose nothing, which is what "none" asks for.
        self.assertEqual(score(task(route={"tool": None}), [turn(ANSWER)]), [])

    def test_the_tier_that_decided_is_checked_when_the_task_names_one(self) -> None:
        failures = score(task(route={"via": "heuristic"}), [turn(ROUTE, CALL, ANSWER)])
        self.assertEqual(failures, ["route: decided by 'model', expected 'heuristic'"])

    def test_arguments_are_compared_without_regard_to_case_or_spacing(self) -> None:
        loose = agent.Step("tool", name="weather", input={"location": "  lisbon "})
        self.assertEqual(
            score(task(route={"tool": "weather", "args": {"location": "Lisbon"}}),
                  [turn(ROUTE, loose, ANSWER)]),
            [],
        )
        wrong = agent.Step("tool", name="weather", input={"location": "Porto"})
        self.assertEqual(
            score(task(route={"tool": "weather", "args": {"location": "Lisbon"}}),
                  [turn(ROUTE, wrong, ANSWER)]),
            ["route: location was 'Porto', expected 'Lisbon'"],
        )

    def test_numbers_are_compared_as_numbers(self) -> None:
        route = agent.Step("route", name="convert_units", via="slash")
        ran = agent.Step("tool", name="convert_units", input={"value": 10.0, "from_unit": "km"})
        self.assertEqual(
            score(task(route={"tool": "convert_units", "args": {"value": 10}}),
                  [turn(route, ran, ANSWER)]),
            [],
        )

    def test_only_the_arguments_a_task_names_are_compared(self) -> None:
        ran = agent.Step("tool", name="weather", input={"location": "Lisbon", "days": 5})
        self.assertEqual(
            score(task(route={"tool": "weather", "args": {"location": "Lisbon"}}),
                  [turn(ROUTE, ran, ANSWER)]),
            [],
        )

    def test_a_skills_first_call_is_what_it_chose(self) -> None:
        plan = agent.Step("route", name="compare", via="skill")
        first = agent.Step("tool", name="wikipedia", via="skill", input={"query": "Python"})
        second = agent.Step("tool", name="wikipedia", via="skill", input={"query": "Rust"})
        wanted = task(route={"tool": "wikipedia", "via": "skill", "args": {"query": "Python"}},
                      calls=["wikipedia", "wikipedia"])
        self.assertEqual(score(wanted, [turn(plan, first, second, ANSWER)]), [])

    def test_the_calls_made_must_be_the_calls_expected_in_order(self) -> None:
        self.assertEqual(
            score(task(calls=[]), [turn(ROUTE, CALL, ANSWER)]),
            ["calls: ['weather'], expected []"],
        )

    def test_more_calls_or_passes_than_allowed_is_a_failure(self) -> None:
        failures = score(task(max_steps=0, max_model_passes=1), [turn(ROUTE, CALL, ANSWER)])
        self.assertEqual(
            failures,
            ["steps: 1 tool calls, at most 0 expected",
             "passes: 2 through the model, at most 1 expected"],
        )

    def test_a_run_is_expected_to_end_in_an_answer_unless_the_task_says_otherwise(self) -> None:
        stopped = turn(ROUTE, outcome="stopped")
        self.assertEqual(score(task(), [stopped]), ["outcome: 'stopped', expected 'answered'"])
        self.assertEqual(score(task(outcome="stopped"), [stopped]), [])

    def test_the_prompt_is_read_whole_and_to_the_letter(self) -> None:
        read = [{"role": "system", "content": "Be brief."},
                {"role": "user", "content": "/weather Lisbon"},
                {"role": "assistant", "content": "Light rain."},
                {"role": "user", "content": "and tomorrow?"}]
        done = turn(ANSWER, prompt=read)
        self.assertEqual(score(task(prompt_contains=["Light rain", "tomorrow"]), [done]), [])
        self.assertEqual(
            score(task(prompt_contains=["light rain"], prompt_not_contains=["Be brief"]), [done]),
            ["prompt: does not contain 'light rain'", "prompt: contains 'Be brief'"],
        )

    def test_a_run_that_began_no_answer_cannot_meet_an_expectation_about_its_prompt(self) -> None:
        failures = score(task(prompt_contains=["anything"], outcome="stopped"),
                         [turn(ROUTE, outcome="stopped")])
        self.assertEqual(
            failures, ["prompt: no answer pass was made, so there is no prompt to read"]
        )

    def test_the_answer_is_read_whatever_its_case(self) -> None:
        said = turn(ANSWER, answer="It is raining in LISBON, and I cannot say more.")
        self.assertEqual(score(task(answer_contains=["lisbon"]), [said]), [])
        self.assertEqual(
            score(task(answer_contains=["Porto"], answer_not_contains=["i cannot"]), [said]),
            ["answer: does not contain 'Porto'", "answer: contains 'i cannot'"],
        )

    def test_the_skill_that_ran_is_the_last_the_model_chose_that_held(self) -> None:
        from evals.score import skill_of

        def ran(*steps) -> Turn:
            trace = agent.Trace()
            trace.steps = list(steps)
            return Turn(user="x", events=[], trace=trace)

        chose = agent.Step("route", model=True, name="compare", via="model")
        refused = agent.Step("route", model=True, name="compare", via="model", ok=False)
        tool = agent.Step("route", model=True, name="wikipedia", via="model")
        self.assertEqual(skill_of(ran(chose)), "compare")
        self.assertIsNone(skill_of(ran(refused)))
        self.assertIsNone(skill_of(ran(tool)))
        task = Task(id="t", category="c", turns=("x",), expect={"skill": None})
        self.assertEqual(score(task, [ran(chose)])[0], "skill: ran 'compare', expected None")
        self.assertNotIn("skill", " ".join(score(task, [ran(tool)])))

    def test_a_call_refused_before_it_was_made_is_not_a_call(self) -> None:
        trace = agent.Trace(outcome="answered", answer="Six.")
        trace.steps = [
            agent.Step("tool", name="read_url", via="model"),
            agent.Step("tool", name="read_url", via="model", ok=False, error="not allowed"),
            agent.Step("tool", name="weather", via="model", ok=False, error="no such place"),
        ]
        task = Task(id="t", category="c", turns=("x",), expect={"calls": ["read_url", "weather"]})
        self.assertEqual(score(task, [Turn(user="x", events=[], trace=trace)]), [])

    def test_the_last_message_of_a_task_is_the_one_that_is_scored(self) -> None:
        self.assertEqual(
            score(task(calls=[]), [turn(ROUTE, CALL, ANSWER), turn(ANSWER)]), []
        )

    def test_whether_the_tool_and_its_arguments_were_right_are_two_questions(self) -> None:
        right = turn(ROUTE, CALL, ANSWER)
        self.assertEqual(route_match(task(), right), (None, None))
        self.assertEqual(route_match(task(route={"tool": "weather"}), right), (True, None))
        self.assertEqual(route_match(task(route={"tool": "clock"}), right), (False, None))
        self.assertEqual(
            route_match(task(route={"tool": "weather", "args": {"location": "Porto"}}), right),
            (True, False),
        )


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        support.no_network(self)

    async def test_every_task_ends_as_it_is_marked(self) -> None:
        tasks = load_tasks()
        report = await run_suite(tasks, engine_kind="scripted")
        marked = {t.id: t for t in tasks}
        for row in report["tasks"]:
            with self.subTest(task=row["id"]):
                task_ = marked[row["id"]]
                if task_.script is None:
                    self.assertEqual(row["status"], SKIPPED)
                elif task_.pending:
                    self.assertEqual(row["status"], PENDING, "a known gap closed: unmark it")
                    self.assertTrue(row["failures"])
                else:
                    self.assertEqual(row["status"], PASSED, row["failures"])
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["unexpected_passes"], 0)
        self.assertEqual(report["summary"]["task_success"], 1.0)

    async def test_a_script_changed_to_break_one_task_fails_that_task_and_no_other(self) -> None:
        tasks = load_tasks()
        before = {r["id"]: r["status"] for r in (await run_suite(tasks, engine_kind="scripted"))["tasks"]}
        # The model, asked about a coat in Paris, now says no tool is needed.
        broken = [
            dataclasses.replace(t, script=(NONE, "No idea."))
            if t.id == "model-chooses-a-tool" else t
            for t in tasks
        ]
        report = await run_suite(broken, engine_kind="scripted")
        after = {r["id"]: r for r in report["tasks"]}
        self.assertEqual(after["model-chooses-a-tool"]["status"], FAILED)
        self.assertIn("route: chose None, expected 'weather'", after["model-chooses-a-tool"]["failures"])
        for task_id, status in before.items():
            if task_id != "model-chooses-a-tool":
                with self.subTest(task=task_id):
                    self.assertEqual(after[task_id]["status"], status)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertLess(report["summary"]["routing_accuracy"], 1.0)

    async def test_a_run_that_makes_a_pass_too_many_fails_its_task(self) -> None:
        # The script has nothing for a second pass, and says so when asked.
        short = Task(id="short", category="c", turns=("Suggest a name for my boat",),
                     script=(NONE,), expect={})
        report = await run_suite([short], engine_kind="scripted")
        self.assertEqual(report["tasks"][0]["status"], FAILED)
        self.assertEqual(report["tasks"][0]["failures"], ["outcome: 'failed', expected 'answered'"])

    async def test_a_known_gap_that_starts_passing_is_reported_and_is_not_a_pass(self) -> None:
        closed = Task(id="closed", category="c", turns=("/calc 2+2",),
                      script=("Four.",), expect={"calls": ["calculator"]},
                      pending="thought to be broken")
        report = await run_suite([closed], engine_kind="scripted")
        self.assertEqual(report["tasks"][0]["status"], UNEXPECTED)
        self.assertEqual(report["summary"]["unexpected_passes"], 1)
        self.assertEqual(report["summary"]["passed"], 0)

    async def test_the_report_has_every_figure_its_summary_names(self) -> None:
        report = await run_suite(load_tasks(), engine_kind="scripted")
        self.assertEqual(
            set(report),
            {"engine", "seed", "loop", "rules", "created", "model", "tokens_are_estimates", "summary",
             "by_category", "tasks"},
        )
        summary = report["summary"]
        self.assertEqual(
            set(summary),
            {"tasks", "ran", "passed", "failed", "pending", "unexpected_passes", "skipped",
             "task_success", "routing_accuracy", "skill_selection", "argument_match",
             "invalid_output_rate", "unsupported_rate", "correction_rate",
             "repair_rate", "second_step_rate", "limit_rate", "repeat_rate",
             "steps", "passes", "prompt_tokens", "output_tokens"},
        )
        self.assertEqual(summary["tasks"], summary["ran"] + summary["skipped"])
        self.assertEqual(
            summary["ran"],
            summary["passed"] + summary["failed"] + summary["pending"] + summary["unexpected_passes"],
        )
        for spread in ("steps", "passes", "prompt_tokens", "output_tokens"):
            self.assertEqual(set(summary[spread]), {"mean", "median", "p95", "max"})
        # The loop's own tasks: one made a second lookup, one hit a limit, one repeated itself.
        for rate in ("second_step_rate", "limit_rate", "repeat_rate"):
            self.assertGreater(summary[rate], 0)
        # The one reply in the suite that is cut off before it closes.
        self.assertGreater(summary["invalid_output_rate"], 0)
        # And the one call that was sent back.
        self.assertGreater(summary["repair_rate"], 0)
        self.assertEqual(sum(c["tasks"] for c in report["by_category"].values()), summary["tasks"])

    async def test_a_scripted_report_claims_no_timings_and_says_its_tokens_are_estimates(self) -> None:
        report = await run_suite(load_tasks(), engine_kind="scripted")
        self.assertIs(report["tokens_are_estimates"], True)
        self.assertIsNone(report["model"])
        self.assertNotIn("seconds", report["summary"])
        for row in report["tasks"]:
            self.assertNotIn("ms", row)

    def test_what_the_model_read_is_added_up_only_where_every_pass_reported_it(self) -> None:
        from evals.runner import Outcome, summarise

        def turn(*read: int | None) -> Turn:
            trace = agent.Trace()
            trace.steps = [agent.Step("route", model=True, new_tokens=n) for n in read]
            return Turn(user="x", events=[], trace=trace)

        task = Task(id="t", category="c", turns=("x",))
        every = Outcome(task, PASSED, [], [turn(5, 7), turn(3)])
        some = Outcome(task, PASSED, [], [turn(5, None)])
        self.assertEqual(every.row(False)["read_tokens"], 15)
        self.assertIsNone(some.row(False)["read_tokens"])
        self.assertIsNone(Outcome(task, PASSED, [], [turn()]).row(False)["read_tokens"])
        self.assertEqual(summarise([every, some], timed=True)["read_tokens"]["max"], 15)
        self.assertIsNone(summarise([some], timed=True)["read_tokens"])

    async def test_only_the_categories_asked_for_are_run(self) -> None:
        report = await run_suite(load_tasks(), engine_kind="scripted", categories=["skill-choice"])
        self.assertEqual({row["category"] for row in report["tasks"]}, {"skill-choice"})
        self.assertEqual(report["summary"]["tasks"], 6)

    async def test_the_seed_reaches_every_pass(self) -> None:
        from evals.runner import run_task
        from evals.scripted import ScriptedEngine

        engine = ScriptedEngine([NONE, "Wren."])
        self.addAsyncCleanup(engine.settled)
        await run_task(Task(id="s", category="c", turns=("Suggest a name for my boat",)),
                       engine=engine, seed=11)
        self.assertEqual([p.seed for p in engine.passes], [11, 11])

    async def test_a_later_message_is_read_with_the_earlier_exchange_before_it(self) -> None:
        from evals.runner import run_task
        from evals.scripted import ScriptedEngine

        engine = ScriptedEngine([NONE, "First answer.", NONE, "Second answer."])
        self.addAsyncCleanup(engine.settled)
        outcome = await run_task(Task(id="two", category="c", turns=("One?", "Two?")), engine=engine)
        prompt = outcome.last.prompt[1:]
        self.assertEqual([m["role"] for m in prompt],
                         ["user", "assistant", "user", "assistant", "user", "assistant", "user"])
        self.assertTrue(prompt[0]["content"].startswith("One?"))
        self.assertEqual(prompt[3]["content"], "First answer.")
        self.assertTrue(prompt[4]["content"].startswith("Two?"))

    async def test_a_real_run_needs_an_engine_and_a_known_kind(self) -> None:
        with self.assertRaises(ValueError):
            await run_suite([], engine_kind="real")
        with self.assertRaises(ValueError):
            await run_suite([], engine_kind="imaginary")

    def test_two_reports_are_set_side_by_side(self) -> None:
        was = {"engine": "real", "seed": 0, "created": "then",
               "summary": {"task_success": 0.8, "passed": 8, "seconds": {"total": {}}},
               "tasks": [{"id": "a", "status": PASSED}, {"id": "b", "status": FAILED},
                         {"id": "gone", "status": PASSED}]}
        now = {"summary": {"task_success": 0.9, "passed": 9, "seconds": {"total": {}}},
               "tasks": [{"id": "a", "status": PASSED}, {"id": "b", "status": PASSED},
                         {"id": "new", "status": FAILED}]}
        change = compare(now, was)
        self.assertEqual(change["previous"], {"engine": "real", "seed": 0, "created": "then"})
        self.assertEqual(change["figures"]["task_success"], {"was": 0.8, "now": 0.9, "change": 0.1})
        self.assertEqual(change["figures"]["passed"]["change"], 1)
        self.assertEqual(
            change["changed"],
            [{"id": "b", "was": FAILED, "now": PASSED}, {"id": "new", "was": "absent", "now": FAILED}],
        )
        self.assertEqual(change["removed"], ["gone"])


class CommandTests(unittest.TestCase):
    def run_command(self, *argv: str) -> tuple[int, str]:
        said = io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
            status = eval_agent.main(list(argv))
        return status, said.getvalue()

    def test_the_command_writes_the_report_it_printed_a_summary_of(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            status, said = self.run_command("--out", str(out))
            report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(report["engine"], "scripted")
        self.assertIn(f"{report['summary']['passed']} passed, 0 failed", said)
        self.assertIn("token counts are estimates", said)

    def test_the_command_fails_when_a_task_fails_and_says_which(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.jsonl"
            tasks.write_text(json.dumps({
                "id": "wrong", "category": "c", "turns": ["/calc 2+2"],
                "script": ["Four."], "expect": {"route": {"tool": "clock"}},
            }) + "\n", encoding="utf-8")
            status, said = self.run_command("--tasks", str(tasks), "--out", str(Path(tmp) / "r.json"))
        self.assertEqual(status, 1)
        self.assertIn("FAILED  wrong", said)
        self.assertIn("route: chose 'calculator', expected 'clock'", said)

    def test_a_second_run_can_be_set_beside_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a.json", Path(tmp) / "b.json"
            self.run_command("--out", str(first))
            status, said = self.run_command("--compare", str(first), "--out", str(second))
            report = json.loads(second.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(report["compared"]["changed"], [])
        self.assertIn("nothing changed", said)

    def test_timing_the_routing_pass_needs_the_real_model(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.run_command("--probe-routing")
        self.assertEqual(caught.exception.code, 2)

    def test_probing_the_cache_needs_the_real_model(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.run_command("--probe-cache")
        self.assertEqual(caught.exception.code, 2)


class CacheProbeTests(unittest.IsolatedAsyncioTestCase):
    """The cache probe, played against a script that reports what it read."""

    async def test_every_pass_of_every_turn_is_a_row_with_what_it_read(self) -> None:
        from evals.scripted import ScriptedEngine

        class Reporting(ScriptedEngine):
            def _iter_chunks(self, messages, sampling, *, reading=None, **more):
                yield from super()._iter_chunks(messages, sampling, **more)
                if reading is not None:
                    reading.read, reading.cached = 9, self.prompt_tokens(messages) - 9

        support.with_settings(self, tools_enabled=True, agent_loop=False)
        answers = ["One.", "Two.", "Both."]
        engine = Reporting([*answers, *(step for a in answers for step in (NONE, a))])
        self.addAsyncCleanup(engine.settled)
        rows = await eval_agent.probe_cache(engine, seed=0)

        self.assertEqual(
            [(r["tools"], r["turn"], r["pass"]) for r in rows],
            [("off", 1, "answer"), ("off", 2, "answer"), ("off", 3, "answer"),
             ("on", 1, "choice"), ("on", 1, "answer"), ("on", 2, "choice"),
             ("on", 2, "answer"), ("on", 3, "choice"), ("on", 3, "answer")],
        )
        for row in rows:
            self.assertEqual(row["read"], 9)
            self.assertEqual(row["server"], row["counted"])
        # Each turn is read with the turns before it.
        self.assertEqual(engine.passes[2].messages[-2]["content"], "Two.")
        self.assertNotIn("stream_chat", vars(engine), "the engine is left as it was found")

    async def test_a_server_that_reports_nothing_is_said_to_have_reported_nothing(self) -> None:
        from evals.scripted import ScriptedEngine

        support.with_settings(self, tools_enabled=True, agent_loop=False)
        engine = ScriptedEngine(["One.", "Two.", "Both.", NONE, "A", NONE, "B", NONE, "C"])
        self.addAsyncCleanup(engine.settled)
        rows = await eval_agent.probe_cache(engine, seed=0)
        self.assertTrue(all(r["read"] is None and r["server"] is None for r in rows))
        said = io.StringIO()
        eval_agent.show_probe(rows, file=said)
        self.assertIn("the server reported nothing of what it read", said.getvalue())


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    """The suite is part of the ordinary tests: no weights, no network."""

    async def test_the_scripted_suite_never_looks_a_host_up(self) -> None:
        support.no_network(self)
        report = await run_suite(load_tasks(), engine_kind="scripted")
        # A lookup would have failed the call it came from, and with it a task.
        self.assertEqual(report["summary"]["failed"], 0)

    async def test_nothing_in_the_suite_loads_llama_cpp(self) -> None:
        await run_suite(load_tasks(), engine_kind="scripted")
        self.assertNotIn("llama_cpp", sys.modules)


class LoopTaskTests(unittest.IsolatedAsyncioTestCase):
    """Tasks that are about what happens after a tool the model chose."""

    def setUp(self) -> None:
        support.no_network(self)

    async def test_a_task_that_asks_for_the_loop_gets_it_whatever_the_settings_say(self) -> None:
        support.with_settings(self, agent_loop=False)
        report = await run_suite(load_tasks(), engine_kind="scripted", categories=["two-step"])
        row = next(r for r in report["tasks"] if r["id"] == "loop-two-lookups-for-one-question")
        self.assertEqual((row["status"], row["steps"], row["passes"]), (PASSED, 2, 4))

    async def test_the_same_script_without_the_loop_makes_one_lookup_and_fails_the_task(self) -> None:
        task_ = next(t for t in load_tasks() if t.id == "loop-two-lookups-for-one-question")
        report = await run_suite(
            [dataclasses.replace(task_, loop=False)], engine_kind="scripted", loop=False
        )
        self.assertEqual(report["tasks"][0]["status"], FAILED)
        self.assertIn("calls: ['weather'], expected ['weather', 'weather']", report["tasks"][0]["failures"])

    async def test_the_loop_is_the_settings_unless_one_choice_is_asked_for(self) -> None:
        support.with_settings(self, agent_loop=True)
        looping = await run_suite(load_tasks(), engine_kind="scripted")
        single = await run_suite(load_tasks(), engine_kind="scripted", loop=False)
        self.assertEqual((looping["loop"], single["loop"]), (True, False))
        ran = {r["id"]: r for r in looping["tasks"]}
        once = {r["id"]: r for r in single["tasks"]}
        # One choice, its lookup and the answer: the choice after the lookup
        # is not made.
        self.assertEqual((ran["model-chooses-a-tool"]["passes"], once["model-chooses-a-tool"]["passes"]),
                         (3, 2))
        # A task that asks for the loop has it either way.
        self.assertEqual(once["loop-two-lookups-for-one-question"]["steps"], 2)

    async def test_a_page_with_an_instruction_in_it_gets_nothing_fetched_for_it(self) -> None:
        from evals.runner import run_task
        from evals.scripted import ScriptedEngine

        task_ = next(t for t in load_tasks() if t.id == "loop-page-cannot-send-the-reader-elsewhere")
        engine = ScriptedEngine(task_.script)
        self.addAsyncCleanup(engine.settled)
        outcome = await run_task(task_, engine=engine)
        self.assertEqual(outcome.status, PASSED, outcome.failures)
        fetched = [s for s in outcome.last.steps if s.kind == "tool"]
        self.assertEqual([(s.name, s.input) for s in fetched],
                         [("read_url", {"url": "https://example.org/tides"})])


if __name__ == "__main__":
    unittest.main()
