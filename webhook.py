# webhook.py
import os
import asyncio
import logging
from dotenv import load_dotenv

import config
from bot import dp      # ← используем dp из bot.py

from aiogram.methods import DeleteWebhook
from fastapi import FastAPI, Request
from aiogram.types import Update
import uvicorn
from utils.db_stores import init_pool
from handlers.template_creation import preload_stores
load_dotenv()
logging.basicConfig(level=logging.INFO)

MODE = os.getenv("MODE", "dev")

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    
    await init_pool()
    await preload_stores()
    # ensure Bot instance exists at runtime
    if config.bot is None:
        config.bot = config.get_bot()
    bot = config.bot

    if MODE == "dev":
        logging.info("🧪 dev mode: удаляем webhook и запускаем polling")
        # delete webhook and start polling locally
        await bot(DeleteWebhook(drop_pending_updates=True))
        await dp.start_polling(bot)
    else:
        logging.info("🚀 prod mode: устанавливаем webhook")
        await bot.set_webhook("https://botiiko-production.up.railway.app/webhook")

@app.post("/webhook")
async def handle_webhook(request: Request):
    logging.info("📥 Webhook получил обновление")
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

if __name__ == "__main__":
    if MODE == "dev":
        asyncio.run(on_startup())  # локальный запуск polling
    else:
        uvicorn.run("webhook:app", host="0.0.0.0", port=8000)
