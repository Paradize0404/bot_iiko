import asyncio
import logging
from utils.logging_config import setup_logging

# initialize logging early
setup_logging()

import config
from bot import dp
from utils.db_stores import init_pool
from handlers.template_creation import preload_stores

async def _startup():
    await init_pool()  
    await preload_stores()          # ← добавляем
    # ensure Bot instance exists and use it for polling
    if config.bot is None:
        config.bot = config.get_bot()
    await dp.start_polling(config.bot)


if __name__ == "__main__":
    logging.info("🧪 Локальный режим — запуск polling")
    asyncio.run(_startup())