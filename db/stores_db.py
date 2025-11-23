"""
Синхронизация таблицы stores
из энд‑пойнта /resto/api/corporation/stores
Аналогична db/groups_db.py, но с учётом XML‑ответа и фильтрации по департаменту.
"""

import os, asyncio, httpx, xml.etree.ElementTree as ET
import logging
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

# необязательный фильтр по департаменту (можно не задавать)
DEPARTMENT_ID = os.getenv("DEPARTMENT_ID")

engine        = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

logger = logging.getLogger(__name__)

# ---------- ORM‑модель ----------
class Store(Base):
    __tablename__ = "stores"

    id:       Mapped[str] = mapped_column(String, primary_key=True)
    code:     Mapped[str] = mapped_column(String, nullable=True)
    name:     Mapped[str] = mapped_column(String)
    type:     Mapped[str] = mapped_column(String, nullable=True)
    parentid: Mapped[str] = mapped_column(String, nullable=True)

# ---------- инициализация схемы ----------
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS stores (
    id       VARCHAR PRIMARY KEY,
    code     TEXT,
    name     TEXT NOT NULL,
    type     TEXT,
    parentid VARCHAR
);
"""

async def init_stores_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(CREATE_SQL))
    logger.info("✅ Таблица stores готова")

# ---------- работа с iiko ----------
from iiko.iiko_auth import get_auth_token, get_base_url  # уже есть в проекте

def _parse_xml(xml_data: str, department_id: str | None = None) -> list[dict]:
    """Преобразует XML ответ iiko в список dict и фильтрует по департаменту"""
    tree = ET.fromstring(xml_data)
    rows: list[dict] = []
    for item in tree.findall("corporateItemDto"):
        if department_id and item.findtext("parentId") != department_id:
            continue
        rows.append(
            {
                "id":       item.findtext("id"),
                "code":     item.findtext("code"),
                "name":     item.findtext("name"),
                "type":     item.findtext("type"),
                "parentid": item.findtext("parentId"),
            }
        )
    return rows

async def fetch_stores() -> list[dict]:
    token    = await get_auth_token()
    base_url = get_base_url()
    url      = f"{base_url}/resto/api/corporation/stores"
    r        = httpx.get(url, params={"key": token, "revisionFrom": -1}, verify=False)
    r.raise_for_status()
    xml_data = r.text
    rows = _parse_xml(xml_data, DEPARTMENT_ID)
    logger.info(f"📦 Получено складов: {len(rows)}")
    return rows

# ---------- синхронизация ----------
async def sync_stores(api_rows: list[dict]) -> None:
    async with async_session() as session:
        api_ids = {r["id"] for r in api_rows if r.get("id")}

        # удалить склады, которых нет в API
        db_ids = {r[0] for r in await session.execute(select(Store.id))}
        ids_to_delete = db_ids - api_ids
        if ids_to_delete:
            await session.execute(
                Store.__table__.delete().where(Store.id.in_(ids_to_delete))
            )

        rows = [
            {
                "id":       r["id"],
                "code":     r.get("code"),
                "name":     r.get("name"),
                "type":     r.get("type"),
                "parentid": r.get("parentid"),
            }
            for r in api_rows
            if r.get("id")
        ]

        stmt   = pg_insert(Store).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "code":     stmt.excluded.code,
                "name":     stmt.excluded.name,
                "type":     stmt.excluded.type,
                "parentid": stmt.excluded.parentid,
            },
        )
        await session.execute(upsert)
        await session.commit()

        total = await session.scalar(select(func.count()).select_from(Store))
        logger.info(f"✅ Синхронизировано, складов в БД: {total}")

# ---------- локальный тест ----------
# async def main():
#     await init_stores_table()
#     data = await fetch_stores()
#     await sync_stores(data)
#
# if __name__ == "__main__":
#     asyncio.run(main())
