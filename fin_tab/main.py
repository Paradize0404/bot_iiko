"""
Console entrypoint for FinTablo integration experiments.
Keeps dependencies local to this folder so it can run independently from the bot.
"""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from fin_tab import iiko_auth
from fin_tab.sync_pnl_categories import run_daily_sync
from fin_tab.sync_directions import run_daily_direction_sync
from fin_tab.sync_revenue import run_daily_revenue_sync
from fin_tab.sync_accounts_incoming import sync_incoming_service_accounts
from fin_tab.sync_employees import run_daily_employee_sync
from fin_tab.sync_salary_from_sheet import sync_salary_from_sheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def ensure_env():
    required = ["IIKO_USERNAME", "IIKO_PASSWORD", "IIKO_ORG_ID"]
    missing = [name for name in required if not Path(".env").exists() and not os.getenv(name)]
    if missing:
        logger.warning("Missing env vars: %s", ", ".join(missing))


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    ensure_env()
    logger.info("FinTablo worker запускается: дневное расписание включено")

    # Проверяем iiko-доступ для раннего фейла, если нужен
    try:
        token = await iiko_auth.get_auth_token()
        base_url = iiko_auth.get_base_url()
        logger.info("iiko auth ok, base_url=%s", base_url)
        logger.debug("Token prefix: %s", token[:6])
    except Exception as exc:  # pragma: no cover
        logger.error("iiko auth failed: %s", exc)
        return 1

    # Запускаем вечные циклы: статьи ПиУ, направления и выручка
    task_pnl = asyncio.create_task(run_daily_sync(run_immediately=True))
    task_dir = asyncio.create_task(run_daily_direction_sync(run_immediately=True))
    task_rev = asyncio.create_task(run_daily_revenue_sync(run_immediately=True))
    task_emp = asyncio.create_task(run_daily_employee_sync(run_immediately=True))
    # Однократные задачи при старте
    await sync_salary_from_sheet()
    await sync_incoming_service_accounts()

    await asyncio.gather(task_pnl, task_dir, task_rev, task_emp)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
