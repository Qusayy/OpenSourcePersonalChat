"""Running a program the model wrote, where it can do no harm.

The program runs under bubblewrap, as the app's own user, in namespaces of
its own: no network, no other process to see, and nothing of the server's
files but /usr and the sandbox's own read-only venv. Its working directory and
/tmp are empty and go when it ends. It cannot make namespaces of its own
(--disable-userns), and where deploy/build-sandbox.sh has written the seccomp
filter beside the venv, the kernel refuses it the calls an escape starts
with: unshare, setns, mount, ptrace, bpf, io_uring, keyrings, modules. On top
of that, limits the kernel holds it to (prlimit): address space, CPU time, the
size of a file it writes, open files, processes. When memory runs out it is
what the kernel kills first (choom). A wall clock kills the whole process
group when time is up, and what it prints is cut off at a ceiling.

There is no way round it: where bubblewrap cannot run a program, the sandbox
is not available and the python tool is not offered at all. Nothing is ever
run outside it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Protocol

from ..config import settings

log = logging.getLogger("aurora.sandbox")

# Seconds a program may take, start to finish, and CPU seconds within that.
WALL_SECONDS = 10.0
CPU_SECONDS = 10
# Address space, the largest file it may write, and open files.
MEMORY_BYTES = 1024 * 1024 * 1024
FILE_BYTES = 8 * 1024 * 1024
OPEN_FILES = 64
# Processes and threads it may start. The kernel counts every task of the
# user against this (RLIMIT_NPROC), the app's own threads and the model
# server's among them, so the limit a program gets is this many above what
# the user is already running when it starts.
PROCESSES = 64
# Where it stands with the out-of-memory killer: 1000 is taken first.
OOM_SCORE = 1000
# What is kept of what it prints, each of stdout and stderr.
OUTPUT_BYTES = 64 * 1024
# The seccomp filter deploy/build-sandbox.sh writes, beside the venv.
SECCOMP_FILE = "seccomp.bpf"


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


def argv(
    script: str,
    *,
    venv: str,
    project: str | None = None,
    processes: int = PROCESSES,
    seccomp_fd: int | None = None,
    disable_userns: bool = True,
    choom: bool = False,
) -> list[str]:
    """The command line that runs ``script`` (a file on the host) in the jail.
    ``project`` is a directory of files the program may read, at /work/project.

    ``processes`` is the most tasks the user may have while it runs;
    ``seccomp_fd`` an open descriptor of the seccomp filter, which the child
    must be handed; ``disable_userns`` is false only for a bubblewrap too old
    to know the flag; ``choom`` says choom is there to put the program first
    in the out-of-memory killer's line."""
    oom = ["choom", "-n", str(OOM_SCORE), "--"] if choom else []
    limits = [
        "prlimit", f"--as={MEMORY_BYTES}", f"--cpu={CPU_SECONDS}", f"--fsize={FILE_BYTES}",
        f"--nofile={OPEN_FILES}", f"--nproc={processes}", "--core=0", "--",
    ]
    jail = [
        # --unshare-all only tries for a user namespace, and --disable-userns
        # needs one asked for outright.
        "bwrap", "--unshare-all", "--unshare-user",
        *(["--disable-userns"] if disable_userns else []),
        "--die-with-parent", "--new-session", "--cap-drop", "ALL",
        *(["--seccomp", str(seccomp_fd)] if seccomp_fd is not None else []),
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
    return oom + limits + jail


def tasks_running() -> int:
    """How many tasks (processes and their threads) this process's user has
    running now, as RLIMIT_NPROC counts them. 0 where /proc cannot say."""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return 0
    uid = str(getuid()).encode()
    try:
        pids = [name for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return 0
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status", "rb") as status:
                lines = status.read().splitlines()
        except OSError:
            continue
        real = threads = b""
        for line in lines:
            if line.startswith(b"Uid:"):
                real = line.split()[1]
            elif line.startswith(b"Threads:"):
                threads = line.split()[1]
        if real == uid and threads.isdigit():
            total += int(threads)
    return total


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
    """The sandbox on a Linux server with bubblewrap and user namespaces.

    ``seccomp`` is where the filter is (by default beside the venv) and
    ``which`` how a program is found on the PATH; both are for tests."""

    def __init__(
        self,
        venv: str | None = None,
        *,
        seccomp: str | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.venv = venv or settings.sandbox_venv
        self.seccomp = seccomp or str(PurePosixPath(self.venv).parent / SECCOMP_FILE)
        self._which = which
        self._available: bool | None = None
        # Settled by the first probe: whether this bubblewrap takes
        # --disable-userns, and the filter every program is run with.
        self._disable_userns = True
        self._filter: str | None = None
        self._one = asyncio.Semaphore(1)

    def _parts_present(self) -> bool:
        return bool(
            self._which("bwrap") and self._which("prlimit")
            and Path(self.venv, "bin", "python").exists()
        )

    def command(self, script: str, project: str | None = None, *, seccomp_fd: int | None = None) -> list[str]:
        """The command line for one program, with everything found here."""
        return argv(
            script, venv=self.venv, project=project,
            processes=tasks_running() + PROCESSES, seccomp_fd=seccomp_fd,
            disable_userns=self._disable_userns, choom=bool(self._which("choom")),
        )

    @contextmanager
    def _filter_fd(self) -> Iterator[int | None]:
        """The seccomp filter, open for bubblewrap to read, or None where
        there is none. Opened afresh for each program, since it is read from
        the start; if it has gone since the probe, nothing runs."""
        if self._filter is None:
            yield None
            return
        fd = os.open(self._filter, os.O_RDONLY)
        try:
            yield fd
        finally:
            os.close(fd)

    def available(self) -> bool:
        """Whether a program can be run here at all, found out once by
        running the smallest one there is."""
        if self._available is None:
            self._available = self._parts_present() and self._probe()
        return self._available

    def _probe(self) -> bool:
        """The smallest program, with every protection there is. Where the
        filter is missing, programs run without it, and the log says so.
        A bubblewrap older than 0.8 does not know --disable-userns: then
        programs run without that, and the log says so, rather than not at
        all."""
        if Path(self.seccomp).is_file():
            self._filter = self.seccomp
        else:
            log.warning("no seccomp filter at %s; deploy/build-sandbox.sh writes it", self.seccomp)
        ran, why = self._try()
        if ran:
            return True
        self._disable_userns = False
        if self._try()[0]:
            log.warning(
                "the sandbox runs without --disable-userns (bubblewrap 0.8 or later has it): %s",
                why or "no reason given",
            )
            return True
        self._disable_userns = True
        log.warning("the sandbox cannot run a program here: %s", why or "no reason given")
        return False

    def _try(self) -> tuple[bool, str]:
        """Whether the smallest program runs, and what bubblewrap said if not."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "main.py")
            script.write_text("print('ok')\n", encoding="utf-8")
            try:
                with self._filter_fd() as fd:
                    done = subprocess.run(
                        self.command(str(script), seccomp_fd=fd), capture_output=True,
                        timeout=WALL_SECONDS, pass_fds=() if fd is None else (fd,),
                    )
            except (OSError, subprocess.SubprocessError) as exc:
                return False, str(exc)
            if done.returncode == 0 and done.stdout.strip() == b"ok":
                return True, ""
            return False, done.stderr.decode("utf-8", errors="replace").strip()[-300:]

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
            with self._filter_fd() as fd:
                proc = await asyncio.create_subprocess_exec(
                    *self.command(str(script), str(files) if files else None, seccomp_fd=fd),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True, pass_fds=() if fd is None else (fd,),
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
