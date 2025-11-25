"""
Тест OLAP TRANSACTIONS - смотрим данные по расходным накладным
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
    print(f"OLAP TRANSACTIONS - OUTGOING_INVOICE: {date_from} - {date_to}")
    print(f"{'='*80}\n")
    
    # Запрашиваем данные по расходным накладным
    params = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", date_from),
        ("to", date_to),
        ("groupRow", "TransactionType"),
        ("agr", "OutgoingSum"),     # Сумма расхода (себестоимость)
        ("agr", "IncomingSum"),     # Сумма прихода (выручка)
        ("agr", "Sum"),             # Общая сумма
        # Фильтр по типу транзакции
        ("TransactionType", "OUTGOING_INVOICE"),
        ("TransactionType", "OUTGOING_INVOICE_REVENUE"),
    ]
    
    async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
        r = await client.get("/resto/api/reports/olap", params=params)
        
        print(f"Status: {r.status_code}")
        
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
            print(f"RAW Response:\n{r.text[:2000]}")
            return
        
        print(f"Получено {len(rows)} строк:\n")
        for row in rows:
            print(row)
        
        # Суммы
        total_out = sum(float(r.get('OutgoingSum', 0) or 0) for r in rows)
        total_in = sum(float(r.get('IncomingSum', 0) or 0) for r in rows)
        total_sum = sum(float(r.get('Sum', 0) or 0) for r in rows)
        
        print(f"\n{'='*80}")
        print(f"ИТОГО:")
        print(f"  Расход (OutgoingSum): {total_out:,.2f}₽")
        print(f"  Приход (IncomingSum): {total_in:,.2f}₽")
        print(f"  Сумма (Sum): {total_sum:,.2f}₽")
        print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(main())
