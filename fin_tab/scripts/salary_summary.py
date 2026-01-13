import asyncio
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fin_tab.client import FinTabloClient

DATE = "01.2026"
DIRECTION_ID = 148270


def as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


def portion(amount: float, percent: Any) -> float:
    pct = as_float(percent) or 100.0
    return amount * pct / 100.0


async def main(date_str: str, direction_id: int) -> None:
    load_dotenv(ROOT / ".env")
    async with FinTabloClient() as cli:
        salary_items: List[Dict[str, Any]] = await cli.list_salary(date=date_str)

    agg: Dict[str, float] = {}
    total = 0.0
    count = 0
    for item in salary_items:
        total_pay = item.get("totalPay") or {}
        amount = as_float(total_pay.get("amount"))
        positions = item.get("position") or []
        for pos in positions:
            if pos.get("directionId") != direction_id:
                continue
            share = portion(amount, pos.get("percentage"))
            ptype = pos.get("type") or "unknown"
            agg[ptype] = agg.get(ptype, 0.0) + share
            total += share
            count += 1
    print(f"date={date_str} directionId={direction_id} salary_positions={count} total={fmt(total)}")
    for ptype, val in sorted(agg.items()):
        print(f"{ptype}\t{fmt(val)}")


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else DATE
    dir_arg = int(sys.argv[2]) if len(sys.argv) > 2 else DIRECTION_ID
    asyncio.run(main(date_arg, dir_arg))
