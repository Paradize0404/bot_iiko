"""
Автоматический мониторинг изменения должностей сотрудников
Запускается периодически для обновления истории должностей
"""
import asyncio
import logging
from datetime import datetime, date
import httpx
import xml.etree.ElementTree as ET
from iiko.iiko_auth import get_auth_token, get_base_url
from db.employee_position_history_db import update_position_from_iiko, init_employee_position_history_db

# Дата для новых сотрудников при первой загрузке (показывает что должность "с давних времен")
DEFAULT_POSITION_START_DATE = date(2020, 1, 1)

logger = logging.getLogger(__name__)


async def get_employees_with_positions_from_iiko() -> dict:
    """
    Получает список всех сотрудников с их текущими должностями из iiko
    
    Returns:
        {employee_id: {'name': str, 'position': str}, ...}
    """
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        # 1. Получаем справочник должностей
        roles_url = f"{base_url}/resto/api/employees/roles"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            roles_response = await client.get(
                roles_url,
                headers={"Cookie": f"key={token}"}
            )
        roles_response.raise_for_status()
        
        roles_tree = ET.fromstring(roles_response.text)
        roles_dict = {}
        
        for role in roles_tree.findall(".//role"):
            code = role.findtext("code")
            name = role.findtext("name")
            if code and name:
                roles_dict[code] = name
        
        # 2. Получаем список сотрудников
        employees_url = f"{base_url}/resto/api/employees"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            emp_response = await client.get(
                employees_url,
                headers={"Cookie": f"key={token}"},
                params={"includeDeleted": "false"}
            )
        emp_response.raise_for_status()
        
        emp_tree = ET.fromstring(emp_response.text)
        employees = {}
        
        for emp in emp_tree.findall(".//employee"):
            emp_id = emp.findtext("id")
            emp_name = emp.findtext("name", "Неизвестно")
            
            if not emp_id:
                continue
            
            # Пропускаем удаленных
            if emp.findtext("deleted", "false") == "true":
                continue
            
            # Получаем код должности
            position_code = None
            role_codes_element = emp.find('roleCodes')
            if role_codes_element is not None:
                role_code = role_codes_element.find('string')
                if role_code is not None and role_code.text:
                    position_code = role_code.text
            
            if not position_code:
                position_code = emp.findtext('mainRoleCode')
            
            # Преобразуем код в название
            position_name = roles_dict.get(position_code, "—") if position_code else "—"
            
            if position_name != "—":
                employees[emp_id] = {
                    'name': emp_name,
                    'position': position_name
                }
        
        return employees
    
    except Exception as e:
        logger.exception(f"❌ Ошибка получения сотрудников из iiko: {e}")
        return {}


async def monitor_position_changes():
    """
    Основная функция мониторинга изменений должностей
    Сравнивает текущие должности в iiko с историей в БД
    """
    logger.info("🔍 Запуск мониторинга должностей сотрудников...")
    
    try:
        # Получаем актуальные данные из iiko
        iiko_employees = await get_employees_with_positions_from_iiko()
        
        if not iiko_employees:
            logger.warning("⚠️ Не удалось получить данные о сотрудниках из iiko")
            return
        
        logger.info(f"📊 Получено {len(iiko_employees)} активных сотрудников из iiko")
        
        # Обновляем данные в БД (batch операция для ускорения)
        changes_count = 0
        new_count = 0
        
        # Собираем все операции
        from db.employee_position_history_db import get_current_position, set_employee_position
        from datetime import date
        
        for emp_id, data in iiko_employees.items():
            stored_position = await get_current_position(emp_id)
            current_position = data['position']
            emp_name = data['name']
            
            # Пропускаем если должность не изменилась
            if stored_position == current_position:
                continue
            
            if stored_position is None:
                # Новый сотрудник - используем дату из константы
                logger.info(f"🆕 Новый сотрудник: {emp_name} - {current_position} (с {DEFAULT_POSITION_START_DATE.strftime('%d.%m.%Y')})")
                await set_employee_position(emp_id, emp_name, current_position, DEFAULT_POSITION_START_DATE)
                new_count += 1
            else:
                # Должность изменилась - текущая дата
                logger.info(f"🔄 Изменение должности: {emp_name} ({stored_position} → {current_position})")
                await set_employee_position(emp_id, emp_name, current_position, date.today())
            
            changes_count += 1
        
        if changes_count > 0:
            logger.info(f"✅ Обработано изменений: {changes_count} (новых сотрудников: {new_count})")
        else:
            logger.info("✅ Изменений должностей не обнаружено")
    
    except Exception as e:
        logger.exception(f"❌ Ошибка при мониторинге должностей: {e}")


async def run_periodic_monitoring(interval_hours: int = 24):
    """
    Запускает мониторинг периодически
    
    Args:
        interval_hours: Интервал между проверками в часах (по умолчанию 24 часа)
    """
    logger.info(f"🚀 Запуск периодического мониторинга должностей (каждые {interval_hours} ч)")
    
    # Затем периодически (первый запуск уже был в main.py как тест)
    while True:
        await asyncio.sleep(interval_hours * 3600)
        
        try:
            await monitor_position_changes()
        except Exception as e:
            logger.exception(f"❌ Ошибка в периодическом мониторинге: {e}")
        await monitor_position_changes()


# Функция для ручного запуска (для отладки)
async def run_once():
    """Однократный запуск мониторинга (для тестирования)"""
    await init_employee_position_history_db()
    await monitor_position_changes()


if __name__ == "__main__":
    # Для тестирования
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_once())
