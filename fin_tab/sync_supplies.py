"""Ежедневный синк ТМЦ/хозтоваров в FinTablo по направлению Аксакова."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List

import httpx
from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from services.supplies_tmc_report import get_supplies_tmc_report, AccountGroupRow

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DIRECTION_KLIN = 148270

CATEGORY_TMC = 27327  # Покупка ТМЦ
CATEGORY_GLASS = 27328  # Стекло/Посуда
CATEGORY_PACK = 27325  # Упаковка
CATEGORY_CHEM = 27324  # Уборочный инвентарь/Химия
CATEGORY_CONSUMABLES = 27326  # Расходные материалы (Р/М, прочее)

RUN_HOUR = 4
RUN_MINUTE = 10


def _month_window_to_yesterday(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    end = today - timedelta(days=1)
    return start, end


def _next_run(ts: datetime) -> datetime:
    target = datetime.combine(ts.date(), time(hour=RUN_HOUR, minute=RUN_MINUTE))
    if ts < target:
        return target
    return datetime.combine(ts.date() + timedelta(days=1), time(hour=RUN_HOUR, minute=RUN_MINUTE))


def _map_category(row: AccountGroupRow) -> int | None:
    name = row.account_name.strip().lower()
    group = row.group_label.strip().lower()

    if name == "тмц пиццерия":
        if group == "посуда/стекло":
            return CATEGORY_GLASS
        return CATEGORY_TMC

    if name == "хоз. товары пиццерия":
        if group == "упаковка":
            return CATEGORY_PACK
        if group == "уборочный инвентарь/химия":
            return CATEGORY_CHEM
        if group == "р/м бар зал кухня":
            return CATEGORY_CONSUMABLES
        if group == "прочее":
            return CATEGORY_CONSUMABLES
        # Непредвиденные группы по хозтоварам тоже кладем в расходные материалы
        return CATEGORY_CONSUMABLES

    return None


def _build_payloads(rows: List[AccountGroupRow], start: date, end: date) -> List[Dict]:
    month_str = end.strftime("%m.%Y")
    comment_range = f"{start:%d.%m}–{end:%d.%m}"

    payloads: List[Dict] = []
    for row in rows:
        category_id = _map_category(row)
        if not category_id:
            continue
        value = float(row.amount or 0)
        if abs(value) < 0.01:
            continue
        payloads.append(
            {
                "categoryId": category_id,
                "directionId": DIRECTION_KLIN,
                "value": round(value, 2),
                "date": month_str,
                "comment": f"{row.account_name}: {row.group_label} {comment_range}",
            }
        )

    return payloads


async def _apply_delta_mode(cli: FinTabloClient, payloads: List[Dict]) -> List[Dict]:
    adjusted: List[Dict] = []
    for payload in payloads:
        params = {"date": payload["date"], "categoryId": payload["categoryId"]}
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
            logger.info("⏭️ Skip %s: already up to date (existing %.2f)", payload.get("comment", ""), existing_sum)
            continue

        new_payload = dict(payload)
        new_payload["value"] = diff
        new_payload["comment"] = f"{payload.get('comment', '')} (дельта до {payload['value']:.2f})".strip()
        adjusted.append(new_payload)

    return adjusted


async def sync_supplies_once() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    today = date.today()
    start, end = _month_window_to_yesterday(today)
    if end < start:
        logger.info("Период пуст — сегодня первый день месяца")
        return

    date_from = start.strftime("%Y-%m-%d")
    date_to = end.strftime("%Y-%m-%d")
    logger.info("Запрашиваем ТМЦ/хозы %s -> %s", date_from, date_to)

    report = await get_supplies_tmc_report(date_from, date_to)
    payloads = _build_payloads(report.rows, start, end)

    if not payloads:
        logger.warning("Нет данных по ТМЦ/хозам для отправки")
        return

    async with FinTabloClient() as cli:
        payloads = await _apply_delta_mode(cli, payloads)
        if not payloads:
            logger.info("Все записи уже актуальны — отправка не требуется")
            return

        for payload in payloads:
            try:
                created = await cli.create_pnl_item(payload)
                logger.info(
                    "✅ Отправлено %s %.2f в FinTablo за %s (id=%s)",
                    payload.get("comment", ""),
                    payload["value"],
                    payload["date"],
                    created.get("id"),
                )
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                logger.error("❌ Ошибка отправки %s: %s", payload.get("comment"), exc)


async def run_daily_supplies_sync(run_immediately: bool = False) -> None:
    if run_immediately:
        await sync_supplies_once()

    while True:
        now = datetime.now()
        next_time = _next_run(now)
        wait_seconds = max(1.0, (next_time - now).total_seconds())
        logger.info("⏳ Следующий синк ТМЦ/хозы в %s (через %.1f мин)", next_time.strftime("%d.%m %H:%M"), wait_seconds / 60)
        await asyncio.sleep(wait_seconds)
        try:
            await sync_supplies_once()
        except Exception as exc:  # noqa: BLE001
            logger.exception("❌ Синк ТМЦ/хозы упал: %s", exc)


async def main() -> int:
    await sync_supplies_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
