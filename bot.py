import os
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage


from handlers import commands # твои роутеры

# Загрузка токена из переменной окружения

# Инициализация бота и диспетчера

dp = Dispatcher(storage=MemoryStorage())

# Регистрация роутеров
dp.include_router(commands.router)

