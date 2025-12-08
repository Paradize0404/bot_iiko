from __future__ import annotations

import asyncio

from db.nomenclature_db import fetch_nomenclature

TARGETS = {"т_коробка д/ бургеров крафт", "т_стакан 350 мл крафт"}


async def main() -> None:
    data = await fetch_nomenclature()
    found = {item.get("name"): item for item in data if item.get("name") in TARGETS}
    print(f"found {len(found)} items")
    for name, item in found.items():
        print(name, item.get("id"), item.get("type"))


if __name__ == "__main__":
    asyncio.run(main())
