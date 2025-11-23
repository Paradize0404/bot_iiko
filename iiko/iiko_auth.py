import httpx
import logging

# Настройки для авторизации
LOGIN = "Egor"
SHA1_PASSWORD = "7490e2f38428c6056f445693397091a1eaaa0f29"
BASE_URL = "https://pizzayolo.iiko.it"

logger = logging.getLogger(__name__)


async def get_auth_token() -> str:
    """Получить токен авторизации от iiko (async)."""
    auth_url = f"{BASE_URL}/resto/api/auth"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "login": LOGIN,
        "pass": SHA1_PASSWORD
    }

    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
            response = await client.post(auth_url, headers=headers, data=data)

        response.raise_for_status()
        token = response.text.strip()
        if not token:
            raise ValueError("Не удалось получить токен")
        return token
    except httpx.HTTPError as e:
        logger.exception("[Ошибка авторизации] HTTP error: %s", e)
        raise
    except Exception as e:
        logger.exception("[Ошибка авторизации] %s", e)
        raise


def get_base_url() -> str:
    """Вернуть базовый URL для iiko API"""
    return BASE_URL


