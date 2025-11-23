# suppliers_sync.py

import os
import httpx
import logging
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column
from sqlalchemy import String, Text, select, delete
from typing import List

from iiko.iiko_auth import get_auth_token, get_base_url

# ────── ENV ──────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL env var not set")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

logger = logging.getLogger(__name__)

# ────── ORM ──────
class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

# ────── INIT ──────
async def init_suppliers_table():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Таблица suppliers готова.")

# ────── GET API ──────
async def fetch_suppliers():
    token = await get_auth_token()
    base_url = get_base_url()
    url = f"{base_url}/resto/api/v2/suppliers"
    params = {"key": token}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()

            xml_data = response.text
            root = ET.fromstring(xml_data)

            suppliers = []
            for supplier in root.iter("employee"):
                if supplier.findtext("supplier", "false").strip().lower() != "true":
                    continue  # Пропускаем тех, кто не является поставщиком

                suppliers.append({
                    "id": supplier.findtext("id", "").strip(),
                    "code": supplier.findtext("code", "").strip(),
                    "name": supplier.findtext("name", "").strip()
                })

            logger.info(f"🔎 Найдено поставщиков: {len(suppliers)}")
            return suppliers

        except Exception as e:
            logger.exception(f"❌ Ошибка при загрузке поставщиков: {e}")
            return []

# ────── SYNC ──────
async def sync_suppliers():
    await init_suppliers_table()
    suppliers = await fetch_suppliers()

    async with async_session() as session:
        api_ids = {s["id"] for s in suppliers if "id" in s}
        if not api_ids:
            logger.warning("⚠️ Поставщики не получены.")
            return

        result = await session.execute(select(Supplier.id))
        db_ids = {row[0] for row in result.fetchall()}
        ids_to_delete = db_ids - api_ids

        if ids_to_delete:
            await session.execute(delete(Supplier).where(Supplier.id.in_(ids_to_delete)))

        for s in suppliers:
            s_id = s.get("id")
            if not s_id:
                continue
            obj = await session.get(Supplier, s_id)
            if obj:
                obj.name = s.get("name", "")
                obj.code = s.get("code", "")
            else:
                session.add(Supplier(id=s_id, name=s.get("name", ""), code=s.get("code", "")))

        await session.commit()
        logger.info(f"✅ Синхронизировано поставщиков: {len(suppliers)}")
