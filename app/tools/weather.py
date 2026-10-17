"""weather: open-meteo's geocoding, then its forecast. Keyless."""

from __future__ import annotations

from .base import Arg, Tool, ToolResult, register
from .client import api_client


# WMO weather interpretation codes. `icon` names a shape the weather card draws in SVG —
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

    async with api_client() as client:
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
    args=(Arg("location", "string", True, "city or place name", max_len=60),),
    run=_weather,
    card="weather",
    network=True,
    example="/weather Paris",
    fresh_for=3 * 3600,
))
