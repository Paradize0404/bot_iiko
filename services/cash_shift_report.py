import httpx
from iiko.iiko_auth import get_auth_token, get_base_url
import logging
import json
from datetime import datetime


## ────────────── Логгер ──────────────
logger = logging.getLogger(__name__)


## ────────────── Получение отчета по ID (сохраненная конфигурация) ──────────────
async def get_preset_report_by_id(preset_id: str, from_date: str, to_date: str) -> list:
    """Получает данные из сохраненного отчета iiko по его ID"""
    token = await get_auth_token()
    base_url = get_base_url()
    
    # Конвертируем формат даты из 2025-11-01 в 01.11.2025
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d")
        from_date_iiko = from_dt.strftime("%d.%m.%Y")
        to_date_iiko = to_dt.strftime("%d.%m.%Y")
    except:
        from_date_iiko = from_date
        to_date_iiko = to_date
    
    url = f"{base_url}/resto/api/v2/reports/olap/byPresetId/{preset_id}"
    
    params = {
        "key": token,
        "from": from_date_iiko,
        "to": to_date_iiko,
    }
    
    try:
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            response = await client.get(url, params=params)
        
        if response.status_code != 200:
            logger.error(f"❌ Ошибка preset-отчета: {response.status_code}")
            return []
        
        # Парсим JSON ответ
        data = json.loads(response.text)
        orders = []
        
        for item in data.get("data", []):
            close_time = item.get("CloseTime")
            sum_val = item.get("DishDiscountSumInt", 0)
            
            if close_time:
                orders.append({
                    'closeTime': close_time,
                    'sum': float(sum_val)
                })
        
        logger.info(f"✅ Получено {len(orders)} заказов из preset-отчета")
        return orders
        
    except Exception as e:
        logger.error(f"❌ Ошибка preset-отчета: {e}")
        return []





## ────────────── Получение заказов через OLAP отчет ──────────────
async def get_orders_from_olap(from_date: str, to_date: str) -> list:
    """Получает все заказы за период через preset-отчет"""
    PRESET_REPORT_ID = "9555cc88-492c-48f6-9a09-629346af5bde"
    return await get_preset_report_by_id(PRESET_REPORT_ID, from_date, to_date)



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
    
    # Сразу получаем заказы через preset-отчет
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
                            
                            # Пробуем ISO формат с миллисекундами
                            if 'T' in order_time_str:
                                # Формат: 2025-11-01T07:39:58.455
                                order_time = datetime.fromisoformat(order_time_str.replace('Z', '+00:00'))
                            else:
                                # Другие форматы
                                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M']:
                                    try:
                                        order_time = datetime.strptime(order_time_str, fmt)
                                        break
                                    except ValueError:
                                        continue
                            
                            order_time = order_time.replace(tzinfo=None)
                            shift_start_tz = shift_start.replace(tzinfo=None)
                            shift_end_tz = shift_end.replace(tzinfo=None)
                            
                            if shift_start_tz <= order_time <= shift_end_tz:
                                shift_info["orders"].append(order)
                                
                        except Exception as e:
                            logger.debug(f"Ошибка парсинга времени заказа: {e}")
                            continue
                except Exception as e:
                    logger.warning(f"Ошибка обработки смены {shift_info.get('id')}: {e}")
    
    total_orders = sum(len(s["orders"]) for s in shifts_with_orders)
    logger.info(f"✅ Загружено {len(shifts_with_orders)} смен с {total_orders} заказами")
    return shifts_with_orders
