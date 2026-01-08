"""Периодический стоп-лист по остаткам ниже минимума.

Запуск: python -m scripts.low_stock_scheduler
- каждые 2 часа обновляет список позиций ниже minBalanceLevel для складов Бар/Кухня
- отправляет/обновляет сообщение в Telegram (как в stoplists), только при изменениях
- хранит текущее состояние в БД, чтобы не слать лишние уведомления
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Sequence

import httpx
from dotenv import load_dotenv

from iiko.iiko_auth import get_auth_token, get_base_url
from scripts.export_store_balances import fetch_store_balances, _extract_store_name
from utils.db_stores import get_pool, init_pool


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# Какие склады проверяем
TARGET_STORES = {"Бар Пиццерия", "Кухня Пиццерия"}

# Период запуска (сек)
SCHEDULE_SECONDS = 2 * 60 * 60
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 22  # exclusive

BOT_TOKEN = os.getenv("LOW_STOCK_BOT_TOKEN") or os.getenv("BOT_TOKEN")
DECIMAL_Q = Decimal("0.001")


# --------------------------- БАЗА ---------------------------

async def init_tables() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS low_stock_state (
                store TEXT,
                product TEXT,
                qty NUMERIC,
                min_level NUMERIC,
                PRIMARY KEY (store, product)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS low_stock_message (
                chat_id BIGINT PRIMARY KEY,
                message_id BIGINT
            );
            """
        )


async def get_all_chat_ids() -> list[int]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
    return [int(row[0]) for row in rows]


# --------------------------- ВСПОМОГАТЕЛЬНЫЕ ---------------------------

def _extract_first(row: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


# --------------------------- IIKO ---------------------------

async def fetch_store_map() -> dict[str, str]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token, "revisionFrom": -1}
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        resp = await client.get("/resto/api/corporation/stores", params=params)
        resp.raise_for_status()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(resp.text)
    store_map: dict[str, str] = {}
    for item in root.findall("corporateItemDto"):
        store_id = item.findtext("id")
        name = (item.findtext("name") or "").strip()
        type_value = (item.findtext("type") or "").strip().upper()
        if not store_id or not name:
            continue
        if type_value and type_value != "STORE":
            continue
        store_map[store_id] = name
    return store_map


