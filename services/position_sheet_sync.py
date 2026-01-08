"""
Синхронизация должностей из iiko в Google Sheets и переустановка защиты.
- Добавляет новые должности в таблицу, сохраняя настройки B..E, если они уже были.
- После синхронизации блокирует лист целиком, оставляя редактируемыми только B..E на строках с должностями.
- Предоставляет периодический запуск в 12:00 местного времени.
"""
import asyncio
import logging
import os
from datetime import datetime, time, timedelta

from scripts.sync_positions_from_iiko import fetch_positions_from_iiko, read_existing_sheet, write_sheet
from scripts.protect_position_sheet_strict import (
    apply_protection,
    find_sheet_id,
    last_row_with_positions,
    remove_old_protection,
)
from scripts.setup_position_sheet import apply_validation
from services.gsheets_client import GoogleSheetsClient

logger = logging.getLogger(__name__)
DEFAULT_SHEET = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")


def _seconds_until_noon() -> float:
    now = datetime.now()
    target = datetime.combine(now.date(), time(hour=12, minute=0))
    if now >= target:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def sync_positions_sheet(sheet_name: str | None = None) -> int:
    """
    Синхронизирует должности из iiko в таблицу и обновляет защиту.
    Returns количество должностей в таблице после синхронизации.
    """
    sheet = sheet_name or DEFAULT_SHEET

    positions = await fetch_positions_from_iiko()
    existing = read_existing_sheet(sheet)
    write_sheet(sheet, positions, existing)

    client = GoogleSheetsClient()
    sheet_id = find_sheet_id(client.service, client.spreadsheet_id, sheet)
    last = last_row_with_positions(client, sheet)
    apply_validation(sheet, last)
    remove_old_protection(client.service, client.spreadsheet_id, sheet_id)
    apply_protection(client.service, client.spreadsheet_id, sheet_id, last + 1)

    logger.info("✅ Должности синхронизированы: %s строк, защита обновлена", len(positions))
    return len(positions)


async def run_daily_positions_sync_at_noon(sheet_name: str | None = None):
    """Запускает синхронизацию должностей ежедневно в 12:00."""
    while True:
        wait_seconds = _seconds_until_noon()
        next_run = datetime.now() + timedelta(seconds=wait_seconds)
        logger.info("⏳ Следующая синхронизация должностей в %s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
        await asyncio.sleep(wait_seconds)
        try:
            count = await sync_positions_sheet(sheet_name)
            logger.info("✅ Дневная синхронизация завершена, строк: %s", count)
        except Exception:  # noqa: BLE001
            logger.exception("❌ Ошибка при дневной синхронизации должностей")
