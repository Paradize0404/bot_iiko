"""Отчёт остатков ниже минимума для складов Бар и Кухня.

Берём справочник номенклатуры из reports/products_list_dump.json (поле storeBalanceLevels),
получаем текущие остатки через fetch_store_balances(), сопоставляем по названию товара и складу
и показываем только позиции, где остаток < minBalanceLevel.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url
from scripts.export_store_balances import fetch_store_balances, _extract_store_name

PRODUCTS_DUMP_PATH = Path(__file__).resolve().parent.parent / "reports" / "products_list_dump.json"
TARGET_STORES = {"Бар Пиццерия", "Кухня Пиццерия"}


def _auto_cast(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip()


def _extract_first(row: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def load_products_dump() -> list[dict[str, Any]]:
    if not PRODUCTS_DUMP_PATH.exists():
        raise FileNotFoundError(f"Дамп номенклатуры не найден: {PRODUCTS_DUMP_PATH}")
    payload = json.loads(PRODUCTS_DUMP_PATH.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise RuntimeError("Некорректный формат products_list_dump.json: поле items не список")
    return items


async def async_fetch_store_map() -> Dict[str, str]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token, "revisionFrom": -1}
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        resp = await client.get("/resto/api/corporation/stores", params=params)
        resp.raise_for_status()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(resp.text)
    store_map: Dict[str, str] = {}
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


def build_min_levels(products: list[dict[str, Any]], store_map: Dict[str, str]) -> Dict[Tuple[str, str], Decimal]:
    """Ключ: (store_name, product_name) -> minBalanceLevel."""
    result: Dict[Tuple[str, str], Decimal] = {}
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
                min_dec = Decimal(str(min_level))
            except Exception:
                continue
            result[(store_name, name)] = min_dec
    return result


async def main() -> None:
    products = load_products_dump()
    store_map = await async_fetch_store_map()
    min_levels = build_min_levels(products, store_map)

    rows = await fetch_store_balances()

    below: Dict[str, list[tuple[str, Decimal, Decimal]]] = {store: [] for store in TARGET_STORES}
    total_matches = 0

    for row in rows:
        store_name = _extract_store_name(row)
        if store_name not in TARGET_STORES:
            continue
        key = (store_name, str(row.get("Product.Name") or ""))
        min_level = min_levels.get(key)
        if min_level is None:
            continue
        amount_val = _extract_first(row, ("FinalBalance.Amount", "FinalBalance"), 0)
        try:
            amount = Decimal(str(amount_val))
        except Exception:
            amount = Decimal(0)
        if amount < min_level:
            below[store_name].append((key[1], amount, min_level))
            total_matches += 1

    print(f"Всего позиций с min: {len(min_levels)}; ниже минимума: {total_matches}")
    for store in sorted(TARGET_STORES):
        items = below.get(store) or []
        if not items:
            print(f"\n{store}: все в норме (нет позиций ниже min)")
            continue
        print(f"\n{store}: ниже min ({len(items)} шт)")
        for name, qty, min_lvl in sorted(items, key=lambda x: x[0]):
            print(f"- {name}: {qty} < min {min_lvl}")


if __name__ == "__main__":
    asyncio.run(main())
