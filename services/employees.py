# services/employees.py

import httpx
import logging
from iiko.iiko_auth import get_auth_token, get_base_url

import xml.etree.ElementTree as ET

async def fetch_employees():
    token = await get_auth_token()
    base_url = get_base_url()

    url = f"{base_url}/resto/api/employees?key={token}"
    
    try:
        response = httpx.get(url, timeout=10.0, verify=False)
        if response.status_code != 200:
            logging.error(f"❌ Ошибка при запросе: {response.status_code}")
            return []

        # Парсим XML
        employees = []
        root = ET.fromstring(response.text)
        for emp in root.findall("employee"):
            employees.append({
                "id": emp.findtext("id"),
                "name": emp.findtext("name"),
                "phone": emp.findtext("cellPhone"),
                "department": emp.findtext("preferredDepartmentCode"),
                "card": emp.findtext("cardNumber"),
                "role": emp.findtext("mainRoleCode")
            })

        return employees

    except Exception as e:
        logging.error("❌ Ошибка при получении сотрудников: %s", e)
        return []