async def fetch_products() -> list[dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()
    headers = {"Cookie": f"key={token}"}
    async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
        resp = await client.get("/resto/api/v2/entities/products/list", headers=headers)
        resp.raise_for_status()
        return resp.json()


def build_min_levels(products: Sequence[dict[str, Any]], store_map: dict[str, str]) -> dict[tuple[str, str], Decimal]:
    result: dict[tuple[str, str], Decimal] = {}
    for product in products:
        name = product.get("name") or ""
        levels = product.get("storeBalanceLevels") or []
        if not levels:
            continue
        for level in levels:
            store_id = level.get("storeId")
            if not store_id:
                continue
            store_name = store_map.get(store_id)
            if not store_name or store_name not in TARGET_STORES:
                continue
            min_level = level.get("minBalanceLevel")
            if min_level in (None, ""):
                continue
            try:
                min_dec = Decimal(str(min_level)).quantize(DECIMAL_Q, rounding=ROUND_HALF_UP)
            except Exception:
                continue
            result[(store_name, name)] = min_dec
    return result


# --------------------------- ЛОГИКА ОСТАТКОВ ---------------------------

AMOUNT_FIELDS = ("FinalBalance.Amount", "FinalBalance")
UNIT_FIELDS = ("Product.MeasureUnit", "Product.MainUnit", "Product.MeasureName", "Product.Unit")


@dataclass
class LowItem:
    store: str
    product: str
    qty: Decimal
    min_level: Decimal
    unit: str


async def compute_below_min() -> list[LowItem]:
    store_map = await fetch_store_map()
    products = await fetch_products()
    min_levels = build_min_levels(products, store_map)

    rows = await fetch_store_balances()
    result: list[LowItem] = []

    for row in rows:
        store_name = _extract_store_name(row)
        if store_name not in TARGET_STORES:
            continue
        product_name = str(row.get("Product.Name") or "")
        min_level = min_levels.get((store_name, product_name))
        if min_level is None:
            continue

        amount_val = _extract_first(row, AMOUNT_FIELDS, 0)
        try:
            qty = Decimal(str(amount_val)).quantize(DECIMAL_Q, rounding=ROUND_HALF_UP)
        except Exception:
            qty = Decimal(0).quantize(DECIMAL_Q)
        if qty >= min_level:
            continue

        unit = _extract_first(row, UNIT_FIELDS, "")
        result.append(LowItem(store_name, product_name, qty, min_level, str(unit)))

    return result


# --------------------------- DIFF ---------------------------

async def diff_with_state(items: list[LowItem]):
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT store, product, qty, min_level FROM low_stock_state")
        old = {(r[0], r[1]): (Decimal(str(r[2])), Decimal(str(r[3]))) for r in rows}

        # дедуп по (store, product), если вдруг пришли дубль
        unique_items: dict[tuple[str, str], LowItem] = {}
        for i in items:
            key = (i.store, i.product)
            unique_items[key] = i

        new = {key: (val.qty, val.min_level) for key, val in unique_items.items()}

        added: list[LowItem] = []
        removed: list[tuple[str, str]] = []

        for key, val in new.items():
            if key not in old or old[key][0] != val[0]:
                store, product = key
                added.append(unique_items[key])

        for key in old:
            if key not in new:
                removed.append(key)

        await conn.execute("DELETE FROM low_stock_state")
        for item in unique_items.values():
            await conn.execute(
                """
                INSERT INTO low_stock_state (store, product, qty, min_level)
                VALUES ($1, $2, $3, $4)
                """,
                item.store,
                item.product,
                float(item.qty),
                float(item.min_level),
            )
    return added, removed


# --------------------------- TELEGRAM ---------------------------

def format_message(items: list[LowItem]) -> str:
    if not items:
        return "✅ Все остатки выше минимальных.\n#стоплист"
    lines = ["🚫 Позиции ниже минимального остатка:"]
    grouped: dict[str, list[LowItem]] = defaultdict(list)
    for item in items:
        grouped[item.store].append(item)
    for store in sorted(grouped.keys()):
        lines.append(f"\n{store}")
        for it in sorted(grouped[store], key=lambda x: x.product):
            qty_txt = format_decimal(it.qty)
            min_txt = format_decimal(it.min_level)
            lines.append(f"• {it.product}: {qty_txt} {it.unit} < min {min_txt}")
    lines.append("\n#стоплист")
    return "\n".join(lines)


def format_decimal(value: Decimal) -> str:
    return format(value.quantize(DECIMAL_Q, rounding=ROUND_HALF_UP), ".3f")


async def send_or_update_message(text: str) -> None:
    chat_ids = await get_all_chat_ids()
    if not chat_ids:
        logging.info("Нет chat_id для рассылки")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        for chat_id in chat_ids:
            # Сносим старое сообщение, чтобы новое приехало в конец диалога с пушем
            row = await conn.fetchrow("SELECT message_id FROM low_stock_message WHERE chat_id=$1", chat_id)
            if row:
                try:
                    httpx.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": row[0]},
                    )
                except Exception as exc:
                    logging.warning("deleteMessage failed chat=%s err=%s", chat_id, exc)

            r = httpx.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            data = r.json()
            if not data.get("ok"):
                logging.error(f"Telegram send error chat={chat_id}: {data}")
                continue
            msg_id = data["result"]["message_id"]
            await conn.execute(
                """
                INSERT INTO low_stock_message (chat_id, message_id)
                VALUES ($1, $2)
                ON CONFLICT (chat_id) DO UPDATE SET message_id = EXCLUDED.message_id
                """,
                chat_id,
                msg_id,
            )


# --------------------------- ЦИКЛ ---------------------------

async def run_once() -> bool:
    try:
        items = await compute_below_min()
        # обновляем состояние в БД и игнорируем, были ли изменения — всегда шлём актуальный стоп-лист
        await diff_with_state(items)
        text = format_message(items)
        await send_or_update_message(text)
        logging.info("Обновили стоп-лист по min-остаткам: %d позиций", len(items))
        return True
    except Exception:
        logging.exception("Ошибка в run_once")
        return False


async def run_periodic_low_stock(period_seconds: int = SCHEDULE_SECONDS, run_immediately: bool = True) -> None:
    """Фоновый цикл: первый прогон сразу (опционально), далее раз в period_seconds."""
    await init_pool()
    await init_tables()
    if run_immediately:
        now_h = datetime.now().hour
        if WINDOW_START_HOUR <= now_h < WINDOW_END_HOUR:
            await run_once()
        else:
            logging.info(
                "Пропуск мгновенного прогона стоп-листа (вне окна %02d:00-%02d:00)",
                WINDOW_START_HOUR,
                WINDOW_END_HOUR,
            )
    while True:
        await asyncio.sleep(period_seconds)
        now_h = datetime.now().hour
        if WINDOW_START_HOUR <= now_h < WINDOW_END_HOUR:
            await run_once()
        else:
            logging.info(
                "Пропуск обновления стоп-листа (вне окна %02d:00-%02d:00)",
                WINDOW_START_HOUR,
                WINDOW_END_HOUR,
            )


def main() -> None:
    asyncio.run(run_periodic_low_stock())


if __name__ == "__main__":
    main()