"""
Тестовый скрипт для вывода расходных накладных в сыром виде
"""
import asyncio
import httpx
from iiko.iiko_auth import get_auth_token, get_base_url
import xml.etree.ElementTree as ET

async def test_writeoff_raw():
    token = await get_auth_token()
    base_url = get_base_url()
    
    from_date = "2025-11-01"
    to_date = "2025-11-23"
    
    url = f"{base_url}/resto/api/documents/export/outgoingInvoice"
    
    params = {
        "from": from_date,
        "to": to_date
    }
    
    print("=" * 100)
    print("ЗАПРОС РАСХОДНЫХ НАКЛАДНЫХ")
    print("=" * 100)
    print(f"URL: {url}")
    print(f"Параметры: {params}")
    print()
    
    async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
        response = await client.get(
            url,
            params=params,
            headers={"Cookie": f"key={token}"}
        )
    
    print(f"Статус: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")
    print()
    
    if response.status_code != 200:
        print(f"❌ ОШИБКА: {response.text}")
        return
    
    # Парсим XML
    root = ET.fromstring(response.text)
    
    print("=" * 100)
    print("СТРУКТУРА XML (первый документ):")
    print("=" * 100)
    
    first_doc = root.find('.//document')
    if first_doc:
        print("\nПоля документа:")
        for child in first_doc:
            if child.tag != 'items':
                print(f"  {child.tag}: {child.text}")
        
        items = first_doc.find('items')
        if items:
            print(f"\n  items: {len(items.findall('item'))} шт.")
            print("\n  Первый item:")
            first_item = items.find('item')
            if first_item:
                for field in first_item:
                    print(f"    {field.tag}: {field.text}")
    
    print("\n" + "=" * 100)
    print("ВСЕ ДОКУМЕНТЫ (краткая информация):")
    print("=" * 100)
    
    documents = []
    for doc_node in root.findall('.//document'):
        doc_id = doc_node.findtext('id', '')
        date_str = doc_node.findtext('dateIncoming', '')
        doc_number = doc_node.findtext('documentNumber', '')
        status = doc_node.findtext('status', '')
        
        # Фильтруем только проведенные
        if status != 'PROCESSED':
            continue
        
        # Считаем сумму по items
        total_sum = 0.0
        items_node = doc_node.find('items')
        if items_node:
            for item in items_node.findall('item'):
                item_sum = item.findtext('sum', '0')
                try:
                    total_sum += float(item_sum)
                except:
                    pass
        
        documents.append({
            'number': doc_number,
            'date': date_str[:10] if date_str else 'N/A',
            'sum': total_sum,
            'status': status
        })
    
    # Сортируем по дате
    documents.sort(key=lambda x: x['date'])
    
    print(f"\n{'№ док.':<15} {'Дата':<15} {'Сумма, ₽':>15}")
    print("-" * 50)
    
    total = 0.0
    for doc in documents:
        print(f"{doc['number']:<15} {doc['date']:<15} {doc['sum']:>15,.2f}")
        total += doc['sum']
    
    print("-" * 50)
    print(f"{'ИТОГО:':<30} {total:>15,.2f} ₽")
    print(f"{'Количество:':<30} {len(documents):>15} шт.")
    print()
    print("=" * 100)
    print(f"ОЖИДАЕТСЯ ИЗ iiko: 224,576.50 ₽ (45 документов)")
    print(f"ПОЛУЧЕНО:          {total:,.2f} ₽ ({len(documents)} документов)")
    print(f"РАЗНИЦА:           {total - 224576.50:,.2f} ₽")
    print("=" * 100)

if __name__ == "__main__":
    asyncio.run(test_writeoff_raw())
