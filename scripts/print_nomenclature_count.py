from __future__ import annotations

import asyncio

from db.nomenclature_db import fetch_nomenclature


async def main() -> None:
    data = await fetch_nomenclature()
    print(f"Nomenclature entries: {len(data)}")


if __name__ == "__main__":
    asyncio.run(main())
