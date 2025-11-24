## ────────────── Импорт библиотек и инициализация Dispatcher ──────────────
import os
import logging
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from handlers import salary  # новый модуль
from handlers import set_position_commission  # настройка комиссий по должностям
from handlers import correct_position  # корректировка истории должностей
from handlers import yandex_commission_settings  # настройка комиссии Яндекса
from handlers import departments_manager  # управление цехами
from handlers import document
from handlers import template_creation
from handlers import commands # твои роутеры
from handlers import use_template
from utils.db_stores import init_pool
from handlers import writeoff
from keyboards import main_keyboard
from handlers import writeoff_upload
from handlers import sales_olap_console
from handlers import internal_transfer_upload
from handlers import invoice
# from utils.db_stores import init_pool

## ────────────── Настройка логирования и создание диспетчера ──────────────
logging.basicConfig(level=logging.DEBUG)
logging.info("📦 Initializing Dispatcher")

dp = Dispatcher(storage=MemoryStorage())

## ────────────── Регистрация роутеров ──────────────
dp.include_router(commands.router)
dp.include_router(salary.router)
dp.include_router(set_position_commission.router)
dp.include_router(correct_position.router)
dp.include_router(yandex_commission_settings.router)
dp.include_router(departments_manager.router)
dp.include_router(writeoff_upload.router)
dp.include_router(sales_olap_console.router)
dp.include_router(document.router)
dp.include_router(template_creation.router)
dp.include_router(writeoff.router)
dp.include_router(internal_transfer_upload.router)
dp.include_router(invoice.router)
dp.include_router(main_keyboard.router)

dp.include_router(use_template.router)

logging.info("✅ Routers registered")


