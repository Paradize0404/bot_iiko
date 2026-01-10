"""Хранилище сотрудников FinTablo."""
import os
import logging
from datetime import datetime
from typing import Iterable

from dotenv import load_dotenv
from sqlalchemy import Integer, String, DateTime
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


class FinTabEmployee(Base):
    __tablename__ = "fin_tab_employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    post: Mapped[str | None] = mapped_column(String, nullable=True)
    direction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


async def init_fin_tab_employee_table() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ Таблица fin_tab_employees готова")


async def sync_fin_tab_employees(items: Iterable[dict]) -> int:
    rows = []
    now = datetime.utcnow()
    for it in items:
        if not it.get("id"):
            continue
        rows.append(
            {
                "id": it.get("id"),
                "name": it.get("name") or "",
                "department": it.get("department"),
                "post": it.get("post"),
                "direction_id": it.get("direction_id"),
                "type": it.get("type"),
                "percentage": it.get("percentage"),
                "updated_at": now,
            }
        )

    if not rows:
        return 0

    async with async_session() as session:
        stmt = pg_insert(FinTabEmployee).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=[FinTabEmployee.id],
            set_={
                "name": stmt.excluded.name,
                "department": stmt.excluded.department,
                "post": stmt.excluded.post,
                "direction_id": stmt.excluded.direction_id,
                "type": stmt.excluded.type,
                "percentage": stmt.excluded.percentage,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await session.execute(upsert)
        await session.commit()
    return len(rows)
