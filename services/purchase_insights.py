from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from services.purchase_summary import PurchaseSummary, get_purchase_summary
from services.revenue_report import calculate_revenue, get_revenue_report
from services.writeoff_documents import get_segment_writeoff_totals

logger = logging.getLogger(__name__)
PURCHASE_ACCOUNT_NAMES = (
    "Бар Пиццерия",
    "Кухня Пиццерия",
    "ТМЦ Пиццерия",
    "Хоз. товары Пиццерия",
)
PURCHASE_ACCOUNT_TYPES = ("INVENTORY_ASSETS",)


async def fetch_purchase_summary(
    date_from: str,
    date_to: str,
    *,
    timeout: float = 90.0,
) -> PurchaseSummary:
    return await get_purchase_summary(
        date_from,
        date_to,
        store_filter=PURCHASE_ACCOUNT_NAMES,
        account_type_filter=PURCHASE_ACCOUNT_TYPES,
        timeout=timeout,
    )


async def calculate_purchase_metrics(
    summary: PurchaseSummary,
    date_from: str,
    date_to: str,
    *,
    revenue_rows: list[dict[str, Any]] | None = None,
    revenue_data: dict[str, Any] | None = None,
) -> dict[str, dict[str, float]] | None:
    kitchen_purchase = summary.store_totals.get("Кухня Пиццерия")
    bar_purchase = summary.store_totals.get("Бар Пиццерия")
    supplies_purchase = summary.store_totals.get("Хоз. товары Пиццерия")
    tmc_purchase = summary.store_totals.get("ТМЦ Пиццерия")
    if not kitchen_purchase and not bar_purchase:
        if not supplies_purchase and not tmc_purchase:
            return None

    def _to_float(value: Decimal | float | int | None) -> float:
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    if revenue_data is None:
        if revenue_rows is None:
            revenue_rows = await get_revenue_report(date_from, date_to)
        revenue_data = await calculate_revenue(revenue_rows, date_from, date_to)

    bar_revenue = float(revenue_data.get("bar_revenue", 0.0))
    kitchen_revenue = float(revenue_data.get("kitchen_revenue", 0.0))
    delivery_revenue = float(revenue_data.get("delivery_revenue", 0.0))
    writeoff_revenue = float(revenue_data.get("writeoff_revenue", 0.0))
    total_revenue = bar_revenue + kitchen_revenue + delivery_revenue
    total_base = total_revenue + writeoff_revenue

    kitchen_base = kitchen_revenue + delivery_revenue + writeoff_revenue
    bar_base = bar_revenue

    share_result: dict[str, float] = {}
    kitchen_purchase_float = _to_float(kitchen_purchase)
    bar_purchase_float = _to_float(bar_purchase)
    supplies_purchase_float = _to_float(supplies_purchase)
    tmc_purchase_float = _to_float(tmc_purchase)
    total_purchase = kitchen_purchase_float + bar_purchase_float + supplies_purchase_float + tmc_purchase_float

    if kitchen_purchase_float:
        share_result["kitchen_purchase"] = kitchen_purchase_float
        share_result["kitchen_base"] = kitchen_base
        share_result["kitchen_percent"] = (kitchen_purchase_float / kitchen_base * 100.0) if kitchen_base else None
    if bar_purchase_float:
        share_result["bar_purchase"] = bar_purchase_float
        share_result["bar_base"] = bar_base
        share_result["bar_percent"] = (bar_purchase_float / bar_base * 100.0) if bar_base else None
    if supplies_purchase_float:
        share_result["supplies_purchase"] = supplies_purchase_float
        share_result["supplies_base"] = total_base
        share_result["supplies_percent"] = (supplies_purchase_float / total_base * 100.0) if total_base else None
    if tmc_purchase_float:
        share_result["tmc_purchase"] = tmc_purchase_float
        share_result["tmc_base"] = total_base
        share_result["tmc_percent"] = (tmc_purchase_float / total_base * 100.0) if total_base else None
    if total_purchase:
        share_result["total_purchase"] = total_purchase
        share_result["total_base"] = total_base
        share_result["total_percent"] = (total_purchase / total_base * 100.0) if total_base else None

    if not share_result:
        return None

    deviation_info = await _calculate_purchase_deviation(
        revenue_data,
        date_from,
        date_to,
        share_result,
    )

    return {"share": share_result, "deviation": deviation_info} if (share_result or deviation_info) else None


async def _calculate_purchase_deviation(
    revenue_data: dict,
    date_from: str,
    date_to: str,
    share_info: dict[str, float],
) -> dict[str, dict[str, float]] | None:
    try:
        segment_writeoffs = await get_segment_writeoff_totals(date_from, date_to)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось получить списания для расчёта отклонений: %s", exc)
        return None

    def _safe_float(value) -> float:
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    segment_writeoffs = {
        key: _safe_float(value)
        for key, value in (segment_writeoffs or {}).items()
    }
    writeoff_total_cost = _safe_float(revenue_data.get("writeoff_cost"))

    kitchen_cost = (
        _safe_float(revenue_data.get("kitchen_total_cost"))
        + writeoff_total_cost
        + segment_writeoffs.get("kitchen", 0.0)
    )
    bar_cost = _safe_float(revenue_data.get("bar_cost")) + segment_writeoffs.get("bar", 0.0)

    result: dict[str, dict[str, float]] = {}

    def _register_segment(key: str, cost_value: float) -> None:
        base = _safe_float(share_info.get(f"{key}_base"))
        purchase_percent = share_info.get(f"{key}_percent")
        if not base or purchase_percent is None:
            return
        cost_percent = (cost_value / base * 100.0) if base else None
        if cost_percent is None:
            return
        result[key] = {
            "purchase_percent": purchase_percent,
            "cost_percent": cost_percent,
            "deviation": purchase_percent - cost_percent,
            "cost_value": cost_value,
        }

    _register_segment("kitchen", kitchen_cost)
    _register_segment("bar", bar_cost)

    return result or None


__all__ = [
    "PURCHASE_ACCOUNT_NAMES",
    "PURCHASE_ACCOUNT_TYPES",
    "fetch_purchase_summary",
    "calculate_purchase_metrics",
]
