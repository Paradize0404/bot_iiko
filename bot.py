import os
import logging
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage


from handlers import commands # твои роутеры

logging.basicConfig(level=logging.INFO)
logging.info("📦 Initializing Dispatcher")

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(commands.router)

logging.info("✅ Routers registered")

