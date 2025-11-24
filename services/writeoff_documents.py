"""
Получение расходных накладных из iiko API
Документация: https://ru.iiko.help/articles/#!api-documentations/vygruzka-raskhodnykh-nakladnykh
"""
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, date
import logging
from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)


def _strip_tz(dt):
    """Убирает timezone из datetime"""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def normalize_isoformat(dt_str: str) -> str:
    """Нормализует ISO формат даты"""
    if not dt_str:
        return dt_str
    if '.' in dt_str:
        date_part, ms = dt_str.split('.', 1)
        tz = ''
        for sym in ['+', '-']:
            if sym in ms:
                ms, tz = ms.split(sym, 1)
                tz = sym + tz
                break
        ms_digits = ''.join(filter(str.isdigit, ms))
        ms_fixed = (ms_digits + '000000')[:6]
        return f"{date_part}.{ms_fixed}{tz}"
    return dt_str


async def get_writeoff_documents(from_date: str, to_date: str) -> list:
    """
    Получает расходные накладные из iiko за период
    
    Args:
        from_date: Дата начала в формате YYYY-MM-DD
        to_date: Дата окончания в формате YYYY-MM-DD
    
    Returns:
        Список словарей с данными о расходных накладных:
        [
            {
                'id': str,              # ID документа
                'date': datetime,       # Дата создания документа
                'document_number': str, # Номер документа
                'sum': float,          # Общая сумма накладной
                'conception': str,     # Подразделение
                'comment': str         # Комментарий
            }
        ]
    """
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        # Правильный endpoint согласно документации iiko 5.4
        url = f"{base_url}/resto/api/documents/export/outgoingInvoice"
        
        # Параметры запроса согласно документации
        params = {
            "from": from_date,
            "to": to_date
        }
        
        logger.info(f"📥 Запрос расходных накладных с {from_date} по {to_date}...")
        
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Cookie": f"key={token}"}
            )
        
        if response.status_code != 200:
            logger.error(f"❌ Ошибка получения расходных накладных: {response.status_code}")
            logger.error(f"Response: {response.text[:500]}")
            logger.info(f"💡 Продолжаем работу без расходных накладных (комиссия от продаж будет работать)")
            return []
        
        # Парсим XML ответ
        root = ET.fromstring(response.text)
        documents = []
        
        # Структура XML согласно документации iiko 5.4:
        # <outgoingInvoiceDtoes>
        #   <document>
        #     <id>...</id>
        #     <dateIncoming>2012-07-04T23:00:00+04:00</dateIncoming>
        #     <documentNumber>4</documentNumber>
        #     <items>
        #       <item>
        #         <sum>1000.000000000</sum>
        #         ...
        #       </item>
        #     </items>
        #   </document>
        # </outgoingInvoiceDtoes>
        
        for doc_node in root.findall('.//document'):
            try:
                doc_id = doc_node.findtext('id', '')
                date_str = doc_node.findtext('dateIncoming', '')
                doc_number = doc_node.findtext('documentNumber', '')
                conception_id = doc_node.findtext('conceptionId', '')
                conception_code = doc_node.findtext('conceptionCode', '')
                comment = doc_node.findtext('comment', '')
                
                # Парсим дату
                if date_str:
                    try:
                        doc_date = _strip_tz(datetime.fromisoformat(normalize_isoformat(date_str)))
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга даты {date_str}: {e}")
                        doc_date = None
                else:
                    doc_date = None
                
                # Считаем общую сумму по всем строкам (items)
                total_sum = 0.0
                items_node = doc_node.find('items')
                if items_node is not None:
                    for item in items_node.findall('item'):
                        item_sum_str = item.findtext('sum', '0')
                        try:
                            total_sum += float(item_sum_str)
                        except:
                            pass
                
                if doc_id and doc_date:
                    documents.append({
                        'id': doc_id,
                        'date': doc_date,
                        'document_number': doc_number,
                        'sum': total_sum,
                        'conception': conception_code or conception_id,
                        'comment': comment
                    })
            
            except Exception as e:
                logger.warning(f"Ошибка обработки расходной накладной: {e}")
                continue
        
        logger.info(f"✅ Загружено {len(documents)} расходных накладных на общую сумму {sum(d['sum'] for d in documents):.2f}₽")
        return documents
    
    except Exception as e:
        logger.exception(f"❌ Ошибка получения расходных накладных: {e}")
        return []


def get_writeoffs_for_work_dates(writeoff_documents: list, work_dates: list) -> list:
    """
    Фильтрует расходные накладные, созданные в рабочие дни сотрудника
    
    Args:
        writeoff_documents: Список всех расходных накладных
        work_dates: Список дат работы сотрудника (datetime.date или datetime)
    
    Returns:
        Список расходных накладных, созданных в рабочие дни
    """
    # Преобразуем рабочие даты в set для быстрого поиска
    work_dates_set = set()
    for d in work_dates:
        if isinstance(d, datetime):
            work_dates_set.add(d.date())
        elif isinstance(d, date):
            work_dates_set.add(d)
    
    # Фильтруем документы
    result = []
    for doc in writeoff_documents:
        doc_date = doc['date'].date() if isinstance(doc['date'], datetime) else doc['date']
        if doc_date in work_dates_set:
            result.append(doc)
    
    return result


def calculate_writeoff_sum_for_employee(writeoff_documents: list, attendance_periods: list) -> float:
    """
    Рассчитывает сумму расходных накладных для сотрудника по его сменам
    
    Args:
        writeoff_documents: Список всех расходных накладных
        attendance_periods: Список кортежей (datetime_start, datetime_end) - смены сотрудника
    
    Returns:
        Общая сумма расходных накладных, созданных в дни смен сотрудника
    """
    if not attendance_periods:
        return 0.0
    
    # Собираем уникальные даты работы
    work_dates = set()
    for start, end in attendance_periods:
        work_dates.add(start.date())
        work_dates.add(end.date())
    
    # Фильтруем и суммируем
    total_sum = 0.0
    filtered_docs = []
    
    for doc in writeoff_documents:
        doc_date = doc['date'].date() if isinstance(doc['date'], datetime) else doc['date']
        if doc_date in work_dates:
            total_sum += doc['sum']
            filtered_docs.append(doc)
    
    return total_sum, filtered_docs
