"""Печать счетов (pnl-item) с названием категории и ID."""
import asyncio
import json
from dotenv import load_dotenv
from fin_tab.client import FinTabloClient


async def main() -> int:
    load_dotenv()
    async with FinTabloClient() as cli:
        items = await cli.list_pnl_items()
        categories = await cli.list_pnl_categories()

    cat_names = {c.get("id"): c.get("name") for c in categories}

    print(f"Всего счетов: {len(items)}")
    for idx, item in enumerate(items, 1):
        cat_id = item.get("categoryId")
        payload = {
            "id": item.get("id"),
            "categoryId": cat_id,
            "categoryName": cat_names.get(cat_id),
            "value": item.get("value"),
            "date": item.get("date"),
            "directionId": item.get("directionId"),
            "comment": item.get("comment"),
        }
        print(f"#{idx}: {json.dumps(payload, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
