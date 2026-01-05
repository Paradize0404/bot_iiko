"""
Экспорт всех элементов номенклатуры из iikoServer через /resto/api/v2/entities/products/list.
Создаёт дамп JSON в каталоге reports/products_list_dump.json.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "products_list_dump.json"


async def fetch_products(include_deleted: bool = False) -> list:
    token = await get_auth_token()
    base_url = get_base_url()

    params = {
        "includeDeleted": include_deleted,
    }

    headers = {
        "Cookie": f"key={token}",
    }

    async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
        resp = await client.get("/resto/api/v2/entities/products/list", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def main() -> None:
    products = await fetch_products(include_deleted=False)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "count": len(products) if isinstance(products, list) else None,
        "items": products,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {payload['count']} products to {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
