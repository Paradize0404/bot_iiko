"""
Тест модуля получения баланса по поставщикам
"""
import asyncio
from services.supplier_balance import get_supplier_balance, format_supplier_balance_report


async def main():
    # Получаем баланс на конкретную дату
    balance_data = await get_supplier_balance("23.12.2025")
    
    # Форматируем и выводим отчет
    report = format_supplier_balance_report(balance_data)
    print(report)
    
    # Или можно также использовать в формате YYYY-MM-DD
    print("\n" + "="*80 + "\n")
    balance_data_2 = await get_supplier_balance("2025-12-23")
    report_2 = format_supplier_balance_report(balance_data_2)
    print(report_2)


if __name__ == "__main__":
    asyncio.run(main())
