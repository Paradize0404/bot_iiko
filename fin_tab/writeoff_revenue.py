"""Minimal writeoff revenue fetcher for FinTablo (outgoing invoices)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

import httpx

from fin_tab import iiko_auth

logger = logging.getLogger(__name__)


async def fetch_writeoff_revenue(date_from: str, date_to: str) -> float:
    """Return total revenue from outgoingInvoice documents for the period.

    Only PROCESSED documents are counted; revenue is the sum of <sum> across items.
    """
    token = await iiko_auth.get_auth_token()
    base_url = iiko_auth.get_base_url()

    url = f"{base_url}/resto/api/documents/export/outgoingInvoice"
    params = {"from": date_from, "to": date_to}

    async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
        resp = await client.get(url, params=params, headers={"Cookie": f"key={token}"})

    if resp.status_code != 200:
        logger.warning("writeoff export failed: %s", resp.text[:300])
        return 0.0

    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("writeoff XML parse error: %s", exc)
        return 0.0

    total_revenue = 0.0

    for doc_node in root.findall(".//document"):
        status = (doc_node.findtext("status", "") or "").strip()
        if status != "PROCESSED":
            continue

        items_node = doc_node.find("items")
        if items_node is None:
            continue

        for item in items_node.findall("item"):
            try:
                total_revenue += float(item.findtext("sum", "0") or 0)
            except (TypeError, ValueError):
                continue

    logger.info("writeoff revenue %s-%s: %.2f", date_from, date_to, total_revenue)
    return float(total_revenue)


async def fetch_writeoff_cost(date_from: str, date_to: str) -> float:
    """Return cost of outgoing invoices via TRANSACTIONS OLAP.

    Uses transaction type OUTGOING_INVOICE which reflects себестоимость списания.
    """

    try:
        token = await iiko_auth.get_auth_token()
        base_url = iiko_auth.get_base_url()

        date_from_display = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
        date_to_display = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")

        params = [
            ("key", token),
            ("report", "TRANSACTIONS"),
            ("from", date_from_display),
            ("to", date_to_display),
            ("groupRow", "TransactionType"),
            ("agr", "Sum"),
            ("TransactionType", "OUTGOING_INVOICE"),
        ]

        async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
            resp = await client.get("/resto/api/reports/olap", params=params)

        if resp.status_code != 200:
            logger.warning("writeoff cost export failed: %s", resp.text[:300])
            return 0.0

        ct = resp.headers.get("content-type", "")

        if ct.startswith("application/json"):
            data = resp.json()
            report_data = data.get("data", []) or data.get("rows", [])
        elif ct.startswith("application/xml") or ct.startswith("text/xml"):
            import xml.etree.ElementTree as ET

            def _auto_cast(text: str | None):
                if text is None:
                    return None
                try:
                    return int(text)
                except Exception:
                    try:
                        return float(text)
                    except Exception:
                        return text.strip()

            root = ET.fromstring(resp.text)
            report_data = []
            for row in root.findall("./r"):
                report_data.append({child.tag: _auto_cast(child.text) for child in row})
        else:
            logger.warning("writeoff cost: unknown content type %s", ct)
            return 0.0

        total_cost = 0.0
        for row in report_data:
            if row.get("TransactionType") == "OUTGOING_INVOICE":
                try:
                    total_cost = float(row.get("Sum") or 0.0)
                except (TypeError, ValueError):
                    total_cost = 0.0
                break

        logger.info("writeoff cost %s-%s: %.2f", date_from, date_to, total_cost)
        return float(total_cost)
    except Exception as exc:  # noqa: BLE001
        logger.warning("writeoff cost fetch failed: %s", exc)
        return 0.0
