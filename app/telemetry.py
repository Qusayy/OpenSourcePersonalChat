"""What the machine is doing, measured rather than declared.

Everything the page says about the host comes from here: how many CPUs this
process may actually use, how busy they are, how much memory the machine has and
how much of it is in use, and what this process itself holds.

On Linux it reads /proc and the service's own cgroup with the standard library
only, so a server install needs no extra package. Elsewhere (a Windows or macOS
dev machine) it uses psutil when that is installed and reports less when it is
not; it never guesses.

Three things here are easy to get wrong, and are the reason this is not a
five-line helper:

* **How many CPUs.** ``os.cpu_count()`` is every CPU the kernel can see. A
  process pinned to two of them, or under a cgroup quota of 1.5, can use fewer,
  so the count is the affinity set, capped by the quota.
* **Where the cgroup is.** The limit files are not at the top of
  /sys/fs/cgroup. Under systemd they live in the unit's own directory
  (``system.slice/aurora.service``), which /proc/self/cgroup names. Only inside
  a container's cgroup namespace does that path happen to be the root.
* **What "now" means.** CPU load is a difference between two samples. Samples
  are taken when asked for, not on a timer, so an idle server does no work; but
  then the first answer after a quiet hour would be that hour's average. A
  sample older than ``STALE`` is thrown away and the next call starts over.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path

# Recompute no more often than this. A page polls once a second; two visitors
# polling out of step must not halve each other's sampling interval to nothing.
MIN_INTERVAL = 0.5
# A previous sample older than this is not "a moment ago" any more.
STALE = 5.0
# cgroup v1 reports "no limit" as a number near 2**63.
V1_UNLIMITED = 1 << 60

MB = 1024 * 1024


# ----------------------------------------------------------------- parsers ---
# Pure functions over file text, so they can be tested with fixture strings on
# a machine that has no /proc at all.


def parse_cpu_times(text: str) -> dict[int, tuple[int, int]]:
    """Per-CPU ``(idle, total)`` jiffies from /proc/stat.

    The aggregate ``cpu`` line is skipped on purpose: it sums every online CPU,
    including ones this process is not allowed to run on.

    Idle is idle + iowait. Steal counts as busy: it is time this machine wanted
    and the hypervisor gave to someone else, which a visitor waiting on an
    answer experiences exactly as load.
    """
    out: dict[int, tuple[int, int]] = {}
    for line in text.splitlines():
        if not line.startswith("cpu") or len(line) < 4 or not line[3].isdigit():
            continue
        parts = line.split()
        try:
            index = int(parts[0][3:])
            fields = [int(v) for v in parts[1:9]]  # user … steal; guest is inside user
        except ValueError:
            continue
        if len(fields) < 4:
            continue
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        out[index] = (idle, sum(fields))
    return out


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo as ``{name: bytes}``."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        out[name.strip()] = value * 1024 if len(parts) > 1 and parts[1] == "kB" else value
    return out


def parse_cpu_list(text: str) -> set[int]:
    """A kernel CPU list such as ``0-1,4,6-7`` as a set of indices."""
    out: set[int] = set()
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        try:
            start = int(lo)
            end = int(hi) if hi else start
        except ValueError:
            continue
        out.update(range(start, end + 1))
    return out


def parse_status_field(text: str, field: str) -> str | None:
    """One ``Name:\\tvalue`` line from /proc/<pid>/status."""
    prefix = field + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def parse_cgroup(text: str) -> tuple[str | None, dict[str, str]]:
    """/proc/self/cgroup as ``(v2 path, {v1 controller: path})``.

    A v2 line is ``0::/path``. A v1 line is ``N:controller[,controller]:/path``.
    A hybrid host has both, and its ``0::`` line has no memory files behind it,
    which is why the caller checks for the v2 marker file before trusting it.
    """
    v2: str | None = None
    v1: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(":", 2)
        if len(parts) != 3:
            continue
        _, controllers, path = parts
        if controllers == "":
            v2 = path
        else:
            for name in controllers.split(","):
                v1[name] = path
    return v2, v1


def parse_cpu_max(text: str) -> float | None:
    """cgroup v2 ``cpu.max`` ("max 100000" or "150000 100000") as a CPU count."""
    parts = text.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if quota > 0 and period > 0 else None


def parse_limit(text: str) -> int | None:
    """A cgroup byte limit; ``max`` and v1's near-2**63 sentinel mean none."""
    value = text.strip()
    if not value or value == "max":
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return None if number <= 0 or number >= V1_UNLIMITED else number


