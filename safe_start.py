"""
Безопасный запуск бота с предварительными тестами
"""
import sys
import subprocess
from pathlib import Path


def run_tests() -> bool:
    """Запускает все тесты"""
    print("\n" + "="*80)
    print("🧪 ЗАПУСК ТЕСТОВ ПЕРЕД СТАРТОМ БОТА".center(80))
    print("="*80 + "\n")
    
    tests_dir = Path(__file__).parent / "tests"
    
    test_files = [
        ("test_date_conversion.py", "Конвертация дат"),
        ("test_salary_logic.py", "Бизнес-логика"),
        ("test_integration.py", "Интеграционные тесты"),
    ]
    
    all_passed = True
    
    for test_file, description in test_files:
        test_path = tests_dir / test_file
        if not test_path.exists():
            print(f"⚠️  {test_file} не найден")
            continue
        
        print(f"▶️  {description}...", end=" ")
        
        try:
            result = subprocess.run(
                [sys.executable, str(test_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print("✅ PASSED")
            else:
                print("❌ FAILED")
                print(result.stdout)
                print(result.stderr)
                all_passed = False
        except subprocess.TimeoutExpired:
            print("❌ TIMEOUT")
            all_passed = False
        except Exception as e:
            print(f"❌ ERROR: {e}")
            all_passed = False
    
    return all_passed


def start_bot():
    """Запускает бота"""
    print("\n" + "="*80)
    print("🚀 ЗАПУСК БОТА".center(80))
    print("="*80 + "\n")
    
    try:
        subprocess.run([sys.executable, "main.py"], check=True)
    except KeyboardInterrupt:
        print("\n\n⚠️  Бот остановлен пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка запуска бота: {e}")


def main():
    """Главная функция"""
    print("\n" + "🤖 БЕЗОПАСНЫЙ ЗАПУСК BOT_IIKO ".center(80, "="))
    
    # Запускаем тесты
    tests_passed = run_tests()
    
    if not tests_passed:
        print("\n" + "="*80)
        print("❌ ТЕСТЫ ПРОВАЛИЛИСЬ!".center(80))
        print("Исправьте ошибки перед запуском бота.".center(80))
        print("="*80 + "\n")
        return 1
    
    print("\n" + "="*80)
    print("✅ ВСЕ ТЕСТЫ ПРОШЛИ!".center(80))
    print("="*80)
    
    # Спрашиваем подтверждение
    try:
        response = input("\n▶️  Запустить бота? (y/n): ").strip().lower()
        if response not in ['y', 'yes', 'да', 'д', '']:
            print("\n⏹️  Запуск отменён")
            return 0
    except KeyboardInterrupt:
        print("\n\n⏹️  Запуск отменён")
        return 0
    
    # Запускаем бота
    start_bot()
    return 0


if __name__ == "__main__":
    exit(main())
