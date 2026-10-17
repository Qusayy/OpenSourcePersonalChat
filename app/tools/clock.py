"""clock: the time now, in a named zone or city. Needs no network."""

from __future__ import annotations

from datetime import datetime, timezone

from .base import Arg, Tool, ToolResult, register


COMMON_ZONES = ["UTC", "Europe/London", "Europe/Paris", "America/New_York", "Asia/Tokyo"]


# Cities whose IANA zone is not named after them.
CITY_ZONES = {
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "beijing": "Asia/Shanghai", "washington": "America/New_York",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "geneva": "Europe/Zurich", "boston": "America/New_York", "miami": "America/New_York",
    "philadelphia": "America/New_York", "seattle": "America/Los_Angeles",
    "austin": "America/Chicago", "dallas": "America/Chicago", "houston": "America/Chicago",
    "uk": "Europe/London", "england": "Europe/London", "britain": "Europe/London",
    "france": "Europe/Paris", "germany": "Europe/Berlin", "japan": "Asia/Tokyo",
    "india": "Asia/Kolkata", "china": "Asia/Shanghai", "morocco": "Africa/Casablanca",
}


def _resolve_zone(name: str):
    """Accept 'Europe/Paris', but also 'Tokyo', 'new york' and 'Japan'.

    People do not type IANA identifiers, and refusing them would make the tool
    useless for the query that triggers it most often.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

    raw = (name or "UTC").strip()
    for candidate in (raw, raw.replace(" ", "_")):
        try:
            return ZoneInfo(candidate), candidate, None
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass

    target = raw.replace("_", " ").lower()
    if target in CITY_ZONES:
        return ZoneInfo(CITY_ZONES[target]), CITY_ZONES[target], None

    # Match on the last path segment: "tokyo" -> "Asia/Tokyo".
    slug = target.replace(" ", "_")
    try:
        for zone in available_timezones():
            if zone.split("/")[-1].lower() == slug:
                return ZoneInfo(zone), zone, None
    except ZoneInfoNotFoundError:
        pass

    return timezone.utc, "UTC", f"Unknown timezone {raw!r} — showing UTC."


async def _clock(timezone_name: str = "UTC") -> ToolResult:
    try:
        from zoneinfo import ZoneInfo  # noqa: F401 — probe for the stdlib module
    except ImportError:  # pragma: no cover - stdlib since 3.9
        return ToolResult(
            False,
            "This server has no timezone database installed, so only UTC is "
            "available.",
            error="no timezone data",
        )

    tz, name, note = _resolve_zone(timezone_name)

    now = datetime.now(tz)
    others = []
    for zone in COMMON_ZONES:
        try:
            others.append({"zone": zone, "time": datetime.now(ZoneInfo(zone)).strftime("%H:%M")})
        except Exception:  # noqa: BLE001
            continue

    offset = now.utcoffset()
    offset_h = round(offset.total_seconds() / 3600, 2) if offset else 0
    return ToolResult(
        True,
        f"{now.strftime('%A %d %B %Y, %H:%M')} in {name}."
        + (f" ({note})" if note else ""),
        card="clock",
        data={
            "zone": name,
            "iso": now.isoformat(timespec="seconds"),
            "time": now.strftime("%H:%M"),
            "seconds": now.second,
            "date": now.strftime("%A %d %B %Y"),
            "offset_hours": offset_h,
            "others": others,
            "note": note,
        },
    )


register(Tool(
    name="clock",
    label="Clock",
    glyph="◷",
    description="Current date and time, optionally in a named timezone.",
    args=(Arg("timezone_name", "string", False, "IANA zone, e.g. Europe/Paris",
              default="UTC", max_len=40),),
    run=_clock,
    card="clock",
    example="/clock Asia/Tokyo",
    fresh_for=10 * 60,
))
