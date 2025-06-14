import httpx

# Настройки для авторизации
LOGIN = "Egor"
SHA1_PASSWORD = "7490e2f38428c6056f445693397091a1eaaa0f29"
BASE_URL = "https://pizzayolo.iiko.it"


async def get_auth_token() -> str:
    """
    Получить токен авторизации от iiko
    """
    auth_url = f"{BASE_URL}/resto/api/auth"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "login": LOGIN,
        "pass": SHA1_PASSWORD
    }

    try:
        response = httpx.post(auth_url, headers=headers, data=data, verify=False)
        response.raise_for_status()
        token = response.text.strip()
        if not token:
            raise ValueError("Не удалось получить токен")
        return token
    except httpx.HTTPError as e:
        print(f"[Ошибка авторизации] HTTP error: {e}")
        raise
    except Exception as e:
        print(f"[Ошибка авторизации] {e}")
        raise


def get_base_url() -> str:
    """
    Вернуть базовый URL для iiko API
    """
    return BASE_URL


