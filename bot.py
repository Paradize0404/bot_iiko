import os
import logging
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from handlers import salary  # новый модуль
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

logging.basicConfig(level=logging.DEBUG)
logging.info("📦 Initializing Dispatcher")

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(commands.router)
dp.include_router(salary.router)
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


