from __future__ import annotations

import argparse
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence
import xml.etree.ElementTree as ET

import httpx
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


@dataclass
class InvoiceSummary:
    number: str | None
    date: str | None
    supplier_name: str | None
    supplier_id: str | None
    total: Decimal


def decimal_from_text(text: str | None) -> Decimal:
    if not text:
        return Decimal("0")
    cleaned = text.replace("\xa0", " ").strip().replace(" ", "")
    if not cleaned:
        return Decimal("0")
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except Exception:
        logger.debug("Не удалось преобразовать '%s' в Decimal", text)
        return Decimal("0")


async def fetch_incoming_invoices(
    date_from: str,
    date_to: str,
    supplier_id: str | None = None,
    revision_from: int = -1,
    timeout: float = 120.0,
) -> str:
    token = await get_auth_token()
    base_url = get_base_url()
    params: dict[str, str | int] = {
        "key": token,
        "from": date_from,
        "to": date_to,
        "revisionFrom": revision_from,
    }
    if supplier_id:
        params["supplierId"] = supplier_id

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        logger.info(
            "Запрашиваем incomingInvoice: %s → %s%s",
            date_from,
            date_to,
            f" | поставщик {supplier_id}" if supplier_id else "",
        )
        response = await client.get("/resto/api/documents/export/incomingInvoice", params=params)
        response.raise_for_status()
        logger.info("Ответ %d байт", len(response.content))
        return response.text


async def fetch_supplier_name_map(timeout: float = 120.0) -> dict[str, str]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/v2/suppliers", params=params)
        response.raise_for_status()
    root = ET.fromstring(response.text)
    mapping: dict[str, str] = {}
    for employee in root.findall(".//employee"):
        is_supplier = (employee.findtext("supplier") or "").strip().lower()
        if is_supplier not in {"true", "1", "yes"}:
            continue
        supplier_id = (employee.findtext("id") or "").strip()
        name = (
            employee.findtext("name")
            or employee.findtext("printableName")
            or employee.findtext("fullName")
            or ""
        ).strip()
        if supplier_id and name:
            mapping[supplier_id] = name
    return mapping


async def fetch_store_map(timeout: float = 120.0) -> dict[str, str]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token, "revisionFrom": -1}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/corporation/stores", params=params)
        response.raise_for_status()
    root = ET.fromstring(response.text)
    mapping: dict[str, str] = {}
    for item in root.findall("corporateItemDto"):
        store_id = (item.findtext("id") or "").strip()
        name = (item.findtext("name") or "").strip()
        if not store_id or not name:
            continue
        mapping[store_id] = name
    return mapping


def iter_invoice_nodes(root: ET.Element) -> Iterable[ET.Element]:
    possible_tags = {"document", "incomingInvoiceDto"}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.tag in possible_tags:
            yield node
        stack.extend(list(node))


def _first_text(node: ET.Element, paths: Sequence[str]) -> str | None:
    for path in paths:
        value = node.findtext(path)
        if value and value.strip():
            return value.strip()
    return None


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return normalized in {"true", "1", "yes", "y", "да"}


def is_deleted(node: ET.Element) -> bool:
    candidates = (
        node.get("deleted"),
        node.get("isDeleted"),
        node.findtext("deleted"),
        node.findtext("isDeleted"),
    )
    for candidate in candidates:
        if _is_truthy(candidate):
            return True
    status = _first_text(node, ("status", "state", "documentState"))
    if status and status.strip().upper() in {"DELETED", "REMOVED"}:
        return True
    return False


def summarize_invoice(node: ET.Element) -> InvoiceSummary:
    number = _first_text(node, ("documentNumber", "number", "invoiceNumber", "id"))
    date = _first_text(node, ("dateIncoming", "invoiceDate", "date", "createdTime"))
    supplier_name = _first_text(
        node,
        (
            "supplierName",
            "supplier/name",
            "supplier/printableName",
            "supplierShortName",
        ),
    )
    supplier_id = _first_text(
        node,
        (
            "supplierId",
            "supplier/id",
            "supplier/guid",
            "supplier",
        ),
    )
    total = Decimal("0")
    for items in node.findall(".//items"):
        for item in items.findall("item"):
            total += decimal_from_text(item.findtext("sum"))
    return InvoiceSummary(
        number=number,
        date=date,
        supplier_name=supplier_name,
        supplier_id=supplier_id,
        total=total,
    )


def collect_store_totals(root: ET.Element, *, include_deleted: bool) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for doc in iter_invoice_nodes(root):
        if not include_deleted and is_deleted(doc):
            continue
        default_store = _first_text(doc, ("defaultStore", "store", "storeId"))
        for items in doc.findall(".//items"):
            for item in items.findall("item"):
                store_id = (item.findtext("store") or "").strip() or (default_store or "")
                if not store_id:
                    continue
                amount = decimal_from_text(item.findtext("sum"))
                totals[store_id] += amount
    return dict(totals)


