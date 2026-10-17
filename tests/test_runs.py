"""What is kept in the `runs` table, and what is not.

Needs the app's own requirements (pydantic-settings), unlike the telemetry
tests; without them the whole module is skipped.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from app.store import db
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")


class RunStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = SimpleNamespace(mock=False, resolved_db_path=Path(self.dir.name) / "runs.db")
        patcher = mock.patch.object(db, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init()
        self.addCleanup(db.close)

    def add(self, kind: str = "bench") -> None:
        db.add_run(kind, label="short", prompt_tokens=32, gen_tokens=128, ttft_ms=85.0, prefill_tps=780.0, gen_tps=11.7, rss_mb=57.0)

    def test_a_real_run_is_stored(self) -> None:
        self.add("bench")
        self.add("chat")
        self.assertEqual(len(db.recent_runs("bench")), 1)
        self.assertEqual(len(db.recent_runs("chat")), 1)
        self.assertEqual(db.recent_runs("bench")[0]["gen_tps"], 11.7)

    def test_mock_mode_stores_neither_benchmark_nor_chat_runs(self) -> None:
        self.settings.mock = True
        self.add("bench")
        self.add("chat")
        self.assertEqual(db.recent_runs(), [])

    def test_mock_mode_leaves_existing_rows_alone(self) -> None:
        self.add("bench")
        self.settings.mock = True
        self.add("bench")
        rows = db.recent_runs("bench")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "short")


if __name__ == "__main__":
    unittest.main()
