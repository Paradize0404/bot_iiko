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
# from utils.db_stores import init_pool

logging.basicConfig(level=logging.INFO)
logging.info("📦 Initializing Dispatcher")

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(commands.router)
dp.include_router(document.router)
dp.include_router(template_creation.router)
dp.include_router(writeoff.router)
dp.include_router(salary.router)
dp.include_router(use_template.router)

logging.info("✅ Routers registered")


