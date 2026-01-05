"""Экспорт остатков по всем складам через OLAP /resto/api/reports/olap.

Группировка: Account.Name (склад/счет) + Product.Name + Product.MeasureUnit,
агрегации: FinalBalance.Amount (кол-во) и FinalBalance.Money (сумма).
Выводим только в консоль с группировкой по складу и тоталом по складу.
"""
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url
from services.purchase_summary import fetch_store_names


STORE_FIELD_CANDIDATES = ("Account.Name", "Store", "Склад")
UNIT_FIELD_CANDIDATES = (
    "Product.MeasureUnit",
    "Product.MainUnit",
    "Product.MeasureName",
    "Product.Unit",
)
AMOUNT_FIELD_CANDIDATES = ("FinalBalance.Amount", "FinalBalance")
MONEY_FIELD_CANDIDATES = ("FinalBalance.Money",)


def _auto_cast(text: str | None) -> Any:
    if text is None:
        return None
    txt = text.strip()
    if txt == "":
        return None
    try:
        return int(txt)
    except Exception:
        try:
            return float(txt.replace(",", "."))
        except Exception:
            return txt


def parse_xml_report(xml: str) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    return [{child.tag: _auto_cast(child.text) for child in row} for row in root.findall("./r")]


def _extract_store_name(row: dict[str, Any]) -> str:
    for key in STORE_FIELD_CANDIDATES:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _extract_first(row: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


async def fetch_store_balances(date_from: str = "01.01.2020", date_to: str | None = None) -> list[dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()

    if not date_to:
        date_to = datetime.now().strftime("%d.%m.%Y")

    params = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", date_from),
        ("to", date_to),
        ("groupRow", "Account.Name"),
        ("groupRow", "Product.Name"),
        ("groupRow", "Product.MeasureUnit"),
        ("agr", "FinalBalance.Amount"),
        ("agr", "FinalBalance.Money"),
        # фильтр: брать только склады/счета типа "Склад"
        ("Account.StoreOrAccount", "Store"),
    ]

    async with httpx.AsyncClient(base_url=base_url, timeout=180, verify=False) as client:
        resp = await client.get("/resto/api/reports/olap", params=params)
        resp.raise_for_status()

    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = resp.json()
        rows = data.get("data") or data.get("rows") or []
    else:
        rows = parse_xml_report(resp.text)

    return rows


async def main() -> None:
    rows = await fetch_store_balances()

    # Получаем живой список складов из /resto/api/corporation/stores, чтобы не держать ручной список.
    try:
        store_whitelist = await fetch_store_names(store_type="STORE")
        print(f"Загружен список складов из iiko: {len(store_whitelist)} шт.")
    except Exception as exc:
        store_whitelist = None
        print(f"⚠️ Не удалось загрузить список складов, фильтр по складам выключен: {exc}")

    grouped: dict[str, list[dict[str, Any]]] = {}
    store_totals: dict[str, Decimal] = {}

    for row in rows:
        store_name = _extract_store_name(row)
        if store_whitelist and store_name not in store_whitelist:
            continue

        amount_val = _extract_first(row, AMOUNT_FIELD_CANDIDATES, 0)
        money_val = _extract_first(row, MONEY_FIELD_CANDIDATES, 0)
        try:
            amount = Decimal(str(amount_val))
        except Exception:
            amount = Decimal(0)
        try:
            money = Decimal(str(money_val))
        except Exception:
            money = Decimal(0)
        if amount == 0:
            continue

        unit = _extract_first(row, UNIT_FIELD_CANDIDATES, "")
        product = row.get("Product.Name") or "(без названия)"

        grouped.setdefault(store_name, []).append({
            "product": product,
            "amount": amount,
            "unit": unit,
            "money": money,
        })
        store_totals[store_name] = store_totals.get(store_name, Decimal(0)) + money

    # Вывод в консоль: по складам
    print(f"Дата выборки (по to): {datetime.now().strftime('%d.%m.%Y')}")
    print(f"Всего складов: {len(grouped)}")

    overall_total = Decimal(0)
    for store_name in sorted(grouped.keys()):
        items = grouped[store_name]
        store_total = store_totals.get(store_name, Decimal(0))
        overall_total += store_total
        print("\n" + "=" * 80)
        print(f"Склад: {store_name} | строк: {len(items)} | Сумма остатков: {store_total:.2f}")
        print("=" * 80)
        for item in sorted(items, key=lambda x: x["product"]):
            print(f"- {item['product']} | {item['amount']} {item['unit']} | {item['money']:.2f}")

    print("\nИТОГО по всем складам (сумма остатков): {:.2f}".format(overall_total))


if __name__ == "__main__":
    asyncio.run(main())
