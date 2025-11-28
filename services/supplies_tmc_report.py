from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)

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
class AccountGroupRow:
    account_name: str
    group_label: str
    amount: Decimal


@dataclass
class AccountRows:
    account_name: str
    rows: list[AccountGroupRow]
    total: Decimal


@dataclass
class SuppliesTmcReport:
    rows: list[AccountGroupRow]
    total_amount: Decimal


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


async def fetch_filtered_transactions(
    date_from: str,
    date_to: str,
    *,
    accounts: Sequence[str] | None = None,
    product_types: Sequence[str] | None = None,
    timeout: float = 90.0,
) -> list[dict[str, Any]]:
    account_filters = _clean_list(accounts, DEFAULT_ACCOUNT_FILTERS)
    product_filters = _clean_list(product_types, DEFAULT_PRODUCT_TYPE_FILTERS)
    raw_rows = await _fetch_transactions(date_from, date_to, account_filters, product_filters, timeout)
    return filter_rows(raw_rows, account_filters, product_filters)


async def get_supplies_tmc_report(
    date_from: str,
    date_to: str,
    *,
    accounts: Sequence[str] | None = None,
    product_types: Sequence[str] | None = None,
    timeout: float = 90.0,
) -> SuppliesTmcReport:
    filtered_rows = await fetch_filtered_transactions(
        date_from,
        date_to,
        accounts=accounts,
        product_types=product_types,
        timeout=timeout,
    )
    grouped_rows = build_group_rows(filtered_rows)
    total = sum((row.amount for row in grouped_rows), Decimal("0"))
    return SuppliesTmcReport(rows=grouped_rows, total_amount=total)


def build_group_rows(rows: list[dict[str, Any]]) -> list[AccountGroupRow]:
    records = normalize_record(rows)
    grouped = aggregate_groups(records)
    return apply_account_group_overrides(grouped)


def split_rows_by_account(
    rows: Sequence[AccountGroupRow],
    account_priority: Sequence[str] | None = None,
) -> list[AccountRows]:
    from collections import defaultdict

    if not rows:
        return []

    bucket: dict[str, list[AccountGroupRow]] = defaultdict(list)
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        bucket[row.account_name].append(row)
        totals[row.account_name] += row.amount

    priority_map = {name: idx for idx, name in enumerate(account_priority or [])}

    def _account_sort_key(name: str) -> tuple[int, str]:
        return (priority_map.get(name, len(priority_map)), name.lower())

    ordered_accounts = sorted(bucket.keys(), key=_account_sort_key)
    result: list[AccountRows] = []
    for account_name in ordered_accounts:
        config = _get_account_config(account_name) or {}
        preferred = config.get("preferred_groups") or []
        order_map = {label: idx for idx, label in enumerate(preferred)}
        account_rows = sorted(
            bucket[account_name],
            key=lambda row: (order_map.get(row.group_label, len(order_map)), row.group_label.lower()),
        )
        result.append(AccountRows(account_name=account_name, rows=account_rows, total=totals[account_name]))
    return result


async def _fetch_transactions(
    date_from: str,
    date_to: str,
    account_filters: Sequence[str],
    product_filters: Sequence[str],
    timeout: float,
) -> list[dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = build_params(token, _to_human_date(date_from), _to_human_date(date_to), account_filters, product_filters)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/reports/olap", params=params)
    response.raise_for_status()
    rows = parse_response(response)
    logger.info("Получено %d строк TRANSACTIONS для supplies/tmc", len(rows))
    return rows


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


def filter_rows(
    rows: list[dict[str, Any]],
    accounts: Sequence[str],
    product_types: Sequence[str],
) -> list[dict[str, Any]]:
    if not rows:
        return rows
    allowed_accounts = {_normalized(value) for value in accounts if value and value.strip()}
    allowed_product_types = {_normalized(value) for value in product_types if value and value.strip()}
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


def normalize_record(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    trx_field = resolve_field(rows, ("TransactionType", "DocumentType"), "TransactionType")
    prod_field = resolve_field(rows, ("Product.Type", "ProductType"), "Product.Type")
    account_field = resolve_field(rows, ("Account.Name", "Account", "Store"), "Account.Name")
    parent_field = resolve_field(rows, ("Product.ThirdParent", "ProductGroup"), "Product.ThirdParent")
    sum_field = resolve_field(rows, SUM_FIELD_CANDIDATES, "Sum.Incoming")

    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                "transaction_type": str(row.get(trx_field) or "(нет типа)"),
                "product_type": str(row.get(prod_field) or "(нет типа продукта)"),
                "account_name": str(row.get(account_field) or "(нет счета)"),
                "third_parent": str(row.get(parent_field) or "(3-й уровень не задан)"),
                "amount": decimal_from_value(row.get(sum_field)),
            }
        )
    return normalized


