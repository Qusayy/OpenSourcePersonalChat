"""A run's ledger, from its steps or from the rows kept of them. Pure."""

from __future__ import annotations

import unittest

from app.ledger import is_pass, ledger


def row(kind, via=None, total=0.0, queue=None, first=None, **more) -> dict:
    return {"kind": kind, "via": via, "total_ms": total, "queue_ms": queue,
            "first_token_ms": first, "ok": True, "error": None, "name": None,
            "prompt_tokens": None, "new_tokens": None, "output_tokens": None, **more}


class LedgerTests(unittest.TestCase):
    def test_a_pass_is_a_choice_the_model_made_or_the_answer(self) -> None:
        self.assertTrue(is_pass(row("route", "model")))
        self.assertTrue(is_pass(row("fill", "model")))
        self.assertTrue(is_pass(row("answer")))
        self.assertFalse(is_pass(row("route", "skill")))
        self.assertFalse(is_pass(row("route", "heuristic")))
        # A call the model chose is a tool's time, not the model's.
        self.assertFalse(is_pass(row("tool", "model")))

    def test_the_four_parts_add_up_to_the_total_however_they_were_rounded(self) -> None:
        steps = [
            row("route", "model", total=310.04, queue=0.03, first=180.06),
            row("tool", "model", total=880.06),
            row("decide", "model", total=400.04, first=210.05),
            row("answer", total=2900.05, queue=0.02, first=420.04),
        ]
        out = ledger(steps)
        self.assertEqual((out["steps"], out["passes"]), (4, 3))
        self.assertEqual(
            round(out["waiting_ms"] + out["reading_ms"] + out["writing_ms"] + out["tools_ms"], 1),
            out["total_ms"],
        )
        self.assertEqual(out["tools_ms"], 880.1)

    def test_each_step_is_listed_as_the_page_reads_it(self) -> None:
        out = ledger([row("route", "model", total=12.345, name="weather", prompt_tokens=400,
                          new_tokens=18, output_tokens=12)])
        self.assertEqual(out["ledger"], [{
            "kind": "route", "name": "weather", "via": "model", "ok": True, "error": None,
            "tokens_in": 400, "tokens_read": 18, "tokens_out": 12, "ms": 12.3,
        }])

    def test_nothing_done_comes_to_nothing(self) -> None:
        out = ledger([])
        self.assertEqual((out["steps"], out["total_ms"], out["ledger"]), (0, 0.0, []))


if __name__ == "__main__":
    unittest.main()
