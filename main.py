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
from db.employee_position_history_db import init_employee_position_history_db
from services.position_monitor import run_periodic_monitoring

## ────────────── Функция запуска бота ──────────────
async def _startup():
    """
    Инициализация пула соединений БД, кэширование складов и запуск polling
    """
    await init_pool()
    await init_position_commissions_db()  # инициализируем таблицу комиссий по должностям
    await init_employee_position_history_db()  # инициализируем таблицу истории должностей
    await preload_stores()
    
    # Запускаем тестовый мониторинг должностей сразу при старте
    logging.info("🧪 Запуск тестового мониторинга должностей...")
    try:
        from services.position_monitor import run_once
        await run_once()
        logging.info("✅ Тестовый мониторинг завершен")
    except Exception as e:
        logging.error(f"❌ Ошибка при тестовом мониторинге: {e}")
    
    # Запускаем фоновую задачу мониторинга должностей (раз в 24 часа)
    asyncio.create_task(run_periodic_monitoring(24))
    logging.info("🔄 Запущен периодический мониторинг изменений должностей (каждые 24 часа)")
    
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