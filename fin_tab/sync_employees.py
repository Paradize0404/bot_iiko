"""Синхронизация сотрудников FinTablo в локальную БД."""
import asyncio
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import List, Tuple

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from fin_tab.fin_tab_employees_db import init_fin_tab_employee_table, sync_fin_tab_employees

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

RUN_HOUR = 3  # 03:00
RUN_MINUTE = 20  # после направлений


def _next_run(ts: datetime) -> datetime:
    today_target = datetime.combine(ts.date(), time(hour=RUN_HOUR, minute=RUN_MINUTE))
    if ts < today_target:
        return today_target
    return datetime.combine(ts.date() + timedelta(days=1), time(hour=RUN_HOUR, minute=RUN_MINUTE))


def _normalize_employees(items: List[dict]) -> List[dict]:
    normalized = []
    for it in items:
        positions = it.get("positions") or []
        primary = positions[0] if positions else {}
        name = (it.get("name") or "").strip()
        if not name and not primary.get("post"):
            continue
        normalized.append(
            {
                "id": it.get("id"),
                "name": name,
                "department": primary.get("department"),
                "post": primary.get("post"),
                "direction_id": primary.get("directionId"),
                "type": primary.get("type"),
                "percentage": primary.get("percentage"),
            }
        )
    return normalized


async def sync_once() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    async with FinTabloClient() as cli:
        items = await cli.list_employees()
    employees = _normalize_employees(items)
    await init_fin_tab_employee_table()
    total = await sync_fin_tab_employees(employees)
    logger.info("✅ Синхронизировано сотрудников FinTablo: %d", total)


async def run_daily_employee_sync(run_immediately: bool = False) -> None:
    if run_immediately:
        await sync_once()
    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info(
            "⏳ Следующая синхронизация сотрудников: %s (через %.1f мин)",
            next_time.strftime("%d.%m %H:%M"),
            wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        try:
            await sync_once()
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Ошибка синхронизации сотрудников: %s", exc)


async def main() -> int:
    await sync_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
