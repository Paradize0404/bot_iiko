"""Инициализация Dispatcher и регистрация роутеров бота"""
import logging
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Handlers
from handlers import (
    commands,
    salary,
    set_position_commission,
    correct_position,
    yandex_commission_settings,
    cost_plan_settings,
    departments_manager,
    document,
    template_creation,
    use_template,
    writeoff,
    writeoff_upload,
    sales_olap_console,
    purchase_report,
    store_balance_report,
    supplier_balance_report,
    internal_transfer_upload,
    invoice,
)
from keyboards import main_keyboard

logger = logging.getLogger(__name__)
logger.info("📦 Initializing Dispatcher")

dp = Dispatcher(storage=MemoryStorage())

## ────────────── Регистрация роутеров ──────────────
# Порядок важен: более специфичные роутеры должны быть выше
dp.include_router(commands.router)
dp.include_router(salary.router)
dp.include_router(set_position_commission.router)
dp.include_router(correct_position.router)
dp.include_router(yandex_commission_settings.router)
dp.include_router(cost_plan_settings.router)
dp.include_router(departments_manager.router)
dp.include_router(writeoff_upload.router)
dp.include_router(sales_olap_console.router)
dp.include_router(purchase_report.router)
dp.include_router(store_balance_report.router)
dp.include_router(supplier_balance_report.router)
dp.include_router(document.router)
dp.include_router(template_creation.router)
dp.include_router(writeoff.router)
dp.include_router(internal_transfer_upload.router)
dp.include_router(invoice.router)
dp.include_router(main_keyboard.router)
dp.include_router(use_template.router)

logger.info("✅ Routers registered")


