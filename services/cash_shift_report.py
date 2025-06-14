import httpx
from iiko.iiko_auth import get_auth_token, get_base_url  # ⬅️ относительный импорт


async def get_cash_shift_total_payorders(from_date: str, to_date: str) -> float:
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

    response = httpx.get(url, headers=headers, params=params, verify=False)

    if response.status_code != 200:
        raise Exception(f"Ошибка при получении смен: {response.status_code} — {response.text}")

    try:
        data = response.json()
    except Exception as e:
        raise Exception(f"Ошибка при разборе JSON: {e}")

    # Суммируем значения поля payOrders (если оно есть)
    pay_orders_sum = sum(shift.get("payOrders", 0) for shift in data)
    return pay_orders_sum
