import httpx
from iiko.iiko_auth import get_auth_token, get_base_url  # ⬅️ относительный импорт


async def get_cash_shifts_with_details(from_date: str, to_date: str) -> float:
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

    return [
        {
            "id": shift.get("id"),
            "openDate": shift.get("openDate"),
            "closeDate": shift.get("closeDate"),
            "payOrders": shift.get("payOrders", 0)
            # другие поля, если нужны
        }
        for shift in data
    ]
