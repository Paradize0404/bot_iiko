"""
Тест полного отчёта по выручке с расходными накладными
"""
import asyncio
import logging
from utils.db_stores import init_pool
from services.revenue_report import get_revenue_report, calculate_revenue, format_revenue_report

logging.basicConfig(level=logging.INFO, format='%(message)s')


async def main():
    # Инициализируем БД
    await init_pool()
    
    date_from = '2025-11-17'
    date_to = '2025-11-20'
    
    print(f"\n{'='*60}")
    print(f"ПОЛНЫЙ ОТЧЁТ ПО ВЫРУЧКЕ: {date_from} - {date_to}")
    print(f"{'='*60}\n")
    
    print("📊 Загрузка данных из OLAP SALES...")
    data = await get_revenue_report(date_from, date_to)
    
    print("📈 Расчёт выручки и себестоимости...")
    revenue = await calculate_revenue(data, date_from, date_to)
    
    print("\n" + "="*60)
    print("ОТЧЁТ ДЛЯ TELEGRAM:")
    print("="*60)
    
    report = format_revenue_report(revenue, date_from, date_to)
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
