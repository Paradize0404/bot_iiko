"""
Модуль для работы с кассовыми сменами и заказами из iiko API
"""
import httpx
import json
import logging
from datetime import datetime

from iiko.iiko_auth import get_auth_token, get_base_url
from utils.datetime_helpers import parse_datetime, strip_tz

logger = logging.getLogger(__name__)

# ID сохраненного отчета для получения заказов
PRESET_REPORT_ID = "9555cc88-492c-48f6-9a09-629346af5bde"


## ────────────── Получение отчета по ID (сохраненная конфигурация) ──────────────
async def get_preset_report_by_id(preset_id: str, from_date: str, to_date: str) -> list:
    """
    Получает данные из сохраненного отчета iiko по его ID
    
    Args:
        preset_id: ID сохранённого отчёта
        from_date: дата начала в формате YYYY-MM-DD
        to_date: дата конца в формате YYYY-MM-DD
        
    Returns:
        list: список заказов с closeTime и sum
    """
    token = await get_auth_token()
    base_url = get_base_url()
    
    # Проверяем формат входных дат
    try:
        datetime.strptime(from_date, "%Y-%m-%d")
        datetime.strptime(to_date, "%Y-%m-%d")
    except (ValueError, TypeError) as e:
        logger.error(f"❌ Неправильный формат дат {from_date} - {to_date}: {e}")
        return []
    
    url = f"{base_url}/resto/api/v2/reports/olap/byPresetId/{preset_id}"
    params = {"key": token, "from": from_date, "to": to_date}
    
    try:
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.get(url, params=params)
        
        if response.status_code != 200:
            logger.error(f"❌ Ошибка preset-отчета: {response.status_code}")
            return []
        
        data = response.json()
        orders = [
            {'closeTime': item.get("CloseTime"), 'sum': float(item.get("DishDiscountSumInt", 0))}
            for item in data.get("data", [])
            if item.get("CloseTime")
        ]
        
        logger.info(f"✅ Получено {len(orders)} заказов из preset-отчета")
        return orders
        
    except Exception as e:
        logger.error(f"❌ Ошибка preset-отчета: {e}")
        return []


## ────────────── Получение заказов через OLAP отчет ──────────────
async def get_orders_from_olap(from_date: str, to_date: str) -> list:
    """Получает все заказы за период через preset-отчет"""
    return await get_preset_report_by_id(PRESET_REPORT_ID, from_date, to_date)


## ────────────── Получение данных по кассовым сменам ──────────────
async def get_cash_shifts_with_details(from_date: str, to_date: str) -> list:
    """
    Получает кассовые смены с распределенными по ним заказами
    
    Args:
        from_date: дата начала в формате YYYY-MM-DD
        to_date: дата конца в формате YYYY-MM-DD
        
    Returns:
        list: список смен с заказами
    """
    token = await get_auth_token()
    base_url = get_base_url()

    url = f"{base_url}/resto/api/v2/cashshifts/list"
    headers = {"Cookie": f"key={token}"}
    params = {"openDateFrom": from_date, "openDateTo": to_date, "status": "ANY"}

    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        response = await client.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Ошибка при получении смен: {response.status_code}")

    data = response.json()

    # Создаем структуру смен
    shifts_with_orders = [
        {
            "id": shift.get("id"),
            "openDate": shift.get("openDate"),
            "closeDate": shift.get("closeDate"),
            "payOrders": shift.get("payOrders", 0),
            "orders": []
        }
        for shift in data
    ]
    
    # Получаем заказы через preset-отчет
    all_orders = await get_orders_from_olap(from_date, to_date)
    
    if all_orders:
        # Распределяем заказы по сменам
        for shift_info in shifts_with_orders:
            s_open = shift_info.get("openDate")
            s_close = shift_info.get("closeDate")
            
            if not s_open or not s_close:
                continue
                
            try:
                shift_start = strip_tz(parse_datetime(s_open))
                shift_end = strip_tz(parse_datetime(s_close))
                
                if not shift_start or not shift_end:
                    continue
                
                for order in all_orders:
                    order_time = parse_datetime(order['closeTime'])
                    if not order_time:
                        continue
                        
                    order_time = strip_tz(order_time)
                    
                    if shift_start <= order_time <= shift_end:
                        shift_info["orders"].append(order)
                        
            except Exception as e:
                logger.warning(f"Ошибка обработки смены {shift_info.get('id')}: {e}")
    
    total_orders = sum(len(s["orders"]) for s in shifts_with_orders)
    logger.info(f"✅ Загружено {len(shifts_with_orders)} смен с {total_orders} заказами")
    return shifts_with_orders
