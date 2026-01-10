"""
Unit-тесты для проверки конвертации дат в отчётах
"""
import unittest
from datetime import datetime


class TestDateConversion(unittest.TestCase):
    """Тесты конвертации форматов дат"""
    
    def test_ddmmyyyy_to_yyyymmdd(self):
        """Тест конвертации из DD.MM.YYYY в YYYY-MM-DD"""
        # Входные данные в формате DD.MM.YYYY
        test_cases = [
            ("17.11.2025", "2025-11-17"),
            ("23.11.2025", "2025-11-23"),
            ("01.01.2025", "2025-01-01"),
            ("31.12.2025", "2025-12-31"),
            ("29.02.2024", "2024-02-29"),  # Високосный год
        ]
        
        for input_date, expected_output in test_cases:
            with self.subTest(input_date=input_date):
                # Конвертируем
                dt = datetime.strptime(input_date, "%d.%m.%Y")
                output_date = dt.strftime("%Y-%m-%d")
                
                self.assertEqual(output_date, expected_output,
                               f"Ожидалось {expected_output}, получено {output_date}")
    
    def test_invalid_date_formats(self):
        """Тест обработки невалидных дат"""
        invalid_dates = [
            "2025-11-17",  # Неправильный формат
            "32.01.2025",  # Несуществующий день
            "17/11/2025",  # Неправильный разделитель
            "",            # Пустая строка
        ]
        
        for invalid_date in invalid_dates:
            with self.subTest(invalid_date=invalid_date):
                with self.assertRaises(ValueError):
                    datetime.strptime(invalid_date, "%d.%m.%Y")
    
    def test_date_range_calculation(self):
        """Тест расчёта количества дней в периоде"""
        test_cases = [
            ("17.11.2025", "23.11.2025", 7),   # Неделя
            ("01.11.2025", "30.11.2025", 30),  # Месяц
            ("23.11.2025", "23.11.2025", 1),   # Один день
        ]
        
        for date_from, date_to, expected_days in test_cases:
            with self.subTest(date_from=date_from, date_to=date_to):
                from_dt = datetime.strptime(date_from, "%d.%m.%Y")
                to_dt = datetime.strptime(date_to, "%d.%m.%Y")
                days = (to_dt - from_dt).days + 1
                
                self.assertEqual(days, expected_days,
                               f"Ожидалось {expected_days} дней, получено {days}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
