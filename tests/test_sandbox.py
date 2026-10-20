"""The Python sandbox: the jail a program runs in, the tool that runs one,
and the call in which the model writes it.

Most of this needs no sandbox: a stand-in runner takes the program and says
what it did. The tests at the end run real programs in the real jail, and
only where ``AURORA_SANDBOX_TESTS=1`` says the machine has one (the CI job
that installs bubblewrap).

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from dataclasses import dataclass, field

try:
    from app import agent
    from app.agent.calls import render_call
    from app.agent.reply import CODE_END
    from app.agent.route import free_route
    from app.sandbox import runner as sandbox
    from app.sandbox.runner import BwrapRunner, Outcome, argv
    from app.tools import REGISTRY
    from app.tools.base import CALL, CallContext, available, call_tool
    from evals.scripted import ScriptedEngine
    from tests import support
    from tests.support import collect, only
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

CHOOSE = render_call("python", {"code": "print(2 ** 10)\n"})


@dataclass
class FakeRunner:
    """A sandbox that runs nothing: it keeps each program, and says what the
    test has it say."""

    outcome: Outcome = field(default_factory=lambda: Outcome("1024\n", "", 0, False, 12.0))
    ready: bool = True
    programs: list[str] = field(default_factory=list)

    def available(self) -> bool:
        return self.ready

    async def run(self, code: str, project: dict | None = None) -> Outcome:
        self.programs.append(code)
        return self.outcome


def use(case: unittest.TestCase, runner) -> None:
    sandbox.use(runner)
    case.addCleanup(sandbox.use, None)


class JailTests(unittest.TestCase):
    def test_the_program_runs_with_nothing_shared_and_everything_limited(self) -> None:
        line = argv("/tmp/x/main.py", venv="/opt/aurora-sandbox/venv")
        self.assertEqual(line[0], "prlimit")
        for limit in (f"--as={sandbox.MEMORY_BYTES}", f"--cpu={sandbox.CPU_SECONDS}",
                      f"--fsize={sandbox.FILE_BYTES}", f"--nofile={sandbox.OPEN_FILES}", "--core=0"):
            self.assertIn(limit, line)
        jail = line[line.index("bwrap"):]
        for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
            self.assertIn(flag, jail)
        self.assertNotIn("--share-net", jail)
        self.assertEqual(jail[jail.index("--cap-drop") + 1], "ALL")
        # Read-only: /usr and the venv. Writable: two empty tmpfs.
        binds = [(jail[i], jail[i + 1], jail[i + 2]) for i, a in enumerate(jail) if a.startswith("--ro-bind")]
        self.assertIn(("--ro-bind", "/usr", "/usr"), binds)
        self.assertIn(("--ro-bind", "/opt/aurora-sandbox/venv", "/opt/aurora-sandbox/venv"), binds)
        self.assertIn(("--ro-bind", "/tmp/x/main.py", "/work/main.py"), binds)
        self.assertNotIn("--bind", jail)
        self.assertEqual(jail[-4:], ["/opt/aurora-sandbox/venv/bin/python", "-I", "-B", "/work/main.py"])

    def test_a_project_is_mounted_read_only_where_the_program_can_read_it(self) -> None:
        line = argv("/t/main.py", venv="/v", project="/t/project")
        at = line.index("/t/project")
        self.assertEqual(line[at - 1:at + 2], ["--ro-bind", "/t/project", "/work/project"])

    def test_without_its_parts_the_sandbox_is_not_there(self) -> None:
        self.assertFalse(BwrapRunner(venv="/no/such/venv").available())

    def test_the_program_can_make_no_namespace_of_its_own(self) -> None:
        jail = argv("/t/main.py", venv="/v")
        # --disable-userns needs the user namespace asked for outright.
        self.assertLess(jail.index("--unshare-user"), jail.index("--disable-userns"))
        self.assertIn("--unshare-all", jail)
        # Only for a bubblewrap too old to know the flag.
        self.assertNotIn("--disable-userns", argv("/t/main.py", venv="/v", disable_userns=False))

    def test_the_seccomp_filter_is_handed_over_by_descriptor_when_there_is_one(self) -> None:
        jail = argv("/t/main.py", venv="/v", seccomp_fd=7)
        self.assertEqual(jail[jail.index("--seccomp") + 1], "7")
        self.assertLess(jail.index("--seccomp"), jail.index("--", jail.index("bwrap")))
        self.assertNotIn("--seccomp", argv("/t/main.py", venv="/v"))

    def test_processes_are_limited(self) -> None:
        self.assertIn(f"--nproc={sandbox.PROCESSES}", argv("/t/main.py", venv="/v"))
        line = argv("/t/main.py", venv="/v", processes=90)
        self.assertIn("--nproc=90", line[:line.index("bwrap")])

    def test_the_out_of_memory_killer_takes_the_program_first(self) -> None:
        line = argv("/t/main.py", venv="/v", choom=True)
        self.assertEqual(line[:5], ["choom", "-n", "1000", "--", "prlimit"])

    def test_choom_is_used_where_it_is_found(self) -> None:
        def found(*names):
            return lambda name: f"/usr/bin/{name}" if name in names else None

        line = BwrapRunner(venv="/v", which=found("bwrap", "prlimit", "choom")).command("/t/main.py")
        self.assertEqual(line[:4], ["choom", "-n", "1000", "--"])
        line = BwrapRunner(venv="/v", which=found("bwrap", "prlimit")).command("/t/main.py")
        self.assertEqual(line[0], "prlimit")

    def test_the_process_limit_is_counted_above_what_the_user_runs_already(self) -> None:
        line = BwrapRunner(venv="/v", which=lambda name: None).command("/t/main.py")
        nproc = next(a for a in line if a.startswith("--nproc="))
        self.assertGreaterEqual(int(nproc.split("=")[1]), sandbox.PROCESSES)

    def test_the_filter_is_looked_for_beside_the_venv(self) -> None:
        self.assertEqual(BwrapRunner(venv="/opt/aurora-sandbox/venv").seccomp,
                         "/opt/aurora-sandbox/seccomp.bpf")


class ProbeTests(unittest.TestCase):
    """What the first run finds out, with a stand-in for running it."""

    def runner(self, works, seccomp: str = "/no/such/seccomp.bpf") -> BwrapRunner:
        """A runner whose smallest program runs where ``works`` says so, given
        whether --disable-userns is on its command line."""
        runner = BwrapRunner(venv="/v", seccomp=seccomp)
        runner._parts_present = lambda: True
        runner.tried = []

        def tried():
            line = runner.command("/t/main.py")
            runner.tried.append(line)
            ok = works("--disable-userns" in line)
            return ok, "" if ok else "bwrap: Unknown option --disable-userns"

        runner._try = tried
        return runner

    def test_with_every_protection_working_nothing_is_dropped(self) -> None:
        runner = self.runner(lambda userns_off: True)
        with self.assertLogs("aurora.sandbox", "WARNING"):
            self.assertTrue(runner.available())
        self.assertEqual(len(runner.tried), 1)
        self.assertIn("--disable-userns", runner.command("/t/main.py"))

    def test_an_older_bubblewrap_runs_programs_without_the_flag_and_says_so(self) -> None:
        runner = self.runner(lambda userns_off: not userns_off)
        with self.assertLogs("aurora.sandbox", "WARNING") as said:
            self.assertTrue(runner.available())
        self.assertTrue(any("--disable-userns" in line and "Unknown option" in line for line in said.output))
        self.assertNotIn("--disable-userns", runner.command("/t/main.py"))
        # Found out once.
        self.assertTrue(runner.available())
        self.assertEqual(len(runner.tried), 2)

    def test_a_jail_that_does_not_run_at_all_is_not_offered(self) -> None:
        runner = self.runner(lambda userns_off: False)
        with self.assertLogs("aurora.sandbox", "WARNING"):
            self.assertFalse(runner.available())
        self.assertIn("--disable-userns", runner.command("/t/main.py"))

    def test_the_filter_is_used_where_it_is_and_read_from_its_start(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seccomp.bpf")
            with open(path, "wb") as out:
                out.write(b"\x06\x00\x00\x00\x00\x00\xff\x7f")
            runner = self.runner(lambda userns_off: True, seccomp=path)
            self.assertTrue(runner.available())
            for _ in range(2):
                with runner._filter_fd() as fd:
                    self.assertEqual(os.read(fd, 64), b"\x06\x00\x00\x00\x00\x00\xff\x7f")
                    line = runner.command("/t/main.py", seccomp_fd=fd)
                    self.assertEqual(line[line.index("--seccomp") + 1], str(fd))

    def test_without_the_filter_programs_run_without_it_and_the_log_says_so(self) -> None:
        runner = self.runner(lambda userns_off: True)
        with self.assertLogs("aurora.sandbox", "WARNING") as said:
            self.assertTrue(runner.available())
        self.assertTrue(any("no seccomp filter" in line for line in said.output))
        with runner._filter_fd() as fd:
            self.assertIsNone(fd)


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def run_program(self, program: str):
        async def made():
            CALL.set(CallContext())
            return await call_tool("python", {"code": program})
        return await asyncio.create_task(made())

    async def test_it_runs_the_program_it_was_given_and_says_what_it_printed(self) -> None:
        fake = FakeRunner()
        use(self, fake)
        result = await self.run_program("print(2 ** 10)\n")
        self.assertEqual(fake.programs, ["print(2 ** 10)\n"])
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "It printed: 1024")
        self.assertEqual((result.card, result.data["exit_code"]), ("python", 0))

    async def test_a_program_that_fails_is_read_with_the_end_of_its_traceback(self) -> None:
        err = "Traceback (most recent call last):\n  File \"/work/main.py\", line 1\nZeroDivisionError: division by zero\n"
        use(self, FakeRunner(Outcome("", err, 1, False, 5.0)))
        result = await self.run_program("1/0")
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "It stopped with an error: ZeroDivisionError: division by zero")
        read = REGISTRY["python"].observation(result, "")
        self.assertIn("exited with code 1", read)
        self.assertIn("ZeroDivisionError", read)

    async def test_a_long_traceback_is_read_from_its_end_where_the_error_is(self) -> None:
        frames = "".join(f'  File "/usr/lib/x.py", line {n}, in f\n    g()\n' for n in range(400))
        err = "Traceback (most recent call last):\n" + frames + "NameError: name 'totl' is not defined\n"
        use(self, FakeRunner(Outcome("a line printed\n" * 200, err, 1, False, 5.0)))
        result = await self.run_program("print(totl)")
        read = REGISTRY["python"].observation(result, "", 500)
        self.assertIn("NameError: name 'totl' is not defined", read)
        self.assertIn("It printed:", read)
        self.assertLessEqual(len(read.encode("utf-8")), 500 * 3)

    async def test_a_program_out_of_time_is_a_failed_call(self) -> None:
        use(self, FakeRunner(Outcome("", "", None, True, 10000.0)))
        result = await self.run_program("while True: pass")
        self.assertEqual((result.ok, result.error), (False, "timeout"))

    async def test_with_no_program_nothing_is_run(self) -> None:
        fake = FakeRunner()
        use(self, fake)
        result = await self.run_program("   ")
        self.assertEqual((result.ok, fake.programs), (False, []))

    async def test_where_the_sandbox_cannot_run_the_tool_is_neither_offered_nor_run(self) -> None:
        fake = FakeRunner(ready=False)
        use(self, fake)
        self.assertNotIn("python", [t.name for t in available()])
        result = await self.run_program("print(1)")
        self.assertEqual((result.ok, result.error, fake.programs), (False, "not available", []))

    async def test_switched_off_it_is_not_offered_even_where_it_could_run(self) -> None:
        use(self, FakeRunner())
        self.assertIn("python", [t.name for t in available()])
        support.with_settings(self, sandbox="off")
        self.assertNotIn("python", [t.name for t in available()])


class ProgramCallTests(unittest.IsolatedAsyncioTestCase):
    """The model writes the program as its call's code: raw text, sampled as
    code, with nothing to escape."""

    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, tools_enabled=True, allow_outbound=True, agent_loop=True)
        self.fake = FakeRunner()
        use(self, self.fake)

    def scripted(self, *script) -> ScriptedEngine:
        engine = ScriptedEngine(script)
        self.addAsyncCleanup(engine.settled)
        return engine

    async def test_the_program_is_written_in_the_call_then_run_then_read(self) -> None:
        engine = self.scripted(CHOOSE, "It is 1024.")
        trace = agent.Trace()
        stream = await collect(agent.run("what is two to the tenth, worked out", history=[],
                                         trace=trace, engine=engine))
        self.assertEqual(self.fake.programs, ["print(2 ** 10)\n"])
        self.assertEqual([s.kind for s in trace.steps], ["call", "tool", "answer"])
        # Up to the code, the code itself sampled as code, the end of the call;
        # then the answer.
        self.assertEqual([p.stop for p in engine.passes][1], [CODE_END])
        self.assertEqual(engine.passes[1].sampling["presence_penalty"], 0.0)
        # After the tag, the format's line break, then the program.
        self.assertEqual(engine.passes[1].output, "\nprint(2 ** 10)\n")
        self.assertTrue(engine.passes[1].prompt.startswith(engine.passes[0].prompt))
        self.assertIn("It printed:\n1024", engine.passes[3].prompt)
        card = only(stream, "card")[0]
        self.assertEqual((card["card"], card["data"]["stdout"]), ("python", "1024\n"))
        self.assertTrue(engine.free)

    async def test_a_call_with_no_program_in_it_runs_nothing(self) -> None:
        engine = self.scripted(render_call("python", {"code": "  "}), "About a thousand.")
        trace = agent.Trace()
        await collect(agent.run("what is two to the tenth, worked out", history=[],
                                trace=trace, engine=engine))
        self.assertEqual(self.fake.programs, [])
        self.assertEqual(trace.calls[0][1].error, "no program")
        self.assertIn("Error: There was no program to run.", engine.passes[-1].prompt)

    async def test_a_program_the_user_gave_is_run_as_they_gave_it(self) -> None:
        for message in ("/python print(6 * 7)", "/python\n```python\nprint(6 * 7)\n```"):
            with self.subTest(message=message):
                self.fake.programs.clear()
                call, tier = free_route(message)
                self.assertEqual(call.name, "python")
                engine = self.scripted("42.")
                await collect(agent.run(message, history=[], engine=engine))
                self.assertEqual(self.fake.programs, ["print(6 * 7)" + ("\n" if "```" in message else "")])
                self.assertEqual(len(engine.passes), 1)


class RunFileTests(unittest.IsolatedAsyncioTestCase):
    """A Python file of the project, run where it can import the others."""

    def setUp(self) -> None:
        from app.store import projects

        support.temp_database(self)
        projects.add("c", "app.py", "from util import twice\nprint(twice(21))\n", lang="python")
        projects.add("c", "util.py", "def twice(n):\n    return 2 * n\n", lang="python")
        projects.add("c", "notes.md", "# notes\n", lang="markdown")
        self.fake = FakeRunner(Outcome("42\n", "", 0, False, 9.0))
        use(self, self.fake)

    async def run_file(self, path: str):
        async def made():
            CALL.set(CallContext(conversation_id="c"))
            return await call_tool("run_file", {"path": path})
        return await asyncio.create_task(made())

    async def test_the_file_runs_with_the_whole_project_beside_it(self) -> None:
        captured = {}

        async def run(code, project=None):
            captured.update(code=code, project=project)
            return self.fake.outcome

        self.fake.run = run
        result = await self.run_file("app.py")
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "app.py printed: 42")
        self.assertIn("runpy.run_path('app.py'", captured["code"])
        self.assertEqual(sorted(captured["project"]), ["app.py", "notes.md", "util.py"])

    async def test_only_a_python_file_of_this_project_runs(self) -> None:
        self.assertEqual((await self.run_file("notes.md")).error, "not python")
        missing = await self.run_file("main.py")
        self.assertEqual(missing.error, "no such file")
        self.assertIn("app.py, util.py", missing.summary)
        self.assertEqual((await self.run_file("../app.py")).error, "no such file")


@unittest.skipUnless(os.environ.get("AURORA_SANDBOX_TESTS") == "1",
                     "the real sandbox is tested where AURORA_SANDBOX_TESTS=1")
class RealSandboxTests(unittest.IsolatedAsyncioTestCase):
    """Real programs in the real jail."""

    def setUp(self) -> None:
        self.runner = BwrapRunner()
        self.assertTrue(self.runner.available(), "the sandbox should run here")

    async def run_code(self, code: str) -> Outcome:
        return await self.runner.run(code)

    async def test_a_program_runs_and_its_output_is_kept(self) -> None:
        done = await self.run_code("import numpy\nprint(int(numpy.arange(5).sum()))")
        self.assertEqual((done.exit_code, done.stdout.strip()), (0, "10"))

    async def test_there_is_no_network(self) -> None:
        done = await self.run_code(
            "import socket\n"
            "try:\n socket.create_connection(('1.1.1.1', 80), timeout=2)\n print('open')\n"
            "except OSError as e:\n print('closed')"
        )
        self.assertEqual(done.stdout.strip(), "closed")

    async def test_the_servers_files_are_not_there(self) -> None:
        done = await self.run_code(
            "import os\nprint(os.path.exists('/etc/passwd'), os.path.exists('/srv'), os.path.exists('/home'))"
        )
        self.assertEqual(done.stdout.strip(), "False False False")

    async def test_nothing_outside_its_own_directories_can_be_written(self) -> None:
        done = await self.run_code(
            "open('/work/ok.txt','w').write('x')\n"
            "try:\n open('/usr/evil','w')\n print('wrote')\n"
            "except OSError:\n print('refused')"
        )
        self.assertEqual(done.stdout.strip(), "refused")

    async def test_a_program_that_runs_on_is_killed_at_the_wall(self) -> None:
        done = await self.run_code("while True:\n pass")
        self.assertTrue(done.timed_out)
        self.assertIsNone(done.exit_code)

    async def test_memory_is_limited(self) -> None:
        done = await self.run_code("x = bytearray(4 * 1024 ** 3)\nprint('got it')")
        self.assertNotEqual(done.exit_code, 0)
        self.assertNotIn("got it", done.stdout)

    async def test_a_project_file_runs_and_imports_its_neighbours(self) -> None:
        from app.tools.run_file import launcher

        done = await self.runner.run(launcher("app.py"), project={
            "app.py": "from util import twice\nprint(twice(21))\n",
            "util.py": "def twice(n):\n    return 2 * n\n",
        })
        self.assertEqual((done.exit_code, done.stdout.strip()), (0, "42"))

    async def test_output_is_cut_off_at_its_ceiling(self) -> None:
        done = await self.run_code("print('x' * 500_000)")
        self.assertTrue(done.truncated)
        self.assertLessEqual(len(done.stdout), sandbox.OUTPUT_BYTES)

    async def test_a_program_cannot_make_a_user_namespace(self) -> None:
        # os.unshare is 3.12's; the sandbox's Python may be older.
        done = await self.run_code(
            "import ctypes, os\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "if libc.unshare(0x10000000) == 0:\n print('made one')\n"
            "else:\n print('refused:', os.strerror(ctypes.get_errno()))"
        )
        self.assertTrue(done.stdout.startswith("refused:"), done.stdout + done.stderr)

    async def test_a_fork_bomb_is_stopped_and_the_program_still_ends(self) -> None:
        done = await self.run_code(
            "import os, time\n"
            "n = 0\n"
            "try:\n"
            " while n < 1000:\n"
            "  if os.fork() == 0:\n"
            "   time.sleep(5)\n"
            "   os._exit(0)\n"
            "  n += 1\n"
            " print('not stopped')\n"
            "except OSError:\n"
            " print('stopped at', n)"
        )
        self.assertFalse(done.timed_out)
        self.assertEqual(done.exit_code, 0, done.stderr)
        words = done.stdout.split()
        self.assertEqual(words[:2], ["stopped", "at"], done.stdout)
        # The limit is counted above what this user was already running, so
        # the exact figure moves a little with that.
        self.assertLess(int(words[2]), 2 * sandbox.PROCESSES)

    async def test_the_filter_refuses_what_it_lists_and_threads_still_start(self) -> None:
        if not os.path.isfile(self.runner.seccomp):
            self.skipTest(f"no seccomp filter at {self.runner.seccomp}")
        # io_uring_setup and clone3 have the same number on x86-64 and arm64.
        done = await self.run_code(
            "import ctypes, errno, threading\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "def err(*call):\n"
            " return errno.errorcode.get(ctypes.get_errno()) if libc.syscall(*call) == -1 else 'ran'\n"
            "print(err(425, 1, None), err(435, None, 0))\n"
            "t = threading.Thread(target=print, args=('thread',))\n"
            "t.start(); t.join()"
        )
        self.assertEqual(done.stdout.split(), ["EPERM", "ENOSYS", "thread"], done.stderr)


if __name__ == "__main__":
    unittest.main()
