# ───────── db/groups_db.py ─────────
"""
Синхронизация таблицы nomenclature_groups
из энд-пойнта /entities/products/group/list
"""

import os, asyncio, httpx
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column
from sqlalchemy import String, select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ---------- подключение к БД ----------
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("env var DATABASE_URL не установлена")

engine        = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

# ---------- ORM-модель ----------
class NomenclatureGroup(Base):
    __tablename__ = "nomenclature_groups"

    id:          Mapped[str] = mapped_column(String, primary_key=True)
    name:        Mapped[str] = mapped_column(String)
    parentgroup: Mapped[str] = mapped_column(String, nullable=True)

# ---------- инициализация схемы ----------
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nomenclature_groups (
    id   VARCHAR PRIMARY KEY,
    name TEXT NOT NULL
);
"""
ALTER_SQL = """
ALTER TABLE nomenclature_groups
    ADD COLUMN IF NOT EXISTS parentgroup VARCHAR;
"""

async def init_groups_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_SQL))
        await conn.execute(text(ALTER_SQL))
    print("✅ Таблица nomenclature_groups готова")

# ---------- работа с iiko ----------
from iiko.iiko_auth import get_auth_token, get_base_url  # уже есть в проекте

async def fetch_groups() -> list[dict]:
    token    = await get_auth_token()
    base_url = get_base_url()
    url      = f"{base_url}/resto/api/v2/entities/products/group/list"
    r        = httpx.get(url, params={"key": token}, verify=False)
    r.raise_for_status()
    data = r.json()
    print(f"📦 Получено групп: {len(data)}")
    return data

# ---------- синхронизация ----------
async def sync_groups(api_rows: list[dict]) -> None:
    async with async_session() as session:
        api_ids = {r["id"] for r in api_rows if "id" in r}

        # удалить группы, которых нет в API
        db_ids = {r[0] for r in await session.execute(select(NomenclatureGroup.id))}
        ids_to_delete = db_ids - api_ids
        if ids_to_delete:
            await session.execute(
                NomenclatureGroup.__table__.delete()
                .where(NomenclatureGroup.id.in_(ids_to_delete))
            )

        rows = [
            {
                "id":          r["id"],
                "name":        r.get("name"),
                "parentgroup": r.get("parentGroup"),
            }
            for r in api_rows
            if "id" in r
        ]

        stmt = pg_insert(NomenclatureGroup).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name":        stmt.excluded.name,
                "parentgroup": stmt.excluded.parentgroup,
            },
        )
        await session.execute(upsert)
        await session.commit()

        total = await session.scalar(
            select(func.count()).select_from(NomenclatureGroup)
        )
        print(f"✅ Синхронизировано, групп в БД: {total}")

# # ---------- локальный тест ----------
# async def main():
#     await init_groups_table()
#     data = await fetch_groups()
#     await sync_groups(data)

# if __name__ == "__main__":
#     asyncio.run(main())
