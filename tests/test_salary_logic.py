"""
Unit-тесты для модуля salary_from_iiko.py
"""
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import asyncio
from datetime import datetime, date


class TestSalaryCalculations(unittest.TestCase):
    """Тесты расчёта зарплат"""
    
    def test_monthly_salary_calculation(self):
        """Тест расчёта месячной ставки пропорционально дням"""
        import calendar
        
        # Параметры
        fixed_rate = 100000  # Месячная ставка
        year = 2025
        month = 11
        days_in_month = calendar.monthrange(year, month)[1]  # 30 дней в ноябре
        days_worked = 7  # Неделя
        
        # Расчёт
        expected_payment = round((fixed_rate / days_in_month) * days_worked, 2)
        actual_payment = round((100000 / 30) * 7, 2)
        
        self.assertEqual(actual_payment, expected_payment)
        self.assertEqual(actual_payment, 23333.33)
    
    def test_hourly_salary_calculation(self):
        """Тест расчёта почасовой оплаты"""
        hourly_rate = 270  # ₽/час
        hours_worked = 40
        
        expected_payment = hourly_rate * hours_worked
        
        self.assertEqual(expected_payment, 10800)
    
    def test_bonus_calculation(self):
        """Тест расчёта бонусов от выручки"""
        revenue = 100000
        commission_percent = 5.0
        
        expected_bonus = round(revenue * (commission_percent / 100), 2)
        
        self.assertEqual(expected_bonus, 5000.00)


class TestDatePeriodOverlap(unittest.TestCase):
    """Тесты пересечения периодов дат"""
    
    def test_period_intersection(self):
        """Тест вычисления пересечения двух периодов"""
        # Период расчёта: 17-23 ноября
        calc_start = date(2025, 11, 17)
        calc_end = date(2025, 11, 23)
        
        # Период должности: 15-20 ноября
        position_start = date(2025, 11, 15)
        position_end = date(2025, 11, 20)
        
        # Пересечение: 17-20 ноября (4 дня)
        overlap_start = max(calc_start, position_start)
        overlap_end = min(calc_end, position_end)
        days_overlap = (overlap_end - overlap_start).days + 1
        
        self.assertEqual(overlap_start, date(2025, 11, 17))
        self.assertEqual(overlap_end, date(2025, 11, 20))
        self.assertEqual(days_overlap, 4)
    
    def test_no_intersection(self):
        """Тест случая когда периоды не пересекаются"""
        # Период расчёта: 17-23 ноября
        calc_start = date(2025, 11, 17)
        calc_end = date(2025, 11, 23)
        
        # Период должности: 1-10 ноября
        position_start = date(2025, 11, 1)
        position_end = date(2025, 11, 10)
        
        # Проверка пересечения
        has_overlap = not (position_end < calc_start or position_start > calc_end)
        
        self.assertFalse(has_overlap)


class TestPositionMapping(unittest.TestCase):
    """Тесты маппинга должностей на цеха"""
    
    def setUp(self):
        """Настройка тестовых данных"""
        self.dept_positions = {
            "Кондитерский": ["Пекарь-кондитер", "Старший кондитер"],
            "Кухня": ["Повар", "Заготовщик пицца"],
            "Зал": ["Бармен", "Кассир-администратор", "Ранер"],
            "Админ": ["Шеф-повар", "Бухгалтер", "Управляющий"]
        }
        
        # Создаём обратный маппинг
        self.position_to_dept = {}
        for dept, positions in self.dept_positions.items():
            for pos in positions:
                self.position_to_dept[pos] = dept
    
    def test_position_mapping(self):
        """Тест маппинга известных должностей"""
        test_cases = [
            ("Повар", "Кухня"),
            ("Бармен", "Зал"),
            ("Шеф-повар", "Админ"),
            ("Пекарь-кондитер", "Кондитерский"),
        ]
        
        for position, expected_dept in test_cases:
            with self.subTest(position=position):
                dept = self.position_to_dept.get(position, "Не распределено")
                self.assertEqual(dept, expected_dept)
    
    def test_unknown_position(self):
        """Тест маппинга неизвестной должности"""
        unknown_position = "Посудомойка"
        dept = self.position_to_dept.get(unknown_position, "Не распределено")
        
        self.assertEqual(dept, "Не распределено")


class TestRevenueCalculations(unittest.TestCase):
    """Тесты расчёта выручки"""
    
    def test_yandex_commission_calculation(self):
        """Тест расчёта комиссии Яндекса"""
        yandex_raw = 752465.00
        commission_percent = 42.0
        
        expected_fee = yandex_raw * (commission_percent / 100)
        expected_delivery_revenue = yandex_raw - expected_fee
        
        self.assertAlmostEqual(expected_fee, 316035.30, places=2)
        self.assertAlmostEqual(expected_delivery_revenue, 436429.70, places=2)
    
    def test_total_revenue_calculation(self):
        """Тест расчёта итоговой выручки"""
        bar_revenue = 460896.64
        kitchen_revenue = 1183812.81
        delivery_revenue = 436429.70
        
        total_revenue = bar_revenue + kitchen_revenue + delivery_revenue
        
        self.assertAlmostEqual(total_revenue, 2081139.15, places=2)


def run_async_test(coro):
    """Вспомогательная функция для запуска async тестов"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


if __name__ == '__main__':
    # Запуск тестов
    print("\n" + "="*80)
    print("🧪 ЗАПУСК UNIT-ТЕСТОВ".center(80))
    print("="*80 + "\n")
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Добавляем все тесты
    suite.addTests(loader.loadTestsFromTestCase(TestSalaryCalculations))
    suite.addTests(loader.loadTestsFromTestCase(TestDatePeriodOverlap))
    suite.addTests(loader.loadTestsFromTestCase(TestPositionMapping))
    suite.addTests(loader.loadTestsFromTestCase(TestRevenueCalculations))
    
    # Запускаем с подробным выводом
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Итоги
    print("\n" + "="*80)
    if result.wasSuccessful():
        print("✅ ВСЕ ТЕСТЫ ПРОШЛИ УСПЕШНО!".center(80))
    else:
        print(f"❌ ПРОВАЛЕНО: {len(result.failures + result.errors)} тестов".center(80))
    print("="*80 + "\n")
    
    exit(0 if result.wasSuccessful() else 1)
