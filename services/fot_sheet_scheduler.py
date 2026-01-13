from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

from scripts.fill_fot_sheet import main as fill_fot_sheet_main
from fin_tab.sync_salary_from_sheet import sync_salary_from_sheet

logger = logging.getLogger(__name__)
_RUN_HOUR = 7
_SYNC_LOCK = asyncio.Lock()


def _next_run(ts: datetime) -> datetime:
    today_target = datetime.combine(ts.date(), time(hour=_RUN_HOUR, minute=0))
    if ts < today_target:
        return today_target
    return datetime.combine(ts.date() + timedelta(days=1), time(hour=_RUN_HOUR, minute=0))


async def _run_once() -> None:
    async with _SYNC_LOCK:
        start = datetime.now()
        logger.info("📊 Старт ежедневного заполнения ФОТ-листа")
        try:
            await fill_fot_sheet_main()
            await sync_salary_from_sheet()
            duration = (datetime.now() - start).total_seconds()
            logger.info("✅ ФОТ-лист заполнен и зарплаты отправлены за %.1f c", duration)
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Ошибка при заполнении ФОТ-листа: %s", exc)


async def run_daily_fot_fill(run_immediately: bool = False) -> None:
    """Запускает заполнение ФОТ-листа каждый день в 07:00."""
    if run_immediately:
        await _run_once()
    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info(
            "⏳ Следующее заполнение ФОТ-листа запланировано на %s (через %.1f мин)",
            next_time.strftime("%d.%m %H:%M"),
            wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        await _run_once()
