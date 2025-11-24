## ────────────── Конфигурация бота и настройки проекта ──────────────
import os
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

load_dotenv()

# Configuration values. Avoid side-effects on import — create runtime resources lazily.
BOT_TOKEN = os.getenv("BOT_TOKEN")

## ────────────── Функция создания экземпляра бота ──────────────
def get_bot(token: str | None = None):
    """Create and return an aiogram Bot instance. Raises if no token provided."""
    tk = token or BOT_TOKEN
    if not tk:
        raise RuntimeError("BOT_TOKEN not set. Call get_bot(token) with a valid token.")
    from aiogram import Bot
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties

    return Bot(token=tk, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# `bot` is intentionally None at import time; create via `get_bot()` in runtime entrypoints.
bot = None

# ───────────────────── ADMINS & ROLES ──────────────────────
ADMIN_IDS = [1877127405, 1059714785, 1078562089, 6446544048]

# ───────────────────── DOCUMENT CONFIGS ──────────────────────
DOC_CONFIG = {
    "writeoff": {
        "stores": {
            "Бар": ["Списание бар порча", "Списание бар пролив", "Списание бар проработка"],
            "Кухня": ["Списание кухня порча", "Списание кухня проработка", "Питание персонала"]
        }
    },
    "internal_transfer": {
        "stores": ["Бар", "Кухня"]
    },
}

# ───────────────────── NOMENCLATURE FILTERS ──────────────────────
PARENT_FILTERS = [
    '4d2a8e1d-7c24-4df1-a8bd-58a6e2e82a12',
    '6c5f1595-ce55-459d-b368-94bab2f20ee3'
]

STORE_NAME_MAP = {
    "Бар": ["Бар Пиццерия"],
    "Кухня": ["Кухня Пиццерия"]
}
# ───────────────────── ADMINS & ROLES ──────────────────────