# ----------------------------------------------------------------- reading ---


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _tightest(values: list[float | int | None]) -> float | int | None:
    real = [v for v in values if v is not None]
    return min(real) if real else None


def read_cgroup_limits(root: Path) -> dict:
    """The limits that apply to this process, wherever its cgroup is.

    Returns ``cpu_quota`` (CPUs, may be fractional), ``memory_max`` and
    ``memory_high`` (bytes) and ``memory_current`` (bytes), each None when there
    is no such limit. Limits are inherited, so every ancestor is checked and the
    tightest one wins; current use is the leaf's own.
    """
    out: dict = {"cpu_quota": None, "memory_max": None, "memory_high": None, "memory_current": None}
    text = _read(root / "proc" / "self" / "cgroup")
    if text is None:
        return out
    v2_path, v1 = parse_cgroup(text)
    base = root / "sys" / "fs" / "cgroup"

    if v2_path is not None and (base / "cgroup.controllers").exists():
        leaf = base / v2_path.lstrip("/")
        chain = [leaf]
        while chain[-1] != base and base in chain[-1].parents:
            chain.append(chain[-1].parent)
        quotas, maxes, highs = [], [], []
        for directory in chain:
            cpu = _read(directory / "cpu.max")
            quotas.append(parse_cpu_max(cpu) if cpu is not None else None)
            mem = _read(directory / "memory.max")
            maxes.append(parse_limit(mem) if mem is not None else None)
            high = _read(directory / "memory.high")
            highs.append(parse_limit(high) if high is not None else None)
        out["cpu_quota"] = _tightest(quotas)
        out["memory_max"] = _tightest(maxes)
        out["memory_high"] = _tightest(highs)
        current = _read(leaf / "memory.current")
        out["memory_current"] = parse_limit(current) if current is not None else None
        return out

    # cgroup v1: one hierarchy per controller.
    mem_path = v1.get("memory")
    if mem_path is not None:
        directory = base / "memory" / mem_path.lstrip("/")
        limit = _read(directory / "memory.limit_in_bytes")
        out["memory_max"] = parse_limit(limit) if limit is not None else None
        usage = _read(directory / "memory.usage_in_bytes")
        out["memory_current"] = parse_limit(usage) if usage is not None else None
    cpu_path = v1.get("cpu")
    if cpu_path is not None:
        for mount in ("cpu,cpuacct", "cpu"):
            directory = base / mount / cpu_path.lstrip("/")
            quota = _read(directory / "cpu.cfs_quota_us")
            period = _read(directory / "cpu.cfs_period_us")
            if quota is None or period is None:
                continue
            try:
                q, p = int(quota.strip()), int(period.strip())
            except ValueError:
                continue
            out["cpu_quota"] = q / p if q > 0 and p > 0 else None
            break
    return out


def read_affinity(root: Path, live: bool) -> set[int] | None:
    """The CPUs this process may run on.

    ``live`` asks the kernel directly. Otherwise (a fixture directory in a
    test, or a kernel call that is not available) the same answer is read from
    ``Cpus_allowed_list`` in /proc/self/status.
    """
    if live and hasattr(os, "sched_getaffinity"):
        try:
            return set(os.sched_getaffinity(0))
        except OSError:
            pass
    text = _read(root / "proc" / "self" / "status")
    if text is None:
        return None
    allowed = parse_status_field(text, "Cpus_allowed_list")
    if allowed is None:
        return None
    cpus = parse_cpu_list(allowed)
    return cpus or None


