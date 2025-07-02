import os
import sys
import httpx
import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select, Column, String
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

# --- Точка входа ---
if __name__ == "__main__":
    sys.stderr = open(os.devnull, 'w')
    async def main():
        timestamp = "2024-06-30T23:59:59"
        department_guid = "fa85468e-af74-495e-a33c-37d6562e4699"
        logger.info(f"Выгрузка остатков на складах на дату {timestamp} по подразделению {department_guid}")

        raw_balance = await get_store_balance_raw(timestamp, department=department_guid)
        merged = await merge_with_nomenclature_and_store(raw_balance)

        # Выводим 10 строк для проверки
        for row in merged[:10]:
            print(row)
        logger.info(f"Показано {min(10, len(merged))} строк из {len(merged)} остатков")

        # --- (Необязательно) сохранить всё в Excel:
        # import pandas as pd
        # df = pd.DataFrame(merged)
        # df.to_excel("ostatki.xlsx", index=False)
        # logger.info("Остатки сохранены в файл ostatki.xlsx")

    asyncio.run(main())
