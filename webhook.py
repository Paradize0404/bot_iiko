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
from services.negative_transfer_scheduler import run_periodic_negative_transfer
from utils.db_stores import init_pool
from handlers.template_creation import preload_stores
load_dotenv()
logging.basicConfig(level=logging.INFO)

MODE = os.getenv("MODE", "dev")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

startup_complete = False

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    
    await init_pool()
    await preload_stores()
    # Запускаем авто-перемещения по отрицательным остаткам (раз в сутки, первый запуск сразу)
    asyncio.create_task(run_periodic_negative_transfer(run_immediately=True))
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
        webhook_url = WEBHOOK_URL or "https://botiiko-production.up.railway.app/webhook"
        if DRY_RUN:
            logging.info("DRY_RUN=true — пропускаем установку webhook; логируем URL: %s", webhook_url)
        else:
            if not webhook_url:
                logging.error("WEBHOOK_URL не задан — пропускаем установку webhook")
            else:
                await bot.set_webhook(webhook_url)

    # mark startup complete for readiness checks
    global startup_complete
    startup_complete = True

@app.post("/webhook")
async def handle_webhook(request: Request):
    logging.info("📥 Webhook получил обновление")
    data = await request.json()
    update = Update.model_validate(data)
    # ensure we have a Bot instance (startup should have created it)
    bot = config.bot
    if bot is None:
        # fallback: try to create a bot instance on demand
        try:
            bot = config.get_bot()
            config.bot = bot
        except Exception as e:
            logging.exception("Не удалось создать Bot для обработки webhook: %s", e)
            return {"ok": False, "error": str(e)}

    await dp.feed_update(bot, update)
    return {"ok": True}


@app.get("/health")
async def health():
    if startup_complete:
        return {"ok": True, "ready": True}
    return {"ok": False, "ready": False}

if __name__ == "__main__":
    if MODE == "dev":
        asyncio.run(on_startup())  # локальный запуск polling
    else:
        uvicorn.run("webhook:app", host="0.0.0.0", port=8000)
