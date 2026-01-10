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
from db.settings_db import init_settings_table
from db.departments_db import init_departments_table
from services.position_monitor import run_periodic_monitoring
from services.position_sheet_sync import run_daily_positions_sync_at_noon
from services.negative_transfer_scheduler import run_periodic_negative_transfer
from scripts.low_stock_scheduler import run_periodic_low_stock
from services.fot_sheet_scheduler import run_daily_fot_fill

## ────────────── Функция запуска бота ──────────────
async def _startup():
    """
    Инициализация пула соединений БД, кэширование складов и запуск polling
    """
    await init_pool()
    await init_position_commissions_db()  # инициализируем таблицу комиссий по должностям
    await init_employee_position_history_db()  # инициализируем таблицу истории должностей
    await init_settings_table()  # инициализируем таблицу настроек (для Яндекс комиссии и др.)
    await init_departments_table()  # инициализируем таблицу цехов и должностей
    await preload_stores()
    
    # Запускаем фоновую задачу мониторинга должностей (раз в 24 часа)
    # Первая проверка будет через 1 час после запуска, чтобы не замедлять старт бота
    asyncio.create_task(run_periodic_monitoring(24, delay_first_run=True))
    logging.info("🔄 Запущен периодический мониторинг изменений должностей (первая проверка через 1 час)")

    # Ежедневное авто-перемещение по отрицательным остаткам: только по расписанию (23:00), без мгновенного запуска
    asyncio.create_task(run_periodic_negative_transfer(run_immediately=False))
    logging.info("🔄 Запущен планировщик авто-перемещений (только по расписанию 23:00)")

    # Стоп-лист по min-остаткам: только по расписанию (каждые 2 часа), без мгновенного прогона
    asyncio.create_task(run_periodic_low_stock(run_immediately=False))
    logging.info("🔄 Запущен мониторинг остаточных стоп-листов (каждые 2 часа, без старта при запуске)")

    # Ежедневное заполнение ФОТ-листа в 07:00
    asyncio.create_task(run_daily_fot_fill(run_immediately=False))
    logging.info("🔄 Запущено ежедневное заполнение ФОТ-листа (07:00)")

    # Ежедневное обновление должностей в Google Sheets в 12:00
    asyncio.create_task(run_daily_positions_sync_at_noon())
    logging.info("🔄 Запущена ежедневная синхронизация должностей в таблицу (каждый день в 12:00)")
    
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