"""
Тест получения себестоимости расходных накладных через OLAP TRANSACTIONS
"""
import asyncio
import logging
from services.writeoff_documents import get_writeoff_documents, get_writeoff_cost_olap

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    # Тестовый период
    date_from = "2025-11-17"
    date_to = "2025-11-20"
    
    print(f"\n{'='*60}")
    print(f"ТЕСТ РАСХОДНЫХ НАКЛАДНЫХ: {date_from} - {date_to}")
    print(f"{'='*60}\n")
    
    # 1. Получаем выручку из API документов
    print("1️⃣ Получение выручки из API документов...")
    docs = await get_writeoff_documents(date_from, date_to)
    revenue = sum(doc['sum'] for doc in docs)
    print(f"   Количество накладных: {len(docs)}")
    print(f"   Выручка (сумма продаж): {revenue:,.2f}₽")
    
    # 2. Получаем себестоимость через OLAP TRANSACTIONS
    print("\n2️⃣ Получение себестоимости через OLAP TRANSACTIONS...")
    cost = await get_writeoff_cost_olap(date_from, date_to)
    print(f"   Себестоимость: {cost:,.2f}₽")
    
    # 3. Рассчитываем процент
    if revenue > 0:
        percent = cost / revenue * 100
        print(f"\n📊 Процент себестоимости: {percent:.1f}%")
    
    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
