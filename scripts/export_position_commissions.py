"""
Export position_commissions table to Google Sheet so conditions can be edited there.
Usage:
  python -m scripts.export_position_commissions [sheet_name]
Env required:
  DATABASE_URL (for PG, already used elsewhere)
  GOOGLE_SHEETS_CREDENTIALS_PATH
  GOOGLE_SHEETS_SPREADSHEET_ID
Optional:
  GOOGLE_SHEETS_POSITION_SHEET (default sheet if arg not provided)
"""
import asyncio
import os
import logging
from sqlalchemy import select

from db.position_commission_db import async_session, PositionCommission
from services.gsheets_client import GoogleSheetsClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def load_commissions():
    async with async_session() as session:
        result = await session.execute(select(PositionCommission))
        return result.scalars().all()


def build_rows(commissions):
    header = ["Должность", "Тип оплаты", "Ставка оплаты", "Процент", "Тип процента"]
    rows = [header]
    for c in commissions:
        rows.append([
            c.position_name,
            c.payment_type,
            c.fixed_rate if c.fixed_rate is not None else "",
            c.commission_percent if c.commission_percent is not None else 0,
            c.commission_type,
        ])
    return rows


def export(sheet_name: str):
    client = GoogleSheetsClient()
    range_a1 = f"'{sheet_name}'!A1"
    rows = asyncio.run(load_commissions())
    values = build_rows(rows)

    # Clear the sheet before writing fresh data
    client.clear_range(f"'{sheet_name}'!A:Z")
    client.write_range(range_a1, values)
    logger.info("✅ Exported %s rows to sheet '%s'", len(values) - 1, sheet_name)


if __name__ == "__main__":
    sheet = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")
    import sys
    if len(sys.argv) > 1:
        sheet = sys.argv[1]
    export(sheet)
