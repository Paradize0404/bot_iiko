"""
Получение расходных накладных из iiko API
Документация: https://ru.iiko.help/articles/#!api-documentations/vygruzka-raskhodnykh-nakladnykh

Для получения себестоимости используется OLAP отчет по проводкам (TRANSACTIONS)
"""
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, date
from decimal import Decimal
import logging

from iiko.iiko_auth import get_auth_token, get_base_url
from utils.datetime_helpers import strip_tz, normalize_isoformat
from db.stores_db import Store as StoreModel, async_session as stores_async_session
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _auto_cast(text):
    """Автоматическое приведение типов из XML"""
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip() if text else None


def parse_xml_report(xml: str):
    """Парсинг XML отчета в список словарей"""
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def get_writeoff_cost_olap(from_date: str, to_date: str) -> float:
    """
    Получить себестоимость по расходным накладным через OLAP отчет по проводкам
    
    Типы транзакций:
    - OUTGOING_INVOICE - себестоимость (списание товаров со склада)
    - OUTGOING_INVOICE_REVENUE - выручка (сумма продажи)
    
    Args:
        from_date: дата начала в формате YYYY-MM-DD
        to_date: дата конца в формате YYYY-MM-DD
        
    Returns:
        себестоимость (сумма по транзакции OUTGOING_INVOICE)
    """
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        # OLAP API ожидает формат DD.MM.YYYY
        date_from_display = datetime.strptime(from_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        date_to_display = datetime.strptime(to_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        
        # Параметры для OLAP запроса по проводкам (TRANSACTIONS)
        params = [
            ("key", token),
            ("report", "TRANSACTIONS"),
            ("from", date_from_display),
            ("to", date_to_display),
            ("groupRow", "TransactionType"),      # Тип транзакции
            ("agr", "Sum"),                       # Сумма
            # Фильтр - только расходные накладные
            ("TransactionType", "OUTGOING_INVOICE"),
        ]
        
        logger.info(f"🔍 Запрос OLAP TRANSACTIONS для себестоимости расходных накладных...")
        
        async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
            url = "/resto/api/reports/olap"
            r = await client.get(url, params=params)
            
            if r.status_code != 200:
                logger.error(f"Ошибка получения OLAP TRANSACTIONS: {r.status_code}")
                logger.error(f"Ответ: {r.text[:500]}")
                return 0.0
            
            ct = r.headers.get("content-type", "")
            
            if ct.startswith("application/json"):
                data = r.json()
                report_data = data.get("data", []) or data.get("rows", [])
            elif ct.startswith("application/xml") or ct.startswith("text/xml"):
                report_data = parse_xml_report(r.text)
            else:
                logger.error(f"Неизвестный Content-Type: {ct}")
                return 0.0
            
            # Ищем сумму по OUTGOING_INVOICE (себестоимость)
            total_cost = 0.0
            for row in report_data:
                trans_type = row.get("TransactionType", "")
                if trans_type == "OUTGOING_INVOICE":
                    sum_val = row.get("Sum", 0) or 0
                    total_cost = float(sum_val)
                    break
            
            logger.info(f"✅ Себестоимость расходных накладных (OLAP): {total_cost:.2f}₽")
            return total_cost
            
    except Exception as e:
        logger.exception(f"❌ Ошибка получения себестоимости через OLAP: {e}")
        return 0.0


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
                
                # ВАЖНО: Проверяем что накладная проведена (processed)
                status = doc_node.findtext('status', '')
                if status != 'PROCESSED':
                    logger.debug(f"Пропускаем накладную {doc_number} со статусом {status}")
                    continue
                
                # Парсим дату
                if date_str:
                    try:
                        doc_date = strip_tz(datetime.fromisoformat(normalize_isoformat(date_str)))
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга даты {date_str}: {e}")
                        doc_date = None
                else:
                    doc_date = None
                
                # Считаем общую сумму (выручка) по всем строкам (items)
                # Замечание: Себестоимость не возвращается в этом API
                total_sum = 0.0  # Выручка (сумма продажи)
                items_node = doc_node.find('items')
                if items_node is not None:
                    for item in items_node.findall('item'):
                        # Выручка (сумма продажи)
                        item_sum_str = item.findtext('sum', '0')
                        try:
                            total_sum += float(item_sum_str)
                        except (ValueError, TypeError):
                            pass
                
                if doc_id and doc_date:
                    documents.append({
                        'id': doc_id,
                        'date': doc_date,
                        'document_number': doc_number,
                        'sum': total_sum,  # Выручка
                        'conception': conception_code or conception_id,
                        'comment': comment
                    })
            
            except Exception as e:
                logger.warning(f"Ошибка обработки расходной накладной: {e}")
                continue
        
        logger.info(f"✅ Загружено {len(documents)} ПРОВЕДЕННЫХ расходных накладных на общую сумму {sum(d['sum'] for d in documents):.2f}₽")
        return documents
    
    except Exception as e:
        logger.exception(f"❌ Ошибка получения расходных накладных: {e}")
        return []


async def get_segment_writeoff_totals(date_from: str, date_to: str) -> dict[str, float]:
    """Возвращает суммы списаний по основным сегментам (бар, кухня).

    Args:
        date_from: начало периода (YYYY-MM-DD)
        date_to: конец периода (YYYY-MM-DD)

    Returns:
        Словарь с суммами списаний по сегментам.
    """

    try:
        token = await get_auth_token()
        base_url = get_base_url()
        url = f"{base_url}/resto/api/v2/documents/writeoff"
        params = {"dateFrom": date_from, "dateTo": date_to}
        headers = {"Cookie": f"key={token}"}

        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()

        data = response.json() or {}
        documents = data.get("response", []) or []

        store_ids = {doc.get("storeId") for doc in documents if doc.get("storeId")}
        store_name_map: dict[str, str] = {}
        if store_ids:
            async with stores_async_session() as session:
                rows = await session.execute(
                    select(StoreModel.id, StoreModel.name).where(StoreModel.id.in_(store_ids))
                )
                store_name_map = {
                    store_id: (store_name or "").strip().lower()
                    for store_id, store_name in rows.all()
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось получить списания по складам: %s", exc)
        return {}

    totals = {"bar": 0.0, "kitchen": 0.0}

    def _store_label(doc: dict) -> str:
        store_id = doc.get("storeId")
        if store_id and store_name_map.get(store_id):
            return store_name_map[store_id]

        store_obj = doc.get("store") or {}
        return (store_obj.get("name") or doc.get("storeName") or "").strip().lower()

    for doc in documents:
        items = doc.get("items") or []
        total_cost = 0.0
        for item in items:
            try:
                total_cost += float(item.get("cost") or 0.0)
            except (TypeError, ValueError):
                continue

        label = _store_label(doc)
        if "бар" in label:
            totals["bar"] += total_cost
        elif "кух" in label or "пицц" in label:
            totals["kitchen"] += total_cost

    return totals


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
