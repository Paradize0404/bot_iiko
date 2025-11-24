"""
Скрипт для получения СЫРЫХ данных из сохраненного отчета iiko
"""
import asyncio
import httpx
from iiko.iiko_auth import get_auth_token, get_base_url

async def get_preset_report_raw():
    """Получить сырые данные из сохраненного отчета"""
    token = await get_auth_token()
    base_url = get_base_url()
    
    report_id = "3646ed72-6eee-4085-9179-4f7e88fa1cac"
    
    print("=" * 100)
    print("ЗАПРОС К СОХРАНЕННОМУ ОТЧЕТУ")
    print("=" * 100)
    print(f"Report ID: {report_id}")
    print(f"Base URL: {base_url}")
    
    # Пробуем с параметрами даты
    params = [
        ("key", token),
        ("from", "01.11.2025"),
        ("to", "23.11.2025"),
    ]
    
    url = f"/resto/api/v2/reports/olap/byPresetId/{report_id}"
    full_url = f"{base_url}{url}"
    
    print(f"\nURL: {full_url}")
    print(f"Параметры: {dict(params)}")
    print("\n" + "=" * 100)
    
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        try:
            r = await client.get(url, params=params)
            
            print(f"Статус ответа: {r.status_code}")
            print(f"Content-Type: {r.headers.get('Content-Type', 'unknown')}")
            print(f"Content-Length: {len(r.text)} символов")
            print("\n" + "=" * 100)
            print("СЫРОЙ ОТВЕТ:")
            print("=" * 100)
            print(r.text)
            print("\n" + "=" * 100)
            
            if r.status_code != 200:
                print(f"\n❌ ОШИБКА! Статус {r.status_code}")
                return
            
            # Пробуем распарсить если это XML
            if 'xml' in r.headers.get('Content-Type', '').lower():
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(r.text)
                    print("\n✅ XML успешно распарсен")
                    print(f"Корневой элемент: {root.tag}")
                    print(f"Количество строк <r>: {len(root.findall('./r'))}")
                    
                    # Выводим первые 3 строки для примера
                    print("\n" + "=" * 100)
                    print("ПЕРВЫЕ 3 СТРОКИ (структура):")
                    print("=" * 100)
                    for i, row in enumerate(root.findall('./r')[:3]):
                        print(f"\nСтрока {i+1}:")
                        for child in row:
                            print(f"  {child.tag}: {child.text}")
                    
                    # Ищем строки с Яндекс.оплата
                    print("\n" + "=" * 100)
                    print("СТРОКИ С ЯНДЕКС.ОПЛАТА:")
                    print("=" * 100)
                    
                    yandex_count = 0
                    yandex_total = 0
                    
                    for row in root.findall('./r'):
                        pay_type = None
                        cooking_place = None
                        dish_sum = None
                        
                        for child in row:
                            if 'PayTypes' in child.tag:
                                pay_type = child.text
                            elif 'CookingPlace' in child.tag:
                                cooking_place = child.text
                            elif child.tag == 'DishSumInt':
                                dish_sum = float(child.text) if child.text else 0
                        
                        if pay_type and 'Яндекс' in pay_type:
                            yandex_count += 1
                            yandex_total += dish_sum if dish_sum else 0
                            print(f"\n{cooking_place or 'N/A'}:")
                            print(f"  Тип оплаты: {pay_type}")
                            print(f"  DishSumInt: {dish_sum:,.2f}₽" if dish_sum else "  DishSumInt: N/A")
                    
                    print(f"\n{'=' * 100}")
                    print(f"Найдено строк с Яндекс: {yandex_count}")
                    print(f"ИТОГО по Яндекс: {yandex_total:,.2f}₽")
                    print("=" * 100)
                    
                except ET.ParseError as e:
                    print(f"\n❌ Ошибка парсинга XML: {e}")
            
        except Exception as e:
            print(f"\n❌ ОШИБКА ЗАПРОСА: {e}")
            import traceback
            traceback.print_exc()

async def main():
    print("\n🔍 Получение сырых данных из сохраненного отчета iiko...\n")
    await get_preset_report_raw()

if __name__ == "__main__":
    asyncio.run(main())