def read_rss_bytes(root: Path) -> int | None:
    text = _read(root / "proc" / "self" / "status")
    if text is None:
        return None
    value = parse_status_field(text, "VmRSS")
    if value is None:
        return None
    try:
        return int(value.split()[0]) * 1024
    except (ValueError, IndexError):
        return None


# ----------------------------------------------------------------- sampler ---


def _mb(value: int | float | None) -> float | None:
    return None if value is None else round(value / MB, 1)


class Sampler:
    """Takes a snapshot when asked, and remembers the last one.

    ``root`` is the filesystem root to read /proc and /sys under; tests point it
    at a fixture directory. ``procfs`` forces the Linux path on or off; left as
    None it follows the platform.
    """

    def __init__(self, root: Path | str = "/", procfs: bool | None = None) -> None:
        self.root = Path(root)
        self.procfs = sys.platform.startswith("linux") if procfs is None else procfs
        # Only ask the running kernel when we are really reading this machine.
        self.live = self.procfs and self.root == Path("/")
        self._lock = threading.Lock()
        self._raw: dict[int, tuple[int, int]] | None = None
        self._raw_at = 0.0
        self._snapshot: dict | None = None
        self._snapshot_at = 0.0

    # -- raw readings -------------------------------------------------------

    def _cpu_times(self) -> dict[int, tuple[int, int]] | None:
        if self.procfs:
            text = _read(self.root / "proc" / "stat")
            return parse_cpu_times(text) if text is not None else None
        try:
            import psutil  # type: ignore
        except Exception:
            return None
        out: dict[int, tuple[int, int]] = {}
        for index, t in enumerate(psutil.cpu_times(percpu=True)):
            idle = t.idle + getattr(t, "iowait", 0.0)
            # Scaled so the same integer delta code serves both sources.
            out[index] = (int(idle * 1000), int(sum(t) * 1000))
        return out

    def _affinity(self) -> set[int] | None:
        if self.procfs:
            return read_affinity(self.root, self.live)
        try:
            import psutil  # type: ignore

            return set(psutil.Process().cpu_affinity())
        except Exception:
            return None

    def _memory(self) -> tuple[int | None, int | None]:
        """``(total, available)`` bytes for the whole machine."""
        if self.procfs:
            text = _read(self.root / "proc" / "meminfo")
            if text is None:
                return None, None
            info = parse_meminfo(text)
            return info.get("MemTotal"), info.get("MemAvailable")
        try:
            import psutil  # type: ignore

            vm = psutil.virtual_memory()
            return int(vm.total), int(vm.available)
        except Exception:
            return None, None

    def _rss(self) -> int | None:
        if self.procfs:
            return read_rss_bytes(self.root)
        try:
            import psutil  # type: ignore

            return int(psutil.Process().memory_info().rss)
        except Exception:
            return None

    def _load(self) -> list[float] | None:
        if self.procfs and not self.live:
            text = _read(self.root / "proc" / "loadavg")
            if text is None:
                return None
            try:
                return [round(float(v), 2) for v in text.split()[:3]]
            except ValueError:
                return None
        try:
            return [round(v, 2) for v in os.getloadavg()]
        except (AttributeError, OSError):
            return None  # Windows has no load average

    # -- public -------------------------------------------------------------

    def usable_cpus(self) -> float | None:
        """How many CPUs this process can really use; may be fractional."""
        affinity = self._affinity()
        count: float | None = len(affinity) if affinity else os.cpu_count()
        quota = read_cgroup_limits(self.root)["cpu_quota"] if self.procfs else None
        if count is not None and quota is not None:
            count = min(count, quota)
        return count

    def baseline(self) -> None:
        """Take a first reading so the first real request has something to
        measure against. Called once at startup."""
        with self._lock:
            self._raw = self._cpu_times()
            self._raw_at = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._snapshot is not None and now - self._snapshot_at < MIN_INTERVAL:
                return self._snapshot

            affinity = self._affinity()
            limits = (
                read_cgroup_limits(self.root)
                if self.procfs
                else {"cpu_quota": None, "memory_max": None, "memory_high": None, "memory_current": None}
            )

            # CPU: the difference between this reading and the last, over the
            # CPUs in the affinity set only.
            raw = self._cpu_times()
            cores: list[float] | None = None
            total: float | None = None
            previous, previous_at = self._raw, self._raw_at
            if raw is not None and previous is not None and now - previous_at <= STALE:
                ids = sorted(raw) if not affinity else sorted(i for i in raw if i in affinity)
                per_core: list[float] = []
                idle_sum = total_sum = 0
                for i in ids:
                    if i not in previous:
                        continue
                    d_idle = raw[i][0] - previous[i][0]
                    d_total = raw[i][1] - previous[i][1]
                    if d_total <= 0:
                        per_core.append(0.0)
                        continue
                    per_core.append(round(max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))), 1))
                    idle_sum += d_idle
                    total_sum += d_total
                if per_core:
                    cores = per_core
                    total = (
                        round(max(0.0, min(100.0, 100.0 * (1 - idle_sum / total_sum))), 1)
                        if total_sum > 0
                        else 0.0
                    )
            # Either way this reading becomes the one the next call measures from.
            self._raw, self._raw_at = raw, now

            count: float | None = len(affinity) if affinity else os.cpu_count()
            if count is not None and limits["cpu_quota"] is not None:
                count = min(count, limits["cpu_quota"])

            mem_total, mem_available = self._memory()
            mem_used = (
                mem_total - mem_available
                if mem_total is not None and mem_available is not None
                else None
            )

            self._snapshot = {
                # Wall-clock time of this sample, so a client can tell a fresh
                # figure from one it has already been given.
                "at": round(time.time(), 3),
                "source": "procfs" if self.procfs else ("psutil" if raw is not None else "none"),
                "cpus": count,
                "cpu_pct": total,
                "cores_pct": cores,
                "mem_total_mb": _mb(mem_total),
                "mem_used_mb": _mb(mem_used),
                "rss_mb": _mb(self._rss()),
                "limit_mb": _mb(limits["memory_max"]),
                "limit_high_mb": _mb(limits["memory_high"]),
                "cgroup_mb": _mb(limits["memory_current"]),
                "load": self._load(),
            }
            self._snapshot_at = now
            return self._snapshot


