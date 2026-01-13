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
PAGE_SIZE = 500


def fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


async def fetch_items(cli: FinTabloClient, date_str: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = await cli._client.get("/v1/pnl-item", params={"date": date_str, "page": page, "pageSize": PAGE_SIZE})
        resp.raise_for_status()
        chunk = resp.json().get("items", [])
        items.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        page += 1
    return items


async def main(date_str: str, direction_id: int) -> None:
    load_dotenv(ROOT / ".env")
    async with FinTabloClient() as cli:
        categories = await cli.list_pnl_categories()
        cat_info = {c["id"]: c for c in categories}
        items = await fetch_items(cli, date_str)

    filtered = [it for it in items if it.get("directionId") == direction_id]
    total = sum(float(it.get("value") or 0) for it in filtered)
    print(f"date={date_str}, directionId={direction_id}, rows={len(filtered)}, total raw sum={fmt(total)}")
    for it in filtered:
        cat_id = it.get("categoryId")
        val = float(it.get("value") or 0)
        cat = cat_info.get(cat_id) or {}
        name = cat.get("name")
        pnl_type = cat.get("pnlType")
        print(f"categoryId={cat_id} name={name} pnlType={pnl_type} value={fmt(val)}")


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else DATE
    dir_arg = int(sys.argv[2]) if len(sys.argv) > 2 else DIRECTION_ID
    asyncio.run(main(date_arg, dir_arg))
