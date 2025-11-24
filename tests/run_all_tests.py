"""
Главный скрипт для запуска всех тестов перед деплоем
"""
import sys
import subprocess
from pathlib import Path


def run_test_file(test_file: str, description: str) -> bool:
    """Запускает отдельный тестовый файл"""
    print(f"\n{'='*80}")
    print(f"📋 {description}".center(80))
    print('='*80)
    
    try:
        result = subprocess.run(
            [sys.executable, test_file],
            capture_output=False,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Ошибка запуска теста: {e}")
        return False


def main():
    """Запускает все тесты"""
    print("\n" + "🚀 ЗАПУСК ВСЕХ ТЕСТОВ ПЕРЕД ДЕПЛОЕМ ".center(80, "="))
    print("="*80)
    
    tests_dir = Path(__file__).parent
    
    # Список тестов
    test_suite = [
        (tests_dir / "test_date_conversion.py", "Тесты конвертации дат"),
        (tests_dir / "test_salary_logic.py", "Тесты бизнес-логики зарплат"),
        (tests_dir / "test_integration.py", "Интеграционные тесты"),
    ]
    
    results = []
    
    # Запускаем каждый набор тестов
    for test_file, description in test_suite:
        if test_file.exists():
            success = run_test_file(str(test_file), description)
            results.append((description, success))
        else:
            print(f"\n⚠️  Файл {test_file} не найден, пропускаем...")
            results.append((description, False))
    
    # Итоговый отчёт
    print("\n\n" + "="*80)
    print("📊 ИТОГОВЫЙ ОТЧЁТ".center(80))
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
        print("\n" + "🎉 ВСЕ ТЕСТЫ ПРОШЛИ! СИСТЕМА ГОТОВА К ЗАПУСКУ! 🚀".center(80))
        print("="*80 + "\n")
        return 0
    else:
        print("\n" + f"⚠️  {failed} НАБОР(ОВ) ТЕСТОВ ПРОВАЛИЛИСЬ!".center(80))
        print("Исправьте ошибки перед запуском бота!".center(80))
        print("="*80 + "\n")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
