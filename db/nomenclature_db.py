# nomenclature_db.py
import os, httpx, asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Mapped, mapped_column, declarative_base
from sqlalchemy import String, Float, select, func, text, ForeignKey
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ─────────── настройки ───────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL env var not set")

engine        = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

# ─────────── ORM-модели ───────────
class Nomenclature(Base):
    __tablename__ = "nomenclature"

    id:       Mapped[str] = mapped_column(String, primary_key=True)
    name:     Mapped[str] = mapped_column(String)
    parent:   Mapped[str] = mapped_column(String, nullable=True)
    mainunit: Mapped[str] = mapped_column(String, nullable=True)
    type:     Mapped[str] = mapped_column(String, nullable=True)

class NomenclatureStoreBalance(Base):   # ⬅️ NEW
    __tablename__ = "nomenclature_store_balance"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String, ForeignKey("nomenclature.id"))
    store_id: Mapped[str] = mapped_column(String)
    min_balance_level: Mapped[float] = mapped_column(Float, nullable=True)
    max_balance_level: Mapped[float] = mapped_column(Float, nullable=True)

# ──────────────────────────────────
# 1. инициализация (создать таблицы, если нет)
# ──────────────────────────────────

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nomenclature (
    id        VARCHAR PRIMARY KEY,
    name      TEXT NOT NULL,
    parent    VARCHAR,
    mainunit  VARCHAR,
    type      VARCHAR
);
"""

ALTER_SQL = """
ALTER TABLE nomenclature
    ADD COLUMN IF NOT EXISTS parent   VARCHAR,
    ADD COLUMN IF NOT EXISTS mainunit VARCHAR,
    ADD COLUMN IF NOT EXISTS type VARCHAR;
"""

CREATE_BALANCE_SQL = """         -- ⬅️ NEW
CREATE TABLE IF NOT EXISTS nomenclature_store_balance (
    id SERIAL PRIMARY KEY,
    product_id VARCHAR NOT NULL REFERENCES nomenclature(id),
    store_id VARCHAR NOT NULL,
    min_balance_level FLOAT,
    max_balance_level FLOAT
);
"""

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_SQL))
        await conn.execute(text(ALTER_SQL))
        await conn.execute(text(CREATE_BALANCE_SQL))    # ⬅️ NEW
    print("✅ Таблицы nomenclature и balances готовы.")

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
# 3. синхронизация основной таблицы
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
                "type":     r.get("type")
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
                "type":     stmt.excluded.type,
            },
        )
        await session.execute(upsert)
        await session.commit()

        total = await session.scalar(select(func.count()).select_from(Nomenclature))
        print(f"✅ Синхронизировано, записей в БД: {total}")

# ──────────────────────────────────
# 4. синхронизация балансов (storeBalanceLevels)   ⬅️ NEW
# ──────────────────────────────────
async def sync_store_balances(api_rows: list[dict]):
    async with async_session() as session:
        balances = []
        for r in api_rows:
            product_id = r.get("id")
            for s in r.get("storeBalanceLevels", []):
                min_bal = s.get("minBalanceLevel")
                max_bal = s.get("maxBalanceLevel")
                # Записываем только если есть хоть одно НЕ null значение
                if min_bal is not None or max_bal is not None:
                    balances.append({
                        "product_id": product_id,
                        "store_id": s.get("storeId"),
                        "min_balance_level": min_bal,
                        "max_balance_level": max_bal,
                    })

        product_ids = {b["product_id"] for b in balances if b["product_id"]}
        if product_ids:
            await session.execute(
                NomenclatureStoreBalance.__table__.delete().where(
                    NomenclatureStoreBalance.product_id.in_(product_ids)
                )
            )
        if balances:
            await session.execute(
                NomenclatureStoreBalance.__table__.insert(),
                balances
            )
        await session.commit()
        print(f"✅ Синхронизировано store balances для {len(product_ids)} товаров. Записано {len(balances)} балансов.")

# ──────────────────────────────────
# 5. точка входа
# ──────────────────────────────────
async def main():
    await init_db()
    data = await fetch_nomenclature()
    await sync_nomenclature(data)
    await sync_store_balances(data)  # ⬅️ NEW

if __name__ == "__main__":
    asyncio.run(main())
