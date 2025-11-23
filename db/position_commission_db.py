"""
База данных для хранения процентов комиссии по должностям
"""
import os
from dotenv import load_dotenv
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Float
from sqlalchemy.orm import sessionmaker

## ────────────── Загрузка переменных окружения и настройка БД ──────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set!")

Base = declarative_base()

## ────────────── Модель процента комиссии по должности ──────────────
class PositionCommission(Base):
    __tablename__ = 'position_commissions'

    position_name = Column(String, primary_key=True)  # Название должности (уникальное)
    commission_percent = Column(Float, nullable=False, default=0.0)  # Процент комиссии

## ────────────── Логгер и подключение к БД ──────────────
logger = logging.getLogger(__name__)
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

## ────────────── Инициализация таблицы ──────────────
async def init_position_commissions_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("📦 Таблица position_commissions создана или уже существует.")
