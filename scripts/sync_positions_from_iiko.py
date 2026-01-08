"""
Sync positions (roles) from iiko into the Google Sheet so all roles appear.
Keeps existing settings where possible; adds new positions with empty settings.
Usage:
  python -m scripts.sync_positions_from_iiko [sheet_name]
"""
import os
import asyncio
import logging
import xml.etree.ElementTree as ET
import httpx

from services.gsheets_client import GoogleSheetsClient
from iiko.iiko_auth import get_auth_token, get_base_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def fetch_positions_from_iiko() -> list[str]:
    token = await get_auth_token()
    base_url = get_base_url()
    roles_url = f"{base_url}/resto/api/employees/roles"
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        resp = await client.get(roles_url, headers={"Cookie": f"key={token}"})
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    names = []
    for role in root.findall(".//role"):
        name = role.findtext("name")
        if name:
            names.append(name.strip())
    # unique and sorted
    uniq = sorted(set(names), key=lambda x: x.lower())
    logger.info("✅ Получено должностей из iiko: %s", len(uniq))
    return uniq


def read_existing_sheet(sheet_name: str) -> dict:
    client = GoogleSheetsClient()
    values = client.read_range(f"'{sheet_name}'!A2:E")
    existing = {}
    for row in values:
        if not row:
            continue
        pos = (row[0] or "").strip()
        if not pos:
            continue
        # pad row to 5 columns
        padded = row + [""] * (5 - len(row))
        existing[pos] = padded[1:5]  # B..E
    return existing


def write_sheet(sheet_name: str, positions: list[str], existing: dict):
    client = GoogleSheetsClient()
    rows = []
    for pos in positions:
        prev = existing.get(pos, ["", "", "", ""])
        rows.append([pos, *prev])
    target_range = f"'{sheet_name}'!A2:E"
    client.clear_range(f"'{sheet_name}'!A2:E")
    if rows:
        client.write_range(target_range, rows)
    logger.info("✅ Обновлено строк: %s", len(rows))


def main():
    sheet = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")
    import sys
    if len(sys.argv) > 1:
        sheet = sys.argv[1]
    positions = asyncio.run(fetch_positions_from_iiko())
    existing = read_existing_sheet(sheet)
    write_sheet(sheet, positions, existing)


if __name__ == "__main__":
    main()
