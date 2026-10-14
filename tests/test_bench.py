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

    def test_this_services_limit_does_not_bound_a_model_in_another_service(self) -> None:
        out = bench.larger_model(BUDGET, system(total=15770, limit=3072), limited=False)
        self.assertEqual(out["room_mb"], 15770)
        self.assertFalse(out["by_limit"])
        self.assertIs(out["fits"], True)

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

    def test_weights_are_the_files_size_or_say_they_are_not(self) -> None:
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        with mock.patch.object(bench.engine, "cfg", SimpleNamespace(resolved_model_path=Path(__file__), model_quant="Q4_K_M")):
            mb, on_disk = bench.weights_mb()
            self.assertTrue(on_disk)
            self.assertEqual(mb, round(Path(__file__).stat().st_size / (1024 * 1024)))
        with mock.patch.object(bench.engine, "cfg", SimpleNamespace(resolved_model_path=Path(__file__).with_name("no-such.gguf"), model_quant="Q4_K_M")):
            self.assertEqual(bench.weights_mb(), (bench.DEFAULT_WEIGHTS_MB, False))
            first = bench.memory_budget(4096)[0]
            self.assertFalse(first["on_disk"])
            self.assertIn("no model file", first["note"])


if __name__ == "__main__":
    unittest.main()
