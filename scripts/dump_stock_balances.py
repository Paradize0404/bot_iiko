from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

BALANCE_GROUP_DIMENSIONS = (
    "Account.Name",        # склад / счёт
    "Product.TopParent",   # группа 1 уровня
    "Product.SecondParent",# группа 2 уровня
    "Product.Category",    # категория
    "Product.Name",        # конкретная позиция
    "Product.MeasureUnit", # единица измерения
)
BALANCE_METRICS = (
    "FinalBalance.Amount",  # остаток в количестве
    "FinalBalance.Money",   # остаток по себестоимости
)


def _auto_cast(value: str | None) -> Any:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    for caster in (int, float, Decimal):
        try:
            return caster(text)
        except Exception:
            continue
    return text


def parse_xml_report(xml: str) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    rows = []
    for elem in root.findall("./r"):
        row: dict[str, Any] = {}
        for child in elem:
            row[child.tag] = _auto_cast(child.text)
        rows.append(row)
    return rows


def parse_response(response: httpx.Response) -> list[dict[str, Any]]:
    content_type = (response.headers.get("content-type") or "").lower()
    if content_type.startswith("application/json"):
        payload = response.json()
        return payload.get("data") or payload.get("rows") or payload.get("report") or []
    if "xml" in content_type or content_type.startswith("text/xml"):
        return parse_xml_report(response.text)
    raise RuntimeError(f"Неизвестный формат ответа: {content_type}\n{response.text[:400]}")


def human_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")


def to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except Exception:
        return 0.0


async def fetch_stock_balances(date_from: str, date_to: str, timeout: float = 90.0) -> list[dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()
    params: list[tuple[str, str]] = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", date_from),
        ("to", date_to),
    ]
    for dim in BALANCE_GROUP_DIMENSIONS:
        params.append(("groupRow", dim))
    for metric in BALANCE_METRICS:
        params.append(("agr", metric))

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        logger.info("⏳ Запрашиваем остатки TRANSACTIONS: %s → %s", date_from, date_to)
        response = await client.get("/resto/api/reports/olap", params=params)
        response.raise_for_status()
        rows = parse_response(response)
        logger.info("✅ Получено %d строк", len(rows))
        return rows


def filter_rows(
    rows: list[dict[str, Any]],
    stores: list[str],
    min_amount: float | None,
    include_zero: bool,
    only_negative: bool,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    store_keys = ("Account.Name", "Store", "Счет", "Склад")
    result: list[dict[str, Any]] = []
    for row in rows:
        store_name = next((str(row.get(key)) for key in store_keys if row.get(key)), "")
        if stores and store_name not in stores:
            continue
        amount = to_float(row.get("FinalBalance.Amount"))
        if only_negative and amount >= 0:
            continue
        if not include_zero and abs(amount) < 1e-9:
            continue
        if min_amount is not None and amount < min_amount:
            continue
        result.append(row)
    return result


def print_preview(rows: list[dict[str, Any]], limit: int | None) -> None:
    if not rows:
        print("Нет строк после фильтрации.")
        return

    amount_key = "FinalBalance.Amount"
    money_key = "FinalBalance.Money"
    store_key = "Account.Name"
    product_key = "Product.Name"
    unit_key = "Product.MeasureUnit"
    top_key = "Product.TopParent"
    second_key = "Product.SecondParent"
    category_key = "Product.Category"

    subset = rows if limit is None else rows[:limit]
    print(f"Первые {len(subset)} строк:")
    for idx, row in enumerate(subset, start=1):
        store = row.get(store_key) or row.get("Store") or row.get("Счет")
        product = row.get(product_key) or row.get("Product")
        unit = row.get(unit_key) or row.get("Unit")
        top_group = row.get(top_key) or row.get("Product.Group")
        second_group = row.get(second_key)
        category = row.get(category_key)
        amount = to_float(row.get(amount_key))
        money = to_float(row.get(money_key))
        print(
            f"{idx:>3}. Склад: {store or '-'} | Группа1: {top_group or '-'} | Группа2: {second_group or '-'} "
            f"| Категория: {category or '-'} | Номенклатура: {product or '-'} ({unit or '-'}) "
            f"| Остаток: {amount:.3f} | Себестоимость: {money:,.2f}"
        )

    print("\nRAW JSON (ensure_ascii=False):")
    print(json.dumps(subset, ensure_ascii=False, indent=2, default=lambda o: float(o) if isinstance(o, Decimal) else str(o)))


def parse_args() -> argparse.Namespace:
    today = datetime.now().date()
    default_date = today.strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Сырые остатки по складам из OLAP TRANSACTIONS")
    parser.add_argument(
        "--from-date",
        dest="date_from",
        default=default_date,
        help="Дата начала периода (YYYY-MM-DD). По умолчанию сегодня.",
    )
    parser.add_argument(
        "--to-date",
        dest="date_to",
        default=default_date,
        help="Дата конца периода (YYYY-MM-DD). По умолчанию сегодня.",
    )
    parser.add_argument(
        "--store",
        action="append",
        default=[],
        help="Фильтр по названию склада (как в iiko Account.Name). Можно повторять.",
    )
    parser.add_argument(
        "--min-amount",
        type=float,
        default=None,
        help="Оставить только позиции, где остаток по количеству >= значению.",
    )
    parser.add_argument(
        "--include-zero",
        action="store_true",
        help="Не отбрасывать позиции с нулевым количеством (по умолчанию нули фильтруются).",
    )
    parser.add_argument(
        "--only-negative",
        action="store_true",
        help="Оставить только позиции с отрицательным остатком.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Сколько строк печатать в предпросмотре (по умолчанию 10).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="HTTP timeout в секундах для запроса OLAP.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    date_from = human_date(args.date_from)
    date_to = human_date(args.date_to)
    rows = await fetch_stock_balances(date_from, date_to, timeout=args.timeout)
    filtered = filter_rows(
        rows,
        args.store,
        args.min_amount,
        args.include_zero,
        args.only_negative,
    )
    logger.info("После фильтрации осталось %d строк", len(filtered))
    print_preview(filtered, args.limit)


if __name__ == "__main__":
    asyncio.run(main())
