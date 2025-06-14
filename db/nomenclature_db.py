import os
import httpx
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Mapped, mapped_column
from sqlalchemy import String, Float, Boolean, JSON
from db.employees_db import save_employees, init_db
from typing import List, Dict
from iiko.iiko_auth import get_auth_token, get_base_url

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set!")

Base = declarative_base()

class Product(Base):
    __tablename__ = "products"

    id:           Mapped[str]  = mapped_column(String, primary_key=True)
    code:         Mapped[str]  = mapped_column(String, nullable=True)
    name:         Mapped[str]  = mapped_column(String)
    full_name:    Mapped[str]  = mapped_column(String, nullable=True)
    weight:       Mapped[float] = mapped_column(Float, nullable=True)
    is_deleted:   Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json:     Mapped[dict] = mapped_column(JSON, nullable=True)   # хранит весь объект «как есть»

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# ──────────────────────────────────────────────────────────────────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("📦 Таблица products создана или уже существует.")

# ──────────────────────────────────────────────────────────────────────────


async def fetch_products() -> List[Dict]:
    token = await get_auth_token()
    base_url = get_base_url()
    url   = f"{base_url}/resto/api/v2/entities/products/list"
    resp  = httpx.get(url, params={"key": token}, verify=False)
    resp.raise_for_status()
    return resp.json()


async def save_products(data: list[dict]):
    """
    Синхронизируем всё, что получили от API:
    – удаляем записи, которых больше нет
    – обновляем существующие
    – добавляем новые
    """
    async with async_session() as session:
        # какие ID уже есть в БД
        rows = (await session.execute(Product.__table__.select())).fetchall()
        existing_ids = {row[0] for row in rows}

        new_ids = {item["id"] for item in data}
        ids_to_delete = existing_ids - new_ids

        # DELETE
        if ids_to_delete:
            await session.execute(
                Product.__table__.delete().where(Product.id.in_(ids_to_delete))
            )

        # UPSERT
        for item in data:
            obj = await session.get(Product, item["id"])
            if obj:
                obj.code       = item.get("code")
                obj.name       = item.get("name")
                obj.full_name  = item.get("nameFull")
                obj.weight     = item.get("weight")
                obj.is_deleted = bool(item.get("deleted"))
                obj.raw_json   = item
            else:
                session.add(
                    Product(
                        id         = item["id"],
                        code       = item.get("code"),
                        name       = item.get("name"),
                        full_name  = item.get("nameFull"),
                        weight     = item.get("weight"),
                        is_deleted = bool(item.get("deleted")),
                        raw_json   = item,
                    )
                )
        await session.commit()
