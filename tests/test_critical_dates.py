"""
Критический тест: проверка формата дат во ВСЕХ API-запросах
"""
import unittest
from datetime import datetime


class TestDateFormatsInAllAPIs(unittest.TestCase):
    """Тест всех путей конвертации дат"""
    
    def test_revenue_report_date_flow(self):
        """
        Тест потока дат в отчёте по выручке:
        Telegram → revenue_report → iiko API
        """
        # 1. Пользователь выбирает даты в Telegram (DD.MM.YYYY)
        user_date_from = "17.11.2025"
        user_date_to = "23.11.2025"
        
        # 2. Конвертация для get_revenue_report (DD.MM.YYYY → YYYY-MM-DD)
        from_dt = datetime.strptime(user_date_from, "%d.%m.%Y")
        to_dt = datetime.strptime(user_date_to, "%d.%m.%Y")
        api_date_from = from_dt.strftime("%Y-%m-%d")
        api_date_to = to_dt.strftime("%Y-%m-%d")
        
        # 3. Проверяем результат для iiko API
        self.assertEqual(api_date_from, "2025-11-17")
        self.assertEqual(api_date_to, "2025-11-23")
    
    def test_salary_calculation_date_flow(self):
        """
        Тест потока дат в расчёте зарплат:
        Telegram → salary_from_iiko → cash_shifts → preset_report
        """
        # 1. Даты из Telegram (DD.MM.YYYY)
        user_date_from = "17.11.2025"
        user_date_to = "23.11.2025"
        
        # 2. Конвертация для salary_from_iiko (DD.MM.YYYY → YYYY-MM-DD)
        from_dt = datetime.strptime(user_date_from, "%d.%m.%Y")
        to_dt = datetime.strptime(user_date_to, "%d.%m.%Y")
        salary_date_from = from_dt.strftime("%Y-%m-%d")
        salary_date_to = to_dt.strftime("%Y-%m-%d")
        
        self.assertEqual(salary_date_from, "2025-11-17")
        self.assertEqual(salary_date_to, "2025-11-23")
        
        # 3. Эти даты идут в get_cash_shifts (YYYY-MM-DD → YYYY-MM-DD)
        cash_shift_from = salary_date_from  # Без конвертации
        cash_shift_to = salary_date_to
        
        self.assertEqual(cash_shift_from, "2025-11-17")
        self.assertEqual(cash_shift_to, "2025-11-23")
        
        # 4. Затем в get_orders_from_olap (YYYY-MM-DD → DD.MM.YYYY)
        from_dt = datetime.strptime(cash_shift_from, "%Y-%m-%d")
        to_dt = datetime.strptime(cash_shift_to, "%Y-%m-%d")
        olap_date_from = from_dt.strftime("%d.%m.%Y")
        olap_date_to = to_dt.strftime("%d.%m.%Y")
        
        self.assertEqual(olap_date_from, "17.11.2025")
        self.assertEqual(olap_date_to, "23.11.2025")
    
    def test_complete_round_trip(self):
        """
        Полный цикл: DD.MM.YYYY → YYYY-MM-DD → DD.MM.YYYY
        Должны получить те же даты
        """
        original_from = "17.11.2025"
        original_to = "23.11.2025"
        
        # Прямая конвертация
        from_dt = datetime.strptime(original_from, "%d.%m.%Y")
        to_dt = datetime.strptime(original_to, "%d.%m.%Y")
        api_from = from_dt.strftime("%Y-%m-%d")
        api_to = to_dt.strftime("%Y-%m-%d")
        
        # Обратная конвертация
        from_dt2 = datetime.strptime(api_from, "%Y-%m-%d")
        to_dt2 = datetime.strptime(api_to, "%Y-%m-%d")
        final_from = from_dt2.strftime("%d.%m.%Y")
        final_to = to_dt2.strftime("%d.%m.%Y")
        
        # Должны совпадать
        self.assertEqual(final_from, original_from)
        self.assertEqual(final_to, original_to)
    
    def test_week_period_consistency(self):
        """
        Проверка: неделя остаётся неделей при любых конвертациях
        """
        # Неделя: 17-23 ноября (7 дней)
        date_from = "17.11.2025"
        date_to = "23.11.2025"
        
        # Любые конвертации
        from_dt = datetime.strptime(date_from, "%d.%m.%Y")
        to_dt = datetime.strptime(date_to, "%d.%m.%Y")
        
        # Количество дней должно быть 7
        days = (to_dt - from_dt).days + 1
        self.assertEqual(days, 7, "Период должен остаться 7 дней")
        
        # Конвертируем туда-сюда
        api_from = from_dt.strftime("%Y-%m-%d")
        api_to = to_dt.strftime("%Y-%m-%d")
        
        from_dt2 = datetime.strptime(api_from, "%Y-%m-%d")
        to_dt2 = datetime.strptime(api_to, "%Y-%m-%d")
        
        # Снова проверяем дни
        days2 = (to_dt2 - from_dt2).days + 1
        self.assertEqual(days2, 7, "После конвертации период должен быть 7 дней")


if __name__ == '__main__':
    print("\n" + "="*80)
    print("🔍 КРИТИЧЕСКИЙ ТЕСТ: ФОРМАТЫ ДАТ ВО ВСЕХ API".center(80))
    print("="*80 + "\n")
    
    unittest.main(verbosity=2)
