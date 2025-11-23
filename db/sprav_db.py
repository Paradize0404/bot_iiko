# sprav_db.py

import os, logging, httpx
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column
from sqlalchemy import String, Text, Boolean, select, delete
from sqlalchemy.dialects.postgresql import JSONB
from typing import List

# ─────────── настройки ───────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL env var not set")

engine        = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

logger = logging.getLogger(__name__)

# ─────────── ORM-модель ───────────
class ReferenceData(Base):
    __tablename__ = "reference_data"

    id:        Mapped[str] = mapped_column(String, primary_key=True)
    root_type: Mapped[str] = mapped_column(Text, nullable=False)
    name:      Mapped[str] = mapped_column(Text, nullable=True)
    code:      Mapped[str] = mapped_column(Text, nullable=True)
    deleted:   Mapped[bool] = mapped_column(Boolean, default=False)
    extra:     Mapped[dict] = mapped_column(JSONB, nullable=True)



REFERENCE_TYPES = [
    "Account",
    "Conception",
    "CookingPlaceType",
    "DiscountType",
    "MeasureUnit",
    "PaymentType"
]

# ──────────────────────────────────
# 1. Инициализация таблицы
# ──────────────────────────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Таблица reference_data готова.")

# ──────────────────────────────────
# 2. Синхронизация одной группы справочников
# ──────────────────────────────────


async def get_sprav_data():
    base_url = get_base_url()
    token = await get_auth_token()
    results = {}

    async with httpx.AsyncClient() as client:
        for root_type in REFERENCE_TYPES:
            url = f"{base_url}/resto/api/v2/entities/list"
            params = {
                "key": token,
                "rootType": root_type,
                "includeDeleted": "false"
            }
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                results[root_type] = r.json()
            except Exception as e:
                logger.exception(f"❌ Ошибка при загрузке {root_type}: {e}")

    return results

async def sync_reference_type(root_type: str, entries: List[dict]):
    async with async_session() as session:
        api_ids = {entry["id"] for entry in entries if "id" in entry}
        if not api_ids:
            logger.warning(f"⚠️ Пустой справочник: {root_type}")
            return

        result = await session.execute(
            select(ReferenceData.id).where(ReferenceData.root_type == root_type)
        )
        db_ids = {row[0] for row in result.fetchall()}
        ids_to_delete = db_ids - api_ids

        if ids_to_delete:
            await session.execute(
                delete(ReferenceData)
                .where(ReferenceData.root_type == root_type)
                .where(ReferenceData.id.in_(ids_to_delete))
            )

        for entry in entries:
            entry_id = entry.get("id")
            if not entry_id:
                continue

            record_data = {
                "id": entry_id,
                "root_type": root_type,
                "name": entry.get("name"),
                "code": entry.get("code") or "",
                "deleted": entry.get("deleted", False),
                "extra": {k: v for k, v in entry.items() if k not in {"id", "name", "code", "deleted", "rootType"}},
            }

            obj = await session.get(ReferenceData, entry_id)
            if obj:
                for key, val in record_data.items():
                    setattr(obj, key, val)
            else:
                session.add(ReferenceData(**record_data))

        await session.commit()
        logger.info(f"✅ Обновлено: {root_type} ({len(entries)} элементов)")

# ──────────────────────────────────
# 3. Синхронизация всех справочников
# ──────────────────────────────────
from iiko.iiko_auth import get_auth_token, get_base_url
async def sync_all_references():
      # импорт внутри, чтобы избежать циклов

    await init_db()
    data = await get_sprav_data()

    for key, entries in data.items():
        await sync_reference_type(key, entries)

    logger.info("✅ Все справочники синхронизированы.")
