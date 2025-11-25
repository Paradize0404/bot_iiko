import asyncio
import httpx
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from iiko.iiko_auth import get_auth_token, get_base_url

async def main():
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"dateFrom": "2025-11-17", "dateTo": "2025-11-23"}
    headers = {"Cookie": f"key={token}"}
    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        resp = await client.get(f"{base_url}/resto/api/v2/documents/writeoff", params=params, headers=headers)
    resp.raise_for_status()
    docs = resp.json().get("response", [])
    print("docs", len(docs))
    for idx, doc in enumerate(docs[:3]):
        print(f"doc #{idx}")
        print(doc.keys())
        print("storeId", doc.get("storeId"))
        print("store", doc.get("store"))
        print("storeName", doc.get("storeName"))
        store = doc.get("store")
        if isinstance(store, dict):
            print("store dict keys", store.keys())
        first_item = (doc.get("items") or [])[:1]
        if first_item:
            item = first_item[0]
            print("item storeId", item.get("storeId"))
            print("item store", item.get("store"))
            print("item storeName", item.get("storeName"))
        print("----")

if __name__ == "__main__":
    asyncio.run(main())
