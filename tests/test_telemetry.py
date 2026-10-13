"""Tests for app.telemetry.

The Linux path reads /proc and /sys, which this may well be run without (the
project is developed on Windows too). So every reader takes a filesystem root,
and these tests build that root in a temporary directory: what is checked is
the real path resolution and the real arithmetic, against files with the
contents a Linux kernel would put there.

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import telemetry
from app.telemetry import (
    Sampler,
    parse_cgroup,
    parse_cpu_list,
    parse_cpu_max,
    parse_cpu_times,
    parse_limit,
    parse_meminfo,
    read_cgroup_limits,
)

GIB = 1024**3

# Fourteen CPUs online, as on a workstation; fields are
# user nice system idle iowait irq softirq steal guest guest_nice.
STAT_BEFORE = "\n".join(
    ["cpu  1400 0 1400 14000 140 0 0 0 0 0"]
    + [f"cpu{i} 100 0 100 1000 10 0 0 0 0 0" for i in range(14)]
    + ["intr 12345 0 0", "ctxt 999", "btime 1700000000"]
)


def stat_after(busy: dict[int, int], idle: dict[int, int]) -> str:
    """STAT_BEFORE with `busy` jiffies added to user and `idle` to idle."""
    lines = ["cpu  9999 0 9999 99999 0 0 0 0 0 0"]  # the aggregate must be ignored
    for i in range(14):
        lines.append(f"cpu{i} {100 + busy.get(i, 0)} 0 100 {1000 + idle.get(i, 0)} 10 0 0 0 0 0")
    return "\n".join(lines)


MEMINFO = """MemTotal:        3981312 kB
MemFree:          201216 kB
MemAvailable:    1789952 kB
Buffers:           80000 kB
Cached:          1900000 kB
HugePages_Total:       0
"""

STATUS = """Name:\tuvicorn
VmPeak:\t 2000000 kB
VmRSS:\t 1436672 kB
Cpus_allowed:\t3
Cpus_allowed_list:\t0-1
"""


class Root:
    """A throwaway filesystem root populated from ``{relative path: text}``."""

    def __init__(self, files: dict[str, str]) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        for rel, text in files.items():
            self.write(rel, text)

    def write(self, rel: str, text: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def close(self) -> None:
        self._tmp.cleanup()


class ParserTests(unittest.TestCase):
    def test_cpu_times_skips_the_aggregate_line(self) -> None:
        times = parse_cpu_times(STAT_BEFORE)
        self.assertEqual(sorted(times), list(range(14)))

    def test_cpu_times_counts_iowait_as_idle_and_steal_as_busy(self) -> None:
        #                user nice sys idle iowait irq softirq steal
        times = parse_cpu_times("cpu0 10 0 20 300 40 1 2 50 0 0")
        idle, total = times[0]
        self.assertEqual(idle, 340)
        self.assertEqual(total, 10 + 0 + 20 + 300 + 40 + 1 + 2 + 50)

    def test_cpu_times_ignores_other_lines(self) -> None:
        self.assertEqual(parse_cpu_times("intr 1 2 3\ncpufreq 9\nctxt 5"), {})

    def test_meminfo_converts_kb_to_bytes(self) -> None:
        info = parse_meminfo(MEMINFO)
        self.assertEqual(info["MemTotal"], 3981312 * 1024)
        self.assertEqual(info["MemAvailable"], 1789952 * 1024)
        self.assertEqual(info["HugePages_Total"], 0)  # no unit: left as given

    def test_cpu_list(self) -> None:
        self.assertEqual(parse_cpu_list("0-1,4,6-7\n"), {0, 1, 4, 6, 7})
        self.assertEqual(parse_cpu_list("3"), {3})
        self.assertEqual(parse_cpu_list(""), set())

    def test_cgroup_v2_line(self) -> None:
        v2, v1 = parse_cgroup("0::/system.slice/aurora.service\n")
        self.assertEqual(v2, "/system.slice/aurora.service")
        self.assertEqual(v1, {})

    def test_cgroup_v1_lines_split_joined_controllers(self) -> None:
        v2, v1 = parse_cgroup(
            "5:memory:/system.slice/aurora.service\n3:cpu,cpuacct:/system.slice/aurora.service\n"
        )
        self.assertIsNone(v2)
        self.assertEqual(v1["memory"], "/system.slice/aurora.service")
        self.assertEqual(v1["cpu"], "/system.slice/aurora.service")
        self.assertEqual(v1["cpuacct"], "/system.slice/aurora.service")

    def test_cpu_max(self) -> None:
        self.assertIsNone(parse_cpu_max("max 100000"))
        self.assertEqual(parse_cpu_max("150000 100000\n"), 1.5)
        self.assertEqual(parse_cpu_max("200000 100000"), 2.0)
        self.assertIsNone(parse_cpu_max("garbage"))

    def test_limit(self) -> None:
        self.assertIsNone(parse_limit("max\n"))
        self.assertEqual(parse_limit("3221225472\n"), 3 * GIB)
        self.assertIsNone(parse_limit("9223372036854771712"))  # cgroup v1 "no limit"
        self.assertIsNone(parse_limit(""))


class CgroupTests(unittest.TestCase):
    def tearDown(self) -> None:
        self.root.close()

    def test_v2_under_systemd_reads_the_units_own_directory(self) -> None:
        # This is the production layout. The files are NOT at the cgroup root.
        self.root = Root(
            {
                "proc/self/cgroup": "0::/system.slice/aurora.service\n",
                "sys/fs/cgroup/cgroup.controllers": "cpu memory pids\n",
                "sys/fs/cgroup/system.slice/memory.max": "max\n",
                "sys/fs/cgroup/system.slice/aurora.service/memory.max": f"{3 * GIB}\n",
                "sys/fs/cgroup/system.slice/aurora.service/memory.high": "2726297600\n",
                "sys/fs/cgroup/system.slice/aurora.service/memory.current": "1503238553\n",
            }
        )
        limits = read_cgroup_limits(self.root.path)
        self.assertEqual(limits["memory_max"], 3 * GIB)
        self.assertEqual(limits["memory_high"], 2726297600)
        self.assertEqual(limits["memory_current"], 1503238553)
        # The unit sets no CPU directive, so there is no cpu.max: no quota.
        self.assertIsNone(limits["cpu_quota"])

    def test_v2_in_a_container_namespace_reads_the_root(self) -> None:
        self.root = Root(
            {
                "proc/self/cgroup": "0::/\n",
                "sys/fs/cgroup/cgroup.controllers": "cpu memory\n",
                "sys/fs/cgroup/cpu.max": "150000 100000\n",
                "sys/fs/cgroup/memory.max": f"{3 * GIB}\n",
                "sys/fs/cgroup/memory.current": "104857600\n",
            }
        )
        limits = read_cgroup_limits(self.root.path)
        self.assertEqual(limits["cpu_quota"], 1.5)
        self.assertEqual(limits["memory_max"], 3 * GIB)
        self.assertEqual(limits["memory_current"], 104857600)

    def test_an_ancestors_tighter_limit_wins(self) -> None:
        self.root = Root(
            {
                "proc/self/cgroup": "0::/a/b\n",
                "sys/fs/cgroup/cgroup.controllers": "cpu memory\n",
                "sys/fs/cgroup/a/memory.max": f"{2 * GIB}\n",
                "sys/fs/cgroup/a/cpu.max": "100000 100000\n",
                "sys/fs/cgroup/a/b/memory.max": f"{3 * GIB}\n",
                "sys/fs/cgroup/a/b/cpu.max": "max 100000\n",
            }
        )
        limits = read_cgroup_limits(self.root.path)
        self.assertEqual(limits["memory_max"], 2 * GIB)
        self.assertEqual(limits["cpu_quota"], 1.0)

    def test_v1_hierarchies(self) -> None:
        self.root = Root(
            {
                "proc/self/cgroup": (
                    "5:memory:/system.slice/aurora.service\n"
                    "3:cpu,cpuacct:/system.slice/aurora.service\n"
                ),
                "sys/fs/cgroup/memory/system.slice/aurora.service/memory.limit_in_bytes": f"{3 * GIB}\n",
                "sys/fs/cgroup/memory/system.slice/aurora.service/memory.usage_in_bytes": "1073741824\n",
                "sys/fs/cgroup/cpu,cpuacct/system.slice/aurora.service/cpu.cfs_quota_us": "200000\n",
                "sys/fs/cgroup/cpu,cpuacct/system.slice/aurora.service/cpu.cfs_period_us": "100000\n",
            }
        )
        limits = read_cgroup_limits(self.root.path)
        self.assertEqual(limits["memory_max"], 3 * GIB)
        self.assertEqual(limits["memory_current"], 1073741824)
        self.assertEqual(limits["cpu_quota"], 2.0)

    def test_v1_unlimited_quota_and_memory(self) -> None:
        self.root = Root(
            {
                "proc/self/cgroup": "5:memory:/x\n3:cpu,cpuacct:/x\n",
                "sys/fs/cgroup/memory/x/memory.limit_in_bytes": "9223372036854771712\n",
                "sys/fs/cgroup/cpu,cpuacct/x/cpu.cfs_quota_us": "-1\n",
                "sys/fs/cgroup/cpu,cpuacct/x/cpu.cfs_period_us": "100000\n",
            }
        )
        limits = read_cgroup_limits(self.root.path)
        self.assertIsNone(limits["memory_max"])
        self.assertIsNone(limits["cpu_quota"])

    def test_hybrid_host_does_not_trust_the_empty_v2_line(self) -> None:
        # A "0::" line exists, but with no cgroup.controllers the unified
        # hierarchy carries no controllers; the v1 files are the real ones.
        self.root = Root(
            {
                "proc/self/cgroup": "5:memory:/svc\n0::/svc\n",
                "sys/fs/cgroup/memory/svc/memory.limit_in_bytes": f"{GIB}\n",
            }
        )
        self.assertEqual(read_cgroup_limits(self.root.path)["memory_max"], GIB)

    def test_missing_files_mean_no_limits(self) -> None:
        self.root = Root({})
        self.assertEqual(
            read_cgroup_limits(self.root.path),
            {"cpu_quota": None, "memory_max": None, "memory_high": None, "memory_current": None},
        )


class SamplerTests(unittest.TestCase):
    """The sampler on a fixture root, with the clock under the test's control."""

    def setUp(self) -> None:
        self.root = Root(
            {
                "proc/stat": STAT_BEFORE,
                "proc/meminfo": MEMINFO,
                "proc/self/status": STATUS,
                "proc/loadavg": "1.52 0.75 0.31 2/345 6789\n",
            }
        )
        self.now = 1000.0
        patcher = mock.patch.object(telemetry.time, "monotonic", lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.root.close)
        self.sampler = Sampler(root=self.root.path, procfs=True)

    def test_first_call_has_no_load_to_report(self) -> None:
        snap = self.sampler.snapshot()
        self.assertIsNone(snap["cpu_pct"])
        self.assertIsNone(snap["cores_pct"])

    def test_load_is_measured_on_the_allowed_cpus_only(self) -> None:
        # Affinity is CPUs 0-1 (Cpus_allowed_list) on a 14-CPU machine.
        self.sampler.baseline()
        self.now += 1.0
        # CPU 0 fully busy, CPU 1 half busy; CPU 5 flat out but not ours.
        self.root.write(
            "proc/stat",
            stat_after(busy={0: 100, 1: 50, 5: 100}, idle={1: 50, 2: 100, 3: 100}),
        )
        snap = self.sampler.snapshot()
        self.assertEqual(snap["cores_pct"], [100.0, 50.0])
        self.assertEqual(snap["cpu_pct"], 75.0)
        self.assertEqual(snap["cpus"], 2)

    def test_quota_caps_the_cpu_count_and_may_be_fractional(self) -> None:
        self.root.write("proc/self/cgroup", "0::/\n")
        self.root.write("sys/fs/cgroup/cgroup.controllers", "cpu memory\n")
        self.root.write("sys/fs/cgroup/cpu.max", "150000 100000\n")
        self.assertEqual(self.sampler.snapshot()["cpus"], 1.5)
        self.assertEqual(self.sampler.usable_cpus(), 1.5)

    def test_a_stale_previous_sample_is_not_reported_as_now(self) -> None:
        self.sampler.baseline()
        self.now += 3600.0  # nobody looked for an hour
        self.root.write("proc/stat", stat_after(busy={0: 5000}, idle={0: 300000, 1: 360000}))
        first = self.sampler.snapshot()
        self.assertIsNone(first["cpu_pct"], "an hour's average must not be shown as live")
        # That reading became the new baseline; a second later there is a real figure.
        self.now += 1.0
        self.root.write(
            "proc/stat", stat_after(busy={0: 5100}, idle={0: 300000, 1: 360100})
        )
        second = self.sampler.snapshot()
        self.assertEqual(second["cores_pct"], [100.0, 0.0])
        self.assertEqual(second["cpu_pct"], 50.0)

    def test_calls_closer_than_the_minimum_interval_share_one_sample(self) -> None:
        self.sampler.baseline()
        self.now += 1.0
        first = self.sampler.snapshot()
        self.now += 0.2
        self.root.write("proc/stat", stat_after(busy={0: 999}, idle={}))
        self.assertIs(self.sampler.snapshot(), first)
        self.now += 0.4  # 0.6 s since the sample: stale enough to recompute
        self.assertIsNot(self.sampler.snapshot(), first)

    def test_memory_is_the_machines_and_used_is_total_minus_available(self) -> None:
        snap = self.sampler.snapshot()
        self.assertAlmostEqual(snap["mem_total_mb"], 3981312 / 1024, places=1)
        self.assertAlmostEqual(snap["mem_used_mb"], (3981312 - 1789952) / 1024, places=1)

    def test_process_memory_comes_from_vmrss(self) -> None:
        self.assertAlmostEqual(self.sampler.snapshot()["rss_mb"], 1436672 / 1024, places=1)

    def test_load_average_and_source(self) -> None:
        snap = self.sampler.snapshot()
        self.assertEqual(snap["load"], [1.52, 0.75, 0.31])
        self.assertEqual(snap["source"], "procfs")

    def test_limits_from_the_units_cgroup_reach_the_snapshot(self) -> None:
        self.root.write("proc/self/cgroup", "0::/system.slice/aurora.service\n")
        self.root.write("sys/fs/cgroup/cgroup.controllers", "cpu memory\n")
        self.root.write("sys/fs/cgroup/system.slice/aurora.service/memory.max", f"{3 * GIB}\n")
        self.root.write("sys/fs/cgroup/system.slice/aurora.service/memory.high", "2726297600\n")
        snap = self.sampler.snapshot()
        self.assertEqual(snap["limit_mb"], 3072.0)
        self.assertEqual(snap["limit_high_mb"], 2600.0)

    def test_a_machine_with_nothing_readable_reports_nothing(self) -> None:
        empty = Root({})
        self.addCleanup(empty.close)
        with mock.patch.object(telemetry.os, "cpu_count", lambda: 4):
            snap = Sampler(root=empty.path, procfs=True).snapshot()
        self.assertIsNone(snap["cpu_pct"])
        self.assertIsNone(snap["mem_total_mb"])
        self.assertIsNone(snap["rss_mb"])
        self.assertEqual(snap["cpus"], 4)  # falls back to what the kernel reports


