"""
Скрипт для обновления всех дат начала должностей на 01.01.2020
Используется один раз для исправления начальных данных
"""
import asyncio
import logging
from datetime import date
from utils.logging_config import setup_logging
from utils.db_stores import init_pool
from db.employee_position_history_db import async_session, EmployeePositionHistory
from sqlalchemy import update

setup_logging()
logger = logging.getLogger(__name__)

DEFAULT_DATE = date(2020, 1, 1)

async def fix_all_positions():
    """
    Обновляет все записи с valid_from = сегодня на 01.01.2020
    """
    logger.info("🔧 Запуск исправления дат начала должностей...")
    
    await init_pool()
    
    today = date.today()
    
    async with async_session() as session:
        # Обновляем все записи где valid_from = сегодня
        result = await session.execute(
            update(EmployeePositionHistory)
            .where(EmployeePositionHistory.valid_from == today)
            .values(valid_from=DEFAULT_DATE)
        )
        
        await session.commit()
        
        updated_count = result.rowcount
        logger.info(f"✅ Обновлено записей: {updated_count}")
        logger.info(f"📅 Дата изменена: {today.strftime('%d.%m.%Y')} → {DEFAULT_DATE.strftime('%d.%m.%Y')}")
    
    return updated_count

if __name__ == "__main__":
    asyncio.run(fix_all_positions())
