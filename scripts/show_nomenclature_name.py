from __future__ import annotations

import argparse
import asyncio

from db.nomenclature_db import fetch_nomenclature


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    target = args.name
    data = await fetch_nomenclature()
    found = [item for item in data if item.get("name") == target]
    print(f"Exact matches: {len(found)}")
    for item in found:
        print(repr(item.get("name")), item.get("id"), item.get("type"))


if __name__ == "__main__":
    asyncio.run(main())
