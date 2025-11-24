"""
Модуль для работы с настройками бота в БД
Включает хранение процента комиссии Яндекса для расчета доставки
"""

import logging
from utils.db_stores import get_pool

logger = logging.getLogger(__name__)


async def get_yandex_commission() -> float:
    """
    Получить процент комиссии Яндекса
    Возвращает float (например, 25.5 для 25.5%)
    По умолчанию 0.0 если не установлен
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.fetchval(
            """
            SELECT value FROM settings WHERE key = 'yandex_commission'
            """
        )
        if result is None:
            logger.info("Комиссия Яндекса не установлена, используем 0%")
            return 0.0
        return float(result)


async def set_yandex_commission(percent: float) -> None:
    """
    Установить процент комиссии Яндекса
    
    Args:
        percent: процент комиссии (например, 25.5 для 25.5%)
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('yandex_commission', $1)
            ON CONFLICT (key) 
            DO UPDATE SET value = $1, updated_at = NOW()
            """,
            str(percent)
        )
        logger.info(f"Установлена комиссия Яндекса: {percent}%")


async def init_settings_table():
    """
    Создать таблицу настроек если её нет
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        logger.info("Таблица settings инициализирована")
