# services/employees.py

import httpx
import logging
from iiko.iiko_auth import get_auth_token, get_base_url

async def fetch_employees():
    """
    Получает список активных сотрудников из iiko и возвращает только id, firstName, lastName
    """
    token = await get_auth_token()
    url = f"{get_base_url()}/resto/api/employees?key={token}"

    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw_data = response.json()

        employees = [
            {
                "id": emp.get("id"),
                "first_name": emp.get("firstName"),
                "last_name": emp.get("lastName")
            }
            for emp in raw_data
            if emp.get("id") and emp.get("firstName") and emp.get("lastName")
        ]

        logging.info(f"✅ Загружено сотрудников: {len(employees)}")
        return employees

    except Exception as e:
        logging.error(f"❌ Ошибка при получении сотрудников: {e}")
        return []