sampler = Sampler()


def snapshot() -> dict:
    return sampler.snapshot()


def process_rss_mb() -> float | None:
    """Resident set size of this process in MB, or None where it cannot be read."""
    return _mb(sampler._rss())  # noqa: SLF001 - one reading, no state touched


def usable_cpu_count() -> int | None:
    """Whole CPUs this process can use, for "threads n / N"."""
    count = sampler.usable_cpus()
    return None if count is None else max(1, math.ceil(count))


def format_cpus(count: float | None) -> str | None:
    if count is None:
        return None
    return str(int(count)) if float(count).is_integer() else f"{count:.1f}"


def describe() -> str:
    """The machine as a noun phrase, from measurements: used wherever a page
    says what this runs "on" and no label has been configured.

    It deliberately does not start with the number, so it reads correctly after
    "a" whatever the count is ("an 8-core" would not).
    """
    snap = sampler.snapshot()
    cpus = format_cpus(snap["cpus"])
    total = snap["mem_total_mb"]
    parts = []
    if cpus is not None:
        parts.append(f"{cpus} core" if cpus == "1" else f"{cpus} cores")
    if total is not None:
        parts.append(f"{total / 1024:.1f} GB of RAM")
    if not parts:
        return "single machine"
    return "machine with " + " and ".join(parts)
