import asyncio
import logging
from config import bot
from bot import dp
from utils.db_stores import init_pool

logging.basicConfig(level=logging.INFO)

async def _startup():
    await init_pool()            # ← добавляем
    await dp.start_polling(bot)


if __name__ == "__main__":
    
    logging.info("🧪 Локальный режим — запуск polling")
    asyncio.run(_startup())