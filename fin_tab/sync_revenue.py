"""Daily revenue/cost sync to FinTablo for bar, kitchen, app, yandex, production."""
import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import httpx
from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from fin_tab.iiko_revenue import get_revenue_report
from fin_tab import writeoff_products
from services.revenue_report import calculate_revenue
from fin_tab.writeoff_revenue import fetch_writeoff_cost, fetch_writeoff_revenue

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# FinTablo mappings (categories -> directions)
BAR_CATEGORY_ID = 27315
KITCHEN_CATEGORY_ID = 27314
APP_CATEGORY_ID = 27316
YANDEX_CATEGORY_ID = 27317
PRODUCTION_CATEGORY_ID = 27318
COST_CATEGORY_ID = 27319
WRITE_OFF_PRODUCTS_CATEGORY_ID = 27321

DIRECTION_KLIN = 148270
DIRECTION_PRODUCTION = 159851

# Schedule defaults
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


def _build_payloads(
    metrics: Dict[str, float],
    start: date,
    end: date,
    writeoff_revenue: float,
    writeoff_cost: float,
    writeoff_products_totals: Dict[str, float],
) -> List[Dict]:
    month_str = end.strftime("%m.%Y")
    comment_range = f"{start:%d.%m}–{end:%d.%m}"

    # В боте общая себестоимость = бар + кухня (включая доставку по месту приготовления)
    klin_cost = metrics.get("total_cost", metrics.get("bar_cost", 0.0) + metrics.get("kitchen_total_cost", 0.0))

    logger.info(
        "📊 Клиническая: бар %.2f/%.2f, кухня %.2f/%.2f, приложение %.2f/%.2f, яндекс %.2f/%.2f => cost суммой %.2f",
        metrics.get("bar_revenue", 0.0),
        metrics.get("bar_cost", 0.0),
        metrics.get("kitchen_revenue", 0.0),
        metrics.get("kitchen_cost", 0.0),
        metrics.get("app_revenue", 0.0),
        metrics.get("app_cost", 0.0),
        metrics.get("delivery_revenue", 0.0),
        metrics.get("yandex_cost", 0.0),
        klin_cost,
    )
    logger.info(
        "📦 Производство: выручка %.2f, себестоимость %.2f (%s–%s)",
        writeoff_revenue,
        writeoff_cost,
        start.strftime("%d.%m"),
        end.strftime("%d.%m"),
    )

    entries = [
        {
            "categoryId": BAR_CATEGORY_ID,
            "directionId": DIRECTION_KLIN,
            "value": round(metrics.get("bar_revenue", 0.0), 2),
            "date": month_str,
            "comment": f"Бар: выручка {comment_range}",
        },
        {
            "categoryId": KITCHEN_CATEGORY_ID,
            "directionId": DIRECTION_KLIN,
            "value": round(metrics.get("kitchen_revenue", 0.0), 2),
            "date": month_str,
            "comment": f"Кухня: выручка {comment_range}",
        },
        {
            "categoryId": APP_CATEGORY_ID,
            "directionId": DIRECTION_KLIN,
            "value": round(metrics.get("app_revenue", 0.0), 2),
            "date": month_str,
            "comment": f"Приложение: выручка {comment_range}",
        },
        {
            "categoryId": YANDEX_CATEGORY_ID,
            "directionId": DIRECTION_KLIN,
            "value": round(metrics.get("delivery_revenue", 0.0), 2),
            "date": month_str,
            "comment": f"Яндекс: выручка {comment_range}",
        },
        {
            "categoryId": PRODUCTION_CATEGORY_ID,
            "directionId": DIRECTION_PRODUCTION,
            "value": round(writeoff_revenue, 2),
            "date": month_str,
            "comment": f"Производство: расходные накладные {comment_range}",
        },
        {
            "categoryId": COST_CATEGORY_ID,
            "directionId": DIRECTION_KLIN,
            "value": round(klin_cost, 2),
            "date": month_str,
            "comment": f"Сырьевая себестоимость (Клиническая) {comment_range}",
        },
        {
            "categoryId": COST_CATEGORY_ID,
            "directionId": DIRECTION_PRODUCTION,
            "value": round(writeoff_cost, 2),
            "date": month_str,
            "comment": f"Себестоимость расходных накладных {comment_range}",
        },
        {
            "categoryId": WRITE_OFF_PRODUCTS_CATEGORY_ID,
            "directionId": DIRECTION_KLIN,
            "value": round(writeoff_products_totals.get("total", 0.0), 2),
            "date": month_str,
            "comment": f"Списания продуктов (бар+кухня) {comment_range}",
        },
    ]

    # Отфильтруем нулевые значения, чтобы не спамить пустыми записями
    return [entry for entry in entries if entry["value"] != 0]


