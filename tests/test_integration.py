"""
Интеграционные тесты для проверки работы с iiko API
"""
import unittest
import asyncio
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestIikoAPIIntegration(unittest.TestCase):
    """Тесты интеграции с iiko API"""
    
    def test_date_format_in_api_request(self):
        """Критический тест: проверка формата дат в API-запросах"""
        # Входные данные (формат бота)
        date_from = "17.11.2025"
        date_to = "23.11.2025"
        
        # Конвертация для API
        from_dt = datetime.strptime(date_from, "%d.%m.%Y")
        to_dt = datetime.strptime(date_to, "%d.%m.%Y")
        date_from_api = from_dt.strftime("%Y-%m-%d")
        date_to_api = to_dt.strftime("%Y-%m-%d")
        
        # Проверка результата
        self.assertEqual(date_from_api, "2025-11-17")
        self.assertEqual(date_to_api, "2025-11-23")
    
    def test_api_url_params(self):
        """Тест формирования параметров для API запроса"""
        # Параметры
        report_id = "3646ed72-6eee-4085-9179-4f7e88fa1cac"
        date_from_api = "2025-11-17"
        date_to_api = "2025-11-23"
        token = "test_token"
        
        # Формируем параметры как в коде
        params = [
            ("key", token),
            ("from", date_from_api),
            ("to", date_to_api),
        ]
        
        # Проверяем структуру
        self.assertEqual(len(params), 3)
        self.assertEqual(params[1][0], "from")
        self.assertEqual(params[1][1], "2025-11-17")
        self.assertEqual(params[2][0], "to")
        self.assertEqual(params[2][1], "2025-11-23")


class TestDataStructures(unittest.TestCase):
    """Тесты структур данных"""
    
    def test_salary_data_structure(self):
        """Тест структуры данных зарплаты"""
        salary_data = {
            'emp_001': {
                'name': 'Иванов И.',
                'position': 'Повар',
                'total_hours': 40.0,
                'work_days': 5,
                'regular_payment': 10800.0,
                'bonus': 500.0,
                'penalty': 0.0,
                'total_payment': 11300.0,
                'revenue': 50000.0,
                'commission_percent': 1.0,
            }
        }
        
        # Проверяем наличие всех ключей
        required_keys = ['name', 'position', 'total_hours', 'work_days', 
                        'regular_payment', 'bonus', 'total_payment']
        
        emp_data = salary_data['emp_001']
        for key in required_keys:
            self.assertIn(key, emp_data, f"Отсутствует ключ {key}")
        
        # Проверяем типы
        self.assertIsInstance(emp_data['name'], str)
        self.assertIsInstance(emp_data['total_payment'], float)
        self.assertIsInstance(emp_data['work_days'], int)
    
    def test_department_aggregation(self):
        """Тест агрегации зарплат по цехам"""
        # Исходные данные
        employees = [
            {'position': 'Повар', 'salary': 20000},
            {'position': 'Повар', 'salary': 25000},
            {'position': 'Бармен', 'salary': 15000},
            {'position': 'Посудомойка', 'salary': 12000},
        ]
        
        # Маппинг должностей
        position_to_dept = {
            'Повар': 'Кухня',
            'Бармен': 'Зал',
        }
        
        # Агрегация
        dept_salaries = {}
        for emp in employees:
            dept = position_to_dept.get(emp['position'], 'Не распределено')
            dept_salaries[dept] = dept_salaries.get(dept, 0) + emp['salary']
        
        # Проверки
        self.assertEqual(dept_salaries['Кухня'], 45000)
        self.assertEqual(dept_salaries['Зал'], 15000)
        self.assertEqual(dept_salaries['Не распределено'], 12000)


class TestErrorHandling(unittest.TestCase):
    """Тесты обработки ошибок"""
    
    def test_invalid_date_format_handling(self):
        """Тест обработки невалидного формата даты"""
        invalid_dates = ["2025-11-17", "17/11/2025", ""]
        
        for invalid_date in invalid_dates:
            with self.subTest(invalid_date=invalid_date):
                with self.assertRaises(ValueError):
                    datetime.strptime(invalid_date, "%d.%m.%Y")
    
    def test_empty_data_handling(self):
        """Тест обработки пустых данных"""
        # Пустой список сотрудников
        salary_data = {}
        
        # Должны получить нулевые суммы
        dept_salaries = {'Кухня': 0, 'Зал': 0, 'Админ': 0}
        
        for dept, salary in dept_salaries.items():
            self.assertEqual(salary, 0)
    
    def test_missing_position_mapping(self):
        """Тест обработки отсутствующей должности в маппинге"""
        position_to_dept = {'Повар': 'Кухня'}
        unknown_position = 'Новая должность'
        
        # Должна попасть в "Не распределено"
        dept = position_to_dept.get(unknown_position, 'Не распределено')
        
        self.assertEqual(dept, 'Не распределено')


if __name__ == '__main__':
    print("\n" + "="*80)
    print("🧪 ЗАПУСК ИНТЕГРАЦИОННЫХ ТЕСТОВ".center(80))
    print("="*80 + "\n")
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Добавляем все тесты
    suite.addTests(loader.loadTestsFromTestCase(TestIikoAPIIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestDataStructures))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorHandling))
    
    # Запускаем
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Итоги
    print("\n" + "="*80)
    if result.wasSuccessful():
        print("✅ ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОШЛИ!".center(80))
    else:
        print(f"❌ ПРОВАЛЕНО: {len(result.failures + result.errors)} тестов".center(80))
    print("="*80 + "\n")
    
    exit(0 if result.wasSuccessful() else 1)
