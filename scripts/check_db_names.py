from __future__ import annotations

import asyncio

from sqlalchemy import select

from db.nomenclature_db import Nomenclature, async_session

TARGETS = ["т_коробка д/ бургеров крафт", "т_стакан 350 мл крафт"]


async def main() -> None:
    async with async_session() as session:
        stmt = select(Nomenclature).where(Nomenclature.name.in_(TARGETS))
        rows = (await session.execute(stmt)).scalars().all()
    print(f"DB rows: {len(rows)}")
    for row in rows:
        print(row.id, row.name, row.mainunit)


if __name__ == "__main__":
    asyncio.run(main())
