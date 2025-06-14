import asyncio
import logging
from config import bot
from bot import dp

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    logging.info("🧪 Локальный режим — запуск polling")
    asyncio.run(dp.start_polling(bot))