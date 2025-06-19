# accounts_db.py

import os
import httpx
import logging
from dotenv import load_dotenv
from typing import List
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column
from sqlalchemy import String, Text, Boolean, select, delete
from sqlalchemy.dialects.postgresql import JSONB

# ─────────── настройки ───────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL env var not set")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

# ─────────── ORM-модель ───────────
class Account(Base):
    __tablename__ = "accounts"

    id:      Mapped[str]  = mapped_column(String, primary_key=True)
    name:    Mapped[str]  = mapped_column(Text, nullable=True)
    code:    Mapped[str]  = mapped_column(Text, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    extra:   Mapped[dict] = mapped_column(JSONB, nullable=True)

# ───────────────────────────────
# Функции
# ───────────────────────────────
from iiko.iiko_auth import get_auth_token, get_base_url

async def init_account_table():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Таблица accounts готова.")

async def fetch_accounts():
    url = f"{get_base_url()}/resto/api/v2/entities/list"
    token = await get_auth_token()
    params = {
        "key": token,
        "rootType": "Account",
        "includeDeleted": "false"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

async def sync_accounts():
    await init_account_table()
    accounts = await fetch_accounts()

    async with async_session() as session:
        api_ids = {acc["id"] for acc in accounts if "id" in acc}
        result = await session.execute(select(Account.id))
        db_ids = {row[0] for row in result.fetchall()}
        ids_to_delete = db_ids - api_ids

        if ids_to_delete:
            await session.execute(delete(Account).where(Account.id.in_(ids_to_delete)))

        for acc in accounts:
            acc_id = acc.get("id")
            if not acc_id:
                continue

            record_data = {
                "id": acc_id,
                "name": acc.get("name"),
                "code": acc.get("code") or "",
                "deleted": acc.get("deleted", False),
                "extra": {k: v for k, v in acc.items() if k not in {"id", "name", "code", "deleted", "rootType"}},
            }

            obj = await session.get(Account, acc_id)
            if obj:
                for key, val in record_data.items():
                    setattr(obj, key, val)
            else:
                session.add(Account(**record_data))

        await session.commit()
        print(f"✅ Счета синхронизированы: {len(accounts)} элементов")
