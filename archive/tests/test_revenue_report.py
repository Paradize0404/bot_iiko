"""
Тестовый скрипт для проверки отчета по выручке
"""

import asyncio
import logging
from services.revenue_report import get_revenue_report, calculate_revenue, format_revenue_report
from db.settings_db import init_settings_table, set_yandex_commission, get_yandex_commission
from utils.db_stores import init_pool

logging.basicConfig(level=logging.INFO)


async def test_revenue_report():
    """Тест получения и расчета отчета по выручке"""
    
    # Инициализация
    await init_pool()
    await init_settings_table()
    
    # Устанавливаем тестовую комиссию (если еще не установлена)
    current = await get_yandex_commission()
    if current == 0.0:
        print("Устанавливаем тестовую комиссию Яндекса: 25%")
        await set_yandex_commission(25.0)
    else:
        print(f"Текущая комиссия Яндекса: {current}%")
    
    # Даты для тестирования (ноябрь 2024)
    date_from = "01.11.2024"
    date_to = "30.11.2024"
    
    print(f"\n🔍 Получаем отчет за период: {date_from} - {date_to}")
    
    try:
        # Получаем данные
        raw_data = await get_revenue_report(date_from, date_to)
        print(f"✅ Получено {len(raw_data)} строк отчета")
        
        if raw_data:
            print("\n📝 Первые 3 строки отчета:")
            for i, row in enumerate(raw_data[:3]):
                print(f"  {i+1}. {row}")
        
        # Рассчитываем выручку
        print("\n💰 Расчет выручки...")
        revenue_data = await calculate_revenue(raw_data)
        
        # Форматируем отчет
        print("\n" + "="*60)
        report_text = format_revenue_report(revenue_data, date_from, date_to)
        print(report_text)
        print("="*60)
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_revenue_report())
