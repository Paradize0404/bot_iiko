"""Create internal transfers for negative leftover items (utility script).

Steps:
1. Read balances via OLAP TRANSACTIONS (scripts.dump_stock_balances).
2. Keep only stores "Бар Пиццерия" and "Кухня Пиццерия".
3. Filter by Product.SecondParent == "Расходные материалы" and negative amount.
4. Create internal transfer from "Хоз. товары Пиццерия" to the store with deficit.

Run: python -m scripts.create_negative_transfer
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy import select

from db.nomenclature_db import Nomenclature, async_session
from handlers.common import STORE_CACHE, preload_stores
from iiko.iiko_auth import get_auth_token, get_base_url
from scripts.dump_stock_balances import fetch_stock_balances, to_float
from services.internal_transfer import send_internal_transfer
from services.nomenclature_scheduler import sync_nomenclature_and_balances

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("auto_transfer")

TARGET_STORES = ("Бар Пиццерия", "Кухня Пиццерия")
SOURCE_STORE_NAME = "Хоз. товары Пиццерия"
SECOND_GROUP_FILTER = "Расходные материалы"


def _normalize_name(value: str | None) -> str:
    return (value or "").strip()


async def collect_negative_rows(date_from: str, date_to: str) -> list[dict[str, Any]]:
    rows = await fetch_stock_balances(date_from, date_to)
    result: list[dict[str, Any]] = []
    for row in rows:
        store_name = _normalize_name(row.get("Account.Name"))
        if store_name not in TARGET_STORES:
            continue
        second_group = _normalize_name(row.get("Product.SecondParent"))
        if second_group != SECOND_GROUP_FILTER:
            continue
        amount = to_float(row.get("FinalBalance.Amount"))
        if amount >= 0:
            continue
        row["_product_name_clean"] = _normalize_name(row.get("Product.Name"))
        result.append(row)
    return result


async def load_products(rows: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    names = {row.get("_product_name_clean") or _normalize_name(row.get("Product.Name")) for row in rows}
    names.discard("")
    if not names:
        return {}
    async with async_session() as session:
        stmt = select(Nomenclature.name, Nomenclature.id, Nomenclature.mainunit).where(Nomenclature.name.in_(names))
        result = await session.execute(stmt)
        mapping: dict[str, tuple[str, str]] = {}
        for name, product_id, measure in result:
            mapping[_normalize_name(name)] = (product_id, measure)
        return mapping


async def build_transfers(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    products = await load_products(rows)
    missing = {
        row.get("Product.Name") or ""
        for row in rows
        if (_normalize_name(row.get("Product.Name")) not in products)
    }
    if missing:
        logger.warning("Не найдены в БД %d товаров: %s", len(missing), ", ".join(sorted(missing)))

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        product_name = _normalize_name(row.get("Product.Name"))
        if not product_name:
            continue
        product_meta = products.get(product_name)
        if not product_meta:
            continue
        store_name = _normalize_name(row.get("Account.Name"))
        amount = abs(to_float(row.get("FinalBalance.Amount")))
        if amount == 0:
            continue
        product_id, measure_unit = product_meta
        grouped[store_name].append(
            {
                "productId": product_id,
                "amount": round(amount, 6),
                "measureUnitId": measure_unit,
                "productName": product_name,
            }
        )
    return grouped


async def resolve_store_id(name: str) -> str | None:
    if name in STORE_CACHE:
        return STORE_CACHE[name]
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token, "revisionFrom": -1}
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, verify=False) as client:
        response = await client.get("/resto/api/corporation/stores", params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for item in root.findall("corporateItemDto"):
            store_name = (item.findtext("name") or "").strip()
            if store_name == name:
                store_id = item.findtext("id") or ""
                if store_id:
                    STORE_CACHE[store_name] = store_id
                    return store_id
    return None


async def run_negative_transfer(sync_before: bool = True) -> None:
    today = datetime.now().strftime("%d.%m.%Y")
    if sync_before:
        logger.info("🔄 Перед запуском обновляем номенклатуру")
        await sync_nomenclature_and_balances()
    await preload_stores()
    store_from_id = await resolve_store_id(SOURCE_STORE_NAME)
    if not store_from_id:
        raise RuntimeError(f"Не найден id склада-источника '{SOURCE_STORE_NAME}'")

    rows = await collect_negative_rows(today, today)
    if not rows:
        logger.info("Отрицательные остатки не найдены — нечего перемещать")
        return

    transfers = await build_transfers(rows)
    if not transfers:
        logger.info("После фильтрации не осталось товаров для перемещения")
        return

    for store_name, items in transfers.items():
        store_id = STORE_CACHE.get(store_name)
        if not store_id:
            store_id = await resolve_store_id(store_name)
        if not store_id:
            logger.warning("Нет id для склада %s — пропускаю", store_name)
            continue
        comment = f"Авто-перемещение расходных материалов ({store_name})"
        logger.info("Отправляем %d позиций: %s → %s", len(items), SOURCE_STORE_NAME, store_name)
        await send_internal_transfer(
            store_from_id=store_from_id,
            store_to_id=store_id,
            items=items,
            comment=comment,
        )
        for item in items:
            logger.info(" • %s — %.3f", item.get("productName"), item.get("amount"))
    logger.info("Готово")


async def main() -> None:
    await run_negative_transfer()


if __name__ == "__main__":
    asyncio.run(main())
