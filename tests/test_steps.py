"""What is kept of each step of a run, for how long, and what forgets it.

Needs the app's own requirements (pydantic-settings); without them the whole
module is skipped. The database is a file that lasts for one test.
"""

from __future__ import annotations

import time
import unittest

try:
    from app import db
    from tests import support
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

DAY = 86400.0


class StepStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = support.temp_database(self)
        self.cid = db.create_conversation("agent", "Do I need a coat in Paris")

    def add(self, run: str = "run-1", idx: int = 0, kind: str = "answer", **more):
        return db.add_step(run, self.cid, idx, kind, **more)

    def test_the_steps_of_a_run_come_back_in_the_order_they_happened(self) -> None:
        self.add(idx=2, kind="answer")
        self.add(idx=0, kind="route")
        self.add(idx=1, kind="tool")
        self.assertEqual([r["kind"] for r in db.run_steps("run-1")], ["route", "tool", "answer"])
        self.assertEqual([r["idx"] for r in db.run_steps("run-1")], [0, 1, 2])

    def test_one_run_does_not_see_the_steps_of_another(self) -> None:
        self.add("run-1", kind="route")
        self.add("run-2", kind="answer")
        self.assertEqual([r["kind"] for r in db.run_steps("run-1")], ["route"])
        self.assertEqual(db.run_steps("no-such-run"), [])

    def test_every_figure_a_step_carries_is_kept(self) -> None:
        sid = self.add(
            kind="route", name="weather", via="model", input={"text": "Paris?"},
            output='{"tool": "weather", "args": {"location": "Paris"}}',
            prompt_tokens=255, output_tokens=18, queue_ms=120.5, first_token_ms=4100.0,
            total_ms=6200.25, ok=True, created_at=1_800_000_000.0,
        )
        (row,) = db.run_steps("run-1")
        self.assertEqual(row["id"], sid)
        self.assertEqual(row["conversation_id"], self.cid)
        self.assertEqual((row["name"], row["via"]), ("weather", "model"))
        self.assertEqual(row["input"], {"text": "Paris?"})
        self.assertEqual(row["output_text"], '{"tool": "weather", "args": {"location": "Paris"}}')
        self.assertEqual((row["prompt_tokens"], row["output_tokens"]), (255, 18))
        self.assertEqual((row["queue_ms"], row["first_token_ms"], row["total_ms"]),
                         (120.5, 4100.0, 6200.25))
        self.assertIs(row["ok"], True)
        self.assertIsNone(row["error"])
        self.assertIsNone(row["message_id"])
        self.assertEqual(row["created_at"], 1_800_000_000.0)

    def test_tokens_nobody_measured_are_stored_as_unknown_and_not_as_zero(self) -> None:
        self.add(kind="answer", prompt_tokens=300)
        self.assertIsNone(db.run_steps("run-1")[0]["new_tokens"])

    def test_what_a_step_was_given_survives_as_it_was(self) -> None:
        given = {"location": "São Paulo", "calls": [{"tool": "weather", "args": {"n": 1.5}}]}
        self.add(idx=0, kind="tool", input=given)
        self.add(idx=1, kind="answer")
        first, second = db.run_steps("run-1")
        self.assertEqual(first["input"], given)
        self.assertIsNone(second["input"])

    def test_a_step_that_failed_says_so_and_why(self) -> None:
        self.add(kind="tool", name="calculator", ok=False, error="division by zero")
        (row,) = db.run_steps("run-1")
        self.assertIs(row["ok"], False)
        self.assertEqual(row["error"], "division by zero")

    def test_attaching_ties_every_step_of_that_run_to_the_answer(self) -> None:
        self.add("run-1", 0, "route")
        self.add("run-1", 1, "answer")
        self.add("run-2", 0, "answer")
        mid = db.add_message(self.cid, "assistant", "Take a coat.")
        db.attach_steps(mid, "run-1")
        self.assertEqual({r["message_id"] for r in db.run_steps("run-1")}, {mid})
        self.assertEqual({r["message_id"] for r in db.run_steps("run-2")}, {None})

    def test_deleting_a_conversation_deletes_its_steps_and_nobody_elses(self) -> None:
        other = db.create_conversation("agent", "Another")
        self.add("run-1")
        db.add_step("run-2", other, 0, "answer")
        db.delete_conversation(self.cid)
        self.assertEqual(db.run_steps("run-1"), [])
        self.assertEqual(len(db.run_steps("run-2")), 1)

    def test_steps_older_than_the_retention_are_forgotten(self) -> None:
        self.add("old", created_at=time.time() - 31 * DAY)
        self.add("new", created_at=time.time() - 29 * DAY)
        db.prune_steps()
        self.assertEqual(db.run_steps("old"), [])
        self.assertEqual(len(db.run_steps("new")), 1)

    def test_the_retention_is_the_setting_when_there_is_one(self) -> None:
        self.settings.steps_keep_days = 2
        self.add("old", created_at=time.time() - 3 * DAY)
        self.add("new", created_at=time.time() - 1 * DAY)
        db.prune_steps()
        self.assertEqual(db.run_steps("old"), [])
        self.assertEqual(len(db.run_steps("new")), 1)

    def test_old_steps_are_cleared_out_when_the_server_starts(self) -> None:
        self.add("old", created_at=time.time() - 31 * DAY)
        db.close()
        db.init()
        self.assertEqual(db.run_steps("old"), [])

    def test_a_server_that_runs_for_days_still_clears_them_out(self) -> None:
        self.add("old", created_at=time.time() - 31 * DAY)
        self.assertEqual(len(db.run_steps("old")), 1)
        # As if the last clear-out were more than an hour ago.
        db._pruned_at = time.time() - db.PRUNE_EVERY - 1  # noqa: SLF001 - the clock is the thing under test
        self.add("new")
        self.assertEqual(db.run_steps("old"), [])
        self.assertEqual(len(db.run_steps("new")), 1)

    def test_a_retention_of_zero_stores_nothing_at_all(self) -> None:
        self.settings.steps_keep_days = 0
        self.assertIsNone(self.add())
        self.assertEqual(db.run_steps("run-1"), [])

    def test_a_step_written_by_a_server_without_a_model_is_marked(self) -> None:
        self.add("real")
        self.settings.mock = True
        self.add("mock")
        self.assertIs(db.run_steps("real")[0]["mock"], False)
        self.assertIs(db.run_steps("mock")[0]["mock"], True)


if __name__ == "__main__":
    unittest.main()
