"""
Тест полного расчёта расходных накладных
"""
import asyncio
from services.revenue_report import calculate_writeoffs


async def main():
    data = await calculate_writeoffs('2025-11-17', '2025-11-20')
    
    print("\n" + "="*60)
    print("РАСХОДНЫЕ НАКЛАДНЫЕ (17.11.2025 - 20.11.2025)")
    print("="*60)
    print(f"  Выручка: {data['writeoff_revenue']:,.2f} руб")
    print(f"  Себестоимость: {data['writeoff_cost']:,.2f} руб ({data['writeoff_cost_percent']:.1f}%)")
    print(f"  Количество: {data['writeoff_count']} шт")
    print(f"  Дней без накладных: {data['days_without_writeoff']} из {data['total_days']}")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
