"""Which names a project file may have, and how large a project may grow.

A file's name is the model's, written in its answer, and ends up as a path
inside a zip someone unpacks on their own machine. So it is held to the
plainest form that works everywhere: relative, forward slashes, no step up
out of the project, no name Windows will not create, nothing but letters,
digits, dots, dashes and underscores.
"""

from __future__ import annotations

import re

MAX_PATH = 160
MAX_DEPTH = 12
MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_PROJECT_BYTES = 2 * 1024 * 1024

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
# Names Windows reserves, with or without an extension.
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)
# A name with no extension that is still plainly a file.
BARE_NAMES = frozenset({
    "Makefile", "Dockerfile", "Procfile", "LICENSE", "README", "Gemfile", "Rakefile",
    "Vagrantfile", "Jenkinsfile", "Caddyfile",
})

LANGUAGES = {
    "py": "python", "js": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "tsx", "jsx": "jsx", "html": "html", "htm": "html",
    "css": "css", "scss": "scss", "json": "json", "md": "markdown", "markdown": "markdown",
    "yml": "yaml", "yaml": "yaml", "toml": "toml", "ini": "ini", "cfg": "ini", "sh": "bash",
    "bash": "bash", "ps1": "powershell", "sql": "sql", "rs": "rust", "go": "go",
    "java": "java", "kt": "kotlin", "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp",
    "cs": "csharp", "rb": "ruby", "php": "php", "swift": "swift", "lua": "lua",
    "txt": "text", "csv": "csv", "xml": "xml", "svg": "xml", "env": "text",
}


def safe(raw: str) -> str | None:
    """``raw`` as a project path, or None when it cannot be one."""
    path = (raw or "").strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or len(path) > MAX_PATH or path.startswith("/") or ":" in path:
        return None
    parts = path.split("/")
    if len(parts) > MAX_DEPTH:
        return None
    for part in parts:
        if part in ("", ".", "..") or not _SEGMENT.match(part) or _RESERVED.match(part):
            return None
        if part.endswith("."):
            return None
    name = parts[-1]
    if "." not in name.lstrip(".") and name not in BARE_NAMES and not name.startswith("."):
        return None
    return path


def safe_folder(raw: str) -> str | None:
    """``raw`` as a folder of a project, or None when it cannot be one: the
    same rules as a file's path, but its last part needs no extension. A
    slash at its end is left off; the project's root is no folder."""
    path = (raw or "").strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if not path or len(path) > MAX_PATH or path.startswith("/") or ":" in path:
        return None
    parts = path.split("/")
    if len(parts) > MAX_DEPTH - 1:
        return None
    for part in parts:
        if part in ("", ".", "..") or not _SEGMENT.match(part) or _RESERVED.match(part):
            return None
        if part.endswith("."):
            return None
    return path


def language(path: str, said: str = "") -> str:
    """The language a file is in: what its fence said, or what its name says."""
    if said:
        return said.lower()
    name = path.rsplit("/", 1)[-1]
    if name == "Dockerfile":
        return "dockerfile"
    if name == "Makefile":
        return "makefile"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return LANGUAGES.get(ext, "text")
