"""
Тестовый скрипт для ручного запуска мониторинга должностей
Использует: python test_position_monitor.py
"""
import asyncio
import logging
from utils.logging_config import setup_logging
from utils.db_stores import init_pool
from db.employee_position_history_db import init_employee_position_history_db
from services.position_monitor import run_once

setup_logging()
logger = logging.getLogger(__name__)

async def main():
    """Запускаем разовую проверку должностей"""
    logger.info("🚀 Запуск тестового мониторинга должностей...")
    
    # Инициализируем БД
    await init_pool()
    await init_employee_position_history_db()
    
    # Запускаем проверку один раз
    await run_once()
    
    logger.info("✅ Мониторинг завершен")

if __name__ == "__main__":
    asyncio.run(main())
