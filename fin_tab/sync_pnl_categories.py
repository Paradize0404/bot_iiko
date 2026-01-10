"""Синхронизация статей ПиУ FinTablo в локальную БД с расписанием."""
import asyncio
import logging
from datetime import datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from fin_tab.fin_tab_pnl_db import init_fin_tab_pnl_table, sync_fin_tab_pnl_categories

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

RUN_HOUR = 3  # 03:00 по умолчанию


def _next_run(ts: datetime) -> datetime:
    today_target = datetime.combine(ts.date(), time(hour=RUN_HOUR, minute=0))
    if ts < today_target:
        return today_target
    return datetime.combine(ts.date() + timedelta(days=1), time(hour=RUN_HOUR, minute=0))


async def sync_once() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    await init_fin_tab_pnl_table()
    async with FinTabloClient() as cli:
        items = await cli.list_pnl_categories()
    total = await sync_fin_tab_pnl_categories(items)
    logger.info("✅ Синхронизировано статей ПиУ: %d", total)


async def run_daily_sync(run_immediately: bool = False) -> None:
    if run_immediately:
        await sync_once()
    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info(
            "⏳ Следующая синхронизация статей ПиУ: %s (через %.1f мин)",
            next_time.strftime("%d.%m %H:%M"),
            wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        try:
            await sync_once()
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Ошибка синхронизации статей ПиУ: %s", exc)


async def main() -> int:
    await sync_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
