from fastapi import FastAPI, Request
from aiogram.types import Update
from bot import dp
from config import bot  # ✅ заменил импорт

app = FastAPI()

@app.on_event("startup")
async def startup():
    await bot.set_webhook("https://botiiko-production.railway.app/webhook")

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
