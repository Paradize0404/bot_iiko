"""Синхронизация входящих услуг (INCOMING_SERVICE) по счетам iiko в FinTablo.

Шаги:
- Читаем маппинг из листа "настройка счетов": колонка B — имя счёта iiko, C — FinTablo categoryId.
- Получаем отчёт TRANSACTIONS за текущий месяц по TransactionType=INCOMING_SERVICE.
- Суммируем по Account.Name.
- Для совпадающих имён из маппинга отправляем сумму в FinTablo по categoryId, месяцем MM.YYYY.
- Работает в дельта-режиме: если в FinTablo уже есть сумма за месяц по категории, докидываем разницу.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
# Направление FinTablo для записей INCOMING_SERVICE ("Клиническая")
DIRECTION_ID = 148270
from services.gsheets_client import GoogleSheetsClient
from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)


def _month_bounds(today: date) -> tuple[str, str, str]:
    start = today.replace(day=1)
    # для OLAP нужен формат dd.MM.yyyy
    start_h = start.strftime("%d.%m.%Y")
    end_h = today.strftime("%d.%m.%Y")
    return start_h, end_h, today.strftime("%m.%Y")


def _parse_response(response: httpx.Response) -> List[Dict[str, Any]]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = response.json()
        return payload.get("data") or payload.get("rows") or []
    if content_type.startswith("application/xml") or content_type.startswith("text/xml"):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(response.text)
        rows: List[Dict[str, Any]] = []
        for row in root.findall("./r"):
            rows.append({child.tag: child.text for child in row})
        return rows
    raise RuntimeError(f"Неизвестный формат ответа: {content_type}\n{response.text[:400]}")


async def _fetch_transactions(date_from: str, date_to: str) -> List[Dict[str, Any]]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", date_from),
        ("to", date_to),
        ("groupRow", "TransactionType"),
        ("groupRow", "Account.Name"),
        ("agr", "Sum.Incoming"),
        ("TransactionType", "INCOMING_SERVICE"),
    ]

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0, verify=False) as client:
        response = await client.get("/resto/api/reports/olap", params=params)
    response.raise_for_status()
    rows = _parse_response(response)
    logger.info("Получено %d строк TRANSACTIONS INCOMING_SERVICE", len(rows))
    return rows


def _aggregate_incoming(rows: List[Dict[str, Any]]) -> Dict[str, Decimal]:
    totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        trx_type = str(row.get("TransactionType") or "").strip().upper()
        if trx_type != "INCOMING_SERVICE":
            continue
        account_name = str(row.get("Account.Name") or row.get("Account") or "").strip()
        if not account_name:
            continue
        raw_val = row.get("Sum.Incoming") or row.get("Sum") or row.get("SumIn")
        try:
            amount = Decimal(str(raw_val))
        except Exception:
            amount = Decimal("0")
        totals[account_name] += amount
    return totals


def _read_sheet_mapping(client: GoogleSheetsClient) -> List[Tuple[str, int]]:
    values = client.read_range(f"'{'настройка счетов'}'!A2:D")
    pairs: List[Tuple[str, int]] = []
    for row in values:
        if len(row) < 3:
            continue
        account_name = (row[1] or "").strip()
        cat_id_raw = row[2]
        if not account_name or not cat_id_raw:
            continue
        try:
            cat_id = int(cat_id_raw)
        except Exception:
            continue
        pairs.append((account_name, cat_id))
    return pairs


async def _build_payloads() -> List[Dict[str, Any]]:
    today = date.today()
    date_from, date_to, month_str = _month_bounds(today)

    sheet_client = GoogleSheetsClient()
    mapping = _read_sheet_mapping(sheet_client)
    if not mapping:
        logger.warning("Нет маппинга счёт -> categoryId в листе, пропускаем отправку")
        return []

    trx_rows = await _fetch_transactions(date_from, date_to)
    totals = _aggregate_incoming(trx_rows)
    if not totals:
        logger.warning("В TRANSACTIONS нет строк INCOMING_SERVICE за период %s-%s", date_from, date_to)
        return []

    payloads: List[Dict[str, Any]] = []
    for account_name, cat_id in mapping:
        amount = totals.get(account_name)
        if amount is None:
            continue
        value = float(amount)
        payloads.append(
            {
                "categoryId": cat_id,
                "value": value,
                "date": month_str,
                "comment": f"iiko INCOMING_SERVICE {account_name}",
                "directionId": DIRECTION_ID,
            }
        )
    return payloads


async def _apply_delta(cli: FinTabloClient, payload: Dict[str, Any]) -> Dict[str, Any] | None:
    params = {"date": payload["date"], "categoryId": payload["categoryId"]}
    existing = await cli.list_pnl_items(**params)
    existing_sum = sum((item.get("value") or 0) for item in existing)
    desired = round(payload["value"], 2)
    diff = round(desired - existing_sum, 2)

    if abs(diff) < 0.005:
        return None

    # Если текущие данные больше требуемых — чистим и ставим точное значение одной записью
    if diff < 0:
        for item in existing:
            item_id = item.get("id")
            if item_id:
                await cli.delete_pnl_item(int(item_id))
        new_payload = dict(payload)
        new_payload["value"] = desired
        new_payload["comment"] = f"{payload.get('comment', '')} (reset to exact)".strip()
        return new_payload

    # Если меньше — докидываем дельту
    new_payload = dict(payload)
    new_payload["value"] = diff
    new_payload["comment"] = f"{payload.get('comment', '')} (delta +{diff:.2f} до {desired:.2f})".strip()
    return new_payload


async def sync_incoming_service_accounts() -> None:
    load_dotenv()
    payloads = await _build_payloads()
    if not payloads:
        return

    async with FinTabloClient() as cli:
        for payload in payloads:
            delta_payload = await _apply_delta(cli, payload)
            if not delta_payload:
                logger.info("Пропуск: %s уже актуально", payload.get("comment"))
                continue
            try:
                created = await cli.create_pnl_item(delta_payload)
                logger.info(
                    "✅ Отправлено %.2f в FinTablo для категории %s (%s) id=%s",
                    delta_payload["value"],
                    payload["categoryId"],
                    payload["comment"],
                    created.get("id"),
                )
            except httpx.HTTPStatusError as exc:  # noqa: BLE001
                logger.error("❌ Не удалось отправить %s: %s", payload.get("comment"), exc)


async def main() -> int:
    await sync_incoming_service_accounts()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))