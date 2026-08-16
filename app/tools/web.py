"""Keyless network tools: Wikipedia, weather, currency.

Every endpoint here is free and needs no API key or signup — a deliberate
constraint, so the repository stays clone-and-run rather than
clone-then-go-register-for-three-services.
"""

from __future__ import annotations

import httpx

from ..config import settings
from .base import Arg, Tool, ToolResult, register


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.tool_timeout),
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        follow_redirects=True,
        max_redirects=3,
    )


# -------------------------------------------------------------- wikipedia ---


async def _wikipedia(query: str) -> ToolResult:
    q = (query or "").strip()
    if not q:
        return ToolResult(False, "Nothing to look up.", error="empty")

    async with _client() as client:
        search = await client.get(
            "https://en.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": q, "limit": 1},
        )
        if search.status_code == 403:
            # Wikimedia blocks anonymous-looking clients. The visitor cannot
            # act on that; the operator can, so the fix lives in the step label
            # and the logs rather than in the sentence they read.
            return ToolResult(
                False,
                "Wikipedia turned this server away. Lookups are unavailable "
                "until the server operator sets a contact address.",
                error="wikipedia 403 — set AURORA_USER_AGENT with a contact URL",
            )
        search.raise_for_status()
        pages = (search.json() or {}).get("pages") or []
        if not pages:
            return ToolResult(
                False,
                f"Wikipedia has no article for {q!r}. Try a different spelling "
                "or a broader term.",
                error="no article",
            )

        key = pages[0].get("key") or pages[0].get("title", "").replace(" ", "_")
        summary = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{key}")
        summary.raise_for_status()
        page = summary.json()

    extract = (page.get("extract") or "").strip()
    title = page.get("title") or q
    url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page") or (
        f"https://en.wikipedia.org/wiki/{key}"
    )
    # The model gets a trimmed extract; the card keeps the whole thing.
    short = extract if len(extract) <= 700 else extract[:700].rsplit(" ", 1)[0] + "…"

    return ToolResult(
        True,
        f"Wikipedia — {title}: {short}",
        card="wikipedia",
        data={
            "title": title,
            "description": page.get("description") or "",
            "extract": extract,
            "url": url,
            "lang": page.get("lang", "en"),
        },
    )


register(Tool(
    name="wikipedia",
    label="Wikipedia",
    glyph="◍",
    description="Look up a factual summary of a person, place, thing or event.",
    args=(Arg("query", "string", True, "what to look up"),),
    run=_wikipedia,
    card="wikipedia",
    network=True,
    example="/wiki Ada Lovelace",
))


# ---------------------------------------------------------------- weather ---

# WMO weather interpretation codes. `icon` names a shape cards.js draws in SVG —
# nothing is fetched from a weather provider's CDN.
WMO = {
    0: ("Clear sky", "sun"), 1: ("Mainly clear", "sun"), 2: ("Partly cloudy", "cloud-sun"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"), 57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"), 67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"), 80: ("Rain showers", "rain"), 81: ("Rain showers", "rain"),
    82: ("Violent rain showers", "rain"), 85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"), 95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm with hail", "storm"), 99: ("Thunderstorm with hail", "storm"),
}


async def _weather(location: str) -> ToolResult:
    place = (location or "").strip()
    if not place:
        return ToolResult(False, "No location given.", error="empty")

    async with _client() as client:
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place, "count": 1, "language": "en", "format": "json"},
        )
        geo.raise_for_status()
        results = (geo.json() or {}).get("results") or []
        if not results:
            return ToolResult(
                False,
                f"No place called {place!r} was found. Try adding a country, "
                "like “Amman, Jordan”.",
                error="no such place",
            )
        spot = results[0]

        forecast = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": spot["latitude"],
                "longitude": spot["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                           "precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "hourly": "temperature_2m",
                "forecast_days": 5,
                "timezone": "auto",
            },
        )
        forecast.raise_for_status()
        data = forecast.json()

    current = data.get("current") or {}
    code = int(current.get("weather_code", 0))
    condition, icon = WMO.get(code, ("Unknown", "cloud"))
    name = spot.get("name", place)
    country = spot.get("country", "")

    daily = data.get("daily") or {}
    days = []
    for i, day in enumerate(daily.get("time", [])[:5]):
        d_code = int((daily.get("weather_code") or [0])[i])
        days.append({
            "date": day,
            "max": (daily.get("temperature_2m_max") or [None])[i],
            "min": (daily.get("temperature_2m_min") or [None])[i],
            "condition": WMO.get(d_code, ("Unknown", "cloud"))[0],
            "icon": WMO.get(d_code, ("Unknown", "cloud"))[1],
        })

    temp = current.get("temperature_2m")
    return ToolResult(
        True,
        f"{name}, {country}: {condition}, {temp}°C "
        f"(feels {current.get('apparent_temperature')}°C), "
        f"wind {current.get('wind_speed_10m')} km/h.",
        card="weather",
        data={
            "place": name,
            "country": country,
            "condition": condition,
            "icon": icon,
            "temperature": temp,
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "precipitation": current.get("precipitation"),
            "wind": current.get("wind_speed_10m"),
            "days": days,
            # 24h of hourly temperatures drives the sparkline on the card.
            "hourly": (data.get("hourly", {}).get("temperature_2m") or [])[:24],
            "units": "°C",
        },
    )


register(Tool(
    name="weather",
    label="Weather",
    glyph="☁",
    description="Current conditions and a five-day forecast for a place.",
    args=(Arg("location", "string", True, "city or place name"),),
    run=_weather,
    card="weather",
    network=True,
    example="/weather Paris",
))


# --------------------------------------------------------------- currency ---


async def _currency(amount: float, from_currency: str, to_currency: str) -> ToolResult:
    src = (from_currency or "").strip().upper()[:3]
    dst = (to_currency or "").strip().upper()[:3]
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return ToolResult(False, "That amount is not a number.", error="bad amount")
    if not src or not dst:
        return ToolResult(
            False,
            "Give both currencies, like “100 EUR to USD”.",
            error="missing currency",
        )

    async with _client() as client:
        res = await client.get(
            "https://api.frankfurter.app/latest",
            params={"amount": amount, "from": src, "to": dst},
        )
        if res.status_code == 404:
            return ToolResult(
                False,
                f"No rate available for {src} to {dst}. Use three-letter codes, "
                "like EUR, USD or GBP.",
                error="unknown pair",
            )
        res.raise_for_status()
        payload = res.json()

    rates = payload.get("rates") or {}
    if dst not in rates:
        return ToolResult(
            False,
            f"No rate available for {src} to {dst} today.",
            error="no rate",
        )

    converted = rates[dst]
    rate = converted / amount if amount else 0
    return ToolResult(
        True,
        f"{amount:,.2f} {src} = {converted:,.2f} {dst} (rate {rate:,.4f}, "
        f"ECB {payload.get('date')}).",
        card="currency",
        data={
            "amount": amount, "from": src, "to": dst,
            "result": converted, "rate": rate, "date": payload.get("date"),
            "source": "European Central Bank via Frankfurter",
        },
    )


register(Tool(
    name="currency",
    label="Currency",
    glyph="⇌",
    description="Convert an amount between currencies at the current ECB rate.",
    args=(
        Arg("amount", "number", True, "how much"),
        Arg("from_currency", "string", True, "ISO code, e.g. EUR"),
        Arg("to_currency", "string", True, "ISO code, e.g. USD"),
    ),
    run=_currency,
    card="currency",
    network=True,
    example="/currency 100 EUR USD",
))