class DescribeTests(unittest.TestCase):
    def describe_with(self, cpus, total_mb) -> str:
        fake = {"cpus": cpus, "mem_total_mb": total_mb}
        with mock.patch.object(telemetry.sampler, "snapshot", lambda: fake):
            return telemetry.describe()

    def test_reads_as_a_noun_phrase_after_an_article(self) -> None:
        # Templates write "on a {{ hardware }}"; the phrase must not start with
        # a number, or eight cores would need "an".
        self.assertEqual(self.describe_with(2, 3888.0), "machine with 2 cores and 3.8 GB of RAM")
        self.assertEqual(self.describe_with(8, 16000.0), "machine with 8 cores and 15.6 GB of RAM")

    def test_singular_fractional_and_missing(self) -> None:
        self.assertEqual(self.describe_with(1, None), "machine with 1 core")
        self.assertEqual(self.describe_with(1.5, 2048.0), "machine with 1.5 cores and 2.0 GB of RAM")
        self.assertEqual(self.describe_with(None, None), "single machine")

    def test_usable_cpu_count_rounds_a_fractional_quota_up(self) -> None:
        with mock.patch.object(telemetry.sampler, "usable_cpus", lambda: 1.5):
            self.assertEqual(telemetry.usable_cpu_count(), 2)
        with mock.patch.object(telemetry.sampler, "usable_cpus", lambda: None):
            self.assertIsNone(telemetry.usable_cpu_count())


if __name__ == "__main__":
    unittest.main()
