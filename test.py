import asyncio
import httpx
from iiko.iiko_auth import get_auth_token, get_base_url


async def fetch_suppliers():
    token = await get_auth_token()
    base_url = get_base_url()
    url = f"{base_url}/resto/api/suppliers?key={token}"

    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            print("🧾 Ответ сервера:")
            print(response.text)  # ВАЖНО: покажет, что реально вернулось
            suppliers = response.json()

            print("✅ Получено количество поставщиков:", len(suppliers))
            for supplier in suppliers:
                print(f"🧾 {supplier.get('name', 'Без имени')} | ID: {supplier.get('id')}")

    except httpx.HTTPError as e:
        print(f"[Ошибка запроса] HTTP error: {e}")
    except Exception as e:
        print(f"[Ошибка] {e}")


if __name__ == "__main__":
    asyncio.run(fetch_suppliers())