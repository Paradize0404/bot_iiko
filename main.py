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
    await init_pool()  # подключение к PostgreSQL до запуска бота
    
    # ensure Bot instance exists and use it for polling
    if config.bot is None:
        config.bot = config.get_bot()
    
    # Удаляем webhook перед запуском polling
    from aiogram.methods import DeleteWebhook
    await config.bot(DeleteWebhook(drop_pending_updates=True))
    logging.info("✅ Webhook удалён, запускаем polling")

    # Сначала поднимаем бота
    polling_task = asyncio.create_task(dp.start_polling(config.bot))
    logging.info("🤖 Polling запущен, теперь поднимаем планировщики и FinTablo")

    # После старта polling — инициализация таблиц/кэшей
    await init_position_commissions_db()  # таблица комиссий по должностям
    await init_employee_position_history_db()  # история должностей
    await init_settings_table()  # настройки (например, комиссия Яндекс)
    await init_departments_table()  # цеха и должности
    await preload_stores()

    # После старта polling — поднимаем планировщики
    asyncio.create_task(run_periodic_monitoring(24, delay_first_run=True))
    logging.info("🔄 Запущен периодический мониторинг изменений должностей (первая проверка через 1 час)")

    asyncio.create_task(run_periodic_negative_transfer(run_immediately=False))
    logging.info("🔄 Запущен планировщик авто-перемещений (только по расписанию 23:00)")

    asyncio.create_task(run_periodic_low_stock(run_immediately=False))
    logging.info("🔄 Запущен мониторинг остаточных стоп-листов (каждые 2 часа, без старта при запуске)")

    asyncio.create_task(run_daily_fot_fill(run_immediately=False))
    logging.info("🔄 Запущено ежедневное заполнение ФОТ-листа (07:00)")

    asyncio.create_task(run_daily_positions_sync_at_noon())
    logging.info("🔄 Запущена ежедневная синхронизация должностей в таблицу (каждый день в 12:00)")

    async def start_fin_tab_worker():
        from fin_tab.main import main as fin_tab_main

        try:
            await fin_tab_main()
        except Exception:  # pragma: no cover
            logging.exception("FinTablo worker crashed")

    asyncio.create_task(start_fin_tab_worker())

    await polling_task


if __name__ == "__main__":
    logging.info("🧪 Локальный режим — запуск polling")
    asyncio.run(_startup())