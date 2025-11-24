"""
Скрипт для сравнения данных из SALES отчета и сохраненного отчета iiko
"""
import asyncio
import httpx
from iiko.iiko_auth import get_auth_token, get_base_url
import xml.etree.ElementTree as ET
import pandas as pd

async def get_sales_report():
    """Получить данные из стандартного SALES отчета"""
    token = await get_auth_token()
    base_url = get_base_url()
    
    params = [
        ("key", token),
        ("report", "SALES"),
        ("from", "01.11.2025"),
        ("to", "23.11.2025"),
        ("groupRow", "CookingPlace"),
        ("groupRow", "PayTypes"),
        ("agr", "DishSumInt"),
        ("agr", "DishDiscountSumInt"),
        ("DeletedWithWriteoff", "NOT_DELETED"),
        ("OrderDeleted", "NOT_DELETED"),
        ("OpenDate.Typed", "ONLY_CLOSED_CASH_SESSIONS")  # Только закрытые смены
    ]
    
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        r = await client.get("/resto/api/reports/olap", params=params)
        return r.text

async def get_preset_report():
    """Получить данные из сохраненного отчета"""
    token = await get_auth_token()
    base_url = get_base_url()
    
    report_id = "3646ed72-6eee-4085-9179-4f7e88fa1cac"
    
    # Пробуем разные варианты параметров
    params = [
        ("key", token),
        ("from", "01.11.2025"),
        ("to", "23.11.2025"),
    ]
    
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        url = f"/resto/api/v2/reports/olap/byPresetId/{report_id}"
        print(f"Запрос к: {base_url}{url}")
        r = await client.get(url, params=params)
        print(f"Статус: {r.status_code}")
        if r.status_code != 200:
            print(f"Ошибка: {r.text}")
            return None
        return r.text

def parse_xml_to_df(xml_text):
    """Парсим XML в DataFrame"""
    root = ET.fromstring(xml_text)
    
    rows = []
    for row in root.findall("./r"):
        data = {}
        for child in row:
            try:
                # Пытаемся преобразовать в число
                data[child.tag] = float(child.text) if '.' in child.text else int(child.text)
            except (ValueError, TypeError, AttributeError):
                data[child.tag] = child.text
        rows.append(data)
    
    return pd.DataFrame(rows)

async def main():
    print("=" * 80)
    print("СРАВНЕНИЕ ОТЧЕТОВ")
    print("=" * 80)
    
    # Получаем оба отчета
    print("\n1. Получение SALES отчета...")
    sales_xml = await get_sales_report()
    sales_df = parse_xml_to_df(sales_xml)
    
    print(f"✓ Получено {len(sales_df)} строк")
    print("\nКолонки:", sales_df.columns.tolist())
    
    # Фильтруем только Яндекс
    pay_col = "PayTypes.Combo" if "PayTypes.Combo" in sales_df.columns else "PayTypes"
    cooking_col = "CookingPlace" if "CookingPlace" in sales_df.columns else "CookingPlaceType"
    
    yandex_df = sales_df[sales_df[pay_col].str.contains("Яндекс.оплата", case=False, na=False)]
    
    print(f"\n2. Строки с Яндекс.оплата ({len(yandex_df)} шт):")
    print("=" * 80)
    
    # Выводим детальную информацию
    if len(yandex_df) > 0:
        for idx, row in yandex_df.iterrows():
            place = row.get(cooking_col, "N/A")
            pay_type = row.get(pay_col, "N/A")
            dish_sum = row.get("DishSumInt", 0)
            dish_discount = row.get("DishDiscountSumInt", 0)
            
            print(f"\n{place}:")
            print(f"  Тип оплаты: {pay_type}")
            print(f"  DishSumInt: {dish_sum:,.2f}₽")
            print(f"  DishDiscountSumInt: {dish_discount:,.2f}₽")
    
    # Итоги по Яндекс
    total_yandex = yandex_df["DishSumInt"].sum()
    print("\n" + "=" * 80)
    print(f"ИТОГО ЯНДЕКС (DishSumInt): {total_yandex:,.2f}₽")
    print("=" * 80)
    
    # Разбивка по местам
    print("\nПо местам приготовления:")
    for place in yandex_df[cooking_col].unique():
        place_sum = yandex_df[yandex_df[cooking_col] == place]["DishSumInt"].sum()
        print(f"  {place}: {place_sum:,.2f}₽")
    
    print("\n" + "=" * 80)
    print("ОЖИДАЕМЫЕ ЗНАЧЕНИЯ ИЗ iiko:")
    print("=" * 80)
    print("  Бар:         7,460.00₽")
    print("  Кухня:     168,190.00₽")
    print("  Кухня-Пицца: 4,120.00₽")
    print("  Пицца:     557,810.00₽")
    print("  ---------------------")
    print("  ИТОГО:     737,580.00₽")
    print("\n" + "=" * 80)
    
    # Разница
    expected = 737580
    diff = total_yandex - expected
    print(f"\nРазница: {diff:,.2f}₽ ({diff/expected*100:.2f}%)")

if __name__ == "__main__":
    asyncio.run(main())
