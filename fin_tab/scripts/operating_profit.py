import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fin_tab.client import FinTabloClient

DATE = "01.2026"
DIRECTION_ID = 148270
PAGE_SIZE = 500


def as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


async def fetch_pnl_items(cli: FinTabloClient, date_str: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = await cli._client.get(
            "/v1/pnl-item", params={"date": date_str, "page": page, "pageSize": PAGE_SIZE}
        )
        resp.raise_for_status()
        chunk = resp.json().get("items", [])
        items.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
    return items


def portion(amount: float, percent: Any) -> float:
    pct = as_float(percent) or 100.0
    return amount * pct / 100.0


async def aggregate_pnl(date_str: str, direction_id: int) -> Tuple[Dict[str, float], List[Tuple[str, str, float]]]:
    async with FinTabloClient() as cli:
        cats = {c["id"]: c for c in await cli.list_pnl_categories()}
        items = await fetch_pnl_items(cli, date_str)

    filtered = [it for it in items if it.get("directionId") == direction_id]
    by_type: Dict[str, float] = defaultdict(float)
    detailed: List[Tuple[str, str, float]] = []
    for it in filtered:
        val = as_float(it.get("value"))
        cat = cats.get(it.get("categoryId"), {})
        pnl_type = cat.get("pnlType") or "unknown"
        name = cat.get("name") or "unknown"
        by_type[pnl_type] += val
        detailed.append((pnl_type, name, val))
    return dict(by_type), detailed


async def aggregate_salary(date_str: str, direction_id: int) -> Dict[str, Dict[str, float]]:
    async with FinTabloClient() as cli:
        salary_items = await cli.list_salary(date=date_str)

    agg: Dict[str, Dict[str, float]] = defaultdict(lambda: {"amount": 0.0, "tax": 0.0, "fee": 0.0})
    for item in salary_items:
        amount = as_float((item.get("totalPay") or {}).get("amount"))
        tax = as_float(item.get("tax"))
        fee = as_float(item.get("fee"))
        positions = item.get("position") or []
        for pos in positions:
            if pos.get("directionId") != direction_id:
                continue
            share = portion(amount, pos.get("percentage"))
            tax_share = portion(tax, pos.get("percentage"))
            fee_share = portion(fee, pos.get("percentage"))
            ptype = pos.get("type") or "unknown"
            agg[ptype]["amount"] += share
            agg[ptype]["tax"] += tax_share
            agg[ptype]["fee"] += fee_share
    return agg


async def main(date_str: str, direction_id: int) -> None:
    load_dotenv(ROOT / ".env")

    pnl_by_type, pnl_rows = await aggregate_pnl(date_str, direction_id)
    salary_by_type = await aggregate_salary(date_str, direction_id)

    income = pnl_by_type.get("income", 0.0)
    direct_var = pnl_by_type.get("direct-variable", 0.0)
    direct_prod_items = pnl_by_type.get("direct-production", 0.0)
    commercial_items = pnl_by_type.get("commercial", 0.0)
    admin_items = pnl_by_type.get("administrative", 0.0)

    salary_dp = salary_by_type.get("direct-production", {})
    salary_comm = salary_by_type.get("commercial", {})
    salary_admin = salary_by_type.get("administrative", {})

    def total_salary(block: Dict[str, float]) -> float:
        return block.get("amount", 0.0) + block.get("tax", 0.0) + block.get("fee", 0.0)

    direct_prod_total = direct_prod_items + total_salary(salary_dp)
    commercial_total = commercial_items + total_salary(salary_comm)
    administrative_total = admin_items + total_salary(salary_admin)

    operating_profit = income - direct_var - direct_prod_total - commercial_total - administrative_total

    print("=== PnL items (by pnlType) ===")
    for key, val in sorted(pnl_by_type.items()):
        print(f"{key}\t{fmt(val)}")

    print("\n--- PnL item details ---")
    for pnl_type, name, val in pnl_rows:
        print(f"{pnl_type}\t{name}\t{fmt(val)}")

    print("\n=== Salary (amount + tax + fee) by position type ===")
    for key in sorted(salary_by_type.keys()):
        block = salary_by_type[key]
        total = total_salary(block)
        print(
            f"{key}\tamount={fmt(block['amount'])}\ttax={fmt(block['tax'])}\tfee={fmt(block['fee'])}\ttotal={fmt(total)}"
        )

    print("\n=== Combined totals ===")
    print(f"income\t{fmt(income)}")
    print(f"direct-variable\t{fmt(direct_var)}")
    print(f"direct-production (items)\t{fmt(direct_prod_items)}")
    print(f"direct-production (salary)\t{fmt(total_salary(salary_dp))}")
    print(f"commercial (items)\t{fmt(commercial_items)}")
    print(f"commercial (salary)\t{fmt(total_salary(salary_comm))}")
    print(f"administrative (items)\t{fmt(admin_items)}")
    print(f"administrative (salary)\t{fmt(total_salary(salary_admin))}")

    print("\n=== Operating profit ===")
    print(
        "income - direct-variable - direct-production - commercial - administrative = "
        f"{fmt(operating_profit)}"
    )


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else DATE
    dir_arg = int(sys.argv[2]) if len(sys.argv) > 2 else DIRECTION_ID
    asyncio.run(main(date_arg, dir_arg))