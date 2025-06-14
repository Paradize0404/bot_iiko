# webhook.py
import os
import asyncio
import logging
from dotenv import load_dotenv

from config import bot  # ← теперь используем bot из config
from bot import dp      # ← используем dp из bot.py

from aiogram.methods import DeleteWebhook
from fastapi import FastAPI, Request
from aiogram.types import Update
import uvicorn

load_dotenv()
logging.basicConfig(level=logging.INFO)

MODE = os.getenv("MODE", "dev")

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    if MODE == "dev":
        logging.info("🧪 dev mode: удаляем webhook и запускаем polling")
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
        uvicorn.run
