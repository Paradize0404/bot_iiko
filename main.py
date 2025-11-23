## ────────────── Импорт библиотек и настройка логирования ──────────────
import asyncio
import logging
from utils.logging_config import setup_logging

# initialize logging early
setup_logging()

import config
from bot import dp
from utils.db_stores import init_pool
from handlers.template_creation import preload_stores
from db.position_commission_db import init_position_commissions_db

## ────────────── Функция запуска бота ──────────────
async def _startup():
    """
    Инициализация пула соединений БД, кэширование складов и запуск polling
    """
    await init_pool()
    await init_position_commissions_db()  # инициализируем таблицу комиссий по должностям
    await preload_stores()
    # ensure Bot instance exists and use it for polling
    if config.bot is None:
        config.bot = config.get_bot()
    
    # Удаляем webhook перед запуском polling
    from aiogram.methods import DeleteWebhook
    await config.bot(DeleteWebhook(drop_pending_updates=True))
    logging.info("✅ Webhook удалён, запускаем polling")
    
    await dp.start_polling(config.bot)


if __name__ == "__main__":
    logging.info("🧪 Локальный режим — запуск polling")
    asyncio.run(_startup())