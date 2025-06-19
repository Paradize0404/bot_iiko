import json
import httpx
from iiko.iiko_auth import get_auth_token, get_base_url


async def fetch_accounts():
    try:
        token = await get_auth_token()
        url = f"{get_base_url()}/resto/api/v2/entities/accounts/list?key={token}"

        response = httpx.get(url, verify=False)
        response.raise_for_status()

        accounts = response.json()

        with open("accounts_dump.json", "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=4, ensure_ascii=False)

        print("✅ Счета успешно сохранены в accounts_dump.json")

    except Exception as e:
        print(f"❌ Ошибка при получении счетов: {e}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(fetch_accounts())
