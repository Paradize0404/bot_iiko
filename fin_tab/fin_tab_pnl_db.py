"""Хранилище статей ПиУ (FinTablo) для кэширования categoryId.
Позволяет синхронизировать список и получать id без повторных запросов к API.
"""
import os
import logging
from datetime import datetime
from typing import Iterable, Optional

from dotenv import load_dotenv
from sqlalchemy import String, Integer, Text, DateTime, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, mapped_column, Mapped, sessionmaker

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


class FinTabPnlCategory(Base):
    __tablename__ = "fin_tab_pnl_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pnl_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


async def init_fin_tab_pnl_table() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Таблица fin_tab_pnl_categories готова")


async def sync_fin_tab_pnl_categories(items: Iterable[dict]) -> int:
    """
    Upsert всех статей ПиУ. Возвращает число обработанных строк.
    """
    rows = []
    now = datetime.utcnow()
    for it in items:
        rows.append(
            {
                "id": it.get("id"),
                "name": it.get("name"),
                "type": it.get("type"),
                "pnl_type": it.get("pnlType"),
                "category_id": it.get("categoryId"),
                "comment": it.get("comment"),
                "updated_at": now,
            }
        )

    if not rows:
        return 0

    async with async_session() as session:
        stmt = pg_insert(FinTabPnlCategory).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=[FinTabPnlCategory.id],
            set_={
                "name": stmt.excluded.name,
                "type": stmt.excluded.type,
                "pnl_type": stmt.excluded.pnl_type,
                "category_id": stmt.excluded.category_id,
                "comment": stmt.excluded.comment,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await session.execute(upsert)
        await session.commit()
    return len(rows)


async def get_category_id_by_name(name: str) -> Optional[int]:
    async with async_session() as session:
        result = await session.execute(
            select(FinTabPnlCategory.id).where(FinTabPnlCategory.name == name)
        )
        row = result.first()
        return row[0] if row else None
