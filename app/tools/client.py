"""The HTTP client the keyless network tools share.

Every endpoint they call is free and needs no API key or signup — a deliberate
constraint, so the repository stays clone-and-run rather than
clone-then-go-register-for-three-services.
"""

from __future__ import annotations

import httpx

from ..config import settings


def api_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.tool_timeout),
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        follow_redirects=True,
        max_redirects=3,
    )
