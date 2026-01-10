"""Daily bar revenue sync to FinTablo (month-to-date, excluding today)."""
import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from fin_tab.iiko_revenue import calculate_bar_metrics, get_revenue_report

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# FinTablo mappings
BAR_CATEGORY_ID = 27315  # "Бар" статья (income)
BAR_DIRECTION_ID = 148270  # "Клиническая" направление (дочернее)

# Schedule: default 03:20
RUN_HOUR = 3
RUN_MINUTE = 20


def _month_window_to_yesterday(today: date) -> Tuple[date, date]:
    start = today.replace(day=1)
    end = today - timedelta(days=1)
    return start, end


def _next_run(ts: datetime) -> datetime:
    target = datetime.combine(ts.date(), time(hour=RUN_HOUR, minute=RUN_MINUTE))
    if ts < target:
        return target
    return datetime.combine(ts.date() + timedelta(days=1), time(hour=RUN_HOUR, minute=RUN_MINUTE))


async def sync_bar_revenue_once() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    today = date.today()
    start, end = _month_window_to_yesterday(today)

    if end < start:
        logger.info("Nothing to sync: first day of month, window is empty")
        return

    date_from = start.strftime("%Y-%m-%d")
    date_to = end.strftime("%Y-%m-%d")
    logger.info("Fetching bar revenue %s -> %s", date_from, date_to)

    report_rows = await get_revenue_report(date_from, date_to)
    metrics = calculate_bar_metrics(report_rows)
    bar_revenue = round(float(metrics.get("bar_revenue", 0.0)), 2)

    if bar_revenue == 0:
        logger.warning("Bar revenue is 0 for %s-%s; skipping FinTablo push", date_from, date_to)
        return

    payload = {
        "categoryId": BAR_CATEGORY_ID,
        "directionId": BAR_DIRECTION_ID,
        "value": bar_revenue,
        "date": end.strftime("%m.%Y"),
        "comment": f"Бар: выручка {start:%d.%m}–{end:%d.%m}",
    }

    async with FinTabloClient() as cli:
        created = await cli.create_pnl_item(payload)
    logger.info(
        "✅ Sent bar revenue %.2f to FinTablo for %s (item id=%s)",
        bar_revenue,
        payload["date"],
        created.get("id"),
    )


async def run_daily_bar_revenue_sync(run_immediately: bool = False) -> None:
    if run_immediately:
        await sync_bar_revenue_once()

    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info(
            "⏳ Next bar revenue sync at %s (in %.1f min)",
            next_time.strftime("%d.%m %H:%M"),
            wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        try:
            await sync_bar_revenue_once()
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Bar revenue sync failed: %s", exc)


async def main() -> int:
    await sync_bar_revenue_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
