"""Хранилище направлений FinTablo (кэш directionId)."""
import os
import logging
from datetime import datetime
from typing import Iterable, Optional

from dotenv import load_dotenv
from sqlalchemy import String, Integer, Text, Boolean, DateTime, select
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


class FinTabDirection(Base):
    __tablename__ = "fin_tab_directions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


async def init_fin_tab_direction_table() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Таблица fin_tab_directions готова")


async def sync_fin_tab_directions(items: Iterable[dict]) -> int:
    rows = []
    now = datetime.utcnow()
    for it in items:
        rows.append(
            {
                "id": it.get("id"),
                "name": it.get("name"),
                "parent_id": it.get("parentId"),
                "description": it.get("description"),
                "archived": bool(it.get("archived", 0)),
                "updated_at": now,
            }
        )

    if not rows:
        return 0

    async with async_session() as session:
        stmt = pg_insert(FinTabDirection).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=[FinTabDirection.id],
            set_={
                "name": stmt.excluded.name,
                "parent_id": stmt.excluded.parent_id,
                "description": stmt.excluded.description,
                "archived": stmt.excluded.archived,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await session.execute(upsert)
        await session.commit()
    return len(rows)


async def get_direction_id_by_name(name: str) -> Optional[int]:
    async with async_session() as session:
        result = await session.execute(
            select(FinTabDirection.id).where(FinTabDirection.name == name)
        )
        row = result.first()
        return row[0] if row else None
