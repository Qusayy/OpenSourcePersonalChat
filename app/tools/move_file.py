"""move_file and delete_file: how the model reorganises the project.

A project grows by files, and the first place a file is written is not
always where it belongs. ``move_file`` moves or renames one file, or a whole
folder; ``delete_file`` removes one, or a whole folder. Either changes all of
what it names or none of it, and neither ever writes over a file that is
there. The conversation is the call's own (``CALL``), never an argument the
model gives.
"""

from __future__ import annotations

from ..projects import paths
from ..store import projects
from .base import CALL, Arg, Tool, ToolResult, register


def _have(cid: str) -> str:
    return ", ".join(f["path"] for f in projects.manifest(cid)) or "no files yet"


def _files(n: int) -> str:
    return f"{n} file{'' if n == 1 else 's'}"


def _named(cid: str, raw: str) -> tuple[str, str | None, dict[str, dict]]:
    """What a path names in the project: ("file", path, …), ("folder", path,
    the files below it), or ("", None, {}) for nothing there."""
    files = projects.latest(cid)
    as_file = paths.safe(raw)
    if as_file is not None and as_file in files:
        return "file", as_file, {as_file: files[as_file]}
    as_folder = paths.safe_folder(raw)
    if as_folder is not None:
        below = {p: f for p, f in files.items() if p.startswith(as_folder + "/")}
        if below:
            return "folder", as_folder, below
    return "", None, {}


async def _move_file(source: str, destination: str) -> ToolResult:
    call = CALL.get()
    cid = call.conversation_id
    if not cid:
        return ToolResult(False, "There is no conversation whose files could be moved.",
                          error="no conversation")
    kind, src, moving = _named(cid, source)
    if not kind:
        return ToolResult(False, f"There is no file or folder {source!r} in this project; it has: "
                          f"{_have(cid)}.", error="no such file")
    raw = (destination or "").strip()
    into = paths.safe_folder(raw)
    if kind == "file":
        # Into a folder: one named with a slash at its end, or one that is
        # there already. Otherwise the destination is the file's new path.
        name = src.rsplit("/", 1)[-1]
        folder_there = into is not None and bool(projects.under(cid, into))
        if raw.endswith("/") or (folder_there and paths.safe(raw) not in moving):
            target = paths.safe(f"{into}/{name}") if into else None
        else:
            target = paths.safe(raw)
        if target is None:
            return ToolResult(False, f"{destination!r} cannot be where the file goes; give a path "
                              "such as app/main.py, or a folder ending in /.", error="bad path")
        pairs = [(src, target)]
    else:
        if into is None:
            return ToolResult(False, f"{destination!r} cannot be a folder's path; give one such as "
                              "src/app.", error="bad path")
        if into == src or into.startswith(src + "/"):
            return ToolResult(False, f"{src}/ cannot be moved into itself.", error="bad path")
        pairs = [(p, paths.safe(into + p[len(src):])) for p in moving]
        if any(target is None for _, target in pairs):
            return ToolResult(False, f"Some files of {src}/ would be too deep under {into}/.",
                              error="bad path")
    if all(a == b for a, b in pairs):
        return ToolResult(False, f"{src} is there already, so nothing was moved.", error="unchanged")
    kept = projects.move(cid, pairs, run_id=call.run_id)
    if not kept["kept"]:
        return ToolResult(False, f"Nothing was moved: {kept['why']}.", error="not kept")
    moved = kept["moved"]
    if kind == "file":
        said = f"Moved {src} to {moved[0]['to']}."
    else:
        said = f"Moved {_files(len(moved))} from {src}/ to {into}/."
    return ToolResult(True, said, card="file", data={"action": "move", "moves": moved})


async def _delete_file(path: str) -> ToolResult:
    call = CALL.get()
    cid = call.conversation_id
    if not cid:
        return ToolResult(False, "There is no conversation whose files could be deleted.",
                          error="no conversation")
    kind, named, doomed = _named(cid, path)
    if not kind:
        return ToolResult(False, f"There is no file or folder {path!r} in this project; it has: "
                          f"{_have(cid)}.", error="no such file")
    kept = projects.remove(cid, doomed, run_id=call.run_id)
    if not kept["kept"]:
        return ToolResult(False, f"Nothing was deleted: {kept['why']}.", error="not kept")
    removed = [r["path"] for r in kept["removed"]]
    said = f"Deleted {named}." if kind == "file" else f"Deleted {_files(len(removed))} in {named}/."
    return ToolResult(True, said, card="file", data={"action": "delete", "paths": removed})


register(Tool(
    name="move_file",
    label="Move file",
    glyph="⇢",
    description=(
        "Move or rename a file of this conversation's project, or a whole folder. source is "
        "the file or folder; destination is its new path, or a folder ending in / to move it "
        "into. Nothing is ever moved onto a file that is there."
    ),
    args=(
        Arg("source", "string", True, "the file or folder to move, such as main.py or app", max_len=160),
        Arg("destination", "string", True, "its new path, or a folder ending in /", max_len=160),
    ),
    run=_move_file,
    card="file",
    example="",
    project=True,
    changes=True,
))

register(Tool(
    name="delete_file",
    label="Delete file",
    glyph="⌫",
    description="Delete a file of this conversation's project, or a whole folder.",
    args=(Arg("path", "string", True, "the file or folder to delete", max_len=160),),
    run=_delete_file,
    card="file",
    example="",
    project=True,
    changes=True,
))
