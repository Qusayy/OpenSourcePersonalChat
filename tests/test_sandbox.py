"""The Python sandbox: the jail a program runs in, the tool that runs one,
and the pass in which the model writes it.

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

NONE = '{"tool": "none"}'
CHOOSE = '{"tool": "python", "args": {"task": "two to the tenth"}}'
PROGRAM = "```python\nprint(2 ** 10)\n```"


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


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def run_program(self, program: str):
        async def made():
            CALL.set(CallContext(program=program))
            return await call_tool("python", {"task": "a sum"})
        return await asyncio.create_task(made())

    async def test_it_runs_the_program_it_was_handed_and_says_what_it_printed(self) -> None:
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


class ProgramPassTests(unittest.IsolatedAsyncioTestCase):
    """The model chooses the tool, then writes the program in a pass of its own."""

    def setUp(self) -> None:
        support.no_network(self)
        support.with_settings(self, tools_enabled=True, allow_outbound=True, agent_loop=True)
        self.fake = FakeRunner()
        use(self, self.fake)

    def scripted(self, *script) -> ScriptedEngine:
        engine = ScriptedEngine(script)
        self.addAsyncCleanup(engine.settled)
        return engine

    async def test_the_program_is_written_then_run_then_read(self) -> None:
        engine = self.scripted(CHOOSE, PROGRAM, NONE, "It is 1024.")
        trace = agent.Trace()
        stream = await collect(agent.run("what is two to the tenth, worked out", history=[],
                                         trace=trace, engine=engine))
        self.assertEqual(self.fake.programs, ["print(2 ** 10)\n"])
        self.assertEqual([s.kind for s in trace.steps], ["route", "compose", "tool", "decide", "answer"])
        written = support.step(stream, "code-1", "ok")
        self.assertEqual(written["label"], "Wrote the code")
        # The program pass is held to the grammar of one fenced block, and reads
        # on from the choice.
        self.assertIn("```python", engine.passes[1].grammar)
        self.assertTrue(engine.passes[1].prompt.startswith(engine.passes[0].prompt + CHOOSE))
        self.assertIn(PROGRAM, engine.passes[2].prompt)
        self.assertIn("It printed:\n1024", engine.passes[3].prompt)
        card = only(stream, "card")[0]
        self.assertEqual((card["card"], card["data"]["stdout"]), ("python", "1024\n"))
        self.assertTrue(engine.free)

    async def test_a_reply_with_no_program_in_it_runs_nothing(self) -> None:
        engine = self.scripted(CHOOSE, "I would use pow.", NONE, "About a thousand.")
        trace = agent.Trace()
        stream = await collect(agent.run("what is two to the tenth, worked out", history=[],
                                         trace=trace, engine=engine))
        self.assertEqual(self.fake.programs, [])
        self.assertIs(trace.steps[1].ok, False)
        self.assertEqual(support.step(stream, "code-1", "failed")["label"], "No code written")
        self.assertEqual(trace.calls[0][1].error, "no program")

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


if __name__ == "__main__":
    unittest.main()