def aggregate_groups(records: Sequence[dict[str, Any]]) -> list[AccountGroupRow]:
    from collections import defaultdict

    if not records:
        return []

    grouped: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for record in records:
        label = _resolve_group_label(record["account_name"], record["third_parent"])
        grouped[(record["account_name"], label)] += Decimal(record["amount"])

    return [
        AccountGroupRow(account_name=account, group_label=label, amount=amount)
        for (account, label), amount in grouped.items()
    ]


def apply_account_group_overrides(rows: Sequence[AccountGroupRow]) -> list[AccountGroupRow]:
    from collections import defaultdict

    bucket: dict[str, list[AccountGroupRow]] = defaultdict(list)
    for row in rows:
        bucket[row.account_name].append(row)

    adjusted: list[AccountGroupRow] = []
    for account_name, account_rows in bucket.items():
        config = _get_account_config(account_name) or {}
        if config.get("group_strategy") == "hoz":
            adjusted.extend(_build_hoz_groups(account_name, account_rows))
        else:
            adjusted.extend(account_rows)
    return adjusted


def _build_hoz_groups(account_name: str, rows: Sequence[AccountGroupRow]) -> list[AccountGroupRow]:
    from collections import defaultdict

    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_amount = Decimal("0")
    for row in rows:
        key = _normalized(row.group_label)
        totals[key] += row.amount
        total_amount += row.amount

    mapping = {
        "упаковка": "Упаковка",
        "уборочный инвентарь/химия": "Уборочный инвентарь/Химия",
        "р/м бар зал кухня": "Р/М бар зал кухня",
    }

    result: list[AccountGroupRow] = []
    allocated = Decimal("0")
    for key, label in mapping.items():
        amount = totals.get(key, Decimal("0"))
        allocated += amount
        result.append(AccountGroupRow(account_name=account_name, group_label=label, amount=amount))

    remainder = total_amount - allocated
    if remainder < Decimal("0"):
        remainder = Decimal("0")
    result.append(AccountGroupRow(account_name=account_name, group_label="Прочее", amount=remainder))
    return result


def parse_response(response: httpx.Response) -> list[dict[str, Any]]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = response.json()
        return payload.get("data") or payload.get("rows") or []
    if content_type.startswith("application/xml") or content_type.startswith("text/xml"):
        return parse_xml_report(response.text)
    raise RuntimeError(f"Неизвестный формат ответа: {content_type}\n{response.text[:400]}")


def parse_xml_report(xml_payload: str) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_payload)
    rows: list[dict[str, Any]] = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


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


def _clean_list(values: Sequence[str] | None, fallback: Sequence[str]) -> list[str]:
    cleaned = [value.strip() for value in values or [] if value and value.strip()]
    return cleaned or list(fallback)


def _to_human_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")


def _normalized(text: str | None) -> str:
    if text is None:
        return ""
    return text.strip().lower()


def _get_account_config(account_name: str) -> dict[str, Any] | None:
    return ACCOUNT_CUSTOMIZATION.get(account_name)


def _get_alias_map(account_name: str) -> dict[str, str]:
    config = _get_account_config(account_name)
    if not config:
        return {}
    cache_key = "_alias_map"
    if cache_key not in config:
        aliases = config.get("aliases", {}) or {}
        config[cache_key] = {_normalized(key): value for key, value in aliases.items()}
    return config[cache_key]


def _resolve_group_label(account_name: str, raw_label: str) -> str:
    alias_map = _get_alias_map(account_name)
    alias = alias_map.get(_normalized(raw_label)) if alias_map else None
    if alias:
        return alias
    stripped = raw_label.strip() if raw_label and raw_label.strip() else "(3-й уровень не задан)"
    return stripped


__all__ = [
    "AccountGroupRow",
    "AccountRows",
    "SuppliesTmcReport",
    "ACCOUNT_CUSTOMIZATION",
    "DEFAULT_ACCOUNT_FILTERS",
    "DEFAULT_PRODUCT_TYPE_FILTERS",
    "fetch_filtered_transactions",
    "get_supplies_tmc_report",
    "build_group_rows",
    "split_rows_by_account",
]
