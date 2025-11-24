"""
База данных для управления цехами (отделами) и привязки должностей
"""
import logging
from typing import List, Dict, Optional
from utils.db_stores import get_pool

logger = logging.getLogger(__name__)

# Список доступных цехов
DEPARTMENTS = [
    "Кондитерский",
    "Кухня",
    "Зал",
    "Админ"
]


async def init_departments_table():
    """
    Создает таблицу для хранения привязки должностей к цехам
    
    Структура:
    - department: название цеха (Кондитерский, Кухня, Зал, Админ)
    - position_name: название должности из iiko
    - added_at: дата добавления
    
    UNIQUE(department, position_name) - одна должность только в одном цеху
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS department_positions (
                id SERIAL PRIMARY KEY,
                department TEXT NOT NULL,
                position_name TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(position_name)
            )
        """)
        
        # Создаем индекс для быстрого поиска
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_department_positions_dept 
            ON department_positions(department)
        """)
        
        logger.info("✅ Таблица department_positions инициализирована")


async def get_all_departments() -> List[str]:
    """Получить список всех цехов"""
    return DEPARTMENTS.copy()


async def get_department_positions(department: str) -> List[str]:
    """
    Получить список должностей в конкретном цехе
    
    Args:
        department: название цеха
        
    Returns:
        список названий должностей
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT position_name 
            FROM department_positions 
            WHERE department = $1
            ORDER BY position_name
        """, department)
        
        return [row['position_name'] for row in rows]


async def get_all_department_positions() -> Dict[str, List[str]]:
    """
    Получить все цеха с их должностями
    
    Returns:
        словарь {название_цеха: [список_должностей]}
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT department, position_name 
            FROM department_positions 
            ORDER BY department, position_name
        """)
        
        result = {dept: [] for dept in DEPARTMENTS}
        for row in rows:
            result[row['department']].append(row['position_name'])
        
        return result


async def get_position_department(position_name: str) -> Optional[str]:
    """
    Узнать в каком цехе находится должность
    
    Args:
        position_name: название должности
        
    Returns:
        название цеха или None если должность не привязана
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT department 
            FROM department_positions 
            WHERE position_name = $1
        """, position_name)
        
        return row['department'] if row else None


async def add_position_to_department(department: str, position_name: str) -> bool:
    """
    Добавить должность в цех
    
    Args:
        department: название цеха
        position_name: название должности
        
    Returns:
        True если успешно, False если должность уже в другом цехе
    """
    pool = get_pool()
    
    # Проверяем что цех существует
    if department not in DEPARTMENTS:
        logger.error(f"Неизвестный цех: {department}")
        return False
    
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO department_positions (department, position_name)
                VALUES ($1, $2)
            """, department, position_name)
            
            logger.info(f"✅ Должность '{position_name}' добавлена в цех '{department}'")
            return True
            
        except Exception as e:
            if "unique" in str(e).lower():
                logger.warning(f"Должность '{position_name}' уже привязана к другому цеху")
                return False
            else:
                logger.error(f"Ошибка добавления должности в цех: {e}")
                return False


async def remove_position_from_department(position_name: str) -> bool:
    """
    Удалить должность из цеха
    
    Args:
        position_name: название должности
        
    Returns:
        True если успешно удалено, False если должность не найдена
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("""
            DELETE FROM department_positions 
            WHERE position_name = $1
        """, position_name)
        
        # Проверяем сколько строк удалено
        deleted_count = int(result.split()[-1])
        
        if deleted_count > 0:
            logger.info(f"✅ Должность '{position_name}' удалена из цеха")
            return True
        else:
            logger.warning(f"Должность '{position_name}' не найдена в цехах")
            return False


async def get_available_positions() -> List[str]:
    """
    Получить список всех должностей из iiko,
    которые еще не привязаны ни к одному цеху
    
    Returns:
        список названий должностей
    """
    from iiko.iiko_auth import get_auth_token, get_base_url
    import httpx
    import xml.etree.ElementTree as ET
    
    try:
        # Получаем должности из iiko
        token = await get_auth_token()
        base_url = get_base_url()
        
        roles_url = f"{base_url}/resto/api/employees/roles"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            roles_response = await client.get(
                roles_url,
                headers={"Cookie": f"key={token}"}
            )
        
        if roles_response.status_code != 200:
            logger.error(f"Ошибка получения должностей из iiko: {roles_response.status_code}")
            return []
        
        # Парсим XML
        roles_tree = ET.fromstring(roles_response.text)
        all_positions = []
        
        for role in roles_tree.findall(".//role"):
            name = role.findtext("name")
            if name:
                all_positions.append(name)
        
        # Получаем уже привязанные должности
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT position_name FROM department_positions
            """)
            assigned_positions = {row['position_name'] for row in rows}
        
        # Фильтруем - возвращаем только не привязанные
        available = [pos for pos in all_positions if pos not in assigned_positions]
        available.sort()
        
        return available
        
    except Exception as e:
        logger.error(f"Ошибка получения доступных должностей: {e}")
        return []
