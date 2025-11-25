"""
База данных для хранения процентов комиссии по должностям
"""
import os
from dotenv import load_dotenv
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Float, Enum, text
import enum

## ────────────── Загрузка переменных окружения и настройка БД ──────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set!")

Base = declarative_base()

## ────────────── Тип комиссии ──────────────
class CommissionType(enum.Enum):
    SALES = "sales"  # От продаж
    WRITEOFF = "writeoff"  # От расходных накладных

## ────────────── Тип оплаты ──────────────
class PaymentType(enum.Enum):
    HOURLY = "hourly"  # Почасовая (из iiko)
    PER_SHIFT = "per_shift"  # Посменная
    MONTHLY = "monthly"  # Помесячная

## ────────────── Модель процента комиссии по должности ──────────────
class PositionCommission(Base):
    __tablename__ = 'position_commissions'

    position_name = Column(String, primary_key=True)  # Название должности (уникальное)
    payment_type = Column(String(10), nullable=False, default="hourly")  # Тип оплаты: "hourly", "per_shift", "monthly"
    fixed_rate = Column(Float, nullable=True)  # Фиксированная ставка (за смену или месяц), NULL для почасовой
    commission_percent = Column(Float, nullable=False, default=0.0)  # Процент комиссии
    commission_type = Column(String(10), nullable=False, default="sales")  # Тип комиссии: "sales" или "writeoff"

## ────────────── Логгер и подключение к БД ──────────────
logger = logging.getLogger(__name__)
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

## ────────────── Инициализация таблицы ──────────────
async def init_position_commissions_db():
    async with engine.begin() as conn:
        # Создаем таблицу если её нет
        await conn.run_sync(Base.metadata.create_all)
        
        # Миграции для добавления новых колонок
        try:
            await conn.execute(text(
                """
                DO $$
                BEGIN
                    -- Добавляем commission_type если нет
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='position_commissions' AND column_name='commission_type'
                    ) THEN
                        ALTER TABLE position_commissions 
                        ADD COLUMN commission_type VARCHAR(10) DEFAULT 'sales' NOT NULL;
                    END IF;
                    
                    -- Добавляем payment_type если нет
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='position_commissions' AND column_name='payment_type'
                    ) THEN
                        ALTER TABLE position_commissions 
                        ADD COLUMN payment_type VARCHAR(10) DEFAULT 'hourly' NOT NULL;
                    END IF;
                    
                    -- Добавляем fixed_rate если нет
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='position_commissions' AND column_name='fixed_rate'
                    ) THEN
                        ALTER TABLE position_commissions 
                        ADD COLUMN fixed_rate FLOAT;
                    END IF;
                END $$;
                """
            ))
            logger.info("📦 Таблица position_commissions создана или обновлена.")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка миграции: {e}")
            logger.info("📦 Таблица position_commissions создана или уже существует.")
