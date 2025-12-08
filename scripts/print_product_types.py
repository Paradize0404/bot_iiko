from __future__ import annotations

import asyncio
from collections import Counter

from db.nomenclature_db import fetch_nomenclature


async def main() -> None:
    data = await fetch_nomenclature()
    counter = Counter(item.get("type") for item in data)
    print(f"Total items: {len(data)}")
    for type_, count in counter.most_common():
        print(f"{type_}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
