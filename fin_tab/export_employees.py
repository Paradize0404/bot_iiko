"""Экспорт таблицы fin_tab_employees в JSON."""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set")

engine = create_async_engine(DATABASE_URL, future=True)


async def main() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT id, name, department, post, direction_id, type, percentage "
                "FROM fin_tab_employees ORDER BY name"
            )
        )
        rows = [dict(r._mapping) for r in result]
    out = Path("reports/fin_tab_employees.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(rows)} employees to {out}")


if __name__ == "__main__":
    asyncio.run(main())
