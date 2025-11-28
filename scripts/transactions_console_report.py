from __future__ import annotations

"""Console helper for OLAP TRANSACTIONS report filtered by account and product type.

The script reproduces the report requested in chat:
- aggregation: Sum.Incoming
- dimensions: TransactionType, Product.Type, Account.Name, Product.ThirdParent
- filters: Account.Name in ("ТМЦ Пиццерия", "Хоз. товары Пиццерия")
           Product.Type == "Товар (GOODS)"

Example:
    python scripts/transactions_console_report.py --from-date 2025-11-01 --to-date 2025-11-27
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

GROUP_DIMENSIONS: tuple[str, ...] = (
    "TransactionType",
    "Product.Type",
    "Account.Name",
    "Product.ThirdParent",
)
SUM_FIELD_CANDIDATES: tuple[str, ...] = (
    "Sum.Incoming",
    "IncomingSum",
    "SumIn",
    "Sum",
)

DEFAULT_ACCOUNT_FILTERS: tuple[str, ...] = (
    "ТМЦ Пиццерия",
    "Хоз. товары Пиццерия",
)
DEFAULT_PRODUCT_TYPE_FILTERS: tuple[str, ...] = (
    "Товар",
    "Товар (GOODS)",
    "GOODS",
)

@dataclass
class RowRecord:
    transaction_type: str
    product_type: str
    account_name: str
    third_parent: str
    amount: Decimal


@dataclass
class AccountGroupRow:
    account_name: str
    group_label: str
    amount: Decimal


ACCOUNT_CUSTOMIZATION: dict[str, dict[str, Any]] = {
    "ТМЦ Пиццерия": {
        "preferred_groups": ["ТМЦ", "Форма для персонала", "Барное стекло"],
        "aliases": {
            "": "ТМЦ",
            "(3-й уровень не задан)": "ТМЦ",
            "тмц бар зал кухня": "ТМЦ",
        },
    },
    "Хоз. товары Пиццерия": {
        "preferred_groups": [
            "Упаковка",
            "Уборочный инвентарь/Химия",
            "Р/М бар зал кухня",
            "Прочее",
        ],
        "group_strategy": "hoz",
    },
}


def _auto_cast(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip() if text else None


def parse_xml_report(xml_payload: str) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_payload)
    rows: list[dict[str, Any]] = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


def parse_response(response: httpx.Response) -> list[dict[str, Any]]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = response.json()
        return payload.get("data") or payload.get("rows") or []
    if content_type.startswith("application/xml") or content_type.startswith("text/xml"):
        return parse_xml_report(response.text)
    raise RuntimeError(f"Неизвестный формат ответа: {content_type}\n{response.text[:400]}")


def to_human_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")


def resolve_field(rows: Sequence[dict[str, Any]], candidates: Iterable[str], default: str) -> str:
    if not rows:
        return default
    keys = {key for row in rows for key in row.keys()}
    for candidate in candidates:
        if candidate in keys:
            return candidate
    return default


def decimal_from_value(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def money_str(value: Decimal) -> str:
    return f"{value:,.2f}"


def uniq(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _normalized(text: str | None) -> str:
    if text is None:
        return ""
    return text.strip().lower()


def filter_rows(
    rows: list[dict[str, Any]],
    accounts: Sequence[str] | None,
    product_types: Sequence[str] | None,
) -> list[dict[str, Any]]:
    if not rows:
        return rows

    allowed_accounts = {_normalized(value) for value in accounts or [] if value and value.strip()}
    allowed_product_types = {
        _normalized(value) for value in product_types or [] if value and value.strip()
    }
    if not allowed_accounts and not allowed_product_types:
        return rows

    account_field = resolve_field(rows, ("Account.Name", "Account", "Store"), "Account.Name")
    product_field = resolve_field(rows, ("Product.Type", "ProductType"), "Product.Type")

    filtered: list[dict[str, Any]] = []
    for row in rows:
        account_label = _normalized(str(row.get(account_field) or ""))
        if allowed_accounts and account_label not in allowed_accounts:
            continue
        product_label = _normalized(str(row.get(product_field) or ""))
        if allowed_product_types and product_label not in allowed_product_types:
            continue
        filtered.append(row)
    return filtered


def _get_account_config(account_name: str) -> dict[str, Any] | None:
    return ACCOUNT_CUSTOMIZATION.get(account_name)


def _get_alias_map(account_name: str) -> dict[str, str]:
    config = _get_account_config(account_name)
    if not config:
        return {}
    cache_key = "_alias_map"
    if cache_key not in config:
        aliases = config.get("aliases", {}) or {}
        config[cache_key] = { _normalized(key): value for key, value in aliases.items() }
    return config[cache_key]


def _resolve_group_label(account_name: str, raw_label: str) -> str:
    base_label = raw_label.strip() if raw_label and raw_label.strip() else "(3-й уровень не задан)"
    alias_map = _get_alias_map(account_name)
    if alias_map:
        alias = alias_map.get(_normalized(raw_label))
        if alias:
            return alias
    return base_label


def build_params(
    token: str,
    from_date: str,
    to_date: str,
    account_filters: Sequence[str],
    product_type_filters: Sequence[str],
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", from_date),
        ("to", to_date),
    ]
    for dim in GROUP_DIMENSIONS:
        params.append(("groupRow", dim))
    params.append(("agr", "Sum.Incoming"))
    for account in account_filters:
        params.append(("Account.Name", account))
    for product_type in product_type_filters:
        params.append(("Product.Type", product_type))
    return params


def normalize_record(rows: list[dict[str, Any]]) -> list[RowRecord]:
    if not rows:
        return []
    trx_field = resolve_field(rows, ("TransactionType", "DocumentType"), "TransactionType")
    prod_field = resolve_field(rows, ("Product.Type", "ProductType"), "Product.Type")
    account_field = resolve_field(rows, ("Account.Name", "Account", "Store"), "Account.Name")
    parent_field = resolve_field(rows, ("Product.ThirdParent", "ProductGroup"), "Product.ThirdParent")
    sum_field = resolve_field(rows, SUM_FIELD_CANDIDATES, "Sum.Incoming")

    records: list[RowRecord] = []
    for row in rows:
        record = RowRecord(
            transaction_type=str(row.get(trx_field) or "(нет типа)"),
            product_type=str(row.get(prod_field) or "(нет типа продукта)"),
            account_name=str(row.get(account_field) or "(нет счета)"),
            third_parent=str(row.get(parent_field) or "(3-й уровень не задан)"),
            amount=decimal_from_value(row.get(sum_field)),
        )
        records.append(record)
    return records


def aggregate_groups(records: Sequence[RowRecord]) -> list[AccountGroupRow]:
    from collections import defaultdict

    if not records:
        return []
    totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for record in records:
        label = _resolve_group_label(record.account_name, record.third_parent)
        totals[(record.account_name, label)] += record.amount
    grouped_rows: list[AccountGroupRow] = []
    for (account_name, label), amount in totals.items():
        grouped_rows.append(AccountGroupRow(account_name=account_name, group_label=label, amount=amount))
    return grouped_rows


def _build_hoz_groups(account_name: str, rows: Sequence[AccountGroupRow]) -> list[AccountGroupRow]:
    from collections import defaultdict

    if not rows:
        return []
    buckets = {
        "упаковка": "Упаковка",
        "уборочный инвентарь/химия": "Уборочный инвентарь/Химия",
        "р/м бар зал кухня": "Р/М бар зал кухня",
    }
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_amount = Decimal("0")
    for row in rows:
        normalized = _normalized(row.group_label)
        totals[normalized] += row.amount
        total_amount += row.amount
    result: list[AccountGroupRow] = []
    allocated = Decimal("0")
    for key, display in buckets.items():
        amount = totals.get(key, Decimal("0"))
        allocated += amount
        result.append(AccountGroupRow(account_name=account_name, group_label=display, amount=amount))
    remainder = total_amount - allocated
    if remainder < Decimal("0"):
        remainder = Decimal("0")
    result.append(AccountGroupRow(account_name=account_name, group_label="Прочее", amount=remainder))
    return result


def apply_account_group_overrides(grouped_rows: Sequence[AccountGroupRow]) -> list[AccountGroupRow]:
    from collections import defaultdict

    rows_by_account: dict[str, list[AccountGroupRow]] = defaultdict(list)
    for row in grouped_rows:
        rows_by_account[row.account_name].append(row)

    adjusted: list[AccountGroupRow] = []
    for account_name, rows in rows_by_account.items():
        config = _get_account_config(account_name) or {}
        strategy = config.get("group_strategy")
        if strategy == "hoz":
            adjusted.extend(_build_hoz_groups(account_name, rows))
        else:
            adjusted.extend(rows)
    return adjusted


def render_grouped_report(
    grouped_rows: Sequence[AccountGroupRow],
    account_priority: Sequence[str] | None,
    limit: int | None,
) -> None:
    if not grouped_rows:
        print("Нет данных по заданным фильтрам.")
        return

    from collections import defaultdict

    account_order: dict[str, int] = {}
    if account_priority:
        account_order = {name: idx for idx, name in enumerate(account_priority)}

    rows_by_account: dict[str, list[AccountGroupRow]] = defaultdict(list)
    account_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in grouped_rows:
        rows_by_account[row.account_name].append(row)
        account_totals[row.account_name] += row.amount

    def account_sort_key(account_name: str) -> tuple[int, str]:
        return (account_order.get(account_name, len(account_order)), account_name.lower())

    overall_total = sum((row.amount for row in grouped_rows), Decimal("0"))
    printed_rows = 0
    max_rows = limit if limit is not None and limit > 0 else None

    for account_name in sorted(rows_by_account.keys(), key=account_sort_key):
        if max_rows is not None and printed_rows >= max_rows:
            break
        account_rows = rows_by_account[account_name]
        config = _get_account_config(account_name)
        preferred = config.get("preferred_groups", []) if config else []
        order_map = {label: idx for idx, label in enumerate(preferred)}
        account_rows.sort(key=lambda row: (order_map.get(row.group_label, len(order_map)), row.group_label.lower()))

        print("\n" + "=" * 80)
        print(f"Счёт: {account_name}")
        print("=" * 80)
        for row in account_rows:
            if max_rows is not None and printed_rows >= max_rows:
                break
            print(f"{row.group_label[:40]:<40} {money_str(row.amount):>15} ₽")
            printed_rows += 1
        print("-" * 60)
        print(f"Итого по счёту: {money_str(account_totals[account_name])} ₽")

    print("\n" + "=" * 60)
    print(f"ОБЩАЯ СУММА ПРИХОДА: {money_str(overall_total)} ₽")
    print("=" * 60)


def format_default_start() -> str:
    today = date.today()
    default_start = today - timedelta(days=7)
    return default_start.strftime("%Y-%m-%d")


def format_default_end() -> str:
    return date.today().strftime("%Y-%m-%d")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Выводит TRANSACTIONS отчёт (Sum.Incoming) по типу транзакции/товара/счету/3-му уровню",
    )
    parser.add_argument("--from-date", dest="date_from", default=format_default_start(), help="Начало периода (YYYY-MM-DD)")
    parser.add_argument("--to-date", dest="date_to", default=format_default_end(), help="Конец периода (YYYY-MM-DD)")
    parser.add_argument(
        "--account",
        dest="accounts",
        action="append",
        default=[],
        help="Название счёта/склада для фильтра (можно несколько)",
    )
    parser.add_argument(
        "--product-type",
        dest="product_types",
        action="append",
        default=[],
        help="Фильтр по типу элемента номенклатуры (можно несколько)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Ограничить количество строк в таблице")
    parser.add_argument("--timeout", type=float, default=90.0, help="HTTP timeout в секундах")
    parser.add_argument("--raw", action="store_true", help="Вывести сырые строки OLAP в JSON")
    parser.add_argument("--raw-limit", type=int, default=20, help="Сколько строк печатать в RAW режиме")
    return parser.parse_args()


async def fetch_report(
    date_from: str,
    date_to: str,
    accounts: Sequence[str],
    product_types: Sequence[str],
    timeout: float,
) -> list[dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()
    human_from = to_human_date(date_from)
    human_to = to_human_date(date_to)
    params = build_params(token, human_from, human_to, accounts, product_types)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/reports/olap", params=params)
    response.raise_for_status()
    rows = parse_response(response)
    logger.info("Получено %d строк", len(rows))
    return rows


async def run_report(args: argparse.Namespace) -> None:
    accounts = uniq(args.accounts or []) or list(DEFAULT_ACCOUNT_FILTERS)
    product_types = uniq(args.product_types or []) or list(DEFAULT_PRODUCT_TYPE_FILTERS)
    rows = await fetch_report(args.date_from, args.date_to, accounts, product_types, args.timeout)
    rows = filter_rows(rows, accounts, product_types)

    if args.raw:
        subset = rows[: max(1, min(args.raw_limit, len(rows)))] if rows else []
        print(json.dumps(subset, ensure_ascii=False, indent=2, default=str))
        if rows and len(subset) < len(rows):
            print(f"… всего строк: {len(rows)}")

    records = normalize_record(rows)
    grouped_rows = aggregate_groups(records)
    grouped_rows = apply_account_group_overrides(grouped_rows)
    render_grouped_report(grouped_rows, accounts, args.limit)


def main() -> None:
    args = parse_args()
    asyncio.run(run_report(args))


if __name__ == "__main__":
    main()
