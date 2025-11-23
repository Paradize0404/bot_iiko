import httpx
from iiko.iiko_auth import get_auth_token, get_base_url
import logging
import xml.etree.ElementTree as ET
from datetime import datetime


## ────────────── Логгер ──────────────
logger = logging.getLogger(__name__)


## ────────────── Получение заказов через кастомный OLAP отчет ──────────────
async def get_orders_from_custom_olap(from_date: str, to_date: str) -> list:
    """Получает заказы через настроенный OLAP отчет с группировкой по времени закрытия"""
    token = await get_auth_token()
    base_url = get_base_url()
    
    url = f"{base_url}/resto/api/reports/olap"
    
    # Конвертируем формат даты из 2025-11-01 в 01.11.2025
    from datetime import datetime
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d")
        from_date_iiko = from_dt.strftime("%d.%m.%Y")
        to_date_iiko = to_dt.strftime("%d.%m.%Y")
    except:
        # Если уже в правильном формате
        from_date_iiko = from_date
        to_date_iiko = to_date
    
    # Параметры отчета согласно описанию из iiko
    params = {
        "key": token,
        "report": "SALES",
        "from": from_date_iiko,
        "to": to_date_iiko,
        # Группировка по времени закрытия
        "groupByRowFields": "CloseTime",
        # Агрегация - сумма со скидкой
        "groupByColFields": "DishDiscountSumInt",
    }
    
    try:
        logger.info(f"🔍 Запрос кастомного OLAP отчета с {from_date} по {to_date}")
        logger.info(f"   URL: {url}")
        logger.info(f"   Параметры: report={params['report']}, from={params['from']}, to={params['to']}")
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.get(url, params=params)
        
        logger.info(f"📊 Ответ OLAP: статус {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"❌ Ошибка OLAP: {response.status_code} - {response.text[:500]}")
            return []
        
        # Выводим сырой XML в консоль для анализа
        logger.info("=" * 80)
        logger.info("===== СЫРОЙ XML ОТВЕТ OLAP =====")
        logger.info("=" * 80)
        logger.info(response.text[:2000])  # Первые 2000 символов
        logger.info("=" * 80)
        
        # Парсим XML
        root = ET.fromstring(response.text)
        orders = []
        
        # Ищем все записи о продажах
        for row in root.findall('.//r'):
            # Ищем время закрытия в разных тегах
            close_time = (
                row.findtext('CloseTime') or 
                row.findtext('d0') or
                row.findtext('Date')
            )
            # Ищем сумму в разных тегах
            sum_val = (
                row.findtext('DishDiscountSumInt') or
                row.findtext('v0') or
                row.findtext('Sum')
            )
            
            if close_time and sum_val:
                try:
                    orders.append({
                        'closeTime': close_time,
                        'sum': float(sum_val)
                    })
                except (ValueError, TypeError) as e:
                    logger.debug(f"Ошибка парсинга строки: closeTime={close_time}, sum={sum_val}, error={e}")
                    continue
        
        logger.info(f"✅ Получено {len(orders)} заказов из OLAP")
        if orders:
            logger.info(f"   Первые 3 заказа: {orders[:3]}")
        
        return orders
        
    except Exception as e:
        logger.exception(f"❌ Ошибка при запросе OLAP: {e}")
        return []


## ────────────── Получение заказов через OLAP отчет (старый метод) ──────────────
async def get_orders_from_olap(from_date: str, to_date: str) -> list:
    """Получает все заказы за период через OLAP отчет"""
    token = await get_auth_token()
    base_url = get_base_url()
    
    # Сначала пробуем кастомный отчет
    custom_orders = await get_orders_from_custom_olap(from_date, to_date)
    if custom_orders:
        return custom_orders
    
    # Если не сработал - пробуем другие варианты
    logger.warning("⚠️ Кастомный OLAP не вернул данных, пробуем альтернативные отчеты...")
    reports_to_try = [
        ("SALES_BY_HOUR", {}),  # Продажи по часам
        ("SALES", {"groupRow": "OpenDate.Typed"}),  # Группировка по дате открытия
        ("SALES_DETAILED", {}),  # Детализированные продажи
    ]
    
    for report_name, extra_params in reports_to_try:
        url = f"{base_url}/resto/api/reports/olap"
        params = {
            "key": token,
            "report": report_name,
            "from": from_date,
            "to": to_date,
        }
        params.update(extra_params)
        
        try:
            logger.info(f"🔍 Пробую OLAP отчет: {report_name}")
            async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
                response = await client.get(url, params=params)
            
            if response.status_code != 200:
                logger.debug(f"   ❌ {report_name}: {response.status_code}")
                continue
            
            # Парсим XML
            root = ET.fromstring(response.text)
            orders = []
            
            # Ищем все записи о продажах
            for row in root.findall('.//r'):
                # Пытаемся найти дату/время и сумму в разных форматах
                date_val = row.findtext('d0') or row.findtext('Date')
                time_val = row.findtext('d1') or row.findtext('Time') or row.findtext('Hour')
                sum_val = row.findtext('v0') or row.findtext('Sum') or row.findtext('DishDiscountSumInt')
                
                if date_val and sum_val:
                    try:
                        # Формируем время закрытия
                        if time_val:
                            close_time = f"{date_val} {time_val}"
                        else:
                            close_time = date_val
                        
                        orders.append({
                            'closeTime': close_time,
                            'sum': float(sum_val)
                        })
                    except (ValueError, TypeError):
                        continue
            
            if orders:
                logger.info(f"✅ {report_name}: получено {len(orders)} записей")
                return orders
            else:
                logger.debug(f"   ⚠️ {report_name}: нет данных")
                
        except Exception as e:
            logger.debug(f"   ❌ {report_name}: {e}")
            continue
    
    logger.warning(f"⚠️ Ни один OLAP отчет не вернул данные о заказах")
    return []


## ────────────── Получение данных по кассовым сменам ──────────────
async def get_cash_shifts_with_details(from_date: str, to_date: str) -> list:
    token = await get_auth_token()
    base_url = get_base_url()

    url = f"{base_url}/resto/api/v2/cashshifts/list"
    headers = {
        "Cookie": f"key={token}"
    }

    params = {
        "openDateFrom": from_date,
        "openDateTo": to_date,
        "status": "ANY"
    }

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Ошибка при получении смен: {response.status_code} — {response.text}")

    try:
        data = response.json()
    except Exception as e:
        logger.exception("Ошибка при разборе JSON: %s", e)
        raise Exception(f"Ошибка при разборе JSON: {e}")

    # Создаем базовую структуру смен
    shifts_with_orders = []
    
    for shift in data:
        shift_info = {
            "id": shift.get("id"),
            "openDate": shift.get("openDate"),
            "closeDate": shift.get("closeDate"),
            "payOrders": shift.get("payOrders", 0),
            "orders": []
        }
        shifts_with_orders.append(shift_info)
    
    # Сразу получаем заказы через OLAP
    logger.info("📊 Получение заказов через OLAP...")
    all_orders = await get_orders_from_olap(from_date, to_date)
    
    if all_orders:
        # Распределяем заказы по сменам
        for shift_info in shifts_with_orders:
            s_open = shift_info.get("openDate")
            s_close = shift_info.get("closeDate")
            
            if s_open and s_close:
                try:
                    shift_start = datetime.fromisoformat(s_open.replace('Z', '+00:00'))
                    shift_end = datetime.fromisoformat(s_close.replace('Z', '+00:00'))
                    
                    for order in all_orders:
                        try:
                            order_time_str = order['closeTime']
                            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M']:
                                try:
                                    order_time = datetime.strptime(order_time_str, fmt)
                                    order_time = order_time.replace(tzinfo=None)
                                    shift_start_tz = shift_start.replace(tzinfo=None)
                                    shift_end_tz = shift_end.replace(tzinfo=None)
                                    
                                    if shift_start_tz <= order_time <= shift_end_tz:
                                        shift_info["orders"].append(order)
                                    break
                                except ValueError:
                                    continue
                        except Exception as e:
                            logger.debug(f"Ошибка парсинга времени заказа: {e}")
                            continue
                except Exception as e:
                    logger.warning(f"Ошибка обработки смены {shift_info.get('id')}: {e}")
    
    total_orders = sum(len(s["orders"]) for s in shifts_with_orders)
    logger.info(f"✅ Загружено {len(shifts_with_orders)} смен с {total_orders} заказами")
    return shifts_with_orders
