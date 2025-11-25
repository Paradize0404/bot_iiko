"""Сервисные функции для месячных планов по процентной себестоимости."""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime, date
from typing import Dict, List

from db.cost_plan_db import init_cost_plan_table, get_cost_plans_for_months

logger = logging.getLogger(__name__)
SEGMENTS = ("bar", "kitchen")


def _iter_months(start: date, end: date) -> List[date]:
    months: List[date] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _month_end(value: date) -> date:
    days_in_month = monthrange(value.year, value.month)[1]
    return date(value.year, value.month, days_in_month)


async def get_cost_plan_summary(date_from: str, date_to: str) -> Dict[str, object]:
    """Вернуть планы и агрегаты для заданного периода дат."""
    summary: Dict[str, object] = {
        "monthly": [],
        "aggregated": {"bar": None, "kitchen": None},
    }

    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось разобрать даты для планов: %s", exc)
        return summary

    if start > end:
        start, end = end, start

    months = _iter_months(start, end)
    if not months:
        return summary

    try:
        await init_cost_plan_table()
        plans_by_month = await get_cost_plans_for_months(months)
    except RuntimeError:
        logger.warning("Пул БД не инициализирован — планы себестоимости недоступны")
        return summary

    monthly_breakdown = []
    aggregated_values = {segment: 0.0 for segment in SEGMENTS}
    coverage = {segment: 0.0 for segment in SEGMENTS}

    for month_start in months:
        month_plan = plans_by_month.get(month_start, {})
        monthly_breakdown.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "bar": month_plan.get("bar"),
                "kitchen": month_plan.get("kitchen"),
            }
        )
        month_end = _month_end(month_start)
        coverage_start = max(start, month_start)
        coverage_end = min(end, month_end)
        days_in_period = (coverage_end - coverage_start).days + 1
        days_in_month = (month_end - month_start).days + 1
        share = days_in_period / days_in_month if days_in_month else 0.0

        for segment in SEGMENTS:
            value = month_plan.get(segment)
            if value is None:
                continue
            aggregated_values[segment] += float(value) * share
            coverage[segment] += share

    aggregated = {}
    for segment in SEGMENTS:
        if coverage[segment] > 0:
            aggregated[segment] = aggregated_values[segment] / coverage[segment]
        else:
            aggregated[segment] = None

    summary["monthly"] = monthly_breakdown
    summary["aggregated"] = aggregated
    summary["has_data"] = any(val is not None for val in aggregated.values())
    return summary