async def _apply_delta_mode(cli: FinTabloClient, payloads: List[Dict]) -> List[Dict]:
    """Сравнить с уже существующими записями за месяц и оставить только дельту.

    Если сумма по (categoryId, directionId, date) уже есть, отправляем только разницу.
    При совпадении суммы запись не отправляется.
    """

    adjusted: List[Dict] = []
    for payload in payloads:
        params = {
            "date": payload["date"],
            "categoryId": payload["categoryId"],
        }
        if payload.get("directionId"):
            params["directionId"] = payload["directionId"]

        existing = await cli.list_pnl_items(**params)
        existing_sum = 0.0
        for item in existing:
            try:
                existing_sum += float(item.get("value") or 0.0)
            except (TypeError, ValueError):
                continue

        target = payload["value"]

        # Если в FinTablo сумма больше, чем целевая — чистим месяц и пишем целевую
        if existing_sum - target > 0.01:
            logger.info(
                "♻️ Reset %s: existing %.2f > target %.2f, deleting and re-posting",
                payload.get("comment", ""),
                existing_sum,
                target,
            )
            for item in existing:
                item_id = item.get("id")
                if not item_id:
                    continue
                try:
                    await cli.delete_pnl_item(item_id)
                except httpx.HTTPStatusError as exc:  # noqa: BLE001
                    logger.error("Не удалось удалить запись id=%s: %s", item_id, exc)
            adjusted.append(payload)
            continue

        diff = round(target - existing_sum, 2)
        if abs(diff) < 0.01:
            logger.info(
                "⏭️ Skip %s: already up to date (existing %.2f)",
                payload.get("comment", ""),
                existing_sum,
            )
            continue

        new_payload = dict(payload)
        new_payload["value"] = diff
        new_payload["comment"] = f"{payload.get('comment', '')} (дельта до {payload['value']:.2f})".strip()
        adjusted.append(new_payload)

    return adjusted


async def sync_revenue_once() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    today = date.today()
    start, end = _month_window_to_yesterday(today)
    if end < start:
        logger.info("Nothing to sync: first day of month, window is empty")
        return

    date_from = start.strftime("%Y-%m-%d")
    date_to = end.strftime("%Y-%m-%d")
    logger.info("Fetching revenue %s -> %s", date_from, date_to)

    report_rows = await get_revenue_report(date_from, date_to)
    metrics = await calculate_revenue(report_rows, date_from, date_to)

    writeoff_revenue = await fetch_writeoff_revenue(date_from, date_to)
    writeoff_cost = await fetch_writeoff_cost(date_from, date_to)
    writeoff_products_totals = await writeoff_products.fetch_writeoff_products_totals(date_from, date_to)

    payloads = _build_payloads(
        metrics,
        start,
        end,
        writeoff_revenue,
        writeoff_cost,
        writeoff_products_totals,
    )
    if not payloads:
        logger.warning("No revenue values to push; skipping")
        return

    async with FinTabloClient() as cli:
        payloads = await _apply_delta_mode(cli, payloads)
        if not payloads:
            logger.info("Все записи уже в актуальном значении — отправка не требуется")
            return

        for payload in payloads:
            try:
                created = await cli.create_pnl_item(payload)
                logger.info(
                    "✅ Sent %s %.2f to FinTablo for %s (item id=%s)",
                    payload["comment"].split(":")[0],
                    payload["value"],
                    payload["date"],
                    created.get("id"),
                )
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                logger.error("❌ Failed to send %s: %s", payload.get("comment"), exc)


async def run_daily_revenue_sync(run_immediately: bool = False) -> None:
    if run_immediately:
        await sync_revenue_once()

    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info(
            "⏳ Next revenue sync at %s (in %.1f min)",
            next_time.strftime("%d.%m %H:%M"),
            wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        try:
            await sync_revenue_once()
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Revenue sync failed: %s", exc)


async def main() -> int:
    await sync_revenue_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
