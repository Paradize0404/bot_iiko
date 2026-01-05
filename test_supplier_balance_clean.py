"""
Получение баланса по поставщикам через OLAP отчет по проводкам
"""
import asyncio
import httpx
import xml.etree.ElementTree as ET
from decimal import Decimal
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(message)s')

from iiko.iiko_auth import get_auth_token, get_base_url


def _auto_cast(text):
    """Автоматическое преобразование текста в число или строку"""
    if text is None:
        return None
    try:
        return int(text)
    except:
        try:
            return Decimal(text)
        except:
            return text.strip() if text else None


def parse_xml_report(xml: str):
    """Парсинг XML отчета"""
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def get_supplier_balance(date_str: str = None, show_only_with_balance: bool = True):
    """
    Получить баланс по поставщикам на конкретную дату
    
    Args:
        date_str: дата в формате DD.MM.YYYY (если None, используется сегодня)
        show_only_with_balance: показывать только поставщиков с ненулевым балансом
    
    Returns:
        list: список поставщиков с балансами
    """
    token = await get_auth_token()
    base_url = get_base_url()
    
    # Если дата не указана, используем сегодня
    if not date_str:
        date_str = datetime.now().strftime("%d.%m.%Y")
    
    print(f"\n{'='*120}")
    print(f"БАЛАНС ПО ПОСТАВЩИКАМ на {date_str}")
    print(f"{'='*120}\n")
    
    # Параметры для OLAP запроса по проводкам (TRANSACTIONS)
    # Баланс формируется от начала времен до указанной даты
    params = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", "01.01.2020"),  # От начала
        ("to", date_str),        # До указанной даты
        ("groupRow", "Counteragent.Name"),  # Группировка по имени контрагента
        ("agr", "Sum.Outgoing"),   # Сумма расхода (отгружено на точки)
        ("agr", "Sum.Incoming"),   # Сумма прихода (поступления от поставщика)
        ("agr", "Sum"),            # Итоговый баланс
        # Фильтр только для поставщиков
        ("Counteragent", "SUPPLIER"),
    ]
    
    print("📊 Запрос данных из iiko...")
    
    async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
        r = await client.get("/resto/api/reports/olap", params=params)
        
        if r.status_code != 200:
            print(f"❌ Ошибка: {r.text[:1000]}")
            return []
        
        ct = r.headers.get("content-type", "")
        
        if ct.startswith("application/json"):
            data = r.json()
            rows = data.get("data", []) or data.get("rows", [])
        elif ct.startswith("application/xml") or ct.startswith("text/xml"):
            rows = parse_xml_report(r.text)
        else:
            print(f"⚠️ Неизвестный формат: {ct}")
            return []
        
        print(f"✅ Получено {len(rows)} записей\n")
        
        # Формируем таблицу результатов
        print(f"{'№':<5} {'Поставщик':<50} {'Отгружено':<20} {'Приход':<20} {'БАЛАНС':<20}")
        print("-" * 120)
        
        total_outgoing = Decimal(0)
        total_incoming = Decimal(0)
        total_balance = Decimal(0)
        
        # Фильтруем и сортируем
        filtered_rows = []
        for row in rows:
            supplier_name = row.get('Counteragent.Name')
            if not supplier_name or supplier_name == 'None':
                continue
            
            outgoing = Decimal(str(row.get('Sum.Outgoing', 0) or 0))
            incoming = Decimal(str(row.get('Sum.Incoming', 0) or 0))
            balance = Decimal(str(row.get('Sum', 0) or 0))
            
            # Пропускаем поставщиков с нулевым балансом, если указано
            if show_only_with_balance and balance == 0:
                continue
            
            filtered_rows.append({
                'name': str(supplier_name),
                'outgoing': outgoing,
                'incoming': incoming,
                'balance': balance
            })
        
        # Сортируем по балансу (по убыванию абсолютного значения)
        filtered_rows.sort(key=lambda x: abs(x['balance']), reverse=True)
        
        # Выводим результаты
        for idx, row in enumerate(filtered_rows, 1):
            print(f"{idx:<5} {row['name']:<50} {row['outgoing']:>15,.2f}₽ {row['incoming']:>15,.2f}₽ {row['balance']:>15,.2f}₽")
            
            total_outgoing += row['outgoing']
            total_incoming += row['incoming']
            total_balance += row['balance']
        
        print("-" * 120)
        print(f"{'ИТОГО':<56} {total_outgoing:>15,.2f}₽ {total_incoming:>15,.2f}₽ {total_balance:>15,.2f}₽")
        print()
        
        # Статистика
        print(f"📈 Статистика:")
        print(f"  Всего поставщиков с балансом: {len(filtered_rows)}")
        debt_to_suppliers = sum(row['balance'] for row in filtered_rows if row['balance'] > 0)
        debt_from_suppliers = sum(row['balance'] for row in filtered_rows if row['balance'] < 0)
        print(f"  Наша задолженность перед поставщиками: {debt_to_suppliers:,.2f}₽")
        print(f"  Задолженность поставщиков перед нами: {abs(debt_from_suppliers):,.2f}₽")
        
        return filtered_rows


async def main():
    # Получаем баланс по поставщикам на 23.12.2025 (как на скриншоте)
    await get_supplier_balance("23.12.2025")
    
    # Можно также получить на текущую дату:
    # await get_supplier_balance()
    
    # Или показать всех поставщиков, включая с нулевым балансом:
    # await get_supplier_balance("23.12.2025", show_only_with_balance=False)


if __name__ == "__main__":
    asyncio.run(main())
