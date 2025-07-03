import os
import sys
import httpx
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select, Column, String, Float, Integer
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

# --- Отключаем лишние логи от sslproto (в самом начале!) ---
logging.getLogger("asyncio.sslproto").setLevel(logging.CRITICAL)

# --- Для Windows: ставим нормальный event loop policy ---
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- Загружаем переменные окружения (.env) ---
load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

# --- Логирование (и в файл, и в консоль) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("iiko_store_balance.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("iiko_store_balance")

# --- SQLAlchemy-модели ---
Base = declarative_base()

class Nomenclature(Base):
    __tablename__ = "nomenclature"
    id = Column(String, primary_key=True)
    name = Column(String)

class Store(Base):
    __tablename__ = "stores"
    id = Column(String, primary_key=True)
    name = Column(String)

class NomenclatureStoreBalance(Base):
    __tablename__ = "nomenclature_store_balance"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String)
    store_id = Column(String)
    min_balance_level = Column(Float)
    max_balance_level = Column(Float)

# --- Импортируй авторизацию из своего файла ---
from iiko.iiko_auth import get_auth_token, get_base_url

# --- Получение остатков по API iiko ---
async def get_store_balance_raw(timestamp: str, department: str = None) -> list:
    base_url = get_base_url()
    token = await get_auth_token()

    params = {"key": token, "timestamp": timestamp}
    if department:
        params["department"] = department
    url = f"{base_url}/resto/api/v2/reports/balance/stores"

    logger.info(f"Запрос к iiko: {url}")
    logger.info(f"Параметры: {params}")

    async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
        response = await client.get(url, params=params)
        logger.info(f"Status code: {response.status_code}")
        response.raise_for_status()
        logger.info(f"Сырой ответ (первые 1000 символов): {response.text[:1000]}")
        return response.json()

# --- Маппинг остатков с названиями продуктов и складов ---
async def merge_with_nomenclature_and_store(raw_store_balance):
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with AsyncSession(engine) as session:
        product_ids = list({item['product'] for item in raw_store_balance})
        store_ids = list({item['store'] for item in raw_store_balance})

        stmt1 = select(Nomenclature.id, Nomenclature.name).where(Nomenclature.id.in_(product_ids))
        result1 = await session.execute(stmt1)
        nomenclature_map = {row[0]: row[1] for row in result1.all()}

        stmt2 = select(Store.id, Store.name).where(Store.id.in_(store_ids))
        result2 = await session.execute(stmt2)
        store_map = {row[0]: row[1] for row in result2.all()}

        for item in raw_store_balance:
            item['product_name'] = nomenclature_map.get(item['product'], 'НЕИЗВЕСТНО')
            item['store_name'] = store_map.get(item['store'], 'НЕИЗВЕСТНО')
        return raw_store_balance

# --- Получить лимиты по всем товарам и складам ---
async def get_min_balances_for_products(session):
    result = await session.execute(
        select(
            NomenclatureStoreBalance.product_id,
            NomenclatureStoreBalance.store_id,
            NomenclatureStoreBalance.min_balance_level
        )
    )
    # Маппинг: (product_id, store_id) -> min_balance_level
    return {(row[0], row[1]): row[2] for row in result.all() if row[2] is not None}

# --- Точка входа ---
if __name__ == "__main__":
    sys.stderr = open(os.devnull, 'w')
    async def main():
        timestamp = "2024-06-30T23:59:59"
        department_guid = "fa85468e-af74-495e-a33c-37d6562e4699"
        logger.info(f"Выгрузка остатков на складах на дату {timestamp} по подразделению {department_guid}")

        raw_balance = await get_store_balance_raw(timestamp, department=department_guid)
        merged = await merge_with_nomenclature_and_store(raw_balance)

        # --- Проверка остатков против лимитов ---
        engine = create_async_engine(DATABASE_URL, echo=False)
        async with AsyncSession(engine) as session:
            min_balances = await get_min_balances_for_products(session)
            violations = []
            for row in merged:
                key = (row["product"], row["store"])
                min_limit = min_balances.get(key)
                if min_limit is not None and row["amount"] < min_limit:
                    violations.append({**row, "min_limit": min_limit})

        for row in violations:
            print(
                f"{row['store_name']}: {row['product_name']} | Остаток: {row['amount']} < Мин.лимит: {row['min_limit']}"
            )
        logger.info(f"Нарушений лимитов: {len(violations)}")

        # --- (Необязательно) сохранить всё в Excel:
        # import pandas as pd
        # df = pd.DataFrame(violations)
        # df.to_excel("ostatki_narusheniya.xlsx", index=False)
        # logger.info("Нарушения лимитов сохранены в файл ostatki_narusheniya.xlsx")

    asyncio.run(main())
