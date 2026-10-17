"""A conversation's project: its files as they stand, one file at any
version, and the whole of it as a zip to download."""

from __future__ import annotations

import io
import re
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..projects import paths
from ..store import conversations, projects

router = APIRouter()


def _conversation(cid: str) -> dict:
    head = conversations.head(cid)
    if head is None:
        raise HTTPException(404, "No such conversation")
    return head


def folder_name(title: str) -> str:
    """The folder a project unzips into, from its conversation's title."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:40].strip("-")
    return slug or "project"


@router.get("/api/conversations/{cid}/project")
async def api_project(cid: str):
    """The project as it stands: every file's newest version, with its content."""
    _conversation(cid)
    return {"files": list(projects.latest(cid).values())}


@router.get("/api/conversations/{cid}/project/files/{path:path}")
async def api_project_file(cid: str, path: str, version: int | None = None):
    _conversation(cid)
    clean = paths.safe(path)
    found = projects.file(cid, clean, version) if clean else None
    if found is None:
        raise HTTPException(404, "No such file")
    return found


@router.get("/api/conversations/{cid}/project.zip")
async def api_project_zip(cid: str):
    """Every file's newest version, in one folder named for the conversation."""
    head = _conversation(cid)
    files = projects.latest(cid)
    if not files:
        raise HTTPException(404, "This conversation has no project")
    folder = folder_name(head.get("title") or "")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, f in files.items():
            archive.writestr(f"{folder}/{path}", f["content"])
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{folder}.zip"'},
    )
