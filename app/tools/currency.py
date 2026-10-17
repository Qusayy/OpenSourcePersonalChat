"""currency: the ECB's reference rates, through Frankfurter. Keyless."""

from __future__ import annotations

from .base import Arg, Tool, ToolResult, register
from .client import api_client


# The currencies the European Central Bank publishes a reference rate for.
# A model choosing this tool can write only one of these codes.
CURRENCY_CODES = (
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD",
    "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW", "MXN", "MYR", "NOK", "NZD",
    "PHP", "PLN", "RON", "RUB", "SEK", "SGD", "THB", "TRY", "USD", "ZAR",
)


def _currency_code(text: str) -> str:
    return (text or "").strip().upper()


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

    async with api_client() as client:
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
        Arg("from_currency", "string", True, "ISO code, e.g. EUR",
            choices=CURRENCY_CODES, canon=_currency_code),
        Arg("to_currency", "string", True, "ISO code, e.g. USD",
            choices=CURRENCY_CODES, canon=_currency_code),
    ),
    run=_currency,
    card="currency",
    network=True,
    example="/currency 100 EUR USD",
    fresh_for=24 * 3600,
))
