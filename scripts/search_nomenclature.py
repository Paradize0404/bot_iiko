from __future__ import annotations

import argparse
import asyncio

from db.nomenclature_db import fetch_nomenclature


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--show-repr", action="store_true")
    args = parser.parse_args()
    needle = args.query.lower()

    data = await fetch_nomenclature()
    matches = [item for item in data if needle in (item.get("name") or "").lower()]
    print(f"Matches: {len(matches)}")
    for item in matches[:20]:
        name = item.get("name")
        value = repr(name) if args.show_repr else name
        print(value, item.get("id"), item.get("type"))


if __name__ == "__main__":
    asyncio.run(main())
