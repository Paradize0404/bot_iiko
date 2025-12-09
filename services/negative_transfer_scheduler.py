from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

from scripts.create_negative_transfer import run_negative_transfer

logger = logging.getLogger(__name__)
_RUN_HOUR = 7
_SYNC_LOCK = asyncio.Lock()


def _next_run(ts: datetime) -> datetime:
    today_target = datetime.combine(ts.date(), time(hour=_RUN_HOUR))
    if ts < today_target:
        return today_target
    return datetime.combine(ts.date() + timedelta(days=1), time(hour=_RUN_HOUR))


async def _run_once() -> None:
    async with _SYNC_LOCK:
        try:
            start = datetime.now()
            logger.info("🚚 Старт авто-перемещения (синхронизируем номенклатуру перед запуском)")
            await run_negative_transfer(sync_before=True)
            duration = (datetime.now() - start).total_seconds()
            logger.info("✅ Авто-перемещение завершено за %.1f c", duration)
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Ошибка автоперемещения: %s", exc)


async def run_periodic_negative_transfer(run_immediately: bool = True) -> None:
    if run_immediately:
        await _run_once()
    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info("⏳ Следующее авто-перемещение запланировано на %s (%.1f мин)", next_time.strftime("%d.%m %H:%M"), wait_seconds / 60)
        await asyncio.sleep(wait_seconds)
        await _run_once()
