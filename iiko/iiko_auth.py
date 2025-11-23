## ────────────── Модуль авторизации в iiko API ──────────────
import httpx
import logging
import asyncio
from datetime import datetime, timedelta

# Настройки для авторизации
LOGIN = "Egor"
SHA1_PASSWORD = "7490e2f38428c6056f445693397091a1eaaa0f29"
BASE_URL = "https://pizzayolo.iiko.it"

logger = logging.getLogger(__name__)

# Кеш токена
_token_cache = {
    "token": None,
    "expires_at": None
}


## ────────────── Получение токена авторизации ──────────────
async def get_auth_token() -> str:
    """Получить токен авторизации от iiko (async) с кешированием."""
    
    # Проверяем кеш
    if _token_cache["token"] and _token_cache["expires_at"]:
        if datetime.now() < _token_cache["expires_at"]:
            logger.debug("✅ Используем кешированный токен")
            return _token_cache["token"]
    
    # Токен устарел или отсутствует - получаем новый
    auth_url = f"{BASE_URL}/resto/api/auth"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "login": LOGIN,
        "pass": SHA1_PASSWORD
    }

    # Попытка с повтором при 403
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
                response = await client.post(auth_url, headers=headers, data=data)

            response.raise_for_status()
            token = response.text.strip()
            if not token:
                raise ValueError("Не удалось получить токен")
            
            # Сохраняем в кеш на 10 минут
            _token_cache["token"] = token
            _token_cache["expires_at"] = datetime.now() + timedelta(minutes=10)
            logger.debug("🔑 Получен новый токен, кешируем на 10 минут")
            
            return token
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403 and attempt == 0:
                logger.warning("⚠️ Rate limit (403), ждём 3 секунды...")
                await asyncio.sleep(3)
                continue
            logger.exception("[Ошибка авторизации] HTTP error: %s", e)
            raise
        except Exception as e:
            logger.exception("[Ошибка авторизации] %s", e)
            raise
    
    raise Exception("Не удалось получить токен после повторных попыток")


## ────────────── Получение базового URL ──────────────
def get_base_url() -> str:
    """Вернуть базовый URL для iiko API"""
    return BASE_URL


