# nomenclature_db.py
import os, httpx, asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Mapped, mapped_column, declarative_base
from sqlalchemy import String, select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ─────────── настройки ───────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL env var not set")

engine        = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

# ─────────── ORM-модель ───────────
class Nomenclature(Base):
    __tablename__ = "nomenclature"

    id:       Mapped[str] = mapped_column(String, primary_key=True)
    name:     Mapped[str] = mapped_column(String)
    parent:   Mapped[str] = mapped_column(String, nullable=True)
    mainunit: Mapped[str] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=True)

# ──────────────────────────────────
# 1. инициализация (создать таблицу, если нет; добавить новые столбцы)
# ──────────────────────────────────

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nomenclature (
    id        VARCHAR PRIMARY KEY,
    name      TEXT NOT NULL,
    parent    VARCHAR,
    mainunit  VARCHAR,
    mainunit  VARCHAR
);
"""

ALTER_SQL = """
ALTER TABLE nomenclature
    ADD COLUMN IF NOT EXISTS parent   VARCHAR,
    ADD COLUMN IF NOT EXISTS mainunit VARCHAR,
    ADD COLUMN IF NOT EXISTS type VARCHAR;
"""

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_SQL))
        await conn.execute(text(ALTER_SQL))
    print("✅ Таблица nomenclature готова.")

# async def init_db():
#     async with engine.begin() as conn:
#         await conn.execute(text(INIT_SQL))
#     print("✅ Таблица nomenclature готова.")

# ──────────────────────────────────
# 2. получаем данные из iiko
# ──────────────────────────────────
from iiko.iiko_auth import get_auth_token, get_base_url       # <- твои функции

async def fetch_nomenclature():
    token    = await get_auth_token()
    base_url = get_base_url()
    url      = f"{base_url}/resto/api/v2/entities/products/list"
    r        = httpx.get(url, params={"key": token}, verify=False)
    r.raise_for_status()
    data = r.json()
    print(f"📦 Получено: {len(data)} позиций")
    return data

# ──────────────────────────────────
# 3. синхронизация таблицы
# ──────────────────────────────────
async def sync_nomenclature(api_rows: list[dict]):
    async with async_session() as session:
        # ——— множество ID из ответа API
        api_ids = {row["id"] for row in api_rows if "id" in row}
        if not api_ids:
            print("⚠️ В ответе нет id – выхожу.")
            return

        # ——— удалить записи, которых больше нет в API
        db_ids = {r[0] for r in await session.execute(select(Nomenclature.id))}
        ids_to_delete = db_ids - api_ids
        if ids_to_delete:
            await session.execute(
                Nomenclature.__table__.delete().where(Nomenclature.id.in_(ids_to_delete))
            )

        # ——— подготовить строки для UPSERT
        rows = [
            {
                "id":       r["id"],
                "name":     r.get("name"),
                "parent":   r.get("parent"),
                "mainunit": r.get("mainUnit"),
                "type": r.get("type")
            }
            for r in api_rows
            if "id" in r
        ]

        stmt = pg_insert(Nomenclature).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name":     stmt.excluded.name,
                "parent":   stmt.excluded.parent,
                "mainunit": stmt.excluded.mainunit,
                "type": stmt.excluded.type,
            },
        )
        await session.execute(upsert)
        await session.commit()

        total = await session.scalar(select(func.count()).select_from(Nomenclature))
        print(f"✅ Синхронизировано, записей в БД: {total}")

# # ──────────────────────────────────
# # 4. пример точка входа (вызывается из твоего /load_products)
# # ──────────────────────────────────
# async def main():
#     await init_db()                        # гарантируем схему
#     data = await fetch_nomenclature()      # тянем из iiko
#     await sync_nomenclature(data)          # обновляем таблицу

# # для локального теста: python nomenclature_db.py
# if __name__ == "__main__":
#     asyncio.run(main())
