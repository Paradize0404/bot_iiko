from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)

STORE_FIELD_CANDIDATES = (
    "Account.Name",
    "Store",
    "Склад",
)
SUPPLIER_FIELD_CANDIDATES = (
    "Counteragent.Name",
    "Contractor.Name",
    "Agent.Name",
    "Supplier",
)
ACCOUNT_TYPE_FIELD_CANDIDATES = (
    "Account.Type",
    "AccountType",
    "Тип счета",
)
SUM_FIELD_CANDIDATES = (
    "Sum.Incoming",
    "Sum",
    "SumIn",
)
TRANSACTION_FIELD_CANDIDATES = (
    "TransactionType",
    "DocumentType",
)
DEFAULT_TRANSACTION_TYPES = ("INCOMING_INVOICE",)
DEFAULT_TRANSACTION_CODES = ("INVOICE",)
DELETION_FIELD_CANDIDATES = (
    "DocumentStatus",
    "DocumentState",
    "DocumentDeleted",
    "Document.IsDeleted",
    "DocumentRemoved",
    "IsDeleted",
    "Deleted",
    "DeletedWithWriteoff",
)
NOT_DELETED_MARKERS = {
    "NOT_DELETED",
    "NOT DELETED",
    "NOTDELETED",
    "НЕ УДАЛЕН",
    "НЕ УДАЛЁН",
    "НЕ УДАЛЕНО",
    "FALSE",
    "0",
    "NO",
    "ACTIVE",
    "CONFIRMED",
    "APPROVED",
}
NEGATIVE_STATUS_KEYWORDS = {
    "DELETED",
    "REMOVED",
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "DECLINED",
    "VOID",
    "ON_APPROVAL",
    "ON-APPROVAL",
    "PENDING",
    "DRAFT",
    "UNCONFIRMED",
    "NOT_CONFIRMED",
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
            return text.strip()


def parse_xml_report(xml_payload: str) -> list[dict[str, Any]]:
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
    raise RuntimeError(
        f"Неизвестный формат ответа: {content_type}\n{response.text[:400]}",
    )


def to_report_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")


async def fetch_store_names(store_type: str = "STORE", timeout: float = 60.0) -> set[str]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token, "revisionFrom": -1}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/corporation/stores", params=params)
        response.raise_for_status()
    root = ET.fromstring(response.text)
    names: set[str] = set()
    for item in root.findall("corporateItemDto"):
        name = (item.findtext("name") or "").strip()
        type_value = (item.findtext("type") or "").strip().upper()
        if not name:
            continue
        if store_type and type_value != store_type.upper():
            continue
        names.add(name)
    return names


async def fetch_transactions_rows(
    date_from: str,
    date_to: str,
    transaction_types: Sequence[str],
    timeout: float,
) -> list[dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()
    human_from = to_report_date(date_from)
    human_to = to_report_date(date_to)
    params: list[tuple[str, str]] = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", human_from),
        ("to", human_to),
        ("groupRow", "Account.Name"),
        ("groupRow", "TransactionType"),
        ("groupRow", "Account.Type"),
        ("agr", "Sum.Incoming"),
    ]
    for trx in transaction_types:
        params.append(("TransactionType", trx))
    logger.info(
        "Запрашиваем TRANSACTIONS: %s → %s | типы %s",
        date_from,
        date_to,
        ", ".join(transaction_types),
    )
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/reports/olap", params=params)
        response.raise_for_status()
    rows = parse_response(response)
    logger.info("Получено %d строк", len(rows))
    return rows


def _is_deleted_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return bool(value)
    text = str(value).strip()
    if not text:
        return False
    upper = text.upper()
    if upper in NOT_DELETED_MARKERS:
        return False
    if upper in {"TRUE", "YES", "1"}:
        return True
    if any(keyword in upper for keyword in NEGATIVE_STATUS_KEYWORDS):
        if "NOT_DELETED" in upper:
            return False
        return True
    return False


def filter_deleted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    keys = {key for row in rows for key in row.keys()}
    deletion_fields = [key for key in DELETION_FIELD_CANDIDATES if key in keys]
    if not deletion_fields:
        # fallback: auto-detect columns containing 'deleted' or 'removed'
        deletion_fields = [
            key
            for key in keys
            if "deleted" in key.lower() or "removed" in key.lower()
        ]
    if not deletion_fields:
        logger.info("Поля для удаления документов не найдены в ответе")
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if any(_is_deleted_value(row.get(field)) for field in deletion_fields):
            continue
        filtered.append(row)
    removed = len(rows) - len(filtered)
    if removed:
        logger.info(
            "Отфильтрованы удалённые документы: %d удалено по полям %s",
            removed,
            ", ".join(deletion_fields),
        )
    return filtered


def resolve_field(
    rows: Sequence[dict[str, Any]],
    candidates: Iterable[str],
    default: str | None = None,
) -> str | None:
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


@dataclass
class PurchaseSummary:
    store_totals: dict[str, Decimal]
    supplier_totals: dict[str, Decimal]
    pair_totals: dict[tuple[str, str], Decimal]
    total_amount: Decimal
    rows_count: int


