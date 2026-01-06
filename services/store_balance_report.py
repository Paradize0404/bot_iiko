from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from scripts.export_store_balances import (
    AMOUNT_FIELD_CANDIDATES,
    MONEY_FIELD_CANDIDATES,
    _extract_first,
    _extract_store_name,
    fetch_store_balances,
)


TARGET_STORES = (
    "Бар Пиццерия",
    "Кухня Пиццерия",
)


def _to_human(date_iso: str) -> str:
    return datetime.strptime(date_iso, "%Y-%m-%d").strftime("%d.%m.%Y")


def _fmt_currency(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")




async def _collect_store_totals(
    date_iso: str,
    store_filter: Iterable[str] = TARGET_STORES,
) -> dict[str, Decimal]:
    human_date = _to_human(date_iso)
    rows = await fetch_store_balances(date_to=human_date)

    totals: dict[str, Decimal] = {store: Decimal(0) for store in store_filter}
    for row in rows:
        store_name = _extract_store_name(row)
        if store_name not in store_filter:
            continue

        amount_val = _extract_first(row, AMOUNT_FIELD_CANDIDATES, 0)
        money_val = _extract_first(row, MONEY_FIELD_CANDIDATES, 0)

        try:
            money = Decimal(str(money_val))
        except Exception:
            continue

        try:
            amount = Decimal(str(amount_val))
        except Exception:
            amount = Decimal(0)

        if amount == 0 and money == 0:
            continue

        totals[store_name] = totals.get(store_name, Decimal(0)) + money

    return totals


async def build_store_balance_text(date_from: str, date_to: str) -> str:
    date_from_h = _to_human(date_from)
    date_to_h = _to_human(date_to)

    start_totals = await _collect_store_totals(date_from)
    end_totals = await _collect_store_totals(date_to)

    start_sum = sum(start_totals.values())
    end_sum = sum(end_totals.values())
    delta = end_sum - start_sum

    def line(label: str, value: Decimal) -> str:
        return f"- {label}: {_fmt_currency(value)} ₽"

    lines = [
        "🏷 *Остатки бар/кухня*",
        f"Период: {date_from_h} — {date_to_h}",
        "",
        "*На начало периода:*",
    ]

    for store in TARGET_STORES:
        lines.append(line(store, start_totals.get(store, Decimal(0))))
    lines.append(f"Итого: {_fmt_currency(start_sum)} ₽")

    lines.extend([
        "",
        "*На конец периода:*",
    ])
    for store in TARGET_STORES:
        lines.append(line(store, end_totals.get(store, Decimal(0))))
    lines.append(f"Итого: {_fmt_currency(end_sum)} ₽")

    lines.extend([
        "",
        f"Δ Итог: {_fmt_currency(delta)} ₽",
    ])

    return "\n".join(lines)