def parse_invoice_payload(
    xml_payload: str,
    *,
    include_deleted: bool,
) -> tuple[list[InvoiceSummary], dict[str, Decimal]]:
    root = ET.fromstring(xml_payload)
    summaries: list[InvoiceSummary] = []
    for node in iter_invoice_nodes(root):
        if not include_deleted and is_deleted(node):
            continue
        summaries.append(summarize_invoice(node))
    if not summaries and root.tag in {"document", "incomingInvoiceDto"}:
        if include_deleted or not is_deleted(root):
            summaries.append(summarize_invoice(root))
    store_totals = collect_store_totals(root, include_deleted=include_deleted)
    return summaries, store_totals


def parse_args() -> argparse.Namespace:
    today = datetime.now().date()
    default_start = (today.replace(day=1) - timedelta(days=0)).strftime("%Y-%m-%d")
    default_end = today.strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(description="Сумма приходных накладных из iiko")
    parser.add_argument("--from-date", dest="date_from", default=default_start, help="Дата начала периода (YYYY-MM-DD)")
    parser.add_argument("--to-date", dest="date_to", default=default_end, help="Дата конца периода (YYYY-MM-DD)")
    parser.add_argument("--supplier-id", help="Фильтр по поставщику (GUID)")
    parser.add_argument("--list", action="store_true", help="Показать суммы по каждой накладной")
    parser.add_argument(
        "--no-supplier-summary",
        action="store_true",
        help="Не группировать суммы по поставщикам",
    )
    parser.add_argument(
        "--no-store-summary",
        action="store_true",
        help="Не группировать суммы по складам",
    )
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Включать удаленные документы в расчет",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout")
    return parser.parse_args()


def print_report(summaries: Sequence[InvoiceSummary], *, skip_supplier_summary: bool) -> None:
    if not summaries:
        print("Накладных не найдено")
        return
    total = sum((s.total for s in summaries), Decimal("0"))
    print(f"Всего накладных: {len(summaries)}")
    print(f"Суммарный приход: {total:,.2f}")
    if skip_supplier_summary:
        return
    totals: dict[str, Decimal] = {}
    for summary in summaries:
        supplier_label = summary.supplier_name or summary.supplier_id or "(Без поставщика)"
        if summary.supplier_id and summary.supplier_name:
            supplier_label = f"{summary.supplier_name} ({summary.supplier_id})"
        totals[supplier_label] = totals.get(supplier_label, Decimal("0")) + summary.total
    print("\nСуммы по поставщикам:")
    for supplier, supplier_total in sorted(totals.items(), key=lambda item: item[1], reverse=True):
        print(f" - {supplier}: {supplier_total:,.2f}")


def print_store_totals(
    store_totals: dict[str, Decimal],
    store_map: dict[str, str],
    *,
    skip_store_summary: bool,
) -> None:
    if skip_store_summary or not store_totals:
        return
    print("\nСуммы по складам:")
    for store_id, total in sorted(store_totals.items(), key=lambda item: item[1], reverse=True):
        store_name = store_map.get(store_id)
        label = store_name if store_name else store_id
        if store_name:
            label = f"{store_name} ({store_id})"
        print(f" - {label}: {total:,.2f}")


def main() -> None:
    args = parse_args()

    async def _run() -> tuple[list[InvoiceSummary], dict[str, Decimal], dict[str, str], dict[str, str]]:
        xml_payload = await fetch_incoming_invoices(
            date_from=args.date_from,
            date_to=args.date_to,
            supplier_id=args.supplier_id,
            timeout=args.timeout,
        )
        summaries, store_totals = parse_invoice_payload(
            xml_payload,
            include_deleted=args.include_deleted,
        )
        supplier_ids = {s.supplier_id for s in summaries if s.supplier_id}
        supplier_map: dict[str, str] = {}
        if supplier_ids:
            supplier_map = await fetch_supplier_name_map(timeout=args.timeout)
        store_map: dict[str, str] = {}
        if store_totals:
            store_map = await fetch_store_map(timeout=args.timeout)
        return summaries, store_totals, supplier_map, store_map

    summaries, store_totals, supplier_map, store_map = asyncio.run(_run())
    if supplier_map:
        for summary in summaries:
            if summary.supplier_id and not summary.supplier_name:
                summary.supplier_name = supplier_map.get(summary.supplier_id)

    print_report(summaries, skip_supplier_summary=args.no_supplier_summary)
    print_store_totals(store_totals, store_map, skip_store_summary=args.no_store_summary)
    if args.list:
        for summary in summaries:
            date_label = summary.date or "?"
            number_label = summary.number or "(без номера)"
            supplier_label = summary.supplier_name or summary.supplier_id or "(Без поставщика)"
            print(f"{date_label} | {number_label} | {supplier_label} | {summary.total:,.2f}")


if __name__ == "__main__":
    main()
