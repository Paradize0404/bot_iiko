# services/employees.py

import httpx
import logging
from iiko.iiko_auth import get_auth_token, get_base_url

import xml.etree.ElementTree as ET
from db.employees_db import save_employees, init_db
logging.basicConfig(level=logging.DEBUG)


async def fetch_employees():
    token = await get_auth_token()
    base_url = get_base_url()

    url = f"{base_url}/resto/api/employees?key={token}"
    
    try:
        response = httpx.get(url, timeout=10.0, verify=False)
        if response.status_code != 200:
            logging.error(f"❌ Ошибка при запросе: {response.status_code}")
            return []

        logging.debug(f"📄 Ответ от API сотрудников:\n{response.text}")

        root = ET.fromstring(response.text)
        employees = []

        for emp in root.findall('employee'):
            emp_id = emp.find('id').text if emp.find('id') is not None else None
            first_name = emp.find('firstName').text if emp.find('firstName') is not None else ""
            last_name = emp.find('lastName').text if emp.find('lastName') is not None else ""

            # ⛔️ Пропускаем если нет first_name
            if not first_name.strip():
                continue

            data = {
                "id": emp_id,
                "first_name": first_name,
                "last_name": last_name,
                "rate": 280.0,
                "commission_percent": 0.4,
                "monthly": False,
                "per_shift": False,
                "department": "Зал"
            }

            print(data)
            employees.append(data)
        await init_db()
        await save_employees(employees)

        return employees


    except Exception as e:
        logging.error("❌ Ошибка при получении сотрудников: %s", e)
        return []


