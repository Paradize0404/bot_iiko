"""Хранение месячных планов по процентной себестоимости (бар и кухня+доставка)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Dict

from utils.db_stores import get_pool

logger = logging.getLogger(__name__)

VALID_SEGMENTS = ("bar", "kitchen")


def _normalize_month(value: date) -> date:
    return value.replace(day=1)


def _normalize_segment(segment: str) -> str:
    normalized = segment.strip().lower()
    if normalized not in VALID_SEGMENTS:
        raise ValueError(f"Недопустимый сегмент плана: {segment}")
    return normalized


async def init_cost_plan_table() -> None:
    """Создать таблицу cost_plans, если её ещё нет."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_plans (
                period_month DATE NOT NULL,
                segment TEXT NOT NULL CHECK (segment IN ('bar', 'kitchen')),
                plan_value NUMERIC NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (period_month, segment)
            )
            """
        )
        logger.info("Таблица cost_plans инициализирована")


async def upsert_cost_plan(period_month: date, segment: str, plan_percent: float) -> None:
    month = _normalize_month(period_month)
    normalized_segment = _normalize_segment(segment)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO cost_plans (period_month, segment, plan_value)
            VALUES ($1, $2, $3)
            ON CONFLICT (period_month, segment)
            DO UPDATE SET plan_value = EXCLUDED.plan_value, updated_at = NOW()
            """,
            month,
            normalized_segment,
            plan_percent,
        )
        logger.info(
            "Обновлён план %s себестоимости: %s = %.2f%%",
            month.isoformat(),
            normalized_segment,
            plan_percent,
        )


async def get_cost_plan(period_month: date, segment: str) -> float | None:
    month = _normalize_month(period_month)
    normalized_segment = _normalize_segment(segment)
    pool = get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT plan_value
            FROM cost_plans
            WHERE period_month = $1 AND segment = $2
            """,
            month,
            normalized_segment,
        )
    return float(value) if value is not None else None


async def get_cost_plans_for_months(months: Iterable[date]) -> Dict[date, Dict[str, float]]:
    unique_months = []
    seen = set()
    for month in months:
        normalized = _normalize_month(month)
        if normalized not in seen:
            unique_months.append(normalized)
            seen.add(normalized)

    if not unique_months:
        return {}

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT period_month, segment, plan_value
            FROM cost_plans
            WHERE period_month = ANY($1::date[])
            """,
            unique_months,
        )

    result: Dict[date, Dict[str, float]] = {month: {} for month in unique_months}
    for row in rows:
        month = row["period_month"]
        segment = row["segment"]
        value = float(row["plan_value"])
        result.setdefault(month, {})[segment] = value

    return result


async def get_month_plan_snapshot(period_month: date) -> Dict[str, float | None]:
    """Вернуть пару значений плана (бар/кухня) для конкретного месяца."""
    data = {"bar": None, "kitchen": None}
    pool = get_pool()
    month = _normalize_month(period_month)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT segment, plan_value
            FROM cost_plans
            WHERE period_month = $1
            """,
            month,
        )
    for row in rows:
        data[row["segment"]] = float(row["plan_value"])
    return data
