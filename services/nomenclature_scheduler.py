from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

from db.nomenclature_db import (
    fetch_nomenclature,
    init_db,
    sync_nomenclature,
    sync_store_balances,
)

logger = logging.getLogger(__name__)
_RUN_HOURS = tuple(range(8, 19, 4))  # 8, 12, 16
_WINDOW_START = time(8, 0)
_WINDOW_END = time(18, 0)
_SYNC_LOCK = asyncio.Lock()


async def sync_nomenclature_and_balances() -> int:
    """Refresh nomenclature and balance tables, returns number of items."""
    async with _SYNC_LOCK:
        logger.info("🔁 Старт автообновления номенклатуры")
        await init_db()
        data = await fetch_nomenclature()
        await sync_nomenclature(data)
        await sync_store_balances(data)
        total = len(data)
        logger.info("✅ Номенклатура обновлена (%d позиций)", total)
        return total


def _is_within_window(ts: datetime) -> bool:
    current = ts.time()
    return _WINDOW_START <= current <= _WINDOW_END


def _next_run_time(ts: datetime) -> datetime:
    current_day = ts.date()
    for hour in _RUN_HOURS:
        candidate = datetime.combine(current_day, time(hour=hour))
        if candidate > ts:
            return candidate
    next_day = current_day + timedelta(days=1)
    return datetime.combine(next_day, time(hour=_RUN_HOURS[0]))


async def run_periodic_nomenclature_sync() -> None:
    """Schedule automatic sync every 4 hours between 08:00 and 18:00."""
    logger.info(
        "🕘 Автообновление номенклатуры: каждые 4 часа (%s) с %s до %s",
        ", ".join(str(h) for h in _RUN_HOURS),
        _WINDOW_START.strftime("%H:%M"),
        _WINDOW_END.strftime("%H:%M"),
    )
    first_run_done = False
    while True:
        now = datetime.now()
        if not first_run_done and _is_within_window(now):
            await _safe_sync()
            first_run_done = True
            continue

        next_run: datetime = _next_run_time(now)
        wait_seconds = max(1.0, (next_run - now).total_seconds())
        logger.info(
            "⏳ Следующее автообновление номенклатуры в %s (через %.1f мин)",
            next_run.strftime("%d.%m %H:%M"),
            wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        await _safe_sync()
        first_run_done = True


async def _safe_sync() -> None:
    try:
        await sync_nomenclature_and_balances()
    except Exception as exc:  # noqa: BLE001
        logger.exception("❌ Ошибка автообновления номенклатуры: %s", exc)