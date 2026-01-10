"""
Тестовый скрипт для проверки корректности дат в отчёте по выручке
Проверяет, что API получает даты в правильном формате YYYY-MM-DD
"""
import asyncio
import logging
from datetime import datetime, timedelta
from services.revenue_report import get_revenue_report, calculate_revenue
from utils.db_stores import init_pool, close_pool

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


async def test_date_range(date_from: str, date_to: str, description: str):
    """
    Тестирует отчёт для указанного периода
    
    Args:
        date_from: дата начала в формате DD.MM.YYYY
        date_to: дата конца в формате DD.MM.YYYY
        description: описание теста
    """
    print("\n" + "="*80)
    print(f"📊 ТЕСТ: {description}")
    print(f"📅 Период: {date_from} - {date_to}")
    print("="*80)
    
    try:
        # Получаем данные отчёта
        raw_data = await get_revenue_report(date_from, date_to)
        
        # Рассчитываем выручку
        revenue_data = await calculate_revenue(raw_data, date_from, date_to)
        
        # Выводим результаты
        print(f"\n✅ УСПЕШНО: Получено {len(raw_data)} строк данных")
        print(f"\n💰 Результаты:")
        print(f"   🍹 БАР: {revenue_data['bar_revenue']:,.2f} ₽")
        print(f"   🍕 КУХНЯ: {revenue_data['kitchen_revenue']:,.2f} ₽")
        print(f"   🚗 ДОСТАВКА: {revenue_data['delivery_revenue']:,.2f} ₽")
        print(f"   💵 ИТОГО: {revenue_data['bar_revenue'] + revenue_data['kitchen_revenue'] + revenue_data['delivery_revenue']:,.2f} ₽")
        print(f"   📦 Расходные: {revenue_data['writeoff_sum']:,.2f} ₽ ({revenue_data['writeoff_count']} шт.)")
        
        # Конвертируем даты для проверки
        from_dt = datetime.strptime(date_from, "%d.%m.%Y")
        to_dt = datetime.strptime(date_to, "%d.%m.%Y")
        days = (to_dt - from_dt).days + 1
        print(f"\n📈 Статистика:")
        print(f"   Дней в периоде: {days}")
        print(f"   Средняя выручка в день: {(revenue_data['bar_revenue'] + revenue_data['kitchen_revenue'] + revenue_data['delivery_revenue']) / days:,.2f} ₽")
        
        return True
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Запускает все тесты"""
    print("\n" + "🧪 ТЕСТИРОВАНИЕ ОТЧЁТА ПО ВЫРУЧКЕ С РАЗНЫМИ ДАТАМИ ".center(80, "="))
    print("Цель: проверить, что даты корректно конвертируются в формат iiko API")
    print("="*80)
    
    # Инициализируем пул соединений БД
    try:
        await init_pool()
        logger.info("✅ Пул соединений БД инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        return
    
    try:
        # Текущая дата для расчётов
        today = datetime.now()
        
        # Список тестов
        tests = [
            # 1. Один день
            {
                "date_from": "23.11.2025",
                "date_to": "23.11.2025",
                "description": "Один день (сегодня)"
            },
            # 2. Неделя (как в вашем запросе)
            {
                "date_from": "17.11.2025",
                "date_to": "23.11.2025",
                "description": "Неделя (17-23 ноября)"
            },
            # 3. Месяц (ноябрь полностью)
            {
                "date_from": "01.11.2025",
                "date_to": "23.11.2025",
                "description": "Месяц (1-23 ноября)"
            },
            # 4. Вчера
            {
                "date_from": (today - timedelta(days=1)).strftime("%d.%m.%Y"),
                "date_to": (today - timedelta(days=1)).strftime("%d.%m.%Y"),
                "description": "Вчера"
            },
            # 5. Последние 3 дня
            {
                "date_from": (today - timedelta(days=2)).strftime("%d.%m.%Y"),
                "date_to": today.strftime("%d.%m.%Y"),
                "description": "Последние 3 дня"
            }
        ]
        
        # Запускаем тесты
        results = []
        for i, test in enumerate(tests, 1):
            print(f"\n\n{'='*80}")
            print(f"▶️  ТЕСТ {i}/{len(tests)}")
            success = await test_date_range(
                test["date_from"],
                test["date_to"],
                test["description"]
            )
            results.append((test["description"], success))
            
            # Пауза между тестами
            if i < len(tests):
                await asyncio.sleep(2)
        
        # Итоги
        print("\n\n" + "="*80)
        print("📋 ИТОГИ ТЕСТИРОВАНИЯ".center(80))
        print("="*80)
        
        passed = sum(1 for _, success in results if success)
        failed = len(results) - passed
        
        for i, (desc, success) in enumerate(results, 1):
            status = "✅ PASSED" if success else "❌ FAILED"
            print(f"{i}. {status} - {desc}")
        
        print("\n" + "="*80)
        print(f"Пройдено: {passed}/{len(results)}")
        print(f"Провалено: {failed}/{len(results)}")
        
        if failed == 0:
            print("\n🎉 ВСЕ ТЕСТЫ ПРОШЛИ УСПЕШНО!")
        else:
            print(f"\n⚠️  {failed} тест(ов) провалились")
        print("="*80 + "\n")
        
    finally:
        # Закрываем пул соединений
        await close_pool()
        logger.info("✅ Пул соединений БД закрыт")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Тестирование прервано пользователем")
    except Exception as e:
        print(f"\n\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
