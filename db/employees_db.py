import os
from dotenv import load_dotenv
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Float, Boolean
from sqlalchemy.orm import sessionmaker



## ────────────── Загрузка переменных окружения и настройка БД ──────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set!")


Base = declarative_base()

## ────────────── Модель сотрудника ──────────────
class Employee(Base):
    __tablename__ = 'employees'

    id = Column(String, primary_key=True)
    first_name = Column(String)
    last_name = Column(String)
    rate = Column(Float)
    commission_percent = Column(Float)
    monthly = Column(Boolean)
    per_shift = Column(Boolean)
    department = Column(String)
    telegram_id = Column(String, nullable=True)


## ────────────── Логгер и подключение к БД ──────────────
logger = logging.getLogger(__name__)
logger.info("🔌 Подключение к PostgreSQL…")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

## ────────────── Инициализация таблицы сотрудников ──────────────
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logging.getLogger(__name__).info("📦 Таблица employees создана или уже существует.")

## ────────────── Сохранение сотрудников в БД ──────────────
async def save_employees(employees_data: list[dict]):
    async with async_session() as session:
        # Получаем все существующие ID из базы
        result = await session.execute(Employee.__table__.select())
        rows = result.fetchall()
        existing_ids = {row[0] for row in rows}  # row[0] — это id, т.к. он первый в таблице

        new_ids = {emp["id"] for emp in employees_data}
        ids_to_delete = existing_ids - new_ids

        # Удаляем отсутствующих
        if ids_to_delete:
            await session.execute(
                Employee.__table__.delete().where(Employee.id.in_(ids_to_delete))
            )

        # Обновляем/добавляем
        for emp in employees_data:
            obj = await session.get(Employee, emp["id"])
            if obj:
                obj.first_name = emp["first_name"]
                obj.last_name = emp["last_name"]
            else:
                session.add(Employee(
                    id=emp["id"],
                    first_name=emp["first_name"],
                    last_name=emp["last_name"]
                ))

        await session.commit()