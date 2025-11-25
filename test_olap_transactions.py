"""
Тест OLAP TRANSACTIONS - смотрим все доступные типы транзакций
"""
import asyncio
import httpx
import xml.etree.ElementTree as ET
from decimal import Decimal
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')

from iiko.iiko_auth import get_auth_token, get_base_url


def _auto_cast(text):
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
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def main():
    token = await get_auth_token()
    base_url = get_base_url()
    
    date_from = "17.11.2025"
    date_to = "20.11.2025"
    
    print(f"\n{'='*80}")
    print(f"OLAP TRANSACTIONS: {date_from} - {date_to}")
    print(f"{'='*80}\n")
    
    # Запрашиваем все типы транзакций
    params = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", date_from),
        ("to", date_to),
        ("groupRow", "TransactionType"),
        ("agr", "OutgoingSum"),    # Сумма расхода
        ("agr", "IncomingSum"),    # Сумма прихода
    ]
    
    async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
        r = await client.get("/resto/api/reports/olap", params=params)
        
        print(f"Status: {r.status_code}")
        print(f"Content-Type: {r.headers.get('content-type', '')}")
        
        if r.status_code != 200:
            print(f"Error: {r.text[:1000]}")
            return
        
        ct = r.headers.get("content-type", "")
        
        if ct.startswith("application/json"):
            data = r.json()
            rows = data.get("data", []) or data.get("rows", [])
        elif ct.startswith("application/xml") or ct.startswith("text/xml"):
            rows = parse_xml_report(r.text)
        else:
            print(f"Неизвестный формат: {ct}")
            print(r.text[:1000])
            return
        
        print(f"\nПолучено {len(rows)} типов транзакций:\n")
        print(f"{'Тип транзакции':<50} {'Расход':<20} {'Приход':<20}")
        print("-" * 90)
        
        for row in sorted(rows, key=lambda x: float(x.get('OutgoingSum', 0) or 0), reverse=True):
            trans_type = row.get('TransactionType', 'N/A')
            outgoing = float(row.get('OutgoingSum', 0) or 0)
            incoming = float(row.get('IncomingSum', 0) or 0)
            
            if outgoing > 0 or incoming > 0:
                print(f"{str(trans_type):<50} {outgoing:>15,.2f}₽ {incoming:>15,.2f}₽")
        
        # Ищем расходную накладную
        print("\n" + "="*80)
        print("Поиск 'расходн' в типах транзакций:")
        for row in rows:
            trans_type = str(row.get('TransactionType', '')).lower()
            if 'расходн' in trans_type or 'invoice' in trans_type or 'outgoing' in trans_type:
                print(f"  Найдено: {row}")


if __name__ == "__main__":
    asyncio.run(main())
