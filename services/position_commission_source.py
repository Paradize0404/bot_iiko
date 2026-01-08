"""
Source of position commission settings.
1) Tries to read from Google Sheet so that managers can edit rates directly.
2) Falls back to database (position_commissions) if Sheets is unavailable.
"""
import os
import logging
from typing import Dict, Any

from sqlalchemy import select

from services.gsheets_client import GoogleSheetsClient
from db.position_commission_db import async_session, PositionCommission

logger = logging.getLogger(__name__)

SHEET_NAME = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")


def _parse_float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(str(value).replace(" ", "").replace(",", "."))
    except Exception:
        return None


HEADER_MAP = {
    "position_name": "position_name",
    "должность": "position_name",
    "payment_type": "payment_type",
    "тип оплаты": "payment_type",
    "fixed_rate": "fixed_rate",
    "ставка": "fixed_rate",
    "ставка оплаты": "fixed_rate",
    "commission_percent": "commission_percent",
    "процент": "commission_percent",
    "commission_type": "commission_type",
    "тип процента": "commission_type",
}

PAYMENT_MAP = {
    "hourly": "hourly",
    "почасовая": "hourly",
    "per_shift": "per_shift",
    "посменная": "per_shift",
    "сменная": "per_shift",
    "monthly": "monthly",
    "ежемесячная": "monthly",
    "оклад": "monthly",
}

COMMISSION_MAP = {
    "sales": "sales",
    "от продаж": "sales",
    "writeoff": "writeoff",
    "от накладных": "writeoff",
}


def load_position_settings_from_sheet(sheet_name: str = SHEET_NAME) -> Dict[str, dict]:
    """Load settings from Google Sheet. Blocking call; expected to be used rarely and cached upstream."""
    client = GoogleSheetsClient()
    range_a1 = f"'{sheet_name}'!A:Z"
    values = client.read_range(range_a1)
    if not values:
        raise RuntimeError("Sheet returned no data")

    header = [HEADER_MAP.get(h.strip().lower(), h.strip().lower()) for h in values[0]]
    col_index = {name: idx for idx, name in enumerate(header)}
    required = ["position_name", "payment_type", "commission_percent", "commission_type"]
    for req in required:
        if req not in col_index:
            raise RuntimeError(f"Column '{req}' not found in sheet header")

    result: Dict[str, dict] = {}
    for row in values[1:]:
        def get(col: str) -> Any:
            idx = col_index.get(col)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        position_name = (get("position_name") or "").strip()
        if not position_name:
            continue

        payment_raw = (get("payment_type") or "hourly").strip().lower()
        payment_type = PAYMENT_MAP.get(payment_raw, "hourly")
        fixed_rate = _parse_float(get("fixed_rate")) if "fixed_rate" in col_index else None
        commission_percent = _parse_float(get("commission_percent")) or 0.0
        commission_raw = (get("commission_type") or "sales").strip().lower()
        commission_type = COMMISSION_MAP.get(commission_raw, "sales")

        result[position_name] = {
            "payment_type": payment_type,
            "fixed_rate": fixed_rate,
            "commission_percent": commission_percent,
            "commission_type": commission_type,
        }

    if not result:
        raise RuntimeError("No rows parsed from sheet")

    logger.info("✅ Loaded %s position settings from sheet '%s'", len(result), sheet_name)
    return result


async def load_position_settings_from_db() -> Dict[str, dict]:
    async with async_session() as session:
        result = await session.execute(select(PositionCommission))
        commissions = result.scalars().all()
        return {
            c.position_name: {
                "payment_type": c.payment_type,
                "fixed_rate": c.fixed_rate,
                "commission_percent": c.commission_percent,
                "commission_type": c.commission_type,
            }
            for c in commissions
        }


async def get_position_settings() -> Dict[str, dict]:
    """Try Sheets first; if fails, fall back to DB."""
    try:
        return load_position_settings_from_sheet()
    except Exception as e:
        logger.warning("⚠️ Sheets position settings unavailable (%s), falling back to DB", e)
    return await load_position_settings_from_db()
