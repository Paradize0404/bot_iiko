from fastapi import FastAPI, Request
from aiogram.types import Update
from bot import bot, dp

app = FastAPI()

@app.on_event("startup")
async def startup():
    await bot.set_webhook("https://<your-railway-app>.railway.app/webhook")

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
