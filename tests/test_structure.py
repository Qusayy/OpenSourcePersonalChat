"""How the server's code is laid out, held as tests.

* Only ``app/store`` reaches into the database module's own state.
* The agent and the store never import the engine's internals.
* Every test module loads. A test module skips itself when the app's
  requirements are missing, and the same guard would hide a module that
  failed to import for any other reason, with every one of its tests.

Needs the app's own requirements; without them the whole module is skipped.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

try:
    import fastapi  # noqa: F401
except ImportError as exc:  # pragma: no cover - only without the requirements
    raise unittest.SkipTest(f"app requirements are not installed: {exc}")

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def modules(folder: Path):
    for path in sorted(folder.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path, ast.parse(path.read_text(encoding="utf-8"))


def imported(tree: ast.AST, path: Path) -> set[str]:
    """Every module a file imports, as a dotted name from the repository."""
    package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    if path.name == "__init__.py":
        package = ".".join(path.relative_to(ROOT).parts[:-1])
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            out.add(module)
            out.update(f"{module}.{alias.name}" for alias in node.names)
    return out


class LayoutTests(unittest.TestCase):
    def test_only_the_store_touches_the_database_modules_own_state(self) -> None:
        for path, tree in modules(APP):
            if path.parent.name == "store":
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "db" and node.attr.startswith("_")):
                    self.fail(f"{path.relative_to(ROOT)}:{node.lineno} reads db.{node.attr}")

    def test_the_agent_and_the_store_do_not_import_the_engines_internals(self) -> None:
        for folder in ("agent", "store"):
            for path, tree in modules(APP / folder):
                with self.subTest(path=path.name):
                    self.assertNotIn("app.llm.core", imported(tree, path))

    def test_the_store_imports_nothing_above_it(self) -> None:
        for path, tree in modules(APP / "store"):
            above = {m for m in imported(tree, path)
                     if m.startswith(("app.agent", "app.api", "app.tools", "app.llm"))}
            self.assertEqual(above, set(), path.name)


class LoadingTests(unittest.TestCase):
    def test_every_test_module_loads(self) -> None:
        for path in sorted(Path(__file__).parent.glob("test_*.py")):
            with self.subTest(module=path.stem):
                try:
                    importlib.import_module(f"tests.{path.stem}")
                except unittest.SkipTest as skipped:
                    self.fail(f"{path.stem} skipped itself: {skipped}")


if __name__ == "__main__":
    unittest.main()
