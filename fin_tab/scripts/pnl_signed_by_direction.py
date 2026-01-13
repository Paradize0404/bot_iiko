import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fin_tab.client import FinTabloClient

POSITIVE = {"income", "income-under-ebitda"}
DATE = "01.2026"


def fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


def parse_args(argv: Iterable[str]) -> str:
    args = list(argv)
    return args[0] if args else DATE


async def main(date_str: str) -> None:
    load_dotenv(ROOT / ".env")
    async with FinTabloClient() as cli:
        cats = await cli.list_pnl_categories()
        cat_to_type = {c["id"]: c.get("pnlType") for c in cats}
        items = await cli.list_pnl_items(date=date_str)

    by_dir = defaultdict(float)
    breakdown = defaultdict(lambda: defaultdict(float))
    for it in items:
        dir_id = it.get("directionId")
        cat_id = it.get("categoryId")
        pnl_type = cat_to_type.get(cat_id)
        val = float(it.get("value") or 0)
        sign = 1 if pnl_type in POSITIVE else -1
        by_dir[dir_id] += sign * val
        breakdown[dir_id][pnl_type] += sign * val

    for dir_id, total in sorted(by_dir.items(), key=lambda kv: abs(kv[1]), reverse=True):
        print(f"dir={dir_id} total={fmt(total)}")
        for t, v in sorted(breakdown[dir_id].items()):
            print(f"  {t}: {fmt(v)}")
        print()


if __name__ == "__main__":
    date_arg = parse_args(sys.argv[1:])
    asyncio.run(main(date_arg))
