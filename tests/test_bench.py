"""The memory estimate and the claim the Benchmark page makes about a larger model.

Needs the app's own requirements; without them the module is skipped.
"""

from __future__ import annotations

import unittest

try:
    from app import bench
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

BUDGET = [
    {"name": "Weights", "mb": 990},
    {"name": "KV cache", "mb": 112},
    {"name": "Compute buffers", "mb": 200},
    {"name": "Runtime", "mb": 120},
]
NEED = bench.LARGER_WEIGHTS_MB + 112 + 200 + 120


def system(total=None, limit=None, high=None) -> dict:
    return {"mem_total_mb": total, "limit_mb": limit, "limit_high_mb": high}


class LargerModelTests(unittest.TestCase):
    def test_does_not_fit_a_small_machine(self) -> None:
        out = bench.larger_model(BUDGET, system(total=3900))
        self.assertEqual(out["need_mb"], NEED)
        self.assertIs(out["fits"], False)
        self.assertEqual(out["room_mb"], 3900)
        self.assertFalse(out["by_limit"])

    def test_fits_a_large_machine_and_says_what_share(self) -> None:
        out = bench.larger_model(BUDGET, system(total=15770))
        self.assertIs(out["fits"], True)
        self.assertEqual(out["share"], round(100 * NEED / 15770))

    def test_a_tighter_service_limit_is_what_it_has_to_fit_in(self) -> None:
        out = bench.larger_model(BUDGET, system(total=15770, limit=3072))
        self.assertIs(out["fits"], False)
        self.assertEqual(out["room_mb"], 3072)
        self.assertTrue(out["by_limit"])

    def test_a_limit_above_the_machine_changes_nothing(self) -> None:
        out = bench.larger_model(BUDGET, system(total=3900, limit=8192, high=6144))
        self.assertEqual(out["room_mb"], 3900)
        self.assertFalse(out["by_limit"])

    def test_nothing_measured_means_no_claim(self) -> None:
        out = bench.larger_model(BUDGET, system())
        self.assertIsNone(out["fits"])
        self.assertIsNone(out["share"])
        self.assertIsNone(out["room_mb"])


class ReferenceTests(unittest.TestCase):
    def test_no_reference_row_claims_to_be_this_server(self) -> None:
        for row in bench.REFERENCE:
            self.assertNotIn("self", row)
            self.assertNotIn("this", row["hw"].lower())


class BudgetTests(unittest.TestCase):
    def test_kv_cache_follows_the_context_length(self) -> None:
        small = bench.memory_budget(2048)[1]["mb"]
        large = bench.memory_budget(4096)[1]["mb"]
        self.assertEqual(large, 2 * small)

    def test_weights_fall_back_when_there_is_no_file(self) -> None:
        # The repository ships no model file; where one is present its size is used.
        self.assertGreater(bench.weights_mb(), 0)


if __name__ == "__main__":
    unittest.main()
