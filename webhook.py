import logging
from fastapi import FastAPI, Request
from aiogram.types import Update
from bot import dp
from config import bot

logging.basicConfig(level=logging.INFO)

app = FastAPI()

@app.on_event("startup")
async def startup():
    logging.info("🚀 Устанавливаю webhook...")
    await bot.set_webhook("https://botiiko-production.up.railway.app/webhook")

@app.post("/webhook")
async def webhook(request: Request):
    logging.info("📥 Webhook получил запрос")
    data = await request.json()
    logging.debug(f"Payload: {data}")
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