def aggregate_totals(
    rows: Sequence[dict[str, Any]],
    store_field: str,
    supplier_field: str | None,
    sum_field: str,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[tuple[str, str], Decimal]]:
    store_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    supplier_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    pair_totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        amount = decimal_from_value(row.get(sum_field))
        if not amount:
            continue
        store_label = str(row.get(store_field) or "(Без склада)")
        supplier_label = (
            str(row.get(supplier_field) or "(Без контрагента)")
            if supplier_field
            else "(Без контрагента)"
        )
        store_totals[store_label] += amount
        if supplier_field:
            supplier_totals[supplier_label] += amount
        pair_totals[(store_label, supplier_label)] += amount
    return store_totals, supplier_totals, pair_totals


async def get_purchase_summary(
    date_from: str,
    date_to: str,
    *,
    transaction_types: Sequence[str] | None = None,
    transaction_codes: Sequence[str] | None = None,
    store_filter: Sequence[str] | None = None,
    account_type_filter: Sequence[str] | None = None,
    include_all_accounts: bool = False,
    timeout: float = 90.0,
) -> PurchaseSummary:
    transaction_types = [
        trx.strip()
        for trx in (transaction_types or DEFAULT_TRANSACTION_TYPES)
        if trx and trx.strip()
    ]
    transaction_codes = [
        code.strip()
        for code in (transaction_codes or DEFAULT_TRANSACTION_CODES)
        if code and code.strip()
    ]

    rows = await fetch_transactions_rows(
        date_from=date_from,
        date_to=date_to,
        transaction_types=transaction_types,
        timeout=timeout,
    )
    rows = filter_deleted_rows(rows)
    if not rows:
        return PurchaseSummary({}, {}, {}, Decimal("0"), 0)

    store_field = resolve_field(rows, STORE_FIELD_CANDIDATES, "Account.Name")
    if not store_field:
        raise RuntimeError("Не удалось определить поле склада")
    supplier_field = resolve_field(rows, SUPPLIER_FIELD_CANDIDATES)
    sum_field = resolve_field(rows, SUM_FIELD_CANDIDATES, "Sum.Incoming")
    if not sum_field:
        raise RuntimeError("Не найдено поле суммы прихода")
    transaction_field = resolve_field(rows, TRANSACTION_FIELD_CANDIDATES)
    account_type_field = resolve_field(rows, ACCOUNT_TYPE_FIELD_CANDIDATES)

    filtered_rows = rows
    if transaction_field and transaction_codes:
        allowed_codes = set(transaction_codes)
        before = len(filtered_rows)
        filtered_rows = [
            row
            for row in filtered_rows
            if str(row.get(transaction_field) or "").strip() in allowed_codes
        ]
        logger.info(
            "После фильтрации по TransactionType (%s) осталось %d из %d строк",
            ", ".join(sorted(allowed_codes)),
            len(filtered_rows),
            before,
        )
        if not filtered_rows:
            return PurchaseSummary({}, {}, {}, Decimal("0"), 0)

    store_whitelist: set[str] | None = None
    if not include_all_accounts:
        try:
            store_names = await fetch_store_names(timeout=timeout)
            store_whitelist = store_names
        except Exception as exc:
            logger.warning("Не удалось загрузить список складов: %s", exc)
            store_whitelist = None

    if store_filter:
        filter_set = {name.strip() for name in store_filter if name and name.strip()}
        if store_whitelist:
            store_whitelist = filter_set & store_whitelist
        else:
            store_whitelist = filter_set

    if store_whitelist is not None:
        before = len(filtered_rows)
        filtered_rows = [
            row for row in filtered_rows if str(row.get(store_field) or "").strip() in store_whitelist
        ]
        logger.info(
            "После фильтрации по складам осталось %d из %d строк",
            len(filtered_rows),
            before,
        )
        if not filtered_rows:
            return PurchaseSummary({}, {}, {}, Decimal("0"), 0)

    if account_type_filter and account_type_field:
        allowed_account_types = {value.strip() for value in account_type_filter if value and value.strip()}
        before = len(filtered_rows)
        filtered_rows = [
            row
            for row in filtered_rows
            if str(row.get(account_type_field) or "").strip() in allowed_account_types
        ]
        logger.info(
            "После фильтрации по типу счёта осталось %d из %d строк",
            len(filtered_rows),
            before,
        )
        if not filtered_rows:
            return PurchaseSummary({}, {}, {}, Decimal("0"), 0)
    elif account_type_filter and not account_type_field:
        logger.warning("Поле типа счёта отсутствует в ответе TRANSACTIONS")

    store_totals, supplier_totals, pair_totals = aggregate_totals(
        filtered_rows,
        store_field,
        supplier_field,
        sum_field,
    )
    total_amount = sum(store_totals.values(), Decimal("0"))
    return PurchaseSummary(store_totals, supplier_totals, pair_totals, total_amount, len(filtered_rows))
