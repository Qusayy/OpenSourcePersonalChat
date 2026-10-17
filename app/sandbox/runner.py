"""Running a program the model wrote, where it can do no harm.

The program runs under bubblewrap, as the app's own user, in namespaces of
its own: no network, no other process to see, and nothing of the server's
files but /usr and the sandbox's own read-only venv. Its working directory and
/tmp are empty and go when it ends. On top of that, limits the kernel holds it
to (prlimit): address space, CPU time, the size of a file it writes, open
files. A wall clock kills the whole process group when time is up, and what it
prints is cut off at a ceiling.

There is no way round it: where bubblewrap cannot run a program, the sandbox
is not available and the python tool is not offered at all. Nothing is ever
run outside it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import settings

# Seconds a program may take, start to finish, and CPU seconds within that.
WALL_SECONDS = 10.0
CPU_SECONDS = 10
# Address space, the largest file it may write, and open files.
MEMORY_BYTES = 1024 * 1024 * 1024
FILE_BYTES = 8 * 1024 * 1024
OPEN_FILES = 64
# What is kept of what it prints, each of stdout and stderr.
OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class Outcome:
    """What a program did."""

    stdout: str
    stderr: str
    # None when it was killed: out of time, or stopped.
    exit_code: int | None
    timed_out: bool
    ms: float
    # Whether what it printed was cut off.
    truncated: bool = False


class Runner(Protocol):
    def available(self) -> bool: ...

    async def run(self, code: str, project: dict[str, str] | None = None) -> Outcome: ...


def argv(script: str, *, venv: str, project: str | None = None) -> list[str]:
    """The command line that runs ``script`` (a file on the host) in the jail.
    ``project`` is a directory of files the program may read, at /work/project."""
    limits = [
        "prlimit", f"--as={MEMORY_BYTES}", f"--cpu={CPU_SECONDS}", f"--fsize={FILE_BYTES}",
        f"--nofile={OPEN_FILES}", "--core=0", "--",
    ]
    jail = [
        "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
        "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind-try", "/etc/localtime", "/etc/localtime",
        "--ro-bind", venv, venv,
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/work",
        "--ro-bind", script, "/work/main.py",
        *(["--ro-bind", project, "/work/project"] if project else []),
        "--chdir", "/work",
        "--clearenv",
        "--setenv", "PATH", f"{venv}/bin:/usr/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "PYTHONIOENCODING", "utf-8",
        "--setenv", "OPENBLAS_NUM_THREADS", "1",
        "--setenv", "OMP_NUM_THREADS", "1",
        "--setenv", "MPLBACKEND", "Agg",
        "--hostname", "sandbox",
        "--", f"{venv}/bin/python", "-I", "-B", "/work/main.py",
    ]
    return limits + jail


async def _read(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """All of a stream, kept up to ``cap`` bytes; the rest is read and dropped
    so the program never blocks on a full pipe."""
    kept, total = b"", 0
    while chunk := await stream.read(8192):
        total += len(chunk)
        if len(kept) < cap:
            kept += chunk[: cap - len(kept)]
    return kept, total > cap


class BwrapRunner:
    """The sandbox on a Linux server with bubblewrap and user namespaces."""

    def __init__(self, venv: str | None = None) -> None:
        self.venv = venv or settings.sandbox_venv
        self._available: bool | None = None
        self._one = asyncio.Semaphore(1)

    def _parts_present(self) -> bool:
        return bool(
            shutil.which("bwrap") and shutil.which("prlimit")
            and Path(self.venv, "bin", "python").exists()
        )

    def available(self) -> bool:
        """Whether a program can be run here at all, found out once by
        running the smallest one there is."""
        if self._available is None:
            self._available = self._parts_present() and self._probe()
        return self._available

    def _probe(self) -> bool:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "main.py")
            script.write_text("print('ok')\n", encoding="utf-8")
            try:
                done = subprocess.run(
                    argv(str(script), venv=self.venv), capture_output=True, timeout=WALL_SECONDS,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            return done.returncode == 0 and done.stdout.strip() == b"ok"

    async def run(self, code: str, project: dict[str, str] | None = None) -> Outcome:
        async with self._one:
            return await self._run(code, project)

    async def _run(self, code: str, project: dict[str, str] | None) -> Outcome:
        began = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="aurora-run-") as tmp:
            script = Path(tmp, "main.py")
            script.write_text(code, encoding="utf-8")
            files = None
            if project:
                files = Path(tmp, "project")
                for path, content in project.items():
                    target = files / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                *argv(str(script), venv=self.venv, project=str(files) if files else None),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            reading = asyncio.gather(_read(proc.stdout, OUTPUT_BYTES), _read(proc.stderr, OUTPUT_BYTES))
            timed_out = False
            try:
                await asyncio.wait_for(proc.wait(), timeout=WALL_SECONDS)
            except asyncio.TimeoutError:
                timed_out = True
            finally:
                if proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    await proc.wait()
            (out, out_cut), (err, err_cut) = await reading
        return Outcome(
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
            exit_code=None if timed_out else proc.returncode,
            timed_out=timed_out,
            ms=(time.perf_counter() - began) * 1000,
            truncated=out_cut or err_cut,
        )


_runner: Runner | None = None


def runner() -> Runner:
    """The sandbox this process runs programs in."""
    global _runner
    if _runner is None:
        _runner = BwrapRunner()
    return _runner


def use(replacement: Runner | None) -> None:
    """Run programs in another sandbox, or put the real one back (None). For
    tests, and for a machine that has no bubblewrap."""
    global _runner
    _runner = replacement
